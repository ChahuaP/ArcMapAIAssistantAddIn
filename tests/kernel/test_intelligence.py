# -*- coding: utf-8 -*-
"""Stage C TaskCompiler + WorkflowEngine tests (§C.3, §11).

Drives both deep modules through ModelRuntime with a FakeModelAdapter that
returns a minimal but valid task_contract and workflow_draft. Validates that
the new intelligence layer produces an IntentSpec and a sealed VerifiedPlan
through the existing deterministic verification cluster.
"""
from __future__ import absolute_import

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from gateway_py3.model_runtime.adapter import ProviderError
from gateway_py3.model_runtime.contracts import ProviderInvocation, ProviderResponse, StructuredOutputContract
from gateway_py3.intelligence.task_compiler import TaskCompiler
from gateway_py3.intelligence.workflow_engine import WorkflowEngine
from gateway_py3.kernel import contracts
from gateway_py3.kernel.contracts import (
    CallerIdentity, CapabilitySnapshot, CapabilitySpec, ContextSnapshot,
    EntityBinding, FieldColumn, IntentSpec, LayerRef, LayerSnapshot,
    RequestEnvelope, SUCCEEDED, CONTRACT_FAILED,
)
from gateway_py3.kernel.store import JournalStore
from tests.kernel.fakes import (
    build_test_model_runtime,
    fake_agent_model_plan,
    fake_model_binding_summary,
)


# --- minimal valid task_contract + workflow fixtures ----------------------

def _task_contract_response():
    """A minimal task_contract the model would return for 'select cities'.

    This is the *model view*: inputs have entity_id/role/reference (no
    evidence/kind), requirements use predicate_json (a JSON string), not
    inline predicate objects. bind_model_task_contract restores the internal
    shape; parse_task_contract validates it.
    """
    return {
        "input_entities": [
            {"entity_id": "input:cities", "role": "primary", "reference": "cities"},
        ],
        "outputs": [],
        "requirements": [
            {"requirement_id": "req1",
             "predicate_json": json.dumps(
                 {"kind": "source_preserved", "subject": "input:cities"},
                 ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
        ],
        "allowed_side_effects": ["read_only"],
        "clarifications": [],
    }


def _workflow_draft_response():
    """A minimal tool_calls response the model would return.

    The planner uses native function calling (``chat_with_tools``) and the
    response is ``{"tool_calls": [{"name": ..., "arguments": ...}]}``. Each
    tool_call maps to one workflow step.
    """
    return {
        "tool_calls": [
            {"name": "step_context-list_layers", "arguments": {}},
        ],
    }


def _add_layer_task_contract_response():
    return {
        "input_entities": [{
            "entity_id": "input:nanjing_shp",
            "role": "source shapefile to add",
            "reference": "D:/Data/shapefile/nanjing.shp",
        }],
        "outputs": [{
            "output_id": "output:nanjing_layer",
            "kind": "feature_layer",
            "name": "nanjing",
            "format": "not_applicable",
            "geometry": "not_applicable",
            "required_fields": [],
            "spatial_reference": "not_applicable",
            "destination_policy": "not_applicable",
            "evidence": "D:/Data/shapefile/nanjing.shp",
        }],
        "requirements": [{
            "requirement_id": "req:add_nanjing_shp",
            "predicate_json": json.dumps({
                "kind": "source_preserved",
                "subject": "input:nanjing_shp",
            }),
        }],
        "allowed_side_effects": ["changes_map"],
        "clarifications": [],
    }


# --- fake adapter that returns fixed responses per role ------------------

class _ScriptedAdapter:
    """Returns a fixed response; records call count."""

    def __init__(self, responses: Dict[str, Dict[str, Any]]):
        self.provider_type = "fake"
        self.connection_id = "fake-connection"
        self.call_count = 0
        self._responses = responses
        self._call_keys = []

    def invoke(self, call: ProviderInvocation, on_token=None) -> ProviderResponse:
        self.call_count += 1
        system = call.messages[0]["content"] if call.messages else ""
        if "任务合同编译器" in system:
            response, tokens = self._responses.get("task_contract", {}), 10
        elif "工作流规划器" in system:
            response, tokens = self._responses.get("workflow", {}), 20
        elif "工作流修复器" in system:
            response, tokens = self._responses.get("repair", self._responses.get("workflow", {})), 15
        elif "G3 审计器" in system:
            default_audit = {"audit_result": {"decision": "pass", "revision": None, "clarification": None}}
            response, tokens = self._responses.get("audit", default_audit), 5
        else:
            response, tokens = self._responses.get("default", {}), 1
        if on_token is not None:
            on_token("token")
        return ProviderResponse(response=dict(response), usage={"total_tokens": tokens})


# --- context + capability fixtures ----------------------------------------

def _context_snapshot() -> ContextSnapshot:
    return ContextSnapshot(
        lease_id="00000000-0000-0000-0000-0000000000aa",
        arcmap_pid=2000, bridge_pid=2001, bridge_port=8766, target_hwnd=3000,
        document_identity={"mxd": "Untitled.mxd"},
        layers=(
            LayerSnapshot(
                identity=LayerRef(name="cities", layer_ref="cities",
                                  data_source="C:/data/cities.shp",
                                  layer_type="Feature Layer"),
                fields=(FieldColumn(name="NAME", dtype="String"),
                        FieldColumn(name="POP", dtype="Integer")),
                geometry_type="Point",
                coordinate_system="WGS84",
                selection_count=0,
            ),
        ),
        active_data_frame="Layers",
        captured_at=1.0,
        deployment_hash="test-deployment-v1",
        content_hash="test-content-hash",
    )


def _capability_snapshot() -> CapabilitySnapshot:
    """Build a capability snapshot from the real catalog's list_layers op."""
    from gateway_py3.catalog_loader import OperationCatalog
    catalog = OperationCatalog()
    op = next(o for o in catalog.all_operations() if o["id"] == "context.list_layers")
    card = catalog.planning_card(op)
    risk = {"read_only": 1, "changes_map": 2, "writes_data": 3, "edits_data": 4}[card["side_effects"]]
    return CapabilitySnapshot(
        operation_cards=(
            CapabilitySpec(
                operation_id=card["id"],
                business_semantic=card["summary"],
                parameters_schema=card["parameters_schema"],
                risk_level=risk,
            ),
        ),
        domain_rule_hash="test-rules-v1",
        registry_version="test-registry-v1",
    )


def _request(text="列出地图图层") -> RequestEnvelope:
    return RequestEnvelope(
        session_id="00000000-0000-0000-0000-000000000001",
        request_id="00000000-0000-0000-0000-000000000002",
        text=text,
        caller=CallerIdentity(user_id="u1", tenant_id="t1", role="analyst"),
        target_selector={"bridge_pid": 2001, "bridge_port": 8766,
                         "arcmap_pid": 2000, "hwnd": 3000,
                         "deployment_hash": "a" * 64},
        model_plan=fake_agent_model_plan().model_dump(mode="json"),
        model_binding_summary=fake_model_binding_summary(),
    )


class _BaseIntelligenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "gp.sqlite"
        self.store = JournalStore(path=self.tmp)
        self.context = _context_snapshot()
        self.capabilities = _capability_snapshot()
        self.request = _request()

    def _runtime(self, responses):
        adapter = _ScriptedAdapter(responses)
        return build_test_model_runtime(adapter, self.store)


class TaskCompilerTest(_BaseIntelligenceTest):
    """§C.1: TaskCompiler produces an IntentSpec via ModelRuntime."""

    def test_compile_succeeds_and_produces_intent_spec(self):
        runtime = self._runtime({"task_contract": _task_contract_response()})
        compiler = TaskCompiler(runtime)
        outcome = compiler.compile(self.request, self.context, self.capabilities)
        self.assertTrue(outcome.succeeded)
        intent = outcome.details.get("intent")
        self.assertIsInstance(intent, IntentSpec)
        self.assertEqual(intent.business_goal, "列出地图图层")
        self.assertEqual(len(intent.bound_inputs), 1)
        self.assertEqual(intent.bound_inputs[0].name, "input:cities")
        self.assertEqual(intent.acceptable_side_effects, 1)
        self.assertIn("task_contract", intent.derived_facts)

    def test_add_external_shapefile_binds_to_map_change(self):
        runtime = self._runtime({"task_contract": _add_layer_task_contract_response()})
        compiler = TaskCompiler(runtime)
        request = _request("D:/Data/shapefile/nanjing.shp添加这个shp")

        outcome = compiler.compile(request, self.context, self.capabilities)

        self.assertTrue(outcome.succeeded, msg=str(outcome))
        contract = outcome.details["task_contract"]
        self.assertEqual("map_state", contract["outputs"][0]["kind"])
        self.assertEqual({
            "kind": "map_change",
            "subject": "output:nanjing_layer",
            "target": "input:nanjing_shp",
            "action": "add_layer",
        }, contract["requirements"][0]["predicate"])

    def test_quota_stop_returns_quota_outcome(self):
        adapter = _ScriptedAdapter({})
        def raise_quota(call, on_token=None):
            raise ProviderError("quota", "额度不足，余额已用完")
        adapter.invoke = raise_quota
        runtime = build_test_model_runtime(adapter, self.store)
        compiler = TaskCompiler(runtime)
        outcome = compiler.compile(self.request, self.context, self.capabilities)
        self.assertEqual(outcome.kind, contracts.QUOTA_STOPPED)
        self.assertTrue(outcome.is_terminal)


class WorkflowEngineTest(_BaseIntelligenceTest):
    """§C.2: WorkflowEngine produces a sealed VerifiedPlan."""

    def _make_intent(self) -> IntentSpec:
        runtime = self._runtime({"task_contract": _task_contract_response()})
        compiler = TaskCompiler(runtime)
        outcome = compiler.compile(self.request, self.context, self.capabilities)
        self.assertTrue(outcome.succeeded)
        return outcome.details["intent"]

    def test_plan_succeeds_and_seals_verified_plan(self):
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": _workflow_draft_response(),
        }
        runtime = self._runtime(responses)
        from gateway_py3.catalog_loader import OperationCatalog
        catalog = OperationCatalog()
        engine = WorkflowEngine(catalog, runtime)
        intent = self._make_intent()
        outcome = engine.plan("00000000-0000-0000-0000-00000000000c", intent, self.context, self.capabilities)
        self.assertTrue(outcome.succeeded, msg=str(outcome))
        plan = outcome.details.get("plan")
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.workflow), 1)
        self.assertEqual(plan.workflow[0].operation, "context.list_layers")
        self.assertEqual(plan.intent_digest, intent.digest)
        self.assertEqual(plan.context_digest, self.context.digest)

    def test_plan_fails_on_invalid_workflow_operation(self):
        bad_response = {
            "tool_calls": [
                {"name": "nonexistent_operation", "arguments": {}},
            ],
        }
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": bad_response,
        }
        runtime = self._runtime(responses)
        from gateway_py3.catalog_loader import OperationCatalog
        catalog = OperationCatalog()
        engine = WorkflowEngine(catalog, runtime)
        intent = self._make_intent()
        outcome = engine.plan("00000000-0000-0000-0000-00000000000c", intent, self.context, self.capabilities)
        self.assertFalse(outcome.succeeded)
        self.assertIn(outcome.kind, (CONTRACT_FAILED, contracts.INFRASTRUCTURE_FAILED))

    def test_add_external_shapefile_plans_add_layer(self):
        responses = {
            "task_contract": _add_layer_task_contract_response(),
            "workflow": {
                "tool_calls": [{
                    "name": "step_layer-add_layer",
                    "arguments": {"path": "D:/Data/shapefile/nanjing.shp"},
                }],
            },
        }
        runtime = self._runtime(responses)
        from gateway_py3.catalog_loader import OperationCatalog
        from gateway_py3.runtime.capability_provider import CapabilityProvider
        catalog = OperationCatalog()
        capabilities = CapabilityProvider(catalog).snapshot("test-add-layer")
        compiler = TaskCompiler(runtime)
        request = _request("D:/Data/shapefile/nanjing.shp添加这个shp")
        compiled = compiler.compile(request, self.context, capabilities)
        self.assertTrue(compiled.succeeded, msg=str(compiled))
        self.assertEqual(
            "D:/Data/shapefile/nanjing.shp",
            compiled.details["intent"].bound_inputs[0].path,
        )
        self.assertIsNone(compiled.details["intent"].bound_inputs[0].layer_ref)

        engine = WorkflowEngine(catalog, runtime)
        planned = engine.plan(
            "00000000-0000-0000-0000-00000000000d",
            compiled.details["intent"], self.context, capabilities,
        )

        self.assertTrue(planned.succeeded, msg=str(planned))
        plan = planned.details["plan"]
        self.assertEqual("layer.add_layer", plan.workflow[0].operation)
        self.assertEqual(
            "D:/Data/shapefile/nanjing.shp",
            plan.workflow[0].arguments["path"],
        )
        self.assertEqual(
            ("D:/Data/shapefile/nanjing.shp",),
            plan.input_identities,
        )


if __name__ == "__main__":
    unittest.main()
