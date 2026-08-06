import sys
import builtins
from contextlib import nullcontext
import importlib
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.modules.setdefault("arcpy", types.SimpleNamespace(ExecuteError=RuntimeError))
sys.modules.setdefault("pythonaddins", types.SimpleNamespace(MessageBox=lambda *args: True))
sys.modules.setdefault("urllib2", types.SimpleNamespace(
    HTTPError=RuntimeError,
    URLError=RuntimeError,
    Request=lambda *args, **kwargs: None,
))
builtins.reload = importlib.reload

from arcmap_runtime_py2 import runtime
from arcmap_runtime_py2 import arcmap_ui_dispatch
from arcmap_runtime_py2.execution_outbox import ExecutionOutbox
from arcmap_runtime_py2 import workflow_executor
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.event_bus import EventBus
from gateway_py3.run_controller import RunController
from gateway_py3.routes import arcmap, handle_post
from gateway_py3.validators import context_hash
from gateway_py3.run_store import RunStore


TARGET = {"bridge_pid": 7, "bridge_port": 8766, "arcmap_pid": 70, "hwnd": 9}
OWNER_ID = "eca7fc62-6289-48d4-aa96-993d3a341b17"


def _plan(path):
    return runtime.execution_session.PublicationPlan.from_records([
        {"path": path, "visible": True, "selection_oids": None},
    ])


class _Runner:
    def __init__(self, store):
        self.store = store

    def plan(self, run_id, command, context, mode, provider, model):
        workflow = {"action": "execute", "summary": command, "steps": []}
        return self.store.update_run(run_id, "planned", workflow=workflow)


class ThreePartyRunProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.temp.name) / "runs.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def test_manual_console_launch_does_not_resolve_bridge_target_inside_arcmap_callback(self):
        events = []
        with patch.object(runtime.gateway_client, "ensure_running", side_effect=lambda: events.append("gateway")), \
                patch.object(runtime.gateway_client, "current_target", side_effect=AssertionError("ArcMap callback deadlock")), \
                patch.object(runtime.map_exporter, "cleanup_stale", side_effect=lambda: events.append("cleanup")), \
                patch.object(runtime, "_drain_execution_outbox", side_effect=AssertionError("targetless recovery")), \
                patch.object(runtime, "_sync_current_context", side_effect=lambda: events.append("context")), \
                patch.object(runtime, "open_web", side_effect=lambda: events.append("web")):
            runtime.open_assistant()

        self.assertEqual(events, ["gateway", "cleanup", "context", "web"])

    def test_silent_execution_claims_then_defers_arcgis_work_to_the_ui_message_loop(self):
        run_id = "e0c1c9b0-0ae6-4d57-978b-64a1014129e3"
        owner_id = OWNER_ID
        events = []
        callbacks = []
        row = {"id": run_id}
        heartbeat = SimpleNamespace(
            start=lambda: events.append("heartbeat"),
            stop=lambda: events.append("stop"),
        )
        outcome = SimpleNamespace(
            result={"ok": True},
            publication_plan=runtime.execution_session.PublicationPlan([]),
        )
        with patch.object(runtime.gateway_client, "ensure_running"), \
                patch.object(runtime.map_exporter, "cleanup_stale"), \
                patch.object(runtime, "_drain_execution_outbox"), \
                patch.object(
                    runtime.gateway_client,
                    "claim_run",
                    side_effect=lambda *args: events.append("claim") or {"run": row},
                ), \
                patch.object(runtime, "_ExecutionHeartbeat", return_value=heartbeat), \
                patch.object(
                    runtime.context_reader,
                    "read_context",
                    side_effect=lambda: events.append("context") or {},
                ), \
                patch.object(
                    runtime.workflow_executor,
                    "execute",
                    side_effect=lambda *args, **kwargs: events.append("workflow") or outcome,
                ), \
                patch.object(
                    runtime,
                    "_persist_publish_and_deliver",
                    side_effect=lambda *args: events.append("persist") or True,
                ), \
                patch.object(
                    runtime,
                    "arcmap_ui_dispatch",
                    SimpleNamespace(
                        defer=lambda callback: events.append("defer") or callbacks.append(callback)
                    ),
                    create=True,
                ):
            runtime._run_silent_command({
                "action": "execute",
                "run_id": run_id,
                "owner_id": owner_id,
                "target": TARGET,
            })

            self.assertEqual(events, ["claim", "heartbeat", "defer"])
            self.assertEqual(len(callbacks), 1)

            callbacks[0]()

            self.assertEqual(events, [
                "claim", "heartbeat", "defer", "context", "workflow", "persist",
            ])

    def test_deferred_execution_schedule_failure_completes_the_claimed_run_as_failed(self):
        run_id = "e0c1c9b0-0ae6-4d57-978b-64a1014129e3"
        events = []
        heartbeat = SimpleNamespace(start=lambda: events.append("heartbeat"))

        def persist(*args):
            events.append((args[2], args[3]))
            return True

        with patch.object(runtime.gateway_client, "ensure_running"), \
                patch.object(runtime.map_exporter, "cleanup_stale"), \
                patch.object(runtime, "_drain_execution_outbox"), \
                patch.object(
                    runtime.gateway_client,
                    "claim_run",
                    side_effect=lambda *args: events.append("claim") or {"run": {"id": run_id}},
                ), \
                patch.object(runtime, "_ExecutionHeartbeat", return_value=heartbeat), \
                patch.object(runtime.arcmap_ui_dispatch, "defer", side_effect=RuntimeError("timer failed")), \
                patch.object(runtime, "_persist_publish_and_deliver", side_effect=persist), \
                patch.object(
                    runtime.workflow_executor,
                    "execute",
                    side_effect=AssertionError("ArcGIS work ran inside the COM callback"),
                ):
            with self.assertRaisesRegex(RuntimeError, "timer failed"):
                runtime._run_silent_command({
                    "action": "execute",
                    "run_id": run_id,
                    "owner_id": OWNER_ID,
                    "target": TARGET,
                })

        self.assertEqual(events[:2], ["claim", "heartbeat"])
        self.assertEqual(events[2][0], "failed")
        self.assertFalse(events[2][1]["ok"])
        self.assertEqual(events[2][1]["failure_phase"], "arcmap_ui_dispatch")

    def test_arcmap_ui_dispatch_runs_once_after_the_current_callback_returns(self):
        events = []
        timer_procs = []

        def set_timer(timer_proc):
            timer_procs.append(timer_proc)
            return 41

        try:
            with patch.object(arcmap_ui_dispatch, "_set_timer", side_effect=set_timer), \
                    patch.object(
                        arcmap_ui_dispatch,
                        "_kill_timer",
                        side_effect=lambda timer_id: events.append(("kill", timer_id)) or True,
                    ):
                arcmap_ui_dispatch.defer(lambda: events.append("run"))
                self.assertEqual(events, [])
                with self.assertRaisesRegex(RuntimeError, "already has"):
                    arcmap_ui_dispatch.defer(lambda: None)

                timer_procs[0](None, 0x0113, 41, 0)
                self.assertEqual(events, [("kill", 41), "run"])

                arcmap_ui_dispatch.defer(lambda: events.append("next"))
                timer_procs[1](None, 0x0113, 41, 0)
                self.assertEqual(events, [("kill", 41), "run", ("kill", 41), "next"])
        finally:
            arcmap_ui_dispatch._clear_pending()

    def test_runtime_persists_plan_then_publishes_then_delivers_authoritative_result(self):
        events = []
        plan = _plan(r"D:\out\final.shp")
        heartbeat = SimpleNamespace(stop=lambda: events.append("stop"))
        incomplete = {"publication_complete": False}
        complete = {"publication_complete": True}
        outbox = SimpleNamespace(
            enqueue=lambda *args: events.append("persist") or incomplete,
            publication_lease=lambda entry: nullcontext(True),
            mark_publication_complete=lambda entry: events.append("publication_ack") or complete,
            deliver=lambda entry, client: events.append("deliver") or True,
        )

        with patch.object(runtime, "EXECUTION_OUTBOX", outbox), \
                patch.object(runtime.output_publisher, "publish", side_effect=lambda value: events.append("publish")):
            acknowledged = runtime._persist_publish_and_deliver(
                "00000000-0000-0000-0000-000000000001", "owner", "executed",
                {"ok": True}, {}, heartbeat, plan,
            )

        self.assertTrue(acknowledged)
        self.assertEqual(events, ["persist", "publish", "publication_ack", "deliver", "stop"])

    def test_runtime_does_not_deliver_success_when_output_publication_fails(self):
        events = []
        plan = _plan(r"D:\out\final.shp")
        heartbeat = SimpleNamespace(stop=lambda: events.append("stop"))
        outbox = SimpleNamespace(
            enqueue=lambda *args: events.append("persist") or {"publication_complete": False},
            publication_lease=lambda entry: nullcontext(True),
            mark_publication_complete=lambda entry: events.append("publication_ack"),
            deliver=lambda entry, client: events.append("deliver"),
        )

        with patch.object(runtime, "EXECUTION_OUTBOX", outbox), \
                patch.object(runtime.output_publisher, "publish", side_effect=RuntimeError("publish failed")), \
                patch.object(runtime, "_log_event"):
            with self.assertRaisesRegex(RuntimeError, "publish failed"):
                runtime._persist_publish_and_deliver(
                    "00000000-0000-0000-0000-000000000001", "owner", "executed",
                    {"ok": True}, {}, heartbeat, plan,
                )

        self.assertEqual(events, ["persist", "stop"])

    def test_runtime_replays_durable_publication_before_delivering_after_restart(self):
        events = []
        incomplete = {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "owner": "owner",
            "target": TARGET,
            "publication_items": [{
                "path": r"D:\out\final.shp", "visible": False, "selection_oids": [1, 3],
            }],
            "publication_complete": False,
        }
        complete = dict(incomplete, publication_complete=True)
        outbox = SimpleNamespace(
            pending=lambda: [incomplete],
            publication_lease=lambda entry: nullcontext(True),
            mark_publication_complete=lambda entry: events.append("publication_ack") or complete,
            deliver=lambda entry, client: events.append("deliver") or True,
        )

        with patch.object(runtime, "EXECUTION_OUTBOX", outbox), \
                patch.object(runtime.output_publisher, "publish", side_effect=lambda plan: events.append("publish")):
            runtime._drain_execution_outbox(TARGET)

        self.assertEqual(events, ["publish", "publication_ack", "deliver"])

    def test_runtime_revalidates_completed_publication_and_ignores_other_arcmap_target(self):
        events = []
        complete = {
            "run_id": "00000000-0000-0000-0000-000000000001",
            "owner": "owner",
            "target": TARGET,
            "publication_items": [{
                "path": r"D:\out\final.shp", "visible": True, "selection_oids": None,
            }],
            "publication_complete": True,
        }
        outbox = SimpleNamespace(
            pending=lambda: [complete],
            publication_lease=lambda entry: nullcontext(True),
            mark_publication_complete=lambda entry: events.append("unexpected_ack"),
            deliver=lambda entry, client: events.append("deliver") or True,
        )
        with patch.object(runtime, "EXECUTION_OUTBOX", outbox), \
                patch.object(runtime.output_publisher, "publish", side_effect=lambda plan: events.append("publish")):
            runtime._drain_execution_outbox(dict(TARGET, hwnd=999))
            self.assertEqual(events, [])
            runtime._drain_execution_outbox(TARGET)

        self.assertEqual(events, ["publish", "deliver"])

    def test_bound_hash_is_the_runtime_execution_fingerprint(self):
        context = {
            "mxd_path": "C:/maps/example.mxd",
            "is_saved": True,
            "data_frame": "Layers",
            "spatial_reference": {"name": "WGS 84", "factoryCode": 4326},
            "layers": [],
        }
        capture = {
            "context": context,
            "context_hash": context_hash(context),
            "captured_at": 1.0,
            "bridge": {"bridge_pid": 10, "bridge_port": 8766, "arcmap_pid": 100, "hwnd": 20},
        }
        run = self.store.create_run("noop", "g1_context")
        controller = RunController(
            _Runner(self.store),
            self.store,
            lambda run_id, target, phase, fence: capture,
            lambda request, row: False,
            lambda run_id, allow_edits, target: None,
        )

        controller.run(run["id"], {"command": "noop", "mode": "g1_context", "execute": False})
        row = self.store.get(run["id"])

        self.assertEqual(row["context_hash"], context_hash(context))
        self.assertTrue(workflow_executor.execute(row, context).result["ok"])

    def test_runtime_claim_rejects_a_different_arcmap_target(self):
        context = _context("before.mxd")
        target = {"bridge_pid": 10, "bridge_port": 8766, "arcmap_pid": 100, "hwnd": 20}
        capture = {"context": context, "context_hash": context_hash(context), "captured_at": 1.0, "bridge": target}
        run = self.store.create_run("noop", "g1_context")
        self.store.bind_context(run["id"], capture)
        self.store.update_run(run["id"], "planned", workflow={"action": "execute", "summary": "noop", "steps": []})
        self.store.update_run(run["id"], "approved")

        with self.assertRaises(ValueError):
            self.store.claim_for_execution(run["id"], dict(target, hwnd=21), "wrong-owner")

        self.assertEqual(self.store.get(run["id"])["status"], "approved")

    def test_real_runtime_sequence_claims_executes_then_syncs_next_context(self):
        before = _context("before.mxd")
        after = _context("after.mxd")
        target = {"bridge_pid": 10, "bridge_port": 8766, "arcmap_pid": 100, "hwnd": 20}
        state = SimpleNamespace(
            store=self.store,
            events=EventBus(),
            catalog=OperationCatalog(),
            bridge_cache={"expires_at": 0.0, "bridges": []},
        )
        run = self.store.create_run("noop", "g1_context")
        transitions = []
        context_by_phase = {"before_planning": before, "after_execution": after}

        def bridge_sync(run_id, sync_token, phase, port=None, hwnd=None):
            callback = {
                "context": context_by_phase[phase],
                "sync_token": sync_token,
                "phase": phase,
                "target": target,
            }
            accepted = handle_post(state, "/runs/%s/context" % run_id, callback)
            self.assertTrue(accepted["ok"])
            with self.assertRaises(ValueError):
                handle_post(state, "/runs/%s/context" % run_id, callback)
            transitions.append((phase, self.store.get(run_id)["status"]))
            return {"ok": True}

        def runtime_claim(run_id, claimed_target, owner_id):
            result = handle_post(state, "/runs/%s/claim" % run_id, {"target": claimed_target, "owner_id": owner_id})
            transitions.append(("claim", result["run"]["status"]))
            return result

        def runtime_complete(run_id, status, result, owner_id, result_hash, completed_target):
            response = handle_post(state, "/runs/%s/complete" % run_id, {
                "status": status, "result": result, "owner_id": owner_id,
                "result_hash": result_hash, "target": completed_target,
            })
            transitions.append(("complete", response["run"]["status"]))
            return response

        def execute_runtime(run_id, allow_edits, bound_target):
            runtime._execute_run(run_id, bound_target, OWNER_ID, silent=True)
            return {"ok": True, "run_id": run_id}

        controller = RunController(
            _Runner(self.store),
            self.store,
            lambda run_id, bound_target, phase, fence: arcmap.sync_context(
                state, run_id, phase, bridge=bound_target or target, finalizer=fence
            ),
            lambda request, row: False,
            execute_runtime,
        )
        with patch("gateway_py3.routes.arcmap.arcmap_bridge_client.sync_context_target", side_effect=bridge_sync), \
             patch("arcmap_runtime_py2.runtime.gateway_client.claim_run", side_effect=runtime_claim), \
             patch("arcmap_runtime_py2.runtime.gateway_client.complete_run", side_effect=runtime_complete), \
             patch.object(runtime, "EXECUTION_OUTBOX", ExecutionOutbox(str(Path(self.temp.name) / "outbox-success"))), \
             patch("arcmap_runtime_py2.runtime.context_reader.read_context", return_value=before):
            row = controller.run(run["id"], {
                "command": "noop",
                "mode": "g1_context",
                "execute": True,
            })

        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["result"]["summary"], "noop")
        self.assertEqual(row["context_hash"], context_hash(before))
        self.assertEqual(row["agent_trace"][0]["run"]["execution"]["context_next_hash"], context_hash(after))
        self.assertEqual(transitions, [
            ("before_planning", "running"),
            ("claim", "executing"),
            ("complete", "executed"),
            ("after_execution", "executed"),
        ])

    def test_post_execution_context_failure_preserves_runtime_result(self):
        before = _context("before.mxd")
        target = {"bridge_pid": 10, "bridge_port": 8766, "arcmap_pid": 100, "hwnd": 20}
        state = SimpleNamespace(
            store=self.store,
            events=EventBus(),
            catalog=OperationCatalog(),
            bridge_cache={"expires_at": 0.0, "bridges": []},
        )
        run = self.store.create_run("noop", "g1_context")

        def bridge_sync(run_id, sync_token, phase, port=None, hwnd=None):
            if phase == "after_execution":
                raise RuntimeError("next context unavailable")
            return handle_post(state, "/runs/%s/context" % run_id, {
                "context": before,
                "sync_token": sync_token,
                "phase": phase,
                "target": target,
            })

        def runtime_claim(run_id, claimed_target, owner_id):
            return handle_post(state, "/runs/%s/claim" % run_id, {"target": claimed_target, "owner_id": owner_id})

        def runtime_complete(run_id, status, result, owner_id, result_hash, completed_target):
            return handle_post(state, "/runs/%s/complete" % run_id, {
                "status": status, "result": result, "owner_id": owner_id,
                "result_hash": result_hash, "target": completed_target,
            })

        controller = RunController(
            _Runner(self.store),
            self.store,
            lambda run_id, bound_target, phase, fence: arcmap.sync_context(
                state, run_id, phase, bridge=bound_target or target, finalizer=fence
            ),
            lambda request, row: False,
            lambda run_id, allow_edits, bound_target: runtime._execute_run(
                run_id, bound_target, OWNER_ID, silent=True
            ),
        )
        with patch("gateway_py3.routes.arcmap.arcmap_bridge_client.sync_context_target", side_effect=bridge_sync), \
             patch("arcmap_runtime_py2.runtime.gateway_client.claim_run", side_effect=runtime_claim), \
             patch("arcmap_runtime_py2.runtime.gateway_client.complete_run", side_effect=runtime_complete), \
             patch.object(runtime, "EXECUTION_OUTBOX", ExecutionOutbox(str(Path(self.temp.name) / "outbox-failure"))), \
             patch("arcmap_runtime_py2.runtime.context_reader.read_context", return_value=before):
            row = controller.run(run["id"], {"command": "noop", "mode": "g1_context", "execute": True})

        self.assertEqual(row["status"], "context_failed")
        self.assertEqual(row["result"]["summary"], "noop")
        self.assertEqual(row["agent_trace"][0]["run"]["failure"]["stage"], "context_after_execution")


def _context(name):
    return {
        "mxd_path": "C:/maps/" + name,
        "is_saved": True,
        "data_frame": "Layers",
        "spatial_reference": {"name": "WGS 84", "factoryCode": 4326},
        "layers": [],
    }


if __name__ == "__main__":
    unittest.main()
