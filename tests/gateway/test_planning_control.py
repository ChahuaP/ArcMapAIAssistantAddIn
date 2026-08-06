import copy
import json
import tempfile
import unittest
from pathlib import Path

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.planning_engine import PlanningEngine
from gateway_py3.run_store import RunStore
from gateway_py3.task_contract import task_contract_model_view
from gateway_py3.validators import context_hash
from tests.gateway.planner_test_utils import model_wire_response
from tests.gateway.test_experiments import CONTEXT, TASK_CONTRACT, WORKFLOW


class Client:
    provider_id = "test-provider"
    model_id = "test-model"
    def __init__(self, store, run_id, replies, cancel_at=None):
        self.store, self.run_id, self.replies, self.cancel_at, self.calls = store, run_id, list(replies), cancel_at, 0
    def chat_structured(self, messages, contract):
        self.calls += 1
        value = self.replies.pop(0)
        if self.calls == self.cancel_at: self.store.cancel(self.run_id)
        return model_wire_response(value, messages)


class TraceObservingClient(Client):
    def __init__(self, store, run_id, replies):
        super().__init__(store, run_id, replies)
        self.observed_traces = []

    def chat_structured(self, messages, contract):
        self.observed_traces.append(self.store.run_trace(self.run_id))
        return super().chat_structured(messages, contract)


class PayloadCaptured(RuntimeError):
    pass


class PayloadCapturingClient:
    provider_id = "test-provider"
    model_id = "test-model"

    def __init__(self, task_contract):
        self.task_contract = task_contract
        self.payloads = []
        self.contracts = []

    def chat_structured(self, messages, contract):
        self.contracts.append(contract)
        self.payloads.append(json.loads(messages[1]["content"]))
        if len(self.payloads) == 1:
            return {"task_contract": task_contract_model_view(self.task_contract)}
        raise PayloadCaptured()


class PlanningControlPublicTests(unittest.TestCase):
    def test_g2_closes_model_capabilities_after_semantic_analysis(self):
        command = "清空当前地图中的全部图层"
        task = {
            "input_entities": [],
            "outputs": [{
                "output_id": "output:map", "kind": "map_state", "name": command,
                "format": "map", "geometry": "not_applicable", "required_fields": [],
                "spatial_reference": "not_applicable", "destination": "not_applicable",
                "evidence": command,
            }],
            "requirements": [{
                "requirement_id": "req:clear", "evidence": command,
                "predicate": {
                    "kind": "map_change", "subject": "output:map", "action": "clear_layers",
                },
            }],
            "allowed_side_effects": ["changes_map"],
            "clarifications": [],
        }
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        store = RunStore(Path(temp.name) / "runs.sqlite")
        run = store.create_run(command, "g2_constrained")
        store.bind_context(run["id"], {
            "context": CONTEXT, "context_hash": context_hash(CONTEXT),
            "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4},
            "captured_at": 1,
        })
        client = PayloadCapturingClient(task)

        with self.assertRaises(PayloadCaptured):
            PlanningEngine(OperationCatalog(), store, lambda p, m: client).plan(
                run["id"], command, CONTEXT, "g2_constrained", "test-provider", "test-model",
            )

        semantic_payload, planner_payload = client.payloads
        self.assertNotIn("capabilities", semantic_payload)
        self.assertIn("capability_index", semantic_payload)
        self.assertLess(
            len(json.dumps(semantic_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")),
            20_000,
        )
        self.assertEqual(
            ["layer.clear_layers"],
            [card["id"] for card in planner_payload["capabilities"]],
        )
        self.assertIn("parameters_schema", planner_payload["capabilities"][0])
        self.assertEqual("submit_workflow_v3", client.contracts[1].name)
        self.assertNotIn("evidence", planner_payload["task_contract"]["requirements"][0])

    def test_planning_provenance_is_durable_before_first_provider_call(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        store = RunStore(Path(temp.name) / "runs.sqlite")
        run = store.create_run("refresh", "g2_constrained")
        store.bind_context(run["id"], {"context": CONTEXT, "context_hash": context_hash(CONTEXT), "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4}, "captured_at": 1})
        client = TraceObservingClient(store, run["id"], [{"task_contract": TASK_CONTRACT}, {"workflow_draft": copy.deepcopy(WORKFLOW)}])

        PlanningEngine(OperationCatalog(), store, lambda p, m: client).plan(run["id"], "refresh", CONTEXT, "g2_constrained", "test-provider", "test-model")

        first_trace = client.observed_traces[0]
        self.assertEqual("test-provider", first_trace["provider"])
        self.assertEqual("test-model", first_trace["model"])
        self.assertEqual({"provider": "test-provider", "model": "test-model"}, first_trace["requested_model_config"])
        self.assertEqual({"provider": "test-provider", "model": "test-model"}, first_trace["role_models"]["g2_constrained"])
        self.assertIn("catalog_hash", first_trace["planning_policy"])
        self.assertIn("protocol_hash", first_trace["planning_policy"])

    def test_g2_cancellation_has_one_terminal_transition(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        store = RunStore(Path(temp.name) / "runs.sqlite")
        run = store.create_run("refresh", "g2_constrained")
        store.bind_context(run["id"], {"context": CONTEXT, "context_hash": context_hash(CONTEXT), "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4}, "captured_at": 1})
        client = Client(store, run["id"], [{"task_contract": TASK_CONTRACT}, {"workflow_draft": copy.deepcopy(WORKFLOW)}], 2)
        row = PlanningEngine(OperationCatalog(), store, lambda p, m: client).plan(run["id"], "refresh", CONTEXT, "g2_constrained", "test-provider", "test-model")
        self.assertEqual("cancelled", row["status"])
        states = [x["state"] for x in row["agent_trace"][0]["run"]["transitions"]]
        self.assertEqual(1, states.count("terminal"))


if __name__ == "__main__": unittest.main()
