import unittest

import jsonschema

from gateway_py3.audit_contract import AUDIT_CONTRACT
from gateway_py3.task_contract import (
    TASK_CONTRACT,
    TaskContractError,
    bind_model_task_contract,
    parse_task_contract,
    task_contract_model_view,
)
from gateway_py3.planning_engine import (
    ContractError,
    _prompt,
    bind_model_workflow_response,
    structured_output_contract,
    workflow_draft_model_view,
)


class ExperimentContractTests(unittest.TestCase):
    def test_auditor_uses_the_only_proof_bound_contract(self):
        self.assertEqual("submit_audit_result", structured_output_contract("audit").name)
        self.assertIn("proof_id", _prompt("auditor"))
        self.assertIn("claims", str(AUDIT_CONTRACT.tool_contract.schema))

    def test_workflow_tool_contract_uses_a_flat_provider_wire_shape(self):
        from gateway_py3.structured_contracts import workflow_contract_for_capabilities

        parameters = {
            "type": "object",
            "properties": {
                "layer": {"type": "string"},
                "selection_type": {"enum": ["NEW_SELECTION"]},
            },
            "required": ["layer", "selection_type"],
            "additionalProperties": False,
        }
        contract = workflow_contract_for_capabilities([{
            "id": "selection.select_by_attribute",
            "parameters_schema": parameters,
        }])
        valid = {"workflow_draft": {
            "action": "execute", "summary": "select", "steps": [{
                "id": "s1", "operation": "selection.select_by_attribute",
                "arguments_json": '{"layer":"roads","selection_type":"NEW_SELECTION"}',
                "reason": "select roads",
            }],
        }}
        jsonschema.validate(valid, contract.schema)
        step_schema = contract.schema["properties"]["workflow_draft"]["properties"]["steps"]["items"]
        self.assertNotIn("oneOf", step_schema)
        self.assertEqual(
            {"id", "operation", "arguments_json", "reason"},
            set(step_schema["properties"]),
        )
        self.assertEqual(
            ["selection.select_by_attribute"],
            step_schema["properties"]["operation"]["enum"],
        )
        invalid = {"workflow_draft": dict(valid["workflow_draft"], steps=[{
            "id": "s1", "operation": "selection.select_by_attribute",
            "arguments": {"layer": "roads", "selection_type": "NEW_SELECTION"},
            "reason": "select roads",
        }])}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(invalid, contract.schema)
        self.assertEqual("submit_workflow_v3", contract.name)

    def test_task_contract_uses_a_flat_requirement_provider_wire_shape(self):
        valid = {"task_contract": {
            "input_entities": [],
            "outputs": [],
            "requirements": [{
                "requirement_id": "req:preserve",
                "predicate_json": '{"kind":"source_preserved","subject":"input:roads"}',
            }],
            "allowed_side_effects": ["read_only"],
            "clarifications": [],
        }}

        jsonschema.validate(valid, TASK_CONTRACT.schema)
        requirement_schema = (
            TASK_CONTRACT.schema["properties"]["task_contract"]
            ["properties"]["requirements"]["items"]
        )
        self.assertNotIn("oneOf", requirement_schema)
        self.assertEqual(
            {"requirement_id", "predicate_json"},
            set(requirement_schema["properties"]),
        )
        self.assertEqual("submit_task_contract_v10", TASK_CONTRACT.name)

    def test_semantic_prompt_carries_the_closed_predicate_catalog_outside_the_tool_schema(self):
        prompt = _prompt("semantic")
        self.assertIn("Closed task_predicate_catalog:", prompt)
        self.assertIn('"kind":"attribute_filter"', prompt)
        self.assertIn('"kind":"spatial_filter"', prompt)
        self.assertIn('"kind":"artifact_export"', prompt)
        self.assertNotIn("oneOf", prompt)
        self.assertLess(len(prompt.encode("utf-8")), 12_000)

    def test_task_contract_wire_binds_to_one_canonical_internal_shape(self):
        request = "preserve the current map"
        wire = {
            "input_entities": [],
            "outputs": [],
            "requirements": [{
                "requirement_id": "req:map",
                "predicate_json": '{"action":"preserve","kind":"map_change","subject":"output:map"}',
            }],
            "allowed_side_effects": ["read_only"],
            "clarifications": [],
        }

        bound = bind_model_task_contract(wire, request)

        self.assertEqual(
            {"action": "preserve", "kind": "map_change", "subject": "output:map"},
            bound["requirements"][0]["predicate"],
        )
        self.assertEqual(request, bound["requirements"][0]["evidence"])
        self.assertEqual(wire, task_contract_model_view(bound))

    def test_task_contract_wire_rejects_removed_flattened_predicate_shape(self):
        legacy = {
            "input_entities": [],
            "outputs": [],
            "requirements": [{
                "requirement_id": "req:map",
                "kind": "map_change",
                "subject": "output:map",
                "action": "preserve",
            }],
            "allowed_side_effects": ["read_only"],
            "clarifications": [],
        }
        with self.assertRaisesRegex(TaskContractError, "invalid fields"):
            bind_model_task_contract(legacy, "preserve the current map")

    def test_task_contract_wire_rejects_malformed_or_non_object_predicate_json(self):
        base = {
            "input_entities": [],
            "outputs": [],
            "allowed_side_effects": ["read_only"],
            "clarifications": [],
        }
        for predicate_json in ("not-json", "[]"):
            with self.subTest(predicate_json=predicate_json):
                wire = dict(base, requirements=[{
                    "requirement_id": "req:map",
                    "predicate_json": predicate_json,
                }])
                with self.assertRaisesRegex(TaskContractError, "predicate_json"):
                    bind_model_task_contract(wire, "preserve the current map")

    def test_task_contract_reports_all_independent_candidate_violations_together(self):
        request = "导出 priority_shelters.shp、priority_shelters.csv 和 flood_response_map.png"
        value = {
            "input_entities": [{
                "entity_id": "input:available_shelters", "role": "source",
                "kind": "feature_layer", "reference": "layer:available_shelters",
                "evidence": request,
            }],
            "outputs": [{
                "output_id": "output:priority_shelters", "kind": "feature_class",
                "name": "priority_shelters", "format": "shp", "geometry": "point",
                "required_fields": [], "spatial_reference": "EPSG:32650",
                "destination": "default", "evidence": "priority_shelters.shp",
            }, {
                "output_id": "output:priority_shelters_csv", "kind": "file",
                "name": "priority_shelters", "format": "csv", "geometry": "not_applicable",
                "required_fields": [], "spatial_reference": "not_applicable",
                "destination": "default", "evidence": "priority_shelters.csv",
            }, {
                "output_id": "output:flood_response_map", "kind": "file",
                "name": "flood_response_map", "format": "png", "geometry": "not_applicable",
                "required_fields": [""], "spatial_reference": "not_applicable",
                "destination": "default", "evidence": "flood_response_map.png",
            }],
            "requirements": [{
                "requirement_id": "export_map", "evidence": request,
                "predicate": {
                    "kind": "artifact_export", "subject": "input:available_shelters",
                    "action": "map_png",
                },
            }],
            "allowed_side_effects": ["writes_data"],
            "clarifications": [],
        }

        with self.assertRaises(TaskContractError) as raised:
            parse_task_contract(value, request)

        message = str(raised.exception)
        self.assertIn("output required_fields is invalid", message)
        self.assertIn("subject must be the declared exported output entity", message)

    def test_workflow_wire_binds_to_one_canonical_internal_shape(self):
        capabilities = [{
            "id": "selection.select_by_attribute",
            "parameters_schema": {
                "type": "object",
                "properties": {"layer": {"type": "string"}},
                "required": ["layer"],
                "additionalProperties": False,
            },
        }]
        wire = {"workflow_draft": {
            "action": "execute", "summary": "select", "steps": [{
                "id": "s1", "operation": "selection.select_by_attribute",
                "arguments_json": '{"layer":"roads"}', "reason": "select roads",
            }],
        }}

        bound = bind_model_workflow_response(wire, capabilities)

        self.assertEqual({"layer": "roads"}, bound["workflow_draft"]["steps"][0]["arguments"])
        self.assertEqual(wire["workflow_draft"], workflow_draft_model_view(bound["workflow_draft"]))

    def test_workflow_wire_rejects_removed_nested_arguments_shape(self):
        capabilities = [{"id": "context.list_layers", "parameters_schema": {"type": "object"}}]
        legacy = {"workflow_draft": {
            "action": "execute", "summary": "refresh", "steps": [{
                "id": "s1", "operation": "context.list_layers",
                "arguments": {}, "reason": "refresh",
            }],
        }}
        with self.assertRaisesRegex(ContractError, "arguments_json"):
            bind_model_workflow_response(legacy, capabilities)

    def test_workflow_wire_rejects_operation_outside_selected_closure(self):
        wire = {"workflow_draft": {
            "action": "execute", "summary": "refresh", "steps": [{
                "id": "s1", "operation": "layer.clear_layers",
                "arguments_json": "{}", "reason": "clear",
            }],
        }}
        with self.assertRaisesRegex(ContractError, "outside the selected capability closure"):
            bind_model_workflow_response(wire, [{
                "id": "context.list_layers", "parameters_schema": {"type": "object"},
            }])


if __name__ == "__main__": unittest.main()
