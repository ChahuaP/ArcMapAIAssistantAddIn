import copy
import json
import tempfile
import unittest
from pathlib import Path

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.llm_providers import ProviderError, ProviderProtocolError
from gateway_py3.planning_engine import ContractError, PlanningEngine
from gateway_py3.plan_artifact import PlanArtifact, PlanArtifactError, canonical_hash
from gateway_py3.run_store import RunStore
from gateway_py3.task_contract import (
    TASK_CONTRACT as TASK_CONTRACT_SCHEMA,
    TaskContractError,
    bind_model_task_contract,
    parse_task_contract,
    task_contract_model_view,
)
from gateway_py3.validators import context_hash
from tests.gateway.planner_test_utils import model_wire_response, task_contract


CONTEXT = {"layers": []}
WORKFLOW = {
    "action": "execute",
    "summary": "refresh",
    "steps": [
        {
            "id": "s1",
            "operation": "context.list_layers",
            "arguments": {},
            "reason": "refresh",
        }
    ],
}
TASK_CONTRACT = task_contract


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, provider, model):
        self.provider_id = provider
        self.model_id = model
        return self

    def chat_structured(self, messages, contract):
        self.calls.append(messages)
        if hasattr(self, "events"):
            role = messages[0]["content"].split("GeoPilot ")[1].split(" role")[0]
            self.events.append(role)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return model_wire_response(reply, messages)


class PlanningEngineTests(unittest.TestCase):
    def test_task_contract_output_schema_matches_the_closed_parser_fields(self):
        task_properties = TASK_CONTRACT_SCHEMA.schema["properties"]["task_contract"]["properties"]
        output_schema = task_properties["outputs"]["items"]
        requirement_schema = task_properties["requirements"]["items"]
        self.assertEqual("submit_task_contract_v10", TASK_CONTRACT_SCHEMA.name)
        self.assertEqual(set(output_schema["properties"]), set(output_schema["required"]))
        self.assertIn("evidence", output_schema["properties"])
        self.assertNotIn("evidence", task_properties["input_entities"]["items"]["properties"])
        self.assertNotIn("oneOf", requirement_schema)
        self.assertEqual(
            {"requirement_id", "predicate_json"},
            set(requirement_schema["properties"]),
        )
        self.assertNotIn("evidence", task_properties["clarifications"]["items"]["properties"])
        self.assertNotIn("grain", output_schema["properties"])

    def test_complex_flat_model_requirements_bind_without_predicate_loss(self):
        command = (
            "继续选址约束分析，生成 school_buf.shp、industry_buf.shp、river_buf.shp、"
            "site_exclusion.shp、site_safe.shp；源数据只读。"
        )
        references = (
            ("input:site_attr_ok", "layer:site_attr_ok", "Polygon"),
            ("input:schools", "layer:schools", "Point"),
            ("input:industry", "layer:industry", "Polygon"),
            ("input:rivers", "layer:rivers", "Polyline"),
            ("input:flood_zones", "layer:flood_zones", "Polygon"),
            ("input:protected", "layer:protected", "Polygon"),
        )
        context = {"layers": [
            {
                "layer_ref": reference,
                "name": reference.split(":", 1)[1],
                "longName": reference.split(":", 1)[1],
                "geometry_type": geometry,
                "fields": [],
                "selected_count": 0,
            }
            for _, reference, geometry in references
        ]}
        outputs = [
            {
                "output_id": "output:" + name,
                "kind": "feature_class",
                "name": name,
                "format": "shp",
                "geometry": "polygon",
                "required_fields": [],
                "spatial_reference": "EPSG:32650",
                "destination": "default",
                "evidence": name + ".shp",
            }
            for name in ("school_buf", "industry_buf", "river_buf", "site_exclusion", "site_safe")
        ]
        predicates = [
            {"kind": "buffer", "subject": "output:school_buf", "source": "input:schools", "distance": {"value": 500, "unit": "meters"}},
            {"kind": "buffer", "subject": "output:industry_buf", "source": "input:industry", "distance": {"value": 1000, "unit": "meters"}},
            {"kind": "buffer", "subject": "output:river_buf", "source": "input:rivers", "distance": {"value": 300, "unit": "meters"}},
            {"kind": "overlay", "subject": "output:site_exclusion", "sources": ["output:school_buf", "output:industry_buf", "output:river_buf", "input:flood_zones", "input:protected"], "method": "union"},
            {"kind": "overlay", "subject": "output:site_safe", "sources": ["input:site_attr_ok", "output:site_exclusion"], "method": "erase"},
        ]
        predicates.extend(
            {"kind": "source_preserved", "subject": entity_id}
            for entity_id, _, _ in references
        )
        requirement_ids = ["school", "industry", "river", "union", "erase"] + [
            "preserve_" + entity_id.split(":", 1)[1] for entity_id, _, _ in references
        ]
        requirements = [
            {
                "requirement_id": requirement_id,
                "predicate_json": json.dumps(predicate, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            }
            for requirement_id, predicate in zip(requirement_ids, predicates)
        ]
        model_value = {
            "input_entities": [
                {"entity_id": entity_id, "role": "source", "reference": reference}
                for entity_id, reference, _ in references
            ],
            "outputs": outputs,
            "requirements": requirements,
            "allowed_side_effects": ["read_only", "changes_map", "writes_data"],
            "clarifications": [],
        }

        bound = bind_model_task_contract(model_value, command, context)
        parsed = parse_task_contract(bound, command, context)

        self.assertEqual(11, len(parsed["requirements"]))
        self.assertEqual(
            [item["kind"] for item in predicates],
            [item["predicate"]["kind"] for item in parsed["requirements"]],
        )

    def test_server_binds_nonsemantic_request_evidence(self):
        command = "\n刷新地图"
        model_value = {
            "input_entities": [],
            "outputs": [{
                "output_id": "o1", "kind": "map_state", "name": command,
                "format": "map", "geometry": "not_applicable",
                "required_fields": [], "spatial_reference": "not_applicable",
                "destination": "not_applicable", "evidence": command,
            }],
            "requirements": [{
                "requirement_id": "r1",
                "predicate_json": '{"action":"refresh","kind":"map_change","subject":"o1"}',
            }],
            "allowed_side_effects": ["changes_map"],
            "clarifications": [],
        }
        bound = bind_model_task_contract(model_value, command)
        self.assertEqual(command, bound["requirements"][0]["evidence"])
        self.assertEqual(command, parse_task_contract(bound, command)["requirements"][0]["evidence"])

    def test_server_rejects_the_removed_model_evidence_field(self):
        model_value = {
            "input_entities": [],
            "outputs": [],
            "requirements": [{
                "requirement_id": "r1",
                "kind": "map_change", "subject": "o1", "action": "refresh",
                "evidence": "legacy model field",
            }],
            "allowed_side_effects": [],
            "clarifications": [],
        }
        with self.assertRaisesRegex(TaskContractError, "requirements\\[0\\] has invalid fields"):
            bind_model_task_contract(model_value, "refresh")

    def test_server_rejects_the_removed_nested_predicate_wire_shape(self):
        model_value = {
            "input_entities": [],
            "outputs": [],
            "requirements": [{
                "requirement_id": "r1",
                "predicate": {"kind": "map_change", "subject": "o1", "action": "refresh"},
            }],
            "allowed_side_effects": [],
            "clarifications": [],
        }
        with self.assertRaisesRegex(TaskContractError, "requirements\\[0\\] has invalid fields"):
            bind_model_task_contract(model_value, "refresh")

    def runner(self, replies):
        self.temp = tempfile.TemporaryDirectory()
        return PlanningEngine(
            OperationCatalog(),
            RunStore(Path(self.temp.name) / "runs.sqlite"),
            FakeModel(replies),
        )

    def tearDown(self):
        getattr(self, "temp", None) and self.temp.cleanup()

    def test_g0_hides_context_but_validator_receives_it(self):
        workflow = copy.deepcopy(WORKFLOW)
        workflow["steps"][0]["operation"] = "layer.clear_layers"
        runner = self.runner([{"workflow_draft": workflow}])
        row = plan_bound(runner, "refresh", CONTEXT, "g0_direct")
        request = json.loads(runner.client_factory.calls[0][1]["content"])
        self.assertNotIn("context", request)
        self.assertEqual(row["agent_trace"][0]["run"]["context_hash"], row["context_hash"])

    def test_g1_has_one_context_role(self):
        runner = self.runner([{"workflow_draft": WORKFLOW}])
        plan_bound(runner, "refresh", CONTEXT, "g1_context")
        self.assertIn('"context"', runner.client_factory.calls[0][1]["content"])
        self.assertIn("context role", runner.client_factory.calls[0][0]["content"])

    def test_g2_repairs_in_same_role(self):
        invalid = dict(
            WORKFLOW,
            steps=[
                {
                    "id": "s1",
                    "operation": "context.list_layers",
                    "arguments": {"unexpected": True},
                    "reason": "invalid",
                }
            ],
        )
        runner = self.runner(
            [
                {"task_contract": TASK_CONTRACT},
                {"workflow_draft": invalid},
                {"workflow_draft": WORKFLOW},
            ]
        )
        row = plan_bound(runner, "refresh", CONTEXT, "g2_constrained")
        self.assertEqual(row["agent_trace"][0]["run"]["counts"]["validation_revisions"], 1)
        self.assertTrue(
            all(
                "planner role" in call[0]["content"]
                for call in runner.client_factory.calls
                if "planner role" in call[0]["content"]
            )
        )

    def test_g2_repairs_a_noncanonical_export_action_before_workflow_planning(self):
        command = "导出当前应急分布图"
        context = {"is_saved": True, "layers": []}
        canonical = {
            "input_entities": [],
            "outputs": [{
                "output_id": "output:emergency_map", "kind": "file",
                "name": "emergency_map", "format": "png",
                "geometry": "not_applicable", "required_fields": [],
                "spatial_reference": "not_applicable", "destination": "default",
                "evidence": command,
            }],
            "requirements": [{
                "requirement_id": "export_map", "evidence": command,
                "predicate": {
                    "kind": "artifact_export", "subject": "output:emergency_map",
                    "action": "map_png", "output_format": "png",
                },
            }],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }
        invalid = json.loads(json.dumps(canonical))
        invalid["requirements"][0]["predicate"]["action"] = "export_map_png"
        workflow = {
            "action": "execute", "summary": command,
            "steps": [{
                "id": "export_map", "operation": "export.map_png",
                "arguments": {"output_name": "emergency_map"}, "reason": command,
            }],
        }
        runner = self.runner([
            {"task_contract": invalid},
            {"task_contract": canonical},
            {"workflow_draft": workflow},
        ])

        row = plan_bound(runner, command, context, "g2_constrained")

        trace = row["agent_trace"][0]["run"]
        roles = [call[0]["content"].split("GeoPilot ")[1].split(" role")[0]
                 for call in runner.client_factory.calls]
        self.assertEqual("planned", row["status"])
        self.assertEqual(["semantic", "semantic", "planner"], roles)
        self.assertEqual(1, trace["counts"]["contract_revisions"])
        self.assertEqual(0, trace["counts"]["validation_revisions"])

    def test_g2_repairs_impossible_passthrough_fields_before_workflow_planning(self):
        command = "将道路导出为 roads.csv"
        context = {"is_saved": True, "layers": [{
            "layer_ref": "layer:roads", "name": "roads", "longName": "roads",
            "geometry_type": "Polyline", "spatial_reference": "EPSG:3857",
            "fields": [{"name": "RID"}, {"name": "CLASS"}], "selected_count": 0,
        }]}
        canonical = {
            "input_entities": [{
                "entity_id": "input:roads", "role": "source", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": command,
            }],
            "outputs": [{
                "output_id": "output:roads_csv", "kind": "file", "name": "roads",
                "format": "csv", "geometry": "not_applicable", "required_fields": [],
                "spatial_reference": "not_applicable", "destination": "default",
                "evidence": "roads.csv",
            }],
            "requirements": [{
                "requirement_id": "export_csv", "evidence": command,
                "predicate": {
                    "kind": "artifact_export", "subject": "output:roads_csv",
                    "target": "input:roads", "action": "table_csv",
                    "selected_only": False, "output_format": "csv",
                },
            }],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }
        invalid = json.loads(json.dumps(canonical))
        invalid["outputs"][0]["required_fields"] = ["SITE_ID"]
        workflow = {
            "action": "execute", "summary": command,
            "steps": [{
                "id": "export_csv", "operation": "export.table_csv",
                "arguments": {
                    "layer": "layer:roads", "selected_only": False,
                    "output_name": "roads",
                },
                "reason": command,
            }],
        }
        runner = self.runner([
            {"task_contract": invalid},
            {"task_contract": canonical},
            {"workflow_draft": workflow},
        ])

        row = plan_bound(runner, command, context, "g2_constrained")

        trace = row["agent_trace"][0]["run"]
        roles = [call[0]["content"].split("GeoPilot ")[1].split(" role")[0]
                 for call in runner.client_factory.calls]
        self.assertEqual("planned", row["status"])
        self.assertEqual(["semantic", "semantic", "planner"], roles)
        self.assertEqual(1, trace["counts"]["contract_revisions"])
        self.assertEqual(0, trace["counts"]["validation_revisions"])

    def test_public_planning_prompt_matches_the_forced_tool_protocol(self):
        class ToolProtocolModel(FakeModel):
            def chat_structured(self, messages, contract):
                system = messages[0]["content"]
                if "Call the supplied structured-output tool exactly once" not in system:
                    raise AssertionError("planning prompt does not require the forced tool call")
                if "Return JSON only" in system:
                    raise AssertionError("planning prompt contradicts the forced tool call")
                return super().chat_structured(messages, contract)

        self.temp = tempfile.TemporaryDirectory()
        model = ToolProtocolModel([
            {"task_contract": TASK_CONTRACT},
            {"workflow_draft": WORKFLOW},
        ])
        runner = PlanningEngine(
            OperationCatalog(), RunStore(Path(self.temp.name) / "runs.sqlite"), model,
        )

        row = plan_bound(runner, "refresh", CONTEXT, "g2_constrained")

        self.assertEqual("planned", row["status"])

    def test_g2_repairs_invalid_response_contract_before_workflow_validation(self):
        invalid = {
            "task_contract": TASK_CONTRACT,
            "workflow_draft": dict(WORKFLOW, unexpected="model noise"),
        }
        runner = self.runner([
            {"task_contract": TASK_CONTRACT},
            {"workflow_draft": dict(WORKFLOW, unexpected="model noise")},
            {"workflow_draft": WORKFLOW},
        ])

        row = plan_bound(runner, "refresh", CONTEXT, "g2_constrained")

        trace = row["agent_trace"][0]["run"]
        self.assertEqual(row["status"], "planned")
        self.assertEqual(trace["counts"]["contract_revisions"], 1)
        self.assertEqual(trace["counts"]["validation_revisions"], 0)
        repair_request = json.loads(runner.client_factory.calls[2][1]["content"])
        self.assertEqual(
            repair_request["response_contract_repair"]["kind"],
            "response_contract",
        )
        self.assertIn(
            "workflow_draft",
            repair_request["response_contract_repair"]["message"],
        )

    def test_g2_binds_request_evidence_without_a_model_revision(self):
        model_contract = task_contract("refresh")
        model_contract["requirements"][0]["evidence"] = "model-controlled text"
        runner = self.runner([
            {"task_contract": model_contract},
            {"workflow_draft": WORKFLOW},
        ])

        row = plan_bound(runner, "refresh", CONTEXT, "g2_constrained")

        trace = row["agent_trace"][0]["run"]
        self.assertEqual(row["status"], "planned")
        self.assertEqual(trace["counts"]["contract_revisions"], 0)
        self.assertEqual("refresh", trace["task_contract"]["requirements"][0]["evidence"])
        planner_request = json.loads(runner.client_factory.calls[1][1]["content"])
        self.assertNotIn("evidence", planner_request["task_contract"]["requirements"][0])

    def test_public_planning_repair_explains_the_closed_distance_shape(self):
        command = "refresh within 500 meters"
        context = {
            "layers": [
                {"layer_ref": "layer:roads", "name": "roads", "geometry_type": "Polyline"},
                {"layer_ref": "layer:schools", "name": "schools", "geometry_type": "Point"},
            ]
        }
        malformed = task_contract(command)
        malformed["input_entities"] = [
            {
                "entity_id": "roads", "role": "target", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": "refresh",
            },
            {
                "entity_id": "schools", "role": "selector", "kind": "feature_layer",
                "reference": "layer:schools", "evidence": "500 meters",
            },
        ]
        malformed["requirements"][0] = {
            "requirement_id": "r1",
            "predicate": {
                "kind": "spatial_filter", "subject": "roads", "target": "roads",
                "selector": "schools", "overlap_type": "within_a_distance",
                "search_distance": {"distance": 500, "units": "Meters"},
                "selection_type": "new_selection",
            },
            "evidence": "500 meters",
        }
        runner = self.runner([
            {"task_contract": malformed},
            {"task_contract": task_contract(command)},
            {"workflow_draft": WORKFLOW},
        ])

        row = plan_bound(runner, command, context, "g2_constrained")

        self.assertEqual("planned", row["status"])
        repair = json.loads(runner.client_factory.calls[1][1]["content"])["response_contract_repair"]
        self.assertIn('{"value": number, "unit":', repair["message"])

    def test_public_planning_repairs_a_contradictory_filter_target_in_the_semantic_role(self):
        command = "refresh roads within schools"
        context = {
            "layers": [
                {"layer_ref": "layer:roads", "name": "roads", "geometry_type": "Polyline"},
                {"layer_ref": "layer:schools", "name": "schools", "geometry_type": "Point"},
            ]
        }
        malformed = task_contract(command)
        malformed["input_entities"] = [
            {
                "entity_id": "roads", "role": "target", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": "roads",
            },
            {
                "entity_id": "schools", "role": "selector", "kind": "feature_layer",
                "reference": "layer:schools", "evidence": "schools",
            },
        ]
        malformed["requirements"][0] = {
            "requirement_id": "r1",
            "predicate": {
                "kind": "spatial_filter", "subject": "roads", "target": "schools",
                "selector": "schools", "overlap_type": "within",
                "selection_type": "new_selection",
            },
            "evidence": "roads within schools",
        }
        runner = self.runner([
            {"task_contract": malformed},
            {"task_contract": task_contract(command)},
            {"workflow_draft": WORKFLOW},
        ])

        row = plan_bound(runner, command, context, "g2_constrained")

        self.assertEqual("planned", row["status"])
        repair = json.loads(runner.client_factory.calls[1][1]["content"])["response_contract_repair"]
        self.assertEqual("semantic", repair["role"])
        self.assertIn("target must equal subject", repair["message"])

    def test_public_planning_rejects_subset_selection_without_an_existing_selection(self):
        command = "refresh selected roads"
        context = {
            "is_saved": True,
            "layers": [{
                "layer_ref": "layer:roads", "name": "roads", "geometry_type": "Polyline", "selected_count": 0,
            }]
        }
        malformed = task_contract(command)
        malformed["input_entities"] = [{
            "entity_id": "roads", "role": "target", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": "roads",
        }]
        malformed["requirements"][0] = {
            "requirement_id": "r1",
            "predicate": {
                "kind": "attribute_filter", "subject": "roads", "target": "roads",
                "where": {"field": "CLASS", "op": "eq", "value": "A"},
                "selection_type": "select_subset",
            },
            "evidence": "selected roads",
        }
        runner = self.runner([
            {"task_contract": malformed},
            {"task_contract": task_contract(command)},
            {"workflow_draft": WORKFLOW},
        ])

        row = plan_bound(runner, command, context, "g2_constrained")

        self.assertEqual("planned", row["status"])
        repair = json.loads(runner.client_factory.calls[1][1]["content"])["response_contract_repair"]
        self.assertEqual("semantic", repair["role"])
        self.assertIn("select_subset requires a selection established", repair["message"])
        self.assertEqual(
            {"task_contract": task_contract_model_view(malformed)},
            repair["rejected_response"],
        )

    def test_g2_repairs_provider_protocol_violation_before_planning(self):
        evidence = {"choices": [{"message": {"content": "text-only JSON"}}]}
        runner = self.runner([
            ProviderProtocolError("结构化响应必须且只能包含一次工具调用。", evidence),
            {"task_contract": TASK_CONTRACT},
            {"workflow_draft": WORKFLOW},
        ])

        row = plan_bound(runner, "refresh", CONTEXT, "g2_constrained")

        trace = row["agent_trace"][0]["run"]
        self.assertEqual(row["status"], "planned")
        self.assertEqual(trace["counts"]["contract_revisions"], 1)
        diagnostic = trace["contract_diagnostics"][0]
        self.assertEqual(diagnostic["kind"], "provider_protocol")
        self.assertEqual(diagnostic["protocol_evidence_hash"], diagnostic["invalid_response_hash"])
        repair_request = json.loads(runner.client_factory.calls[1][1]["content"])
        self.assertEqual(repair_request["response_contract_repair"]["kind"], "provider_protocol")
        self.assertIn("planner role", runner.client_factory.calls[2][0]["content"])

    def test_g2_protocol_violations_exhaust_the_response_contract_budget(self):
        evidence = {"choices": [{"message": {"content": "text-only JSON"}}]}
        runner = self.runner([
            ProviderProtocolError("结构化响应必须且只能包含一次工具调用。", evidence),
            ProviderProtocolError("结构化响应必须且只能包含一次工具调用。", evidence),
            ProviderProtocolError("结构化响应必须且只能包含一次工具调用。", evidence),
        ])

        with self.assertRaisesRegex(ContractError, "failed after 3 attempts"):
            plan_bound(runner, "refresh", CONTEXT, "g2_constrained")

        trace = runner.store.list_recent(limit=1, include_trace=True)[0]["agent_trace"][0]["run"]
        self.assertEqual(trace["counts"]["contract_revisions"], 2)
        self.assertEqual(len(trace["contract_diagnostics"]), 3)

    def test_g2_provider_error_does_not_enter_response_contract_retry(self):
        runner = self.runner([ProviderError("HTTP 403 quota exhausted")])

        with self.assertRaisesRegex(ProviderError, "quota exhausted"):
            plan_bound(runner, "refresh", CONTEXT, "g2_constrained")

        trace = runner.store.list_recent(limit=1, include_trace=True)[0]["agent_trace"][0]["run"]
        self.assertEqual(trace["counts"]["contract_revisions"], 0)
        self.assertEqual(len(runner.client_factory.calls), 1)

    def test_response_contract_stops_after_the_bounded_revision_limit(self):
        invalid = {
            "task_contract": TASK_CONTRACT,
            "workflow_draft": dict(WORKFLOW, unexpected="model noise"),
        }
        runner = self.runner([{"task_contract": TASK_CONTRACT}, invalid, invalid, invalid])

        with self.assertRaisesRegex(ContractError, "failed after 3 attempts"):
            plan_bound(runner, "refresh", CONTEXT, "g2_constrained")

        row = runner.store.list_recent(limit=1, include_trace=True)[0]
        trace = row["agent_trace"][0]["run"]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(trace["counts"]["contract_revisions"], 2)
        self.assertEqual(len(trace["contract_diagnostics"]), 3)
        self.assertTrue(
            all(stage["status"] == "contract_rejected" for stage in trace["stages"][1:])
        )

    def test_g3_isolates_roles_and_requires_audit_pass(self):
        passed = {"decision": "pass", "claims": []}
        self.temp = tempfile.TemporaryDirectory()
        events = []
        primary = FakeModel(
            [
                {"task_contract": TASK_CONTRACT},
                {"workflow_draft": WORKFLOW},
            ]
        )
        auditor = FakeModel([{"audit_result": passed}])
        primary.events = auditor.events = events
        clients = iter((primary, auditor))

        def create_client(provider, model):
            client = next(clients)
            client.provider_id, client.model_id = provider, model
            return client

        runner = PlanningEngine(
            OperationCatalog(),
            RunStore(Path(self.temp.name) / "runs.sqlite"),
            create_client,
        )
        row = plan_bound(runner, "refresh", CONTEXT, "g3_audited")
        trace = row["agent_trace"][0]["run"]
        self.assertEqual(trace["counts"]["audit_revisions"], 0)
        self.assertEqual(trace["audits"][-1]["decision"], "pass")
        self.assertEqual(events, ["semantic", "planner", "auditor"])

    def test_public_g3_repairs_an_explicit_output_filename_before_planning(self):
        command = "把 roads 图层完整属性表导出为 roads.csv。"
        context = {
            "is_saved": True,
            "layers": [{
                "layer_ref": "layer:roads", "name": "roads",
                "geometry_type": "Polyline", "spatial_reference": "EPSG:3857",
                "fields": [{"name": "RID"}],
            }]
        }

        def contract_for(output_name):
            return {
                "input_entities": [{
                    "entity_id": "input:roads", "role": "source", "kind": "feature_layer",
                    "reference": "layer:roads", "evidence": command,
                }],
                "outputs": [{
                    "output_id": "output:roads_csv", "kind": "file", "name": output_name,
                    "format": "csv", "geometry": "not_applicable",
                    "required_fields": ["RID"], "spatial_reference": "not_applicable",
                    "destination": "default", "evidence": "roads.csv",
                }],
                "requirements": [{
                    "requirement_id": "requirement:roads_csv", "evidence": command,
                    "predicate": {
                        "kind": "artifact_export", "subject": "output:roads_csv",
                        "target": "input:roads", "action": "table_csv",
                        "selected_only": False, "output_format": "csv",
                    },
                }],
                "allowed_side_effects": ["writes_data"], "clarifications": [],
            }

        invalid_contract = contract_for("roads_csv")
        corrected_contract = contract_for("roads")

        class FilenameAwareModel(FakeModel):
            def __init__(self):
                super().__init__([])

            def chat_structured(self, messages, response_contract):
                self.calls.append(messages)
                role = messages[0]["content"].split("GeoPilot ")[1].split(" role")[0]
                payload = json.loads(messages[1]["content"])
                if role == "semantic":
                    repaired = "response_contract_repair" in payload
                    selected = corrected_contract if repaired else invalid_contract
                    return {"task_contract": task_contract_model_view(selected)}
                if role == "planner":
                    output_name = payload["task_contract"]["outputs"][0]["name"]
                    return model_wire_response({"workflow_draft": {
                        "action": "execute", "summary": command,
                        "steps": [{
                            "id": "export-roads-csv", "operation": "export.table_csv",
                            "arguments": {
                                "layer": "input:roads", "selected_only": False,
                                "output_name": output_name,
                            },
                            "reason": command,
                        }],
                    }}, messages)
                raise AssertionError("unexpected role: %s" % role)

        self.temp = tempfile.TemporaryDirectory()
        primary = FilenameAwareModel()
        auditor = FakeModel([{"audit_result": {"decision": "pass", "claims": []}}])
        clients = iter((primary, auditor))

        def create_client(provider, model):
            client = next(clients)
            client.provider_id, client.model_id = provider, model
            return client

        runner = PlanningEngine(
            OperationCatalog(), RunStore(Path(self.temp.name) / "runs.sqlite"), create_client,
        )

        row = plan_bound(runner, command, context, "g3_audited")

        trace = row["agent_trace"][0]["run"]
        self.assertEqual(1, trace["counts"]["contract_revisions"])
        self.assertEqual("roads", trace["plan_artifact"]["task_contract"]["outputs"][0]["name"])
        self.assertEqual("roads", row["workflow"]["steps"][0]["arguments"]["output_name"])

    def test_public_g2_does_not_conflate_csv_record_selection_with_file_cardinality(self):
        command = (
            "从 available_shelters 中筛选 CAPACITY 不少于 1000 的场所，生成 "
            "priority_shelters.shp，并导出 priority_shelters.csv 和 flood_response_map.png。"
        )
        context = {
            "is_saved": True,
            "layers": [{
                "layer_ref": "layer:available_shelters",
                "name": "available_shelters",
                "geometry_type": "Point",
                "spatial_reference": "EPSG:32650",
                "selected_count": 0,
                "fields": [
                    {"name": "SHLT_ID"}, {"name": "SHLT_NM"},
                    {"name": "CAPACITY"}, {"name": "STATUS"},
                    {"name": "DIST_ID"},
                ],
            }]
        }
        task = {
            "input_entities": [{
                "entity_id": "input:available_shelters", "role": "source",
                "kind": "feature_layer", "reference": "layer:available_shelters",
                "evidence": "available_shelters",
            }],
            "outputs": [
                {
                    "output_id": "output:priority_shelters_shp", "kind": "feature_class",
                    "name": "priority_shelters", "format": "shp", "geometry": "point",
                    "required_fields": ["SHLT_ID", "SHLT_NM", "CAPACITY", "STATUS", "DIST_ID"],
                    "spatial_reference": "EPSG:32650", "destination": "default",
                    "evidence": "priority_shelters.shp",
                },
                {
                    "output_id": "output:priority_shelters_csv", "kind": "file",
                    "name": "priority_shelters", "format": "csv",
                    "geometry": "not_applicable",
                    "required_fields": ["SHLT_ID", "SHLT_NM", "CAPACITY", "STATUS", "DIST_ID"],
                    "spatial_reference": "not_applicable", "destination": "default",
                    "evidence": "priority_shelters.csv",
                },
                {
                    "output_id": "output:flood_response_map_png", "kind": "file",
                    "name": "flood_response_map", "format": "png",
                    "geometry": "not_applicable",
                    "required_fields": [], "spatial_reference": "not_applicable",
                    "destination": "default", "evidence": "flood_response_map.png",
                },
            ],
            "requirements": [
                {
                    "requirement_id": "req:capacity", "evidence": "CAPACITY 不少于 1000",
                    "predicate": {
                        "kind": "attribute_filter", "subject": "input:available_shelters",
                        "target": "input:available_shelters", "selection_type": "new_selection",
                        "where": {"field": "CAPACITY", "op": "gte", "value": 1000},
                    },
                },
                {
                    "requirement_id": "req:shp", "evidence": "priority_shelters.shp",
                    "predicate": {
                        "kind": "artifact_export", "subject": "output:priority_shelters_shp",
                        "target": "input:available_shelters", "action": "export_selected_features",
                        "selected_only": True, "output_format": "shp",
                    },
                },
                {
                    "requirement_id": "req:csv", "evidence": "priority_shelters.csv",
                    "predicate": {
                        "kind": "artifact_export", "subject": "output:priority_shelters_csv",
                        "target": "output:priority_shelters_shp", "action": "table_csv",
                        "selected_only": False, "output_format": "csv",
                    },
                },
                {
                    "requirement_id": "req:png", "evidence": "flood_response_map.png",
                    "predicate": {
                        "kind": "artifact_export", "subject": "output:flood_response_map_png",
                        "action": "map_png", "output_format": "png",
                    },
                },
            ],
            "allowed_side_effects": ["changes_map", "writes_data"],
            "clarifications": [],
        }
        workflow = {
            "action": "execute", "summary": command,
            "steps": [
                {
                    "id": "select", "operation": "selection.select_by_attribute",
                    "arguments": {
                        "layer": "input:available_shelters", "selection_type": "NEW_SELECTION",
                        "where": {"field": "CAPACITY", "op": "gte", "value": 1000},
                    },
                    "reason": "筛选容量不少于 1000 的场所。",
                },
                {
                    "id": "export-shp", "operation": "selection.export_selected_features",
                    "arguments": {
                        "layer": "input:available_shelters", "output_name": "priority_shelters",
                        "output_format": "shp",
                    },
                    "reason": "生成 priority_shelters.shp。",
                },
                {
                    "id": "export-csv", "operation": "export.table_csv",
                    "arguments": {
                        "layer": "from_step:export-shp", "output_name": "priority_shelters",
                    },
                    "reason": "导出 priority_shelters.csv。",
                },
                {
                    "id": "export-png", "operation": "export.map_png",
                    "arguments": {"output_name": "flood_response_map"},
                    "reason": "导出 flood_response_map.png。",
                },
            ],
        }
        runner = self.runner(
            [{"task_contract": task}] + [{"workflow_draft": workflow}] * 4
        )

        row = plan_bound(runner, command, context, "g2_constrained")

        self.assertEqual("planned", row["status"])
        self.assertEqual(4, len(row["workflow"]["steps"]))

    def test_original_request_remains_visible_after_semantic_rewrite(self):
        command = "统计每条道路的事故数量"
        misleading = task_contract(command)
        misleading["outputs"][0]["name"] = "把道路属性附加到每个事故点"
        self.temp = tempfile.TemporaryDirectory()
        primary = FakeModel([
            {"task_contract": misleading},
            {"workflow_draft": WORKFLOW},
        ])
        auditor = FakeModel([{"audit_result": {"decision": "pass", "claims": []}}])
        clients = iter((primary, auditor))

        def create_client(provider, model):
            client = next(clients)
            client.provider_id, client.model_id = provider, model
            return client

        runner = PlanningEngine(
            OperationCatalog(),
            RunStore(Path(self.temp.name) / "runs.sqlite"),
            create_client,
        )
        plan_bound(runner, command, CONTEXT, "g3_audited")

        planner_payload = json.loads(primary.calls[1][1]["content"])
        auditor_payload = json.loads(auditor.calls[0][1]["content"])
        self.assertEqual(planner_payload["request"], command)
        self.assertEqual(auditor_payload["plan_artifact"]["request"], command)
        self.assertNotIn("task_contract", auditor_payload)

    def test_contract_fails_fast_and_store_can_cancel_export(self):
        workflow = copy.deepcopy(WORKFLOW)
        workflow["steps"][0]["operation"] = "layer.clear_layers"
        runner = self.runner([{"workflow_draft": workflow}])
        with self.assertRaises(ContractError):
            runner.plan("not-created", "refresh", CONTEXT, "bad")
        row = plan_bound(runner, "refresh", CONTEXT, "g0_direct")
        self.assertEqual(runner.store.cancel(row["id"])["status"], "cancelled")
        self.assertEqual(len(runner.store.export_runs()["runs"]), 1)

    def test_task_contract_requires_request_evidence(self):
        contract = task_contract("显示道路")
        self.assertEqual(contract, parse_task_contract(contract, "请显示道路"))
        contract["requirements"][0]["evidence"] = "不存在的证据"
        with self.assertRaises(TaskContractError):
            parse_task_contract(contract, "请显示道路")

    def test_g2_public_plan_persists_an_immutable_complete_artifact(self):
        command = "refresh"
        context = {"layers": []}
        task = task_contract(command)
        artifact = PlanArtifact(
            command, context, "execution", [{"id": "context.list_layers"}], task, WORKFLOW,
            {"ok": True, "prepared_workflow": WORKFLOW, "normalization_events": []},
            {"catalog_hash": "catalog"},
        )
        context["layers"].append("mutated")
        task["outputs"][0]["name"] = "mutated"
        document = PlanArtifact("refresh", {"layers": []}, "execution", [{"id": "context.list_layers"}],
            task_contract("refresh"), WORKFLOW,
            {"ok": True, "prepared_workflow": WORKFLOW, "normalization_events": []},
            {"catalog_hash": "catalog"}).as_dict()
        self.assertEqual([], document["context_snapshot"]["layers"])
        self.assertEqual(command, document["task_contract"]["outputs"][0]["name"])
        for field in ("context", "capability", "task_contract", "baseline_workflow", "baseline_verifier_report", "planning_policy"):
            value = document[field + ("_snapshot" if field in ("context", "capability") else "")]
            hash_field = "context_snapshot_hash" if field == "context" else field + "_hash"
            self.assertEqual(canonical_hash(value), document[hash_field])

        runner = self.runner([{"task_contract": TASK_CONTRACT}, {"workflow_draft": WORKFLOW}])
        row = plan_bound(runner, command, {"layers": []}, "g2_constrained")
        trace = row["agent_trace"][0]["run"]
        persisted = trace["plan_artifact"]
        self.assertEqual(persisted["artifact_hash"], trace["plan_artifact_hash"])
        self.assertEqual(canonical_hash(persisted["planning_policy"]), persisted["planning_policy_hash"])
        self.assertIn("hard_violations", persisted["baseline_verifier_report"])
        self.assertEqual("geopilot-execution-contract/v1", row["execution_contract"]["schema"])
        self.assertEqual(canonical_hash(row["workflow"]), row["execution_contract"]["workflow_hash"])
        self.assertEqual(trace["context_hash"], row["execution_contract"]["context_hash"])

    def test_plan_artifact_from_dict_rejects_nested_tamper_and_report_mismatch(self):
        artifact = PlanArtifact("refresh", {"layers": []}, "execution", [{"id": "context.list_layers"}],
            task_contract("refresh"), WORKFLOW,
            {"ok": True, "prepared_workflow": WORKFLOW, "normalization_events": []},
            {"catalog_hash": "catalog"})
        document = artifact.as_dict()
        self.assertEqual(artifact.hash, PlanArtifact.from_dict(document).hash)
        document["task_contract"]["outputs"][0]["name"] = "tampered"
        with self.assertRaises(PlanArtifactError):
            PlanArtifact.from_dict(document)

    def test_plan_with_artifact_replays_g2_without_models_and_g3_audits_first(self):
        baseline = self.runner([{"task_contract": TASK_CONTRACT}, {"workflow_draft": WORKFLOW}])
        source = plan_bound(baseline, "refresh", CONTEXT, "g2_constrained")
        artifact = source["agent_trace"][0]["run"]["plan_artifact"]
        replay = PlanningEngine(OperationCatalog(), baseline.store, lambda *_args: (_ for _ in ()).throw(AssertionError("G2 called a model")))
        run = baseline.store.create_run("refresh", "g2_constrained")
        g2 = replay.plan_with_artifact(run["id"], "refresh", CONTEXT, "g2_constrained", artifact)
        self.assertEqual("planned", g2["status"])
        self.assertEqual(artifact["artifact_hash"], g2["agent_trace"][0]["run"]["plan_artifact_hash"])
        events, planner = [], FakeModel([])
        auditor = FakeModel([{"audit_result": {"decision": "pass", "claims": []}}])
        planner.events = auditor.events = events
        clients = iter((planner, auditor))
        replay = PlanningEngine(OperationCatalog(), baseline.store, lambda provider, model: next(clients))
        run = baseline.store.create_run("refresh", "g3_audited")
        g3 = replay.plan_with_artifact(run["id"], "refresh", CONTEXT, "g3_audited", artifact, provider="p", model="m")
        self.assertEqual("planned", g3["status"])
        self.assertEqual(["auditor"], events)

    def test_plan_with_artifact_rejects_a_self_hashed_stale_capability_subset(self):
        baseline = self.runner([{"task_contract": TASK_CONTRACT}, {"workflow_draft": WORKFLOW}])
        source = plan_bound(baseline, "refresh", CONTEXT, "g2_constrained")
        document = copy.deepcopy(source["agent_trace"][0]["run"]["plan_artifact"])
        document["capability_snapshot"][0]["summary"] = "stale capability"
        document["capability_hash"] = canonical_hash(document["capability_snapshot"])
        unsigned = {key: value for key, value in document.items() if key != "artifact_hash"}
        document["artifact_hash"] = canonical_hash(unsigned)
        PlanArtifact.from_dict(document)
        run = baseline.store.create_run("refresh", "g2_constrained")
        replay = PlanningEngine(OperationCatalog(), baseline.store, lambda *_args: None)

        with self.assertRaisesRegex(ContractError, "authoritative catalog"):
            replay.plan_with_artifact(
                run["id"], "refresh", CONTEXT, "g2_constrained", document,
            )

    def test_plan_with_artifact_reports_context_mismatch_paths_without_values(self):
        baseline = self.runner([{"task_contract": TASK_CONTRACT}, {"workflow_draft": WORKFLOW}])
        context = {"layers": [{"name": "roads", "visible": True}]}
        source = plan_bound(baseline, "refresh", context, "g2_constrained")
        artifact = source["agent_trace"][0]["run"]["plan_artifact"]
        replay = PlanningEngine(OperationCatalog(), baseline.store, lambda *_args: None)
        run = baseline.store.create_run("refresh", "g2_constrained")
        changed = {"layers": [{"name": "roads", "visible": False}]}

        with self.assertRaisesRegex(
            ContractError,
            r"context_snapshot\.layers\[0\]\.visible",
        ):
            replay.plan_with_artifact(run["id"], "refresh", changed, "g2_constrained", artifact)
        document = PlanArtifact("refresh", {"layers": []}, "execution", [{"id": "context.list_layers"}],
            task_contract("refresh"), WORKFLOW,
            {"ok": True, "prepared_workflow": WORKFLOW, "normalization_events": []},
            {"catalog_hash": "catalog"}).as_dict()
        document["baseline_verifier_report"]["prepared_workflow"] = {"action": "execute", "summary": "other", "steps": []}
        document["baseline_verifier_report_hash"] = canonical_hash(document["baseline_verifier_report"])
        unsigned = {key: value for key, value in document.items() if key != "artifact_hash"}
        document["artifact_hash"] = canonical_hash(unsigned)
        with self.assertRaises(PlanArtifactError):
            PlanArtifact.from_dict(document)

    def test_g2_unresolved_verifier_obligation_is_planned_for_g3_review(self):
        command = "按 CLASS 融合道路"
        contract = task_contract(command)
        contract["outputs"][0].update({"kind": "feature_class", "name": "roads_by_class", "format": "gdb", "geometry": "polyline", "required_fields": ["CLASS"], "spatial_reference": "EPSG:3857", "destination": "default"})
        workflow = {"action": "execute", "summary": command, "steps": [{"id": "dissolve", "operation": "analysis.dissolve", "arguments": {"input_layer": "roads", "output_name": "roads_by_class", "dissolve_fields": ["CLASS"]}, "reason": command}]}
        context = {"is_saved": True, "layers": [{"layer_ref": "layer:roads", "name": "roads", "geometry_type": "Polyline", "fields": [{"name": "CLASS"}]}]}
        runner = self.runner([{"task_contract": contract}, {"workflow_draft": workflow}])
        row = plan_bound(runner, command, context, "g2_constrained")
        self.assertEqual("planned", row["status"])


def plan_bound(runner, command, context, mode):
    run = runner.store.create_run(command, mode)
    runner.store.bind_context(
        run["id"],
        {
            "context": context,
            "context_hash": context_hash(context),
            "bridge": {
                "bridge_pid": 1,
                "bridge_port": 8766,
                "arcmap_pid": 10,
                "hwnd": 1,
            },
            "captured_at": 1.0,
        },
    )
    return runner.plan(
        run["id"],
        command,
        context,
        mode,
        provider="test-provider",
        model="test-model",
    )
