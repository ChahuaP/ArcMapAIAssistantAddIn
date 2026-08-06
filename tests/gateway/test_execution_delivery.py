import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from arcmap_runtime_py2.execution_outbox import ExecutionOutbox
from gateway_py3.run_store import RunStore
from gateway_py3.validators import context_hash


TARGET = {"bridge_pid": 7, "bridge_port": 8766, "arcmap_pid": 70, "hwnd": 9}


class StoreClient:
    def __init__(self, store, fail_first=False):
        self.store = store
        self.fail_first = fail_first
        self.calls = 0

    def complete_run(self, run_id, status, result, owner_id, result_hash, target):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise IOError("gateway unavailable")
        return self.store.complete_execution(run_id, status, result, owner_id, result_hash, target)


class ExecutionDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = RunStore(root / "runs.sqlite")
        context = {"layers": []}
        run = self.store.create_run("x", "g1_context")
        self.store.bind_context(run["id"], {
            "context": context, "context_hash": context_hash(context), "bridge": TARGET, "captured_at": 1,
        })
        self.store.update_run(run["id"], "planned")
        trace = self.store.run_trace(run["id"])
        trace["stages"].append({
            "name": "execution", "started_at": 2.0, "status": "running",
        })
        self.store.update_run(run["id"], "approved", trace=trace)
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner")
        self.run_id = run["id"]
        self.outbox_path = root / "execution-outbox"

    def tearDown(self):
        self.temp.cleanup()

    def test_failed_first_delivery_survives_restart_and_replays_authoritatively(self):
        result = {"ok": True, "summary": "authoritative"}
        outbox = ExecutionOutbox(str(self.outbox_path))
        entry = outbox.enqueue(self.run_id, "runtime-owner", "executed", result, TARGET, [])
        self.assertEqual(entry["run_id"], self.run_id)
        self.assertEqual(entry["owner"], "runtime-owner")
        self.assertEqual(entry["target"], TARGET)
        self.assertEqual(len(entry["result_hash"]), 64)
        with self.assertRaises(IOError):
            outbox.deliver(entry, StoreClient(self.store, fail_first=True))
        self.assertEqual(len(outbox.pending()), 1)
        self.assertEqual(list(self.outbox_path.glob("*.tmp")), [])

        restarted = ExecutionOutbox(str(self.outbox_path))
        restarted.drain(StoreClient(self.store))
        self.assertEqual(restarted.pending(), [])
        row = self.store.get(self.run_id)
        self.assertEqual(row["status"], "executed")
        self.assertEqual(row["result"], result)
        self.assertEqual(list(self.outbox_path.glob("*.guard")), [])

    def test_startup_prunes_orphan_guard_files(self):
        self.outbox_path.mkdir(parents=True)
        run_id = str(uuid.uuid4())
        orphan = self.outbox_path / (run_id + ".lease.guard")
        orphan_lease = self.outbox_path / (run_id + ".lease")
        orphan.write_bytes(b"0")
        orphan_lease.write_text("{}", encoding="ascii")

        ExecutionOutbox(str(self.outbox_path))

        self.assertFalse(orphan.exists())
        self.assertFalse(orphan_lease.exists())

    def test_two_reloaded_outbox_instances_have_one_delivery_owner_for_20_rounds(self):
        for round_index in range(20):
            directory = self.outbox_path / ("reload-%d" % round_index)
            first = ExecutionOutbox(str(directory))
            second = ExecutionOutbox(str(directory))
            entry = first.enqueue(
                str(uuid.uuid4()), "runtime-owner", "executed", {"ok": True}, TARGET, []
            )
            client = BlockingClient()
            outcomes = []
            errors = []

            def deliver(outbox):
                try:
                    outcomes.append(outbox.deliver(entry, client))
                except Exception as exc:
                    errors.append(exc)

            owner = threading.Thread(target=deliver, args=(first,))
            contender = threading.Thread(target=deliver, args=(second,))
            owner.start()
            self.assertTrue(client.entered.wait(1))
            contender.start()
            contender.join(1)
            self.assertFalse(contender.is_alive(), "lease contender must exit without waiting on Gateway")
            client.release.set()
            owner.join(1)
            self.assertFalse(owner.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(client.calls, 1)
            self.assertEqual(sorted(outcomes), [False, True])
            self.assertEqual(first.pending(), [])

    def test_missing_acknowledged_entry_is_success_not_retry(self):
        outbox = ExecutionOutbox(str(self.outbox_path / "ack-race"))
        entry = outbox.enqueue(str(uuid.uuid4()), "runtime-owner", "executed", {"ok": True}, TARGET, [])
        self.assertTrue(outbox.deliver(entry, StorelessClient()))
        self.assertTrue(outbox.deliver(entry, StorelessClient()))

    def test_delivery_lease_has_strict_owner_and_expiry_recovery(self):
        outbox = ExecutionOutbox(str(self.outbox_path / "lease-expiry"))
        run_id = str(uuid.uuid4())
        owner_a = str(uuid.uuid4())
        owner_b = str(uuid.uuid4())
        self.assertTrue(outbox._acquire_delivery_lease(run_id, owner_a, now=10))
        self.assertFalse(outbox._acquire_delivery_lease(run_id, owner_b, now=39))
        self.assertTrue(outbox._acquire_delivery_lease(run_id, owner_b, now=40))
        outbox._release_delivery_lease(run_id, owner_a)
        self.assertFalse(outbox._acquire_delivery_lease(run_id, owner_a, now=41))
        outbox._release_delivery_lease(run_id, owner_b)
        self.assertTrue(outbox._acquire_delivery_lease(run_id, owner_a, now=41))
        outbox._release_delivery_lease(run_id, owner_a)

    def test_expired_lease_has_exactly_one_concurrent_takeover_owner_for_20_rounds(self):
        for round_index in range(20):
            outbox = ExecutionOutbox(str(self.outbox_path / ("expired-%d" % round_index)))
            run_id = str(uuid.uuid4())
            original_owner = str(uuid.uuid4())
            self.assertTrue(outbox._acquire_delivery_lease(run_id, original_owner, now=0))
            contenders = [str(uuid.uuid4()), str(uuid.uuid4())]
            barrier = threading.Barrier(2)
            read_count = [0]
            read_lock = threading.Lock()
            original_read = ExecutionOutbox._read_lease

            def synchronized_read(path):
                lease = original_read(path)
                with read_lock:
                    read_count[0] += 1
                    wait_here = read_count[0] <= 2
                return lease

            outcomes = []
            with patch.object(ExecutionOutbox, "_read_lease", staticmethod(synchronized_read)):
                threads = [threading.Thread(
                    target=lambda owner: outcomes.append(outbox._acquire_delivery_lease(run_id, owner, now=30)),
                    args=(owner,),
                ) for owner in contenders]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(1)
                    self.assertFalse(thread.is_alive())
            self.assertEqual(sorted(outcomes), [False, True])
            self.assertEqual(list((self.outbox_path / ("expired-%d" % round_index)).glob("*.expired.*")), [])

    def test_partial_lease_left_by_crash_is_recoverable_after_expiry(self):
        outbox = ExecutionOutbox(str(self.outbox_path / "partial-lease"))
        run_id = str(uuid.uuid4())
        lease_path = outbox._lease_path(run_id)
        with open(lease_path, "wb") as handle:
            handle.write(b"{")
        os.utime(lease_path, (0, 0))
        owner = str(uuid.uuid4())
        self.assertTrue(outbox._acquire_delivery_lease(run_id, owner, now=31))
        outbox._release_delivery_lease(run_id, owner)

    def test_protected_indeterminate_accepts_late_outbox_ack_and_stops_retrying(self):
        self.store.recover_stale_executions(
            now=10 ** 10, lease_seconds=0, recovery_window_seconds=0
        )
        self.store.resolve_expired_recoveries(now=10 ** 10)
        self.assertEqual(self.store.get(self.run_id)["status"], "indeterminate")
        with self.assertRaisesRegex(ValueError, "indeterminate runs are protected"):
            self.store.delete(self.run_id)

        outbox = ExecutionOutbox(str(self.outbox_path / "late-authoritative"))
        result = {"ok": True, "summary": "late authoritative"}
        entry = outbox.enqueue(self.run_id, "runtime-owner", "executed", result, TARGET, [])
        client = StoreClient(self.store)
        self.assertTrue(outbox.deliver(entry, client))
        self.assertEqual(outbox.pending(), [])
        self.assertTrue(outbox.deliver(entry, client))
        self.assertEqual(client.calls, 1)
        self.assertEqual(self.store.get(self.run_id)["status"], "executed")

    def test_delivery_waits_for_durable_publication_acknowledgement(self):
        outbox = ExecutionOutbox(str(self.outbox_path / "publication"))
        entry = outbox.enqueue(
            str(uuid.uuid4()), "runtime-owner", "executed", {"ok": True}, TARGET,
            [{"path": r"D:\out\final.shp", "visible": True, "selection_oids": None}],
        )
        with self.assertRaisesRegex(ValueError, "have not been published"):
            outbox.deliver(entry, StorelessClient())
        completed = outbox.mark_publication_complete(entry)
        self.assertTrue(completed["publication_complete"])
        self.assertTrue(outbox.deliver(completed, StorelessClient()))


class BlockingClient:
    def __init__(self):
        self.calls = 0
        self.lock = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete_run(self, *args):
        with self.lock:
            self.calls += 1
            call_number = self.calls
        if call_number == 1:
            self.entered.set()
            self.release.wait(2)


class StorelessClient:
    def complete_run(self, *args):
        return {"ok": True}


if __name__ == "__main__":
    unittest.main()
