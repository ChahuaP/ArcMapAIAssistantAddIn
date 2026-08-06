import hashlib
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from gateway_py3.run_controller import RunController
from gateway_py3.gateway_state import GatewayState
from gateway_py3.validators import context_hash
from gateway_py3.run_store import RunStore


TARGET = {"bridge_pid": 7, "bridge_port": 8766, "arcmap_pid": 70, "hwnd": 9}


def result_hash(result):
    payload = json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def capture():
    context = {"layers": []}
    return {
        "context": context,
        "context_hash": context_hash(context),
        "bridge": TARGET,
        "captured_at": time.time(),
    }


class RecoveryContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.temp.name) / "runs.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def approved_run(self):
        run = self.store.create_run("x", "g1_context")
        self.store.bind_context(run["id"], capture())
        self.store.update_run(run["id"], "planned")
        trace = self.store.run_trace(run["id"])
        trace["stages"].append({
            "name": "execution",
            "started_at": 11.0,
            "status": "running",
        })
        return self.store.update_run(run["id"], "approved", trace=trace)

    def test_execution_claim_records_owner_and_heartbeat(self):
        run = self.approved_run()
        claimed = self.store.claim_for_execution(run["id"], TARGET, "runtime-owner")
        execution = self.store.run_trace(run["id"])["execution_owner"]
        self.assertEqual(execution["owner_id"], "runtime-owner")
        self.assertEqual(execution["target"], TARGET)
        first = execution["heartbeat_at"]
        self.store.heartbeat_execution(run["id"], "runtime-owner", now=first + 1)
        self.assertEqual(self.store.run_trace(run["id"])["execution_owner"]["heartbeat_at"], first + 1)
        self.assertEqual(claimed["status"], "executing")

    def test_execution_claim_rejects_an_approved_run_without_execution_stage(self):
        run = self.store.create_run("x", "g1_context")
        self.store.bind_context(run["id"], capture())
        self.store.update_run(run["id"], "planned")
        self.store.update_run(run["id"], "approved")

        with self.assertRaisesRegex(ValueError, "execution stage"):
            self.store.claim_for_execution(run["id"], TARGET, "runtime-owner")

        self.assertEqual(self.store.get(run["id"])["status"], "approved")

    def test_execution_claim_rejects_duplicate_execution_stages(self):
        run = self.approved_run()
        trace = self.store.run_trace(run["id"])
        trace["stages"].insert(0, {
            "name": "execution",
            "started_at": 1.0,
            "finished_at": 1.5,
            "status": "succeeded",
        })
        self.store.update_run(run["id"], "approved", trace=trace)

        with self.assertRaisesRegex(ValueError, "exactly one running execution stage"):
            self.store.claim_for_execution(run["id"], TARGET, "runtime-owner")

        self.assertEqual(self.store.get(run["id"])["status"], "approved")

    def test_gateway_restart_marks_only_stale_execution_recovery_required(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        self.assertEqual(self.store.recover_stale_executions(now=20, lease_seconds=30), [])
        recovered = self.store.recover_stale_executions(now=41, lease_seconds=30)
        self.assertEqual(recovered, [run["id"]])
        self.assertEqual(self.store.get(run["id"])["status"], "recovery_required")
        self.store.heartbeat_execution(run["id"], "runtime-owner", now=42)
        self.assertEqual(self.store.get(run["id"])["status"], "executing")

    def test_runtime_can_complete_after_recovery_required(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        self.store.recover_stale_executions(now=100, lease_seconds=30)
        result = {"ok": True, "summary": "authoritative"}
        row = self.store.complete_execution(
            run["id"], "executed", result, "runtime-owner", result_hash(result), TARGET
        )
        self.assertEqual(row["status"], "executed")
        self.assertEqual(row["result"]["summary"], "authoritative")

    def test_dispatch_failure_after_claim_defers_recovery_to_heartbeat_lease(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        self.store.reconcile_execution_dispatch_failure(
            run["id"],
            "ArcMap Bridge transport failed",
            {"type": "RuntimeError", "message": "transport failed", "hash": "test-hash"},
            now=20,
            recovery_window_seconds=300,
        )

        row = self.store.heartbeat_execution(run["id"], "runtime-owner", now=21)

        self.assertEqual(row["status"], "executing")
        trace = self.store.run_trace(run["id"])
        self.assertEqual(trace["execution_owner"]["heartbeat_at"], 21)
        self.assertIn("ArcMap Bridge transport failed", trace["execution_transport_warning"]["reason"])
        recovered = self.store.recover_stale_executions(now=52, lease_seconds=30)
        self.assertEqual(recovered, [run["id"]])
        self.assertEqual(
            self.store.run_trace(run["id"])["recovery"]["resume_policy"],
            "heartbeat",
        )
        result = {"ok": False, "error": "authoritative late failure"}
        completed = self.store.complete_execution(
            run["id"], "failed", result, "runtime-owner", result_hash(result), TARGET
        )
        self.assertEqual(completed["status"], "failed")

    def test_claimed_runtime_survives_rpc_dispatch_fault_until_authoritative_result(self):
        run = self.store.create_run("x", "g1_context")
        self.store.bind_context(run["id"], capture())
        self.store.update_run(run["id"], "planned")
        self.store.update_run(run["id"], "approved")
        dispatch_returned = threading.Event()

        def execute(run_id, allow_edits, target):
            self.store.claim_for_execution(run_id, target, "runtime-owner")
            self.store.heartbeat_execution(run_id, "runtime-owner")
            dispatch_returned.set()
            raise RuntimeError("RPC_E_SERVERFAULT after ArcMap runtime claim")

        controller = RunController(
            None,
            self.store,
            lambda run_id, target, phase, fence: capture(),
            None,
            execute,
        )
        returned = []
        worker = threading.Thread(
            target=lambda: returned.append(controller._execute(run["id"], False, TARGET))
        )
        worker.start()
        self.assertTrue(dispatch_returned.wait(1))
        time.sleep(0.05)

        self.assertTrue(worker.is_alive())
        self.assertEqual(self.store.get(run["id"])["status"], "executing")
        result = {"ok": True, "summary": "authoritative"}
        self.store.complete_execution(
            run["id"], "executed", result, "runtime-owner", result_hash(result), TARGET
        )
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(returned[0]["status"], "succeeded")
        trace = self.store.run_trace(run["id"])
        self.assertIn("RPC_E_SERVERFAULT", trace["execution_transport_warning"]["reason"])

    def test_stage_finish_and_late_authoritative_result_preserve_each_other(self):
        for index in range(20):
            run = self.approved_run()
            self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
            self.store.reconcile_execution_dispatch_failure(
                run["id"],
                "ArcMap Bridge transport failed",
                {"type": "RuntimeError", "message": "transport failed", "hash": "test-hash"},
                now=12,
            )
            result = {"ok": False, "error": "authoritative-%d" % index}
            barrier = threading.Barrier(3)
            errors = []

            def finish_stage():
                barrier.wait()
                try:
                    self.store.finish_stage(run["id"], "execution", 11.0, "recovery_required")
                except Exception as exc:
                    errors.append(exc)

            def complete():
                barrier.wait()
                try:
                    self.store.complete_execution(
                        run["id"], "failed", result, "runtime-owner",
                        result_hash(result), TARGET,
                    )
                except Exception as exc:
                    errors.append(exc)

            stage_worker = threading.Thread(target=finish_stage)
            result_worker = threading.Thread(target=complete)
            stage_worker.start(); result_worker.start(); barrier.wait()
            stage_worker.join(1); result_worker.join(1)

            self.assertEqual(errors, [])
            row = self.store.get(run["id"])
            final_trace = self.store.run_trace(run["id"])
            self.assertEqual(row["status"], "failed")
            self.assertEqual(final_trace["execution_receipt"]["result_hash"], result_hash(result))
            execution_stage = next(
                item for item in final_trace["stages"]
                if item["name"] == "execution" and item["started_at"] == 11.0
            )
            self.assertEqual(execution_stage["status"], "failed")

    def test_recovery_required_is_excluded_from_report_statistics(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        self.store.recover_stale_executions(now=100, lease_seconds=30)
        report = self.store.export_runs()
        self.assertEqual(report["statistics"]["eligible_runs"], 0)

    def test_recovery_window_expires_to_protected_indeterminate_without_polluting_statistics(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        self.store.recover_stale_executions(
            now=100, lease_seconds=30, recovery_window_seconds=300
        )
        self.assertEqual(self.store.resolve_expired_recoveries(now=399), [])
        self.assertEqual(self.store.resolve_expired_recoveries(now=400), [run["id"]])
        row = self.store.get(run["id"])
        self.assertEqual(row["status"], "indeterminate")
        resolution = self.store.run_trace(run["id"])["indeterminate"]
        self.assertEqual(resolution["owner_id"], "runtime-owner")
        self.assertEqual(resolution["target"], TARGET)
        self.assertEqual(resolution["last_heartbeat_at"], 10)
        self.assertEqual(resolution["reason"], "authoritative ArcMap result unavailable after recovery deadline")
        report = self.store.export_runs()
        self.assertEqual(report["statistics"]["eligible_runs"], 0)
        self.assertEqual(report["statistics"]["indeterminate"], 1)
        with self.assertRaisesRegex(ValueError, "indeterminate runs are protected"):
            self.store.delete(run["id"])

    def test_late_authoritative_result_recovers_indeterminate_with_audit(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        self.store.recover_stale_executions(
            now=100, lease_seconds=30, recovery_window_seconds=300
        )
        self.store.resolve_expired_recoveries(now=400)
        result = {"ok": True, "summary": "late authoritative"}
        row = self.store.complete_execution(
            run["id"], "executed", result, "runtime-owner", result_hash(result), TARGET
        )
        self.assertEqual(row["status"], "executed")
        audit = self.store.run_trace(run["id"])["recovered_after_indeterminate"]
        self.assertEqual(audit["owner_id"], "runtime-owner")
        self.assertEqual(audit["result_hash"], result_hash(result))

    def test_result_after_deadline_before_resolver_tick_is_still_audited(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        self.store.recover_stale_executions(
            now=100, lease_seconds=30, recovery_window_seconds=0
        )
        result = {"ok": True, "summary": "authoritative after deadline"}
        row = self.store.complete_execution(
            run["id"], "executed", result, "runtime-owner", result_hash(result), TARGET
        )
        self.assertEqual(row["status"], "executed")
        trace = self.store.run_trace(run["id"])
        self.assertIn("indeterminate", trace)
        self.assertIn("recovered_after_indeterminate", trace)

    def test_indeterminate_rejects_wrong_identity_then_accepts_authoritative_failure(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        self.store.recover_stale_executions(
            now=100, lease_seconds=30, recovery_window_seconds=0
        )
        self.store.resolve_expired_recoveries(now=100)
        result = {"ok": False, "error": "late authoritative failure"}
        digest = result_hash(result)
        with self.assertRaises(ValueError):
            self.store.complete_execution(
                run["id"], "failed", result, "wrong-owner", digest, TARGET
            )
        with self.assertRaises(ValueError):
            self.store.complete_execution(
                run["id"], "failed", result, "runtime-owner", digest, dict(TARGET, hwnd=10)
            )
        row = self.store.complete_execution(
            run["id"], "failed", result, "runtime-owner", digest, TARGET
        )
        self.assertEqual(row["status"], "failed")
        self.assertEqual(
            self.store.run_trace(run["id"])["recovered_after_indeterminate"]["status"],
            "failed",
        )

    def test_periodic_recovery_resolver_terminates_expired_episode(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        self.store.recover_stale_executions(
            now=time.time(), lease_seconds=0, recovery_window_seconds=0.05
        )
        state = GatewayState(store=self.store)
        stop = threading.Event()
        worker = state.start_recovery_resolver(stop, interval_seconds=0.01)
        deadline = time.time() + 1
        while time.time() < deadline and self.store.get(run["id"])["status"] != "indeterminate":
            time.sleep(0.01)
        stop.set()
        worker.join(1)
        self.assertEqual(self.store.get(run["id"])["status"], "indeterminate")

    def test_execution_completion_is_idempotent_but_rejects_conflicting_replays(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner")
        result = {"ok": True, "summary": "authoritative"}
        digest = result_hash(result)
        first = self.store.complete_execution(
            run["id"], "executed", result, "runtime-owner", digest, TARGET
        )
        duplicate = self.store.complete_execution(
            run["id"], "executed", result, "runtime-owner", digest, TARGET
        )
        self.assertEqual(duplicate, first)
        with self.assertRaises(ValueError):
            self.store.complete_execution(
                run["id"], "failed", {"ok": False}, "runtime-owner", result_hash({"ok": False}), TARGET
            )

    def test_failed_execution_completion_replay_is_idempotent(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner")
        result = {"ok": False, "error": "authoritative failure"}
        digest = result_hash(result)
        first = self.store.complete_execution(
            run["id"], "failed", result, "runtime-owner", digest, TARGET
        )
        duplicate = self.store.complete_execution(
            run["id"], "failed", result, "runtime-owner", digest, TARGET
        )
        self.assertEqual(duplicate, first)

    def test_stale_recovery_cas_cannot_overwrite_a_concurrent_heartbeat(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        scanned = threading.Event()
        heartbeat_started = threading.Event()

        class PausingRecoveryConnection(sqlite3.Connection):
            def execute(connection, sql, parameters=()):
                cursor = super(PausingRecoveryConnection, connection).execute(sql, parameters)
                if "SELECT id, agent_trace_json FROM runs WHERE status = 'executing'" in sql:
                    scanned.set()
                    self.assertTrue(heartbeat_started.wait(2))
                return cursor

        original_connect = self.store._connect
        self.store._connect = lambda: sqlite3.connect(
            str(self.store.path), factory=PausingRecoveryConnection
        )

        def heartbeat():
            self.assertTrue(scanned.wait(2))
            heartbeat_started.set()
            self.store.heartbeat_execution(run["id"], "runtime-owner", now=95)

        worker = threading.Thread(target=heartbeat)
        worker.start()
        try:
            self.store.recover_stale_executions(now=100, lease_seconds=30)
        finally:
            self.store._connect = original_connect
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.store.get(run["id"])["status"], "executing")

    def test_executed_restart_resumes_post_context_sync_idempotently(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner")
        result = {"ok": True}
        self.store.complete_execution(run["id"], "executed", result, "runtime-owner", result_hash(result), TARGET)
        controller = RunController(None, self.store, lambda run_id, target, phase, fence: capture(), None, None)
        first = controller.resume_executed(run["id"])
        second = controller.resume_executed(run["id"])
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(second["status"], "succeeded")

    def test_gateway_restart_reconciles_executing_and_resumes_executed(self):
        stale = self.approved_run()
        self.store.claim_for_execution(stale["id"], TARGET, "stale-owner", now=1)
        executed = self.approved_run()
        self.store.claim_for_execution(executed["id"], TARGET, "done-owner")
        result = {"ok": True}
        self.store.complete_execution(executed["id"], "executed", result, "done-owner", result_hash(result), TARGET)

        state = GatewayState(store=self.store)
        self.assertEqual(self.store.get(stale["id"])["status"], "recovery_required")
        self.assertEqual(self.store.get(executed["id"])["status"], "executed")
        state.resume_interrupted_runs(
            lambda run_id, target, phase, fence: capture(),
            lambda target, args: target(*args),
        )
        self.assertEqual(self.store.get(executed["id"])["status"], "succeeded")

    def test_only_one_post_execution_context_finalizer_runs(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "owner")
        result = {"ok": True}
        self.store.complete_execution(run["id"], "executed", result, "owner", result_hash(result), TARGET)
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def reader(run_id, target, phase, fence):
            calls.append(run_id)
            entered.set()
            release.wait(2)
            return capture()

        controller = RunController(None, self.store, reader, None, None)
        results = []
        workers = [threading.Thread(target=lambda: results.append(controller.resume_executed(run["id"]))) for _ in range(2)]
        workers[0].start()
        self.assertTrue(entered.wait(1))
        workers[1].start()
        release.set()
        for worker in workers:
            worker.join(2)
        self.assertEqual(len(calls), 1)
        self.assertEqual([item["status"] for item in results], ["succeeded", "succeeded"])

    def test_context_finalizer_fence_rejects_every_write_from_expired_owner(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner")
        result = {"ok": True}
        self.store.complete_execution(run["id"], "executed", result, "runtime-owner", result_hash(result), TARGET)
        fence_a = self.store.claim_context_finalization(run["id"], "gateway-a", now=10, lease_seconds=30)
        fence_b = self.store.claim_context_finalization(run["id"], "gateway-b", now=50, lease_seconds=30)
        self.assertEqual(fence_a["epoch"], 1)
        self.assertEqual(fence_b["epoch"], 2)
        trace = self.store.run_trace(run["id"])
        for write in (
            lambda: self.store.update_context_finalization(run["id"], fence_a, trace),
            lambda: self.store.complete_context_sync(run["id"], fence_a, trace),
            lambda: self.store.fail_context_sync(run["id"], fence_a, RuntimeError("late"), trace),
        ):
            with self.assertRaises(ValueError):
                write()
        row = self.store.complete_context_sync(run["id"], fence_b, trace)
        self.assertEqual(row["status"], "succeeded")

    def test_context_callback_token_is_bound_to_current_finalizer_fence(self):
        run = self.approved_run()
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner")
        result = {"ok": True}
        self.store.complete_execution(run["id"], "executed", result, "runtime-owner", result_hash(result), TARGET)
        fence_a = self.store.claim_context_finalization(run["id"], "gateway-a", now=10, lease_seconds=30)
        self.store.set_state("arcmap_context_sync:token", {
            "run_id": run["id"], "phase": "after_execution", "bridge": TARGET,
            "finalizer": fence_a, "consumed": False,
        })
        self.store.claim_context_finalization(run["id"], "gateway-b", now=50, lease_seconds=30)
        with self.assertRaises(ValueError):
            self.store.consume_context_sync("token", run["id"], "after_execution", TARGET, {"layers": []})

    def test_active_runs_cannot_be_deleted_or_cleared(self):
        active = self.approved_run()
        terminal = self.store.create_run("bad", "g1_context")
        self.store.fail_run(terminal["id"], "context_before_planning", RuntimeError("offline"), self.store.run_trace(terminal["id"]))
        with self.assertRaisesRegex(ValueError, "active runs cannot be deleted"):
            self.store.delete(active["id"])
        result = self.store.clear_runs()
        self.assertEqual(result["cleared"]["runs"], 1)
        self.assertEqual(result["preserved_active"], 1)
        self.assertEqual(result["preserved_indeterminate"], 0)
        self.assertEqual(self.store.get(active["id"])["status"], "approved")

    def test_clear_preserves_indeterminate_and_a_new_episode_can_be_created(self):
        indeterminate = self.approved_run()
        self.store.claim_for_execution(indeterminate["id"], TARGET, "runtime-owner", now=10)
        self.store.recover_stale_executions(
            now=100, lease_seconds=30, recovery_window_seconds=0
        )
        self.store.resolve_expired_recoveries(now=100)
        terminal = self.store.create_run("bad", "g1_context")
        self.store.fail_run(
            terminal["id"], "context_before_planning", RuntimeError("offline"),
            self.store.run_trace(terminal["id"]),
        )

        result = self.store.clear_runs()

        self.assertEqual(result["cleared"]["runs"], 1)
        self.assertEqual(result["preserved_active"], 0)
        self.assertEqual(result["preserved_indeterminate"], 1)
        self.assertEqual(self.store.get(indeterminate["id"])["status"], "indeterminate")
        rerun = self.approved_run()
        self.assertNotEqual(rerun["id"], indeterminate["id"])
        self.assertEqual(rerun["status"], "approved")

    def test_indeterminate_target_rejects_new_episode_until_late_failure_releases_it(self):
        run = self.approved_run()
        self.store.reserve_target_episode(run["id"], TARGET)
        self.assertTrue(self.store.claim_target_episode(run["id"]))
        self.store.claim_for_execution(run["id"], TARGET, "runtime-owner", now=10)
        self.store.recover_stale_executions(now=100, lease_seconds=30, recovery_window_seconds=0)
        self.store.resolve_expired_recoveries(now=100)
        with self.assertRaisesRegex(ValueError, "target is quarantined"):
            self.store.create_run_for_target("blocked", "g1_context", TARGET)
        result = {"ok": False, "error": "late"}
        self.store.complete_execution(run["id"], "failed", result, "runtime-owner", result_hash(result), TARGET)
        self.assertEqual(self.store.create_run_for_target("unblocked", "g1_context", TARGET)["status"], "running")

    def test_report_keeps_unbound_failed_run_without_affecting_valid_run(self):
        failed = self.store.create_run("offline", "g1_context")
        self.store.fail_run(failed["id"], "context_before_planning", RuntimeError("offline"), self.store.run_trace(failed["id"]))
        valid = self.store.create_run("bound", "g1_context")
        self.store.bind_context(valid["id"], capture())
        self.store.update_run(valid["id"], "planned")
        self.store.update_run(valid["id"], "cancelled")
        report = self.store.export_runs()
        by_id = {item["id"]: item for item in report["runs"]}
        self.assertFalse(by_id[failed["id"]]["context"]["bound"])
        self.assertTrue(by_id[valid["id"]]["context"]["bound"])
        self.assertEqual(report["statistics"]["eligible_runs"], 0)


class BridgeSourceContractTests(unittest.TestCase):
    def test_silent_command_write_is_atomic_and_propagates_failure(self):
        source = (Path(__file__).parents[2] / "ArcMapBridgeExternal" / "Program.cs").read_text(encoding="utf-8")
        body = source.split("private static void WriteSilentCommand", 1)[1].split("private static", 1)[0]
        self.assertIn("File.Replace", body)
        self.assertIn("File.Move", body)
        self.assertNotIn("silent_marker_failed", body)
        self.assertIn("throw;", body)

    def test_bridge_wait_is_bounded(self):
        source = (Path(__file__).parents[2] / "ArcMapBridgeExternal" / "Program.cs").read_text(encoding="utf-8")
        body = source.split("private string EnqueueAndWait", 1)[1].split("private static", 1)[0]
        self.assertNotIn("WaitOne()", body)
        self.assertIn("WaitOne(TimeSpan", body)

    def test_execution_heartbeat_has_independent_lifetime_and_deterministic_shutdown(self):
        source = (Path(__file__).parents[2] / "ArcMapBridgeExternal" / "Program.cs").read_text(encoding="utf-8")
        dispatch = source.split("private void ExecuteArcMapCommand", 1)[1].split("private IApplication", 1)[0]
        heartbeat = source.split("private sealed class GatewayExecutionHeartbeat", 1)[1].split(
            "private sealed class HttpRequest", 1
        )[0]

        self.assertNotIn("using (var heartbeat", dispatch)
        self.assertIn("heartbeat.Start();", dispatch)
        self.assertNotIn("heartbeat.Cancel();", dispatch)
        self.assertNotIn("finally", dispatch)
        self.assertNotIn("_cancelled", heartbeat)
        self.assertNotIn("executionDiscoveryDeadline", heartbeat)
        self.assertIn("TryPostGatewayHeartbeat", heartbeat)
        self.assertIn("TryReadGatewayRunState", heartbeat)
        self.assertIn("IsTerminalRunState", heartbeat)
        self.assertIn("HeartbeatPostResult.Accepted", heartbeat)
        self.assertIn("HeartbeatPostResult.Terminal", heartbeat)

    def test_active_execution_prevents_rot_idle_shutdown_but_stops_heartbeating_after_arcmap_exit(self):
        source = (Path(__file__).parents[2] / "ArcMapBridgeExternal" / "Program.cs").read_text(encoding="utf-8")
        shutdown = source.split("private void StopIfArcMapClosed", 1)[1].split(
            "private void RegisterWithGateway", 1
        )[0]
        dispatch = source.split("private void ExecuteArcMapCommand", 1)[1].split(
            "private IApplication", 1
        )[0]
        heartbeat = source.split("private sealed class GatewayExecutionHeartbeat", 1)[1].split(
            "private sealed class HttpRequest", 1
        )[0]

        self.assertIn("_activeExecutionCount", shutdown)
        self.assertIn("return;", shutdown)
        self.assertIn("StartExecutionHeartbeat", dispatch)
        self.assertIn("arcMapPid", dispatch)
        self.assertIn("IsArcMapProcessAlive", heartbeat)
        self.assertIn("_onFinished", heartbeat)

    def test_bridge_fails_fast_when_binary_and_source_identity_are_not_verified(self):
        source = (Path(__file__).parents[2] / "ArcMapBridgeExternal" / "Program.cs").read_text(encoding="utf-8")

        self.assertIn("ReadAndVerifyBuildIdentity", source)
        self.assertIn("ComputeFileSha256(executablePath)", source)
        self.assertIn("ArcMap Bridge binary does not match its build identity", source)


if __name__ == "__main__":
    unittest.main()
