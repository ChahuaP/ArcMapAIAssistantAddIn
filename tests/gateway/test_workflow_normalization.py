import copy
import tempfile
import unittest
from pathlib import Path

from gateway_py3.planning_engine import PlanningEngine
from gateway_py3.run_store import RunStore
from gateway_py3.validators import ValidationError, context_hash, prepare_workflow
from tests.gateway.planner_test_utils import model_wire_response, task_contract


TASK_CONTRACT = task_contract


class NormalizationCatalog:
    def __init__(self):
        self.operations = {
            "produce.vector": self._producer("produce.vector", "feature_class"),
            "produce.raster": self._producer("produce.raster", "raster"),
            "produce.file": self._producer("produce.file", "file"),
            "consume.layer": {
                "id": "consume.layer",
                "summary": "consume a layer",
                "category": "analysis",
                "parameters_schema": {
                    "type": "object",
                    "required": ["layer"],
                    "properties": {
                        "layer": {"type": "string", "x-geopilot-kind": "layer"},
                    },
                    "additionalProperties": False,
                },
                "context_requirements": {},
                "side_effects": "read_only",
                "output_policy": {},
                "examples": [],
            },
        }
        self.capabilities = _NormalizationCapabilities()

    @staticmethod
    def _producer(operation_id, output_type):
        return {
            "id": operation_id,
            "summary": "produce a " + output_type,
            "category": "analysis",
            "parameters_schema": {
                "type": "object",
                "required": ["output_name"],
                "properties": {"output_name": {"type": "string"}},
                "additionalProperties": False,
            },
            "context_requirements": {},
            "side_effects": "writes_data",
            "output_policy": {
                "type": output_type,
                "workspace": "mxd_default",
                "add_to_map": output_type in ("feature_class", "raster"),
            },
            "examples": [],
        }

    def get(self, operation_id):
        return self.operations[operation_id]

    def all_operations(self):
        return self.operations.values()

    def planning_card(self, operation):
        return {
            "id": operation["id"],
            "summary": operation["summary"],
            "examples": operation.get("examples", [])[:2],
            **self.capabilities.get(operation["id"]),
        }


class _NormalizationCapabilities:
    def get(self, operation_id):
        if operation_id.startswith("produce."):
            if operation_id == "produce.vector":
                output_format = {"rule": "fixed", "value": "gdb"}
            elif operation_id == "produce.raster":
                output_format = {"rule": "fixed", "value": "tif"}
            else:
                output_format = {"rule": "fixed", "value": "file"}
            return {
                "inputs": [], "side_effects": "writes_data",
                "parameters_schema": {
                    "type": "object", "required": ["output_name"],
                    "properties": {"output_name": {"type": "string"}},
                    "additionalProperties": False,
                },
                "semantic_effects": [{"kind": "feature_create", "action": {"const": "produce"}, "result": {"output": True}}],
                "outputs": {
                    "kind": "feature_class" if operation_id == "produce.vector" else "raster" if operation_id == "produce.raster" else "file",
                    "geometry": {"rule": "fixed", "value": "polygon"} if operation_id == "produce.vector" else {"rule": "fixed", "value": "raster"} if operation_id == "produce.raster" else {"rule": "not_applicable", "value": "not_applicable"},
                    "fields": {"effect": "static_generated", "target": "not_applicable", "static_fields": [], "parameter_field": "not_applicable"},
                    "spatial_reference": {"rule": "not_applicable", "input": "not_applicable"},
                    "cardinality": {"rule": "fixed", "value": "one"},
                    "selection_state": "not_applicable", "map_publication": "published",
                    "format": output_format,
                },
            }
        if operation_id == "consume.layer":
            return {
                "inputs": [{"parameter": "layer", "cardinality": "one", "data_kind": ["feature_layer", "raster_layer"], "geometry": ["point", "polyline", "polygon", "raster"], "required_fields": [], "selection": {"rule": "any"}}],
                "parameters_schema": {
                    "type": "object", "required": ["layer"],
                    "properties": {"layer": {"type": "string", "x-geopilot-kind": "layer"}},
                    "additionalProperties": False,
                },
                "semantic_effects": [{
                    "kind": "map_change", "subject": {"parameter": "layer"},
                    "action": {"const": "refresh"},
                }],
                "side_effects": "read_only",
                "outputs": {"kind": "none", "geometry": {"rule": "not_applicable", "value": "not_applicable"}, "fields": {"effect": "not_applicable", "target": "not_applicable", "static_fields": [], "parameter_field": "not_applicable"}, "spatial_reference": {"rule": "not_applicable", "input": "not_applicable"}, "cardinality": {"rule": "fixed", "value": "not_applicable"}, "selection_state": "not_applicable", "map_publication": "none", "format": {"rule": "not_applicable", "value": "not_applicable"}},
            }
        raise KeyError(operation_id)


class ScriptedClient:
    def __init__(self, replies):
        self.provider_id = "test-provider"
        self.model_id = "test-model"
        self.replies = list(replies)

    def chat_structured(self, messages, contract):
        return model_wire_response(self.replies.pop(0), messages)


class WorkflowNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.catalog = NormalizationCatalog()
        self.context = {"is_saved": True, "layers": []}

    @staticmethod
    def _workflow(producer, output_name="generated", layer_value="generated"):
        return {
            "action": "execute",
            "summary": "derive and inspect a result",
            "steps": [
                {
                    "id": "produce",
                    "operation": producer,
                    "arguments": {"output_name": output_name},
                    "reason": "derive the result",
                },
                {
                    "id": "consume",
                    "operation": "consume.layer",
                    "arguments": {"layer": layer_value},
                    "reason": "inspect the result",
                },
            ],
        }

    def _prepare(self, workflow):
        events = []
        prepared = prepare_workflow(workflow, self.catalog, self.context, events)
        return prepared, events

    @staticmethod
    def _task_contract():
        contract = task_contract("derive and inspect")
        contract["outputs"][0].update({"kind": "feature_class", "name": "generated", "format": "gdb", "geometry": "polygon", "spatial_reference": "not_applicable", "destination": "default"})
        return contract

    def test_feature_class_name_becomes_prior_step_reference_with_event(self):
        prepared, events = self._prepare(self._workflow("produce.vector"))

        self.assertEqual("from_step:produce", prepared["steps"][1]["arguments"]["layer"])
        self.assertEqual(
            [{
                "step_id": "consume", "argument": "layer",
                "original": "generated", "canonical": "from_step:produce",
            }],
            events,
        )

    def test_raster_name_becomes_prior_step_reference_with_event(self):
        prepared, events = self._prepare(
            self._workflow("produce.raster", "surface", "surface")
        )

        self.assertEqual("from_step:produce", prepared["steps"][1]["arguments"]["layer"])
        self.assertEqual("surface", events[0]["original"])

    def test_file_output_name_cannot_be_normalized_as_layer(self):
        with self.assertRaisesRegex(ValidationError, "file and cannot be used as a layer"):
            self._prepare(self._workflow("produce.file"))

    def test_unmatched_name_is_left_for_semantic_validation(self):
        workflow = self._workflow("produce.vector", layer_value="unmatched")
        events = []

        with self.assertRaisesRegex(ValidationError, "没有精确匹配“unmatched”的图层"):
            prepare_workflow(workflow, self.catalog, self.context, events)
        self.assertEqual("unmatched", workflow["steps"][1]["arguments"]["layer"])
        self.assertEqual([], events)

    def test_duplicate_prior_output_names_are_ambiguous(self):
        workflow = self._workflow("produce.vector")
        workflow["steps"].insert(1, {
            "id": "produce_again",
            "operation": "produce.raster",
            "arguments": {"output_name": "generated"},
            "reason": "derive another result",
        })

        with self.assertRaisesRegex(ValidationError, "Ambiguous in-workflow output reference: generated"):
            self._prepare(workflow)

    def test_later_output_name_is_not_used_as_a_backward_reference(self):
        workflow = {
            "action": "execute",
            "summary": "inspect before producing",
            "steps": [
                {
                    "id": "consume",
                    "operation": "consume.layer",
                    "arguments": {"layer": "generated"},
                    "reason": "inspect first",
                },
                {
                    "id": "produce",
                    "operation": "produce.vector",
                    "arguments": {"output_name": "generated"},
                    "reason": "derive later",
                },
            ],
        }

        events = []
        with self.assertRaisesRegex(ValidationError, "当前地图里没有“generated”图层"):
            prepare_workflow(workflow, self.catalog, self.context, events)
        self.assertEqual([], events)

    def test_all_modes_store_the_same_prepared_workflow_and_trace_events(self):
        workflow = self._workflow("produce.vector")
        expected_events = [{
            "step_id": "consume", "argument": "layer",
            "original": "generated", "canonical": "from_step:produce",
        }]
        stored_workflows = []
        for mode in ("g0_direct", "g1_context", "g2_constrained", "g3_audited"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                replies = (
                    [{"workflow_draft": copy.deepcopy(workflow)}]
                    if mode in ("g0_direct", "g1_context") else
                    [{"task_contract": self._task_contract()}, {"workflow_draft": copy.deepcopy(workflow)}]
                )
                if mode == "g3_audited":
                    planner = ScriptedClient(replies)
                    auditor = ScriptedClient([{"audit_result": {"decision": "pass", "claims": []}}])
                    clients = iter((planner, auditor))
                    factory = lambda provider, model: next(clients)
                else:
                    factory = lambda provider, model: ScriptedClient(replies)
                store = RunStore(Path(directory) / "runs.sqlite")
                runner = PlanningEngine(self.catalog, store, factory)
                run = store.create_run("derive and inspect", mode)
                store.bind_context(run["id"], {
                    "context": self.context,
                    "context_hash": context_hash(self.context),
                    "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4},
                    "captured_at": 1,
                })

                row = runner.plan(
                    run["id"], "derive and inspect", self.context, mode,
                    provider="test-provider", model="test-model",
                )
                trace = row["agent_trace"][0]["run"]
                self.assertEqual("planned", row["status"])
                self.assertEqual("from_step:produce", row["workflow"]["steps"][1]["arguments"]["layer"])
                stored_workflows.append(row["workflow"])
                self.assertEqual(expected_events, trace["normalization_events"])
                self.assertEqual(expected_events, trace["validations"][0]["normalization_events"])
                if mode == "g3_audited":
                    self.assertEqual("pass", trace["audits"][0]["decision"])
        self.assertEqual([stored_workflows[0]] * 4, stored_workflows)

    def test_normalized_g2_artifact_replays_through_public_g3_entry(self):
        command = "derive and inspect"
        workflow = self._workflow("produce.vector")
        task = self._task_contract()
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(Path(directory) / "runs.sqlite")
            baseline = PlanningEngine(
                self.catalog,
                store,
                lambda provider, model: ScriptedClient([
                    {"task_contract": task},
                    {"workflow_draft": copy.deepcopy(workflow)},
                ]),
            )
            source = store.create_run(command, "g2_constrained")
            store.bind_context(source["id"], {
                "context": self.context,
                "context_hash": context_hash(self.context),
                "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4},
                "captured_at": 1,
            })
            source = baseline.plan(
                source["id"], command, self.context, "g2_constrained",
                provider="test-provider", model="test-model",
            )
            artifact = source["agent_trace"][0]["run"]["plan_artifact"]

            primary = ScriptedClient([])
            auditor = ScriptedClient([{"audit_result": {"decision": "pass", "claims": []}}])
            clients = iter((primary, auditor))
            replay = PlanningEngine(
                self.catalog,
                store,
                lambda provider, model: next(clients),
            )
            target = store.create_run(command, "g3_audited")
            store.bind_context(target["id"], {
                "context": self.context,
                "context_hash": context_hash(self.context),
                "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4},
                "captured_at": 2,
            })

            result = replay.plan_with_artifact(
                target["id"], command, self.context, "g3_audited", artifact,
                provider="test-provider", model="test-model",
            )

        self.assertEqual("planned", result["status"])
        self.assertEqual(
            artifact["artifact_hash"],
            result["agent_trace"][0]["run"]["plan_artifact_hash"],
        )
