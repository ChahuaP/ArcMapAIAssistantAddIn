import sys
import builtins
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
from arcmap_runtime_py2.execution_outbox import ExecutionOutbox
from arcmap_runtime_py2 import workflow_executor
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.event_bus import EventBus
from gateway_py3.run_controller import RunController
from gateway_py3.routes import arcmap, handle_post
from gateway_py3.validators import context_hash
from gateway_py3.run_store import RunStore


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
        run = self.store.create_run("noop", "context_single")
        controller = RunController(
            _Runner(self.store),
            self.store,
            lambda run_id, target, phase, fence: capture,
            lambda request, row: False,
            lambda run_id, allow_edits, target: None,
        )

        controller.run(run["id"], {"command": "noop", "mode": "context_single", "execute": False})
        row = self.store.get(run["id"])

        self.assertEqual(row["context_hash"], context_hash(context))
        self.assertTrue(workflow_executor.execute(row, context)["ok"])

    def test_runtime_claim_rejects_a_different_arcmap_target(self):
        context = _context("before.mxd")
        target = {"bridge_pid": 10, "bridge_port": 8766, "arcmap_pid": 100, "hwnd": 20}
        capture = {"context": context, "context_hash": context_hash(context), "captured_at": 1.0, "bridge": target}
        run = self.store.create_run("noop", "context_single")
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
        run = self.store.create_run("noop", "context_single")
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
            runtime._execute_run(run_id, bound_target, silent=True)
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
                "mode": "context_single",
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
        run = self.store.create_run("noop", "context_single")

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
            lambda run_id, allow_edits, bound_target: runtime._execute_run(run_id, bound_target, silent=True),
        )
        with patch("gateway_py3.routes.arcmap.arcmap_bridge_client.sync_context_target", side_effect=bridge_sync), \
             patch("arcmap_runtime_py2.runtime.gateway_client.claim_run", side_effect=runtime_claim), \
             patch("arcmap_runtime_py2.runtime.gateway_client.complete_run", side_effect=runtime_complete), \
             patch.object(runtime, "EXECUTION_OUTBOX", ExecutionOutbox(str(Path(self.temp.name) / "outbox-failure"))), \
             patch("arcmap_runtime_py2.runtime.context_reader.read_context", return_value=before):
            row = controller.run(run["id"], {"command": "noop", "mode": "context_single", "execute": True})

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
