import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.event_bus import EventBus
from gateway_py3.routes import handle_get, handle_post
from gateway_py3.routes import runs
from arcmap_runtime_py2.execution_outbox import result_hash
from gateway_py3.validators import context_hash
from gateway_py3.run_store import RunStore


class PlannedRunner:
    def __init__(self, store):
        self.store = store

    def plan(self, run_id, command, context, mode, provider, model):
        return self.store.update_run(
            run_id,
            "planned",
            workflow={"action": "execute", "summary": command, "steps": []},
        )


class RunsRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        store = RunStore(Path(self.temp.name) / "runs.sqlite")
        self.state = SimpleNamespace(
            catalog=OperationCatalog(),
            events=EventBus(),
            runner=PlannedRunner(store),
            run_scheduler=_run_synchronously,
            store=store,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_create_get_cancel_and_filtered_report(self):
        context = {"layers": []}
        capture = {"context": context, "context_hash": context_hash(context), "bridge": {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}, "captured_at": 1}
        with patch("gateway_py3.routes.runs.arcmap.active_bridge", return_value=capture["bridge"]), \
             patch("gateway_py3.routes.runs.arcmap.sync_context", return_value=capture):
            first = handle_post(self.state, "/runs", _request("g1_context"))["run"]
            second = handle_post(self.state, "/runs", _request("g0_direct"))["run"]

        fetched = handle_get(
            self.state,
            "/runs/" + first["id"],
            "test",
        )["run"]
        cancelled = handle_post(
            self.state,
            "/runs/%s/cancel" % first["id"],
            {},
        )["run"]
        report = handle_get(
            self.state,
            "/runs/report",
            "test",
            {"mode": ["g0_direct"]},
        )

        self.assertEqual(fetched["id"], first["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual([run["id"] for run in report["runs"]], [second["id"]])

    def test_invalid_uuid_and_unknown_field_fail(self):
        with self.assertRaises(ValueError):
            handle_get(self.state, "/runs/not-a-uuid", "test")
        with self.assertRaises(ValueError):
            handle_post(
                self.state,
                "/runs/not-a-uuid/cancel",
                {},
            )
        with self.assertRaises(ValueError):
            handle_post(
                self.state,
                "/runs",
                dict(_request("g1_context"), unexpected=True),
            )

    def test_invalid_external_artifacts_do_not_create_run(self):
        invalid = _request("g2_constrained")
        invalid.update({
            "source": "orchestrator",
            "task_contract": {
                "invalid": "external artifact injection",
            },
            "workflow_draft": {"action": "execute"},
        })

        with self.assertRaises(ValueError):
            handle_post(self.state, "/runs", invalid)

        self.assertEqual(self.state.store.export_runs()["runs"], [])

    def test_execution_state_endpoint_exposes_only_the_authoritative_status(self):
        run = self.state.store.create_run("state", "g1_context")

        result = handle_get(
            self.state, "/runs/%s/execution-state" % run["id"], "test",
        )

        self.assertEqual(result, {"status": "running"})

    def test_target_episode_fifo_blocks_next_capture_until_prior_episode_releases(self):
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        first = self.state.store.create_run("first", "g1_context")
        second = self.state.store.create_run("second", "g1_context")
        self.state.store.reserve_target_episode(first["id"], target)
        self.state.store.reserve_target_episode(second["id"], target)
        self.assertTrue(self.state.store.claim_target_episode(first["id"]))
        self.assertFalse(self.state.store.claim_target_episode(second["id"]))
        self.state.store.release_target_episode(first["id"])
        self.assertTrue(self.state.store.claim_target_episode(second["id"]))

    def test_target_episodes_allow_different_targets_and_survive_restart_blocked(self):
        first = self.state.store.create_run("first", "g1_context")
        same_target = self.state.store.create_run("same", "g1_context")
        other_target = self.state.store.create_run("other", "g1_context")
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        self.state.store.reserve_target_episode(first["id"], target)
        self.state.store.reserve_target_episode(same_target["id"], target)
        self.state.store.reserve_target_episode(other_target["id"], {"bridge_pid": 3, "bridge_port": 8766, "arcmap_pid": 30, "hwnd": 2})
        self.assertTrue(self.state.store.claim_target_episode(first["id"]))
        self.assertTrue(self.state.store.claim_target_episode(other_target["id"]))
        restarted = RunStore(Path(self.temp.name) / "runs.sqlite")
        self.assertFalse(restarted.claim_target_episode(same_target["id"]))

    def test_bridge_restart_preserves_episode_but_arcmap_restart_creates_new_target(self):
        first = self.state.store.create_run("first", "g1_context")
        bridge_restarted = self.state.store.create_run("bridge restarted", "g1_context")
        arcmap_restarted = self.state.store.create_run("arcmap restarted", "g1_context")
        original = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        self.state.store.reserve_target_episode(first["id"], original)
        self.state.store.reserve_target_episode(bridge_restarted["id"], {"bridge_pid": 9, "bridge_port": 8770, "arcmap_pid": 20, "hwnd": 1})
        self.state.store.reserve_target_episode(arcmap_restarted["id"], {"bridge_pid": 9, "bridge_port": 8770, "arcmap_pid": 21, "hwnd": 1})
        self.assertTrue(self.state.store.claim_target_episode(first["id"]))
        self.assertFalse(self.state.store.claim_target_episode(bridge_restarted["id"]))
        self.assertTrue(self.state.store.claim_target_episode(arcmap_restarted["id"]))

    def test_atomic_target_reservation_rejects_both_concurrent_creates_after_recovery(self):
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        interrupted = self.state.store.create_run_for_target("interrupted", "g1_context", target)
        self.assertTrue(self.state.store.claim_target_episode(interrupted["id"]))
        context = {"layers": []}
        self.state.store.bind_context(interrupted["id"], {"context": context, "context_hash": context_hash(context), "bridge": target, "captured_at": 1})
        self.state.store.update_run(interrupted["id"], "planned")
        _start_execution_stage(self.state.store, interrupted["id"])
        self.state.store.claim_for_execution(interrupted["id"], target, "owner", now=10)
        recovered = self.state.store.recover_stale_executions(now=41, lease_seconds=30)
        self.assertEqual(recovered, [interrupted["id"]])
        barrier = threading.Barrier(3)
        errors = []

        def create():
            barrier.wait()
            try:
                self.state.store.create_run_for_target("blocked", "g1_context", target)
            except ValueError as exc:
                errors.append(str(exc))

        first = threading.Thread(target=create)
        second = threading.Thread(target=create)
        first.start(); second.start(); barrier.wait()
        first.join(1); second.join(1)
        self.assertEqual(len(errors), 2)
        self.assertEqual(len(list(self.state.store.iter_runs())), 1)

    def test_queued_episode_fails_and_releases_when_head_becomes_recovery_required(self):
        self._assert_queued_episode_stops_for_uncertain_head(False)

    def test_queued_episode_fails_and_releases_when_head_becomes_indeterminate(self):
        self._assert_queued_episode_stops_for_uncertain_head(True)

    def _assert_queued_episode_stops_for_uncertain_head(self, indeterminate):
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        context = {"layers": []}
        first = self.state.store.create_run_for_target("A", "g1_context", target)
        self.assertTrue(self.state.store.claim_target_episode(first["id"]))
        self.state.store.bind_context(first["id"], {"context": context, "context_hash": context_hash(context), "bridge": target, "captured_at": 1})
        self.state.store.update_run(first["id"], "planned")
        _start_execution_stage(self.state.store, first["id"])
        self.state.store.claim_for_execution(first["id"], target, "owner-a", now=10)
        second = self.state.store.create_run_for_target("B", "g1_context", target)
        recovered = self.state.store.recover_stale_executions(
            now=41, lease_seconds=30, recovery_window_seconds=0
        )
        self.assertEqual(recovered, [first["id"]])
        if indeterminate:
            self.state.store.resolve_expired_recoveries(now=41)
        worker = threading.Thread(target=runs._run, args=(None, self.state, second["id"], {}))
        worker.start(); worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.state.store.get(second["id"])["status"], "failed")
        self.assertFalse(self.state.store.claim_target_episode(second["id"]))

    def test_same_target_episode_captures_b_only_after_a_release(self):
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        events = []
        workers = []
        events_lock = threading.Lock()
        a_after_entered = threading.Event()
        allow_a_after = threading.Event()
        original_release = self.state.store.release_target_episode
        original_create_for_target = self.state.store.create_run_for_target
        a_reserved = threading.Event()

        def record(name):
            with events_lock:
                events.append(name)

        def scheduler(callback, args):
            worker = threading.Thread(target=callback, args=args)
            workers.append(worker)
            worker.start()

        def capture(_state, run_id, phase, bridge=None, finalizer=None):
            command = self.state.store.get(run_id)["command"]
            record("%s:capture_%s" % (command, phase))
            if command == "A" and phase == "after_execution":
                a_after_entered.set()
                self.assertTrue(allow_a_after.wait(1))
            context = {"layers": []}
            return {"context": context, "context_hash": context_hash(context), "bridge": target, "captured_at": 1}

        def execute(run_id, allow_edits=False, port=None, hwnd=None):
            command = self.state.store.get(run_id)["command"]
            record("%s:execute" % command)
            self.state.store.claim_for_execution(run_id, target, "owner-" + run_id)
            result = {"ok": True, "summary": command}
            self.state.store.complete_execution(run_id, "executed", result, "owner-" + run_id, result_hash(result), target)

        def release(run_id):
            record("%s:release" % self.state.store.get(run_id)["command"])
            return original_release(run_id)

        def create_for_target(command, mode, bridge):
            if command == "B":
                self.assertTrue(a_reserved.wait(1))
            result = original_create_for_target(command, mode, bridge)
            record("%s:reserve" % command)
            if command == "A":
                a_reserved.set()
            return result

        self.state.run_scheduler = scheduler
        self.state.store.release_target_episode = release
        self.state.store.create_run_for_target = create_for_target
        with patch("gateway_py3.routes.runs.arcmap.active_bridge", side_effect=lambda state: record("active_bridge") or target), \
             patch("gateway_py3.routes.runs.arcmap.sync_context", side_effect=capture), \
             patch("gateway_py3.routes.runs.arcmap.execution_permission", return_value=False), \
             patch("gateway_py3.routes.runs.arcmap.arcmap_bridge_client.execute_run", side_effect=execute):
            barrier = threading.Barrier(3)
            submitted = []

            def submit(command):
                barrier.wait()
                submitted.append(runs.create(self.state, dict(_request("g1_context"), command=command, execute=True)))

            first = threading.Thread(target=submit, args=("A",))
            second = threading.Thread(target=submit, args=("B",))
            first.start(); second.start(); barrier.wait()
            self.assertTrue(a_after_entered.wait(1), events)
            allow_a_after.set()
            first.join(1); second.join(1)
            for worker in workers:
                worker.join(1)
                self.assertFalse(worker.is_alive())

        self.assertLess(events.index("A:release"), events.index("B:capture_before_planning"))
        self.assertEqual(events.count("A:capture_after_execution"), 1)
        self.assertEqual(events.count("B:capture_after_execution"), 1)

    def test_bridge_transport_failure_after_runtime_claim_waits_for_authoritative_result(self):
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        context = {"layers": []}
        capture = {
            "context": context,
            "context_hash": context_hash(context),
            "bridge": target,
            "captured_at": 1,
        }
        workers = []

        def scheduler(callback, args):
            worker = threading.Thread(target=callback, args=args)
            workers.append(worker)
            worker.start()

        def execute(run_id, allow_edits=False, port=None, hwnd=None):
            self.state.store.claim_for_execution(run_id, target, "runtime-owner")
            raise RuntimeError("RPC_E_SERVERFAULT")

        self.state.run_scheduler = scheduler
        with patch("gateway_py3.routes.runs.arcmap.active_bridge", return_value=target), \
             patch("gateway_py3.routes.runs.arcmap.sync_context", return_value=capture), \
             patch("gateway_py3.routes.runs.arcmap.execution_permission", return_value=False), \
             patch("gateway_py3.routes.runs.arcmap.arcmap_bridge_client.execute_run", side_effect=execute):
            submitted = handle_post(
                self.state,
                "/runs",
                dict(_request("g2_constrained"), execute=True),
            )["run"]
            worker = workers[0]
            deadline = time.time() + 1
            while self.state.store.get(submitted["id"])["status"] != "executing" and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(worker.is_alive())
            result = {"ok": True, "summary": "authoritative"}
            self.state.store.complete_execution(
                submitted["id"], "executed", result, "runtime-owner", result_hash(result), target
            )
            worker.join(1)

        self.assertFalse(worker.is_alive())
        row = self.state.store.get(submitted["id"])
        self.assertEqual(row["status"], "succeeded")
        self.assertIn(
            "RPC_E_SERVERFAULT",
            row["agent_trace"][0]["run"]["execution_transport_warning"]["reason"],
        )
        next_run = self.state.store.create_run_for_target("next", "g2_constrained", target)
        self.assertEqual(next_run["status"], "running")

    def test_bridge_transport_failure_before_runtime_claim_is_a_terminal_failure(self):
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        context = {"layers": []}
        capture = {
            "context": context,
            "context_hash": context_hash(context),
            "bridge": target,
            "captured_at": 1,
        }
        with patch("gateway_py3.routes.runs.arcmap.active_bridge", return_value=target), \
             patch("gateway_py3.routes.runs.arcmap.sync_context", return_value=capture), \
             patch("gateway_py3.routes.runs.arcmap.execution_permission", return_value=False), \
             patch(
                 "gateway_py3.routes.runs.arcmap.arcmap_bridge_client.execute_run",
                 side_effect=RuntimeError("Bridge rejected before runtime claim"),
             ):
            row = handle_post(
                self.state,
                "/runs",
                dict(_request("g2_constrained"), execute=True),
            )["run"]

        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["agent_trace"][0]["run"]["failure"]["stage"], "execution")
        self.assertEqual(
            self.state.store.create_run_for_target("next", "g2_constrained", target)["status"],
            "running",
        )

    def test_transport_failure_arbitration_atomically_rejects_a_late_runtime_claim(self):
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        context = {"layers": []}
        capture = {
            "context": context,
            "context_hash": context_hash(context),
            "bridge": target,
            "captured_at": 1,
        }
        original = self.state.store.reconcile_execution_dispatch_failure
        claim_outcomes = []

        def race_claim_after_arbitration(*args, **kwargs):
            row = original(*args, **kwargs)
            try:
                self.state.store.claim_for_execution(args[0], target, "late-runtime-owner")
            except ValueError:
                claim_outcomes.append("rejected")
            else:
                claim_outcomes.append("claimed")
            return row

        with patch("gateway_py3.routes.runs.arcmap.active_bridge", return_value=target), \
             patch("gateway_py3.routes.runs.arcmap.sync_context", return_value=capture), \
             patch("gateway_py3.routes.runs.arcmap.execution_permission", return_value=False), \
             patch(
                 "gateway_py3.routes.runs.arcmap.arcmap_bridge_client.execute_run",
                 side_effect=RuntimeError("Bridge rejected before runtime claim"),
             ), \
             patch.object(
                 self.state.store,
                 "reconcile_execution_dispatch_failure",
                 side_effect=race_claim_after_arbitration,
             ):
            row = handle_post(
                self.state,
                "/runs",
                dict(_request("g2_constrained"), execute=True),
            )["run"]

        self.assertEqual(claim_outcomes, ["rejected"])
        self.assertEqual(row["status"], "failed")
        execution_stage = next(
            stage for stage in row["agent_trace"][0]["run"]["stages"]
            if stage["name"] == "execution"
        )
        self.assertEqual(execution_stage["status"], "failed")

    def test_authoritative_runtime_result_wins_concurrent_bridge_transport_failure(self):
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        context = {"layers": []}
        capture = {
            "context": context,
            "context_hash": context_hash(context),
            "bridge": target,
            "captured_at": 1,
        }

        def execute(run_id, allow_edits=False, port=None, hwnd=None):
            self.state.store.claim_for_execution(run_id, target, "runtime-owner")
            result = {"ok": True, "summary": "authoritative"}
            self.state.store.complete_execution(
                run_id, "executed", result, "runtime-owner", result_hash(result), target
            )
            raise RuntimeError("RPC_E_SERVERFAULT after authoritative completion")

        with patch("gateway_py3.routes.runs.arcmap.active_bridge", return_value=target), \
             patch("gateway_py3.routes.runs.arcmap.sync_context", return_value=capture), \
             patch("gateway_py3.routes.runs.arcmap.execution_permission", return_value=False), \
             patch("gateway_py3.routes.runs.arcmap.arcmap_bridge_client.execute_run", side_effect=execute):
            row = handle_post(
                self.state,
                "/runs",
                dict(_request("g2_constrained"), execute=True),
            )["run"]

        self.assertEqual(row["status"], "succeeded")
        trace = row["agent_trace"][0]["run"]
        self.assertEqual(trace["execution_receipt"]["status"], "executed")
        self.assertEqual(trace["execution_transport_warning"]["type"], "RuntimeError")
        self.assertEqual(trace["execution"]["context_next_hash"], context_hash(context))

    def test_authoritative_success_completed_before_transport_reconciliation_stays_successful(self):
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        context = {"layers": []}
        capture = {
            "context": context,
            "context_hash": context_hash(context),
            "bridge": target,
            "captured_at": 1,
        }

        def execute(run_id, allow_edits=False, port=None, hwnd=None):
            self.state.store.claim_for_execution(run_id, target, "runtime-owner")
            result = {"ok": True, "summary": "authoritative"}
            self.state.store.complete_execution(
                run_id, "executed", result, "runtime-owner", result_hash(result), target
            )
            fence = self.state.store.claim_context_finalization(run_id, "recovery-owner")
            self.state.store.complete_context_sync(
                run_id, fence, self.state.store.run_trace(run_id)
            )
            raise RuntimeError("RPC_E_SERVERFAULT after authoritative completion")

        with patch("gateway_py3.routes.runs.arcmap.active_bridge", return_value=target), \
             patch("gateway_py3.routes.runs.arcmap.sync_context", return_value=capture), \
             patch("gateway_py3.routes.runs.arcmap.execution_permission", return_value=False), \
             patch("gateway_py3.routes.runs.arcmap.arcmap_bridge_client.execute_run", side_effect=execute):
            row = handle_post(
                self.state,
                "/runs",
                dict(_request("g2_constrained"), execute=True),
            )["run"]

        self.assertEqual(row["status"], "succeeded")
        trace = row["agent_trace"][0]["run"]
        execution_stage = next(
            stage for stage in trace["stages"] if stage["name"] == "execution"
        )
        self.assertEqual(execution_stage["status"], "succeeded")
        self.assertEqual(trace["execution_transport_warning"]["type"], "RuntimeError")

    def test_run_wrapper_schedules_recovery_when_controller_stops_after_execution_result(self):
        target = {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}
        context = {"layers": []}
        capture = {
            "context": context,
            "context_hash": context_hash(context),
            "bridge": target,
            "captured_at": 1,
        }
        row = self.state.store.create_run_for_target("run", "g2_constrained", target)
        self.state.store.bind_context(row["id"], capture)
        self.state.store.update_run(row["id"], "planned")
        self.state.store.update_run(row["id"], "approved")
        scheduled = []
        self.state.schedule_executed_recovery = scheduled.append

        class CrashingController:
            def run(inner_self, run_id, payload):
                trace = self.state.store.run_trace(run_id)
                trace["stages"].append({
                    "name": "execution",
                    "started_at": time.time(),
                    "status": "running",
                })
                self.state.store.update_run(run_id, "approved", trace=trace)
                self.state.store.claim_for_execution(run_id, target, "runtime-owner")
                result = {"ok": True}
                self.state.store.complete_execution(
                    run_id, "executed", result, "runtime-owner", result_hash(result), target
                )
                raise RuntimeError("controller stopped after result")

        runs._run(CrashingController(), self.state, row["id"], {})

        self.assertEqual(scheduled, [row["id"]])
        self.assertEqual(self.state.store.get(row["id"])["status"], "executed")


class PlanArtifactRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        store = RunStore(Path(self.temp.name) / "runs.sqlite")
        self.state = SimpleNamespace(catalog=OperationCatalog(), events=EventBus(), runner=PlannedRunner(store), run_scheduler=_run_synchronously, store=store)

    def tearDown(self):
        self.temp.cleanup()

    def test_g1_plan_artifact_is_rejected_before_run_creation(self):
        payload = _request("g1_context")
        payload["plan_artifact"] = {"not": "allowed"}
        with self.assertRaises(ValueError):
            runs.create(self.state, payload)
        self.assertEqual([], self.state.store.export_runs()["runs"])

    def test_g2_plan_artifact_reaches_public_replay_method(self):
        calls = []
        class ReplayRunner(PlannedRunner):
            def plan_with_artifact(self, run_id, command, context, mode, artifact, provider, model):
                calls.append(artifact)
                return self.store.update_run(run_id, "planned", workflow={"action": "execute", "summary": command, "steps": []})
        self.state.runner = ReplayRunner(self.state.store)
        context = {"layers": []}
        capture = {"context": context, "context_hash": context_hash(context), "bridge": {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}, "captured_at": 1}
        payload = _request("g2_constrained")
        payload["plan_artifact"] = {"frozen": "artifact"}
        with patch("gateway_py3.routes.runs.arcmap.active_bridge", return_value=capture["bridge"]), patch("gateway_py3.routes.runs.arcmap.sync_context", return_value=capture):
            runs.create(self.state, payload)
        self.assertEqual([payload["plan_artifact"]], calls)

    def test_g0_plan_artifact_is_rejected(self):
        with self.assertRaises(ValueError):
            runs.create(self.state, dict(_request("g0_direct"), plan_artifact={}))


def _request(mode):
    return {
        "command": "refresh",
        "mode": mode,
        "execute": False,
    }


def _start_execution_stage(store, run_id):
    trace = store.run_trace(run_id)
    trace["stages"].append({
        "name": "execution", "started_at": time.time(), "status": "running",
    })
    return store.update_run(run_id, "approved", trace=trace)


def _run_synchronously(target, args):
    target(*args)


if __name__ == "__main__":
    unittest.main()
