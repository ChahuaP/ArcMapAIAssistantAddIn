import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcmap_runtime_py2.execution_outbox import result_hash
from gateway_py3 import arcmap_bridge_client
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.event_bus import EventBus
from gateway_py3.routes import handle_post
from gateway_py3.validators import context_hash
from gateway_py3.run_store import RunStore


class PlannedRunner:
    def __init__(self, store): self.store = store
    def plan(self, run_id, command, context, mode, provider, model):
        return self.store.update_run(run_id, "planned", workflow={"action": "execute", "summary": command, "steps": []})


class ArcMapRunProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        store = RunStore(Path(self.temp.name) / "runs.sqlite")
        self.state = SimpleNamespace(catalog=OperationCatalog(), events=EventBus(), runner=PlannedRunner(store), store=store, run_scheduler=lambda target, args: target(*args))
        context = {"layers": []}
        self.capture = {"context": context, "context_hash": context_hash(context), "bridge": {"bridge_pid": 7, "bridge_port": 8766, "arcmap_pid": 70, "hwnd": 9}, "captured_at": 1}

    def tearDown(self): self.temp.cleanup()

    def test_bridge_request_carries_exact_run_id(self):
        with patch("gateway_py3.arcmap_bridge_client._request", return_value={"ok": True}) as request:
            arcmap_bridge_client.execute_run("e0c1c9b0-0ae6-4d57-978b-64a1014129e3", port=8766, hwnd=9)
        self.assertEqual(request.call_args.args[1], "/runs/e0c1c9b0-0ae6-4d57-978b-64a1014129e3/execute")

    def test_fake_bridge_claims_and_completes_only_requested_run(self):
        def fake_execute(run_id, allow_edits, port, hwnd):
            self.state.store.claim_for_execution(run_id, self.capture["bridge"], "owner-" + run_id)
            result = {"ok": True, "run_id": run_id}
            self.state.store.complete_execution(
                run_id, "executed", result, "owner-" + run_id,
                result_hash(result), self.capture["bridge"],
            )
            return {"ok": True, "run_id": run_id}
        with patch("gateway_py3.routes.runs.arcmap.sync_context", return_value=self.capture), patch("gateway_py3.routes.runs.arcmap.active_bridge", return_value=self.capture["bridge"]), patch("gateway_py3.routes.runs.arcmap.arcmap_bridge_client.execute_run", side_effect=fake_execute):
            first = handle_post(self.state, "/runs", _request("first"))["run"]
            second = handle_post(self.state, "/runs", _request("second"))["run"]
        self.assertEqual(self.state.store.get(first["id"])["status"], "succeeded")
        self.assertEqual(self.state.store.get(second["id"])["status"], "succeeded")
        self.assertNotEqual(first["id"], second["id"])

    def test_formal_reset_uses_a_deterministic_gateway_workflow(self):
        source_paths = []
        for index in range(14):
            path = Path(self.temp.name) / ("source_%02d.shp" % index)
            path.touch()
            source_paths.append(str(path))

        class ModelRunnerMustNotBeCalled:
            def plan(self, *args, **kwargs):
                raise AssertionError("formal reset must not invoke a model planner")

        self.state.runner = ModelRunnerMustNotBeCalled()
        after_context = {
            "layers": [
                {"name": Path(path).stem, "layer_ref": "layer:%d" % index}
                for index, path in enumerate(source_paths)
            ]
        }

        def capture(state, run_id, phase, bridge=None, finalizer=None):
            context = after_context if phase == "after_execution" else {"layers": []}
            return {
                "context": context,
                "context_hash": context_hash(context),
                "bridge": self.capture["bridge"],
                "captured_at": 1,
            }

        def execute(run_id, allow_edits, port, hwnd):
            self.state.store.claim_for_execution(
                run_id, self.capture["bridge"], "owner-" + run_id
            )
            result = {"ok": True, "run_id": run_id}
            self.state.store.complete_execution(
                run_id,
                "executed",
                result,
                "owner-" + run_id,
                result_hash(result),
                self.capture["bridge"],
            )

        with patch(
            "gateway_py3.routes.runs.arcmap.active_bridge",
            return_value=self.capture["bridge"],
        ), patch(
            "gateway_py3.routes.runs.arcmap.sync_context",
            side_effect=capture,
        ), patch(
            "gateway_py3.routes.runs.arcmap.execution_permission",
            return_value=False,
        ), patch(
            "gateway_py3.routes.runs.arcmap.arcmap_bridge_client.execute_run",
            side_effect=execute,
        ):
            created = handle_post(
                self.state,
                "/experiments/reset",
                {"source_paths": source_paths},
            )["run"]

        row = self.state.store.get(created["id"])
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(
            [step["operation"] for step in row["workflow"]["steps"]],
            ["layer.clear_layers"]
            + ["layer.add_layer"] * 14
            + ["context.list_layers"],
        )
        trace = row["agent_trace"][0]["run"]
        self.assertEqual(trace["control"]["kind"], "formal_experiment_reset")
        self.assertEqual(trace["turns"], [])

    def test_context_payload_is_rejected(self):
        payload = _request("x")
        payload["context"] = {"layers": [{"name": "injected"}]}
        with self.assertRaises(ValueError): handle_post(self.state, "/runs", payload)

    def test_context_callback_requires_the_pending_run_and_target(self):
        run = self.state.store.create_run("x", "g1_context")
        token = "unpredictable-test-token"
        pending = {"run_id": run["id"], "phase": "before_planning", "bridge": self.capture["bridge"], "context": None, "consumed": False}
        self.state.store.set_state("arcmap_context_sync:" + token, pending)
        payload = {"context": {"layers": []}, "sync_token": token, "phase": "before_planning", "target": self.capture["bridge"]}
        accepted = handle_post(self.state, "/runs/%s/context" % run["id"], payload)
        self.assertTrue(accepted["ok"])
        with self.assertRaises(ValueError):
            handle_post(self.state, "/runs/%s/context" % run["id"], payload)

    def test_sync_token_is_consumed_atomically(self):
        run = self.state.store.create_run("x", "g1_context")
        token = "atomic-test-token"
        pending = {"run_id": run["id"], "phase": "before_planning", "bridge": self.capture["bridge"], "context": None, "consumed": False}
        self.state.store.set_state("arcmap_context_sync:" + token, pending)
        barrier = threading.Barrier(3)
        outcomes = []

        def consume():
            barrier.wait()
            try:
                self.state.store.consume_context_sync(token, run["id"], "before_planning", self.capture["bridge"], {"layers": []})
                outcomes.append("accepted")
            except ValueError:
                outcomes.append("rejected")

        threads = [threading.Thread(target=consume) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(outcomes), ["accepted", "rejected"])


def _request(command):
    return {"command": command, "mode": "g2_constrained", "execute": True, "confirmed": True, "allow_edits": False}
