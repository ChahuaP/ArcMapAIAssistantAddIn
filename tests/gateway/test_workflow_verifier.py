import copy
import tempfile
import unittest

import jsonschema

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.dominance_gate import DominanceGate
from gateway_py3.task_contract import (
    TASK_CONTRACT,
    TaskContractError,
    parse_task_contract,
    task_contract_model_view,
)
from gateway_py3.workflow_verifier import WorkflowVerifier
from tests.gateway.planner_test_utils import task_contract


CONTEXT = {"is_saved": True, "layers": [
    {"layer_ref": "layer:roads", "name": "roads", "geometry_type": "Polyline", "spatial_reference": "EPSG:3857", "fields": [{"name": "RID"}, {"name": "CLASS"}]},
    {"layer_ref": "layer:zones", "name": "zones", "geometry_type": "Polygon", "spatial_reference": "EPSG:3857", "fields": [{"name": "ZID"}]},
]}


def model_envelope(value):
    return {"task_contract": task_contract_model_view(value["task_contract"])}


class TaskContractSchemaTests(unittest.TestCase):
    def test_task_contract_closes_output_destination_to_request_evidence(self):
        command = r"导出 roads.shp 到 D:\results"
        value = task_contract(command)
        value["outputs"][0].update({
            "kind": "feature_class", "name": "roads", "format": "shp",
            "geometry": "polyline", "spatial_reference": "EPSG:3857",
            "destination": r"D:\results", "evidence": "roads.shp",
        })

        parsed = parse_task_contract(value, command)

        self.assertEqual(r"D:\results", parsed["outputs"][0]["destination"])
        for invalid in (r"results", r"\results", r"D:\other"):
            candidate = copy.deepcopy(value)
            candidate["outputs"][0]["destination"] = invalid
            with self.assertRaisesRegex(TaskContractError, "destination"):
                parse_task_contract(candidate, command)

    def test_task_contract_distinguishes_map_state_from_persisted_destinations(self):
        map_task = task_contract("刷新地图")
        self.assertEqual(
            "not_applicable",
            parse_task_contract(map_task, "刷新地图")["outputs"][0]["destination"],
        )
        invalid_map = copy.deepcopy(map_task)
        invalid_map["outputs"][0]["destination"] = "default"
        with self.assertRaisesRegex(TaskContractError, "map_state"):
            parse_task_contract(invalid_map, "刷新地图")

        persisted = copy.deepcopy(map_task)
        persisted["outputs"][0].update({
            "kind": "file", "name": "map", "format": "png",
            "destination": "not_applicable", "evidence": "地图",
        })
        with self.assertRaisesRegex(TaskContractError, "persisted output"):
            parse_task_contract(persisted, "刷新地图")

    def test_unsaved_context_requires_a_closed_output_location_or_clarification(self):
        command = "生成 roads_buffer.shp"
        value = task_contract(command)
        value["outputs"][0].update({
            "kind": "feature_class", "name": "roads_buffer", "format": "shp",
            "geometry": "polygon", "spatial_reference": "EPSG:3857",
            "destination": "default", "evidence": "roads_buffer.shp",
        })
        context = {"is_saved": False, "layers": []}

        with self.assertRaisesRegex(TaskContractError, "output-location clarification"):
            parse_task_contract(value, command, context)

        value["clarifications"] = [{
            "clarification_id": "output-location", "question": "请指定输出文件夹。",
            "evidence": command,
        }]
        parsed = parse_task_contract(value, command, context)
        self.assertEqual("output-location", parsed["clarifications"][0]["clarification_id"])

    def test_workflow_argument_validator_rejects_invalid_union_member(self):
        from gateway_py3.validators import ValidationError, prepare_workflow

        workflow = {
            "action": "execute", "summary": "筛选道路", "steps": [{
                "id": "spatial", "operation": "selection.select_by_location",
                "arguments": {
                    "target_layer": "roads", "select_layer": "zones",
                    "overlap_type": "WITHIN", "search_distance": 0,
                    "selection_type": "NEW_SELECTION",
                },
                "reason": "筛选道路",
            }],
        }
        with self.assertRaisesRegex(ValidationError, "search_distance must be one of types: string, null"):
            prepare_workflow(workflow, OperationCatalog(), CONTEXT)

    def test_feature_output_format_owns_exactly_one_matching_location_parameter(self):
        from gateway_py3.validators import ValidationError, prepare_workflow

        with tempfile.TemporaryDirectory() as folder:
            workflow = {
                "action": "execute", "summary": "buffer", "steps": [{
                    "id": "buffer", "operation": "analysis.buffer",
                    "arguments": {
                        "input_layer": "roads", "distance": "10 meters",
                        "output_name": "roads_buffer", "output_folder": folder,
                    },
                    "reason": "buffer",
                }],
            }

            prepared = prepare_workflow(workflow, OperationCatalog(), CONTEXT)
            self.assertEqual("shp", prepared["steps"][0]["arguments"]["output_format"])

            gdb_in_folder = copy.deepcopy(workflow)
            gdb_in_folder["steps"][0]["arguments"]["output_format"] = "gdb"
            with self.assertRaisesRegex(ValidationError, "gdb.*output_workspace"):
                prepare_workflow(gdb_in_folder, OperationCatalog(), CONTEXT)

            shp_in_workspace = copy.deepcopy(workflow)
            arguments = shp_in_workspace["steps"][0]["arguments"]
            arguments.pop("output_folder")
            arguments.update({"output_format": "shp", "output_workspace": folder})
            with self.assertRaisesRegex(ValidationError, "shp.*output_folder"):
                prepare_workflow(shp_in_workspace, OperationCatalog(), CONTEXT)

    def test_task_contract_rejects_an_input_output_id_collision(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract

        value = task_contract("refresh")
        value["input_entities"] = [{
            "entity_id": "o1", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": "refresh",
        }]
        with self.assertRaisesRegex(TaskContractError, "input and output ids"):
            parse_task_contract(value, "refresh", CONTEXT)

    def test_request_alignment_identity_binds_evidence_and_predicate(self):
        contract = task_contract("统计道路，源数据只读")
        first = WorkflowVerifier._request_alignment_obligations(contract)[0]
        revised = copy.deepcopy(contract)
        revised["requirements"][0]["evidence"] = "源数据只读"
        second = WorkflowVerifier._request_alignment_obligations(revised)[0]

        self.assertNotEqual(first["obligation_id"], second["obligation_id"])

    def test_context_specialized_schema_closes_input_references(self):
        from gateway_py3.task_contract import task_contract_for_context
        contract = task_contract_for_context(CONTEXT, "筛选道路")
        schema = contract.schema
        reference = schema["properties"]["task_contract"]["properties"]["input_entities"]["items"]["properties"]["reference"]
        self.assertEqual(["layer:roads", "layer:zones"], reference["enum"])

        value = {"task_contract": task_contract("筛选道路")}
        value["task_contract"]["input_entities"] = [{
            "entity_id": "input:roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": "筛选道路",
        }]
        value["task_contract"]["outputs"][0]["output_id"] = "output:selection"
        value["task_contract"]["requirements"][0] = {
            "requirement_id": "r1", "evidence": "筛选道路",
            "predicate": {
                "kind": "attribute_filter", "subject": "input:roads",
                "where": {"field": "CLASS", "op": "eq", "value": "A"},
                "selection_type": "select_subset",
            },
        }
        jsonschema.validate(model_envelope(value), schema)
        value["task_contract"]["requirements"][0]["predicate"]["selection_type"] = "new_selection"
        jsonschema.validate(model_envelope(value), schema)
        unexpected_evidence = model_envelope(value)
        unexpected_evidence["task_contract"]["input_entities"][0]["evidence"] = "道路"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(unexpected_evidence, schema)
        value["task_contract"]["outputs"][0]["output_id"] = "input:roads"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(model_envelope(value), schema)

    def test_model_input_kind_is_bound_by_the_server_from_live_context(self):
        from gateway_py3.task_contract import bind_model_task_contract, task_contract_for_context

        command = "筛选道路"
        value = task_contract(command)
        value["input_entities"] = [{
            "entity_id": "input:roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": command,
        }]
        value["outputs"][0]["output_id"] = "output:selection"
        value["requirements"][0]["predicate"]["subject"] = "output:selection"
        model_value = task_contract_model_view(value)
        input_item = model_value["input_entities"][0]
        self.assertNotIn("kind", input_item)

        schema = task_contract_for_context(CONTEXT, command).schema
        jsonschema.validate({"task_contract": model_value}, schema)
        unexpected_kind = copy.deepcopy(model_value)
        unexpected_kind["input_entities"][0]["kind"] = "map_state"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"task_contract": unexpected_kind}, schema)

        bound = bind_model_task_contract(model_value, command, CONTEXT)
        self.assertEqual("feature_layer", bound["input_entities"][0]["kind"])

    def test_server_rejects_duplicate_live_context_references(self):
        from gateway_py3.task_contract import TaskContractError, bind_model_task_contract

        command = "筛选道路"
        value = task_contract(command)
        value["input_entities"] = [{
            "entity_id": "input:roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": command,
        }]
        model_value = task_contract_model_view(value)
        duplicate = copy.deepcopy(model_value["input_entities"][0])
        duplicate["entity_id"] = "input:roads_duplicate"
        model_value["input_entities"].append(duplicate)

        with self.assertRaisesRegex(TaskContractError, "references must be unique"):
            bind_model_task_contract(model_value, command, CONTEXT)

    def test_task_contract_allows_subset_after_an_earlier_selection_on_the_same_entity(self):
        from gateway_py3.task_contract import parse_task_contract

        command = "先按分区筛选道路，再保留A类道路"
        value = {
            "input_entities": [
                {"entity_id": "roads", "role": "target", "kind": "feature_layer", "reference": "layer:roads", "evidence": "道路"},
                {"entity_id": "zones", "role": "selector", "kind": "feature_layer", "reference": "layer:zones", "evidence": "分区"},
            ],
            "outputs": [],
            "requirements": [
                {
                    "requirement_id": "spatial", "evidence": "按分区筛选道路",
                    "predicate": {"kind": "spatial_filter", "subject": "roads", "target": "roads", "selector": "zones", "overlap_type": "intersect", "selection_type": "new_selection"},
                },
                {
                    "requirement_id": "attribute", "evidence": "保留A类道路",
                    "predicate": {"kind": "attribute_filter", "subject": "roads", "target": "roads", "where": {"field": "CLASS", "op": "eq", "value": "A"}, "selection_type": "select_subset"},
                },
            ],
            "allowed_side_effects": ["changes_map"],
            "clarifications": [],
        }

        parsed = parse_task_contract(value, command, CONTEXT)

        self.assertEqual("select_subset", parsed["requirements"][1]["predicate"]["selection_type"])

    def test_task_contract_rejects_applying_distance_twice_to_a_buffer_selector(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract

        command = "以受灾社区为中心建立2公里服务区，在服务区内筛选避难所"
        value = {
            "input_entities": [
                {"entity_id": "communities", "role": "source", "kind": "feature_layer", "reference": "layer:communities", "evidence": command},
                {"entity_id": "shelters", "role": "target", "kind": "feature_layer", "reference": "layer:shelters", "evidence": command},
            ],
            "outputs": [{
                "output_id": "service_area", "kind": "feature_class", "name": "service_area",
                "format": "shp", "geometry": "polygon",
                "required_fields": [], "spatial_reference": "EPSG:32650",
                "destination": "default",
                "evidence": "建立2公里服务区",
            }],
            "requirements": [
                {"requirement_id": "buffer", "evidence": command, "predicate": {"kind": "buffer", "subject": "service_area", "source": "communities", "distance": {"value": 2000, "unit": "meters"}}},
                {"requirement_id": "select", "evidence": command, "predicate": {"kind": "spatial_filter", "subject": "shelters", "target": "shelters", "selector": "service_area", "overlap_type": "within_a_distance", "search_distance": {"value": 2000, "unit": "meters"}, "selection_type": "new_selection"}},
            ],
            "allowed_side_effects": ["changes_map", "writes_data"],
            "clarifications": [],
        }

        with self.assertRaisesRegex(TaskContractError, "buffer output selector"):
            parse_task_contract(value, command)
        value["requirements"][1]["predicate"]["overlap_type"] = "intersect"
        del value["requirements"][1]["predicate"]["search_distance"]
        parsed = parse_task_contract(value, command)
        self.assertEqual("intersect", parsed["requirements"][1]["predicate"]["overlap_type"])

    def test_task_contract_explains_that_undeclared_intermediate_buffers_belong_to_workflow(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract

        command = "保留距离社区1500米内的候选地块"
        value = task_contract(command)
        value["input_entities"] = [{
            "entity_id": "communities", "role": "selector", "kind": "feature_layer",
            "reference": "layer:communities", "evidence": command,
        }]
        value["requirements"] = [{
            "requirement_id": "buffer", "evidence": command,
            "predicate": {"kind": "buffer", "subject": "temporary_buffer", "source": "communities", "distance": {"value": 1500, "unit": "meters"}},
        }]

        with self.assertRaisesRegex(TaskContractError, "intermediate buffer belongs to Workflow"):
            parse_task_contract(value, command)

    def test_task_parser_exposes_native_condition_and_closed_selection_types(self):
        value = {"task_contract": task_contract("refresh")}
        predicate = value["task_contract"]["requirements"][0]["predicate"]
        predicate.clear()
        predicate.update({
            "kind": "attribute_filter",
            "subject": "o1",
            "where": {"field": "CLASS", "op": "eq", "value": "A"},
            "selection_type": "new_selection",
        })
        jsonschema.validate(model_envelope(value), TASK_CONTRACT.schema)
        stringified = copy.deepcopy(value)
        stringified["task_contract"]["requirements"][0]["predicate"]["where"] = '{"field":"CLASS","op":"eq","value":"A"}'
        with self.assertRaises(TaskContractError):
            parse_task_contract(stringified["task_contract"], "refresh")
        invalid_selection = copy.deepcopy(value)
        invalid_selection["task_contract"]["requirements"][0]["predicate"]["selection_type"] = "replace"
        with self.assertRaises(TaskContractError):
            parse_task_contract(invalid_selection["task_contract"], "refresh")

    def test_task_parser_rejects_invalid_condition_cardinality(self):
        value = {"task_contract": task_contract("refresh")}
        predicate = value["task_contract"]["requirements"][0]["predicate"]
        predicate.clear()
        predicate.update({
            "kind": "attribute_filter",
            "subject": "o1",
            "where": {"field": "CLASS", "op": "in", "values": []},
            "selection_type": "new_selection",
        })
        with self.assertRaises(TaskContractError):
            parse_task_contract(value["task_contract"], "refresh")

        predicate["where"] = {
            "op": "and",
            "conditions": [
                {"field": "CLASS", "op": "eq", "value": "A"},
                {"field": "RID", "op": "between", "values": [1, 10]},
            ],
        }
        jsonschema.validate(model_envelope(value), TASK_CONTRACT.schema)

    def test_task_parser_closes_spatial_search_distance_shape(self):
        value = {"task_contract": task_contract("refresh")}
        value["task_contract"]["input_entities"] = [
            {
                "entity_id": "roads", "role": "target", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": "refresh",
            },
            {
                "entity_id": "schools", "role": "selector", "kind": "feature_layer",
                "reference": "layer:schools", "evidence": "refresh",
            },
        ]
        value["task_contract"]["requirements"][0]["predicate"] = {
            "kind": "spatial_filter", "subject": "roads", "target": "roads",
            "selector": "schools", "overlap_type": "within_a_distance",
            "search_distance": {"value": 500, "unit": "meters"},
            "selection_type": "new_selection",
        }
        jsonschema.validate(model_envelope(value), TASK_CONTRACT.schema)

        no_distance = copy.deepcopy(value)
        predicate = no_distance["task_contract"]["requirements"][0]["predicate"]
        predicate["overlap_type"] = "within"
        del predicate["search_distance"]
        jsonschema.validate(model_envelope(no_distance), TASK_CONTRACT.schema)

        for invalid_distance in (None, "", {}):
            unexpected_distance = copy.deepcopy(no_distance)
            unexpected_distance["task_contract"]["requirements"][0]["predicate"]["search_distance"] = invalid_distance
            with self.assertRaises(TaskContractError):
                parse_task_contract(unexpected_distance["task_contract"], "refresh")

        missing_distance = copy.deepcopy(value)
        del missing_distance["task_contract"]["requirements"][0]["predicate"]["search_distance"]
        with self.assertRaises(TaskContractError):
            parse_task_contract(missing_distance["task_contract"], "refresh")

        malformed = copy.deepcopy(value)
        malformed["task_contract"]["requirements"][0]["predicate"]["search_distance"] = {
            "distance": 500, "units": "Meters",
        }
        with self.assertRaises(TaskContractError):
            parse_task_contract(malformed["task_contract"], "refresh")

    def test_task_parser_closes_artifact_export_roles_by_action(self):
        value = {"task_contract": task_contract("导出道路表和当前地图")}
        value["task_contract"]["outputs"] = [
            {
                "output_id": "output:roads_csv", "kind": "file", "name": "roads",
                "format": "csv", "geometry": "not_applicable", "required_fields": [],
                "spatial_reference": "not_applicable", "destination": "default",
                "evidence": "道路表",
            },
            {
                "output_id": "output:map_png", "kind": "file", "name": "map",
                "format": "png", "geometry": "not_applicable", "required_fields": [],
                "spatial_reference": "not_applicable", "destination": "default",
                "evidence": "当前地图",
            },
        ]
        value["task_contract"]["input_entities"] = [{
            "entity_id": "input:roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": "道路表",
        }]
        value["task_contract"]["requirements"] = [
            {
                "requirement_id": "csv",
                "predicate": {
                    "kind": "artifact_export", "subject": "output:roads_csv",
                    "target": "input:roads", "action": "table_csv",
                    "selected_only": False, "output_format": "csv",
                },
                "evidence": "导出道路表和当前地图",
            },
            {
                "requirement_id": "map",
                "predicate": {
                    "kind": "artifact_export", "subject": "output:map_png",
                    "action": "map_png", "output_format": "png",
                },
                "evidence": "导出道路表和当前地图",
            },
        ]
        jsonschema.validate(model_envelope(value), TASK_CONTRACT.schema)

        missing_source = copy.deepcopy(value)
        predicate = missing_source["task_contract"]["requirements"][0]["predicate"]
        del predicate["target"]
        del predicate["selected_only"]
        with self.assertRaises(TaskContractError):
            parse_task_contract(missing_source["task_contract"], "导出道路表和当前地图")

        invented_map_source = copy.deepcopy(value)
        invented_map_source["task_contract"]["requirements"][1]["predicate"]["target"] = "input:current_map"
        with self.assertRaises(TaskContractError):
            parse_task_contract(invented_map_source["task_contract"], "导出道路表和当前地图")

    def test_arcmap_selection_parameter_is_canonicalized_to_task_selection_type(self):
        from gateway_py3.semantic_domain import canonicalize_semantic_fact
        value = canonicalize_semantic_fact({
            "kind": "attribute_filter", "subject": "from_step:s1", "target": "layer:roads",
            "where": {"field": "CLASS", "op": "eq", "value": "A"},
            "selection_type": "SUBSET_SELECTION", "step_id": "s1",
        })
        self.assertEqual("select_subset", value["selection_type"])

    def test_schema_rejects_extra_nested_field_and_bad_enum(self):
        value = {"task_contract": task_contract("refresh")}
        jsonschema.validate(model_envelope(value), TASK_CONTRACT.schema)
        extra = copy.deepcopy(value)
        extra["task_contract"]["outputs"][0]["extra"] = True
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(model_envelope(extra), TASK_CONTRACT.schema)
        invalid = copy.deepcopy(value)
        invalid["task_contract"]["outputs"][0]["geometry"] = "circle"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(model_envelope(invalid), TASK_CONTRACT.schema)

    def test_task_parser_rejects_field_name_in_an_entity_slot(self):
        value = {"task_contract": task_contract("统计每条道路的事故数量")}
        value["task_contract"]["requirements"][0]["predicate"] = {
            "kind": "inspect", "subject": "o1", "target": "Join_Count",
        }
        with self.assertRaises(TaskContractError):
            parse_task_contract(value["task_contract"], "统计每条道路的事故数量")

    def test_task_contract_rejects_output_preservation_and_duplicate_requirements(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract
        command = "生成 roads_out，源数据只读"
        value = task_contract(command)
        value["input_entities"] = [{
            "entity_id": "roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": "源数据只读",
        }]
        value["outputs"][0].update({
            "output_id": "roads_out", "name": "roads_out", "kind": "feature_class",
            "format": "gdb", "geometry": "polyline",
            "spatial_reference": "EPSG:3857", "destination": "default",
        })
        value["requirements"] = [{
            "requirement_id": "bad", "evidence": "源数据只读",
            "predicate": {"kind": "source_preserved", "subject": "roads_out"},
        }]
        with self.assertRaisesRegex(TaskContractError, "input entity"):
            parse_task_contract(value, command)
        value["requirements"] = [{
            "requirement_id": "one", "evidence": "源数据只读",
            "predicate": {"kind": "source_preserved", "subject": "roads"},
        }, {
            "requirement_id": "two", "evidence": "源数据只读",
            "predicate": {"kind": "source_preserved", "subject": "roads"},
        }]
        with self.assertRaisesRegex(TaskContractError, "duplicates"):
            parse_task_contract(value, command)

    def test_schema_requires_closed_output_evidence_field(self):
        value = {"task_contract": task_contract("学校500米缓冲区")}
        jsonschema.validate(model_envelope(value), TASK_CONTRACT.schema)
        del value["task_contract"]["outputs"][0]["evidence"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(model_envelope(value), TASK_CONTRACT.schema)

    def test_task_contract_requires_export_subject_to_be_the_created_output(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract
        command = "导出道路为 roads_export"
        value = task_contract(command)
        value["input_entities"] = [{"entity_id": "roads", "role": "source", "kind": "feature_layer", "reference": "layer:roads", "evidence": "道路"}]
        value["outputs"][0]["output_id"] = "roads_export"
        value["outputs"][0]["name"] = "roads_export"
        value["requirements"] = [{"requirement_id": "export", "predicate": {"kind": "artifact_export", "subject": "roads", "target": "roads_export", "action": "export_selected_features", "selected_only": True}, "evidence": command}]
        with self.assertRaises(TaskContractError):
            parse_task_contract(value, command)

    def test_task_contract_rejects_filtering_an_output_before_it_exists(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract
        command = "筛选道路生成 roads_export"
        value = task_contract(command)
        value["input_entities"] = [{
            "entity_id": "roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": "道路",
        }]
        value["outputs"][0].update({
            "output_id": "roads_export", "name": "roads_export", "kind": "feature_class",
            "format": "shp", "geometry": "polyline",
            "spatial_reference": "EPSG:3857", "destination": "default",
        })
        value["requirements"] = [{
            "requirement_id": "filter",
            "predicate": {
                "kind": "attribute_filter", "subject": "roads", "target": "roads_export",
                "where": {"field": "CLASS", "op": "eq", "value": "A"},
                "selection_type": "new_selection",
            },
            "evidence": "筛选道路",
        }]

        with self.assertRaisesRegex(TaskContractError, "attribute_filter.*input entity"):
            parse_task_contract(value, command)

    def test_selected_feature_cardinality_is_owned_by_the_semantic_requirement(self):
        from gateway_py3.task_contract import parse_task_contract
        command = "导出道路为 roads_export"
        value = task_contract(command)
        value["input_entities"] = [{
            "entity_id": "roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": "道路",
        }]
        value["outputs"][0].update({
            "output_id": "roads_export", "name": "roads_export", "kind": "feature_class",
            "format": "shp", "geometry": "polyline",
            "spatial_reference": "EPSG:3857", "destination": "default",
        })
        value["requirements"] = [{
            "requirement_id": "export",
            "predicate": {
                "kind": "artifact_export", "subject": "roads_export", "target": "roads",
                "action": "export_selected_features", "selected_only": True,
            },
            "evidence": command,
        }]

        parsed = parse_task_contract(value, command)

        self.assertNotIn("grain", parsed["outputs"][0])
        self.assertTrue(parsed["requirements"][0]["predicate"]["selected_only"])
        self.assertEqual("roads", parsed["requirements"][0]["predicate"]["target"])

    def test_task_contract_rejects_a_second_export_for_an_already_created_output(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract
        command = "将事故点空间连接到道路，生成 road_accident_join"
        value = task_contract(command)
        value["input_entities"] = [
            {"entity_id": "accidents", "role": "join", "kind": "feature_layer", "reference": "layer:accidents", "evidence": "事故点"},
            {"entity_id": "roads", "role": "target", "kind": "feature_layer", "reference": "layer:roads", "evidence": "道路"},
        ]
        value["outputs"][0].update({
            "output_id": "road_accident_join", "name": "road_accident_join",
            "kind": "feature_class", "format": "shp", "geometry": "polyline",
            "spatial_reference": "EPSG:3857", "destination": "default",
        })
        value["requirements"] = [
            {"requirement_id": "join", "predicate": {
                "kind": "spatial_join", "subject": "road_accident_join",
                "target": "roads", "join": "accidents",
            }, "evidence": "将事故点空间连接到道路"},
            {"requirement_id": "export", "predicate": {
                "kind": "artifact_export", "subject": "road_accident_join", "target": "roads",
                "action": "export_selected_features", "selected_only": True,
            }, "evidence": "生成 road_accident_join"},
        ]

        with self.assertRaisesRegex(TaskContractError, "already created"):
            parse_task_contract(value, command)

    def test_task_contract_accepts_a_targetless_map_snapshot_export(self):
        from gateway_py3.task_contract import parse_task_contract
        command = "导出当前应急分布图"
        value = task_contract(command)
        value["outputs"][0].update({
            "output_id": "emergency_map", "name": "emergency_map", "kind": "file",
            "format": "png", "geometry": "not_applicable",
            "spatial_reference": "not_applicable", "required_fields": [], "destination": "default",
        })
        value["requirements"] = [{
            "requirement_id": "export_map",
            "predicate": {"kind": "artifact_export", "subject": "emergency_map", "action": "map_png"},
            "evidence": command,
        }]

        self.assertEqual("map_png", parse_task_contract(value, command)["requirements"][0]["predicate"]["action"])

    def test_task_contract_rejects_export_operation_names_as_semantic_actions(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract

        command = "导出当前应急分布图"
        value = task_contract(command)
        value["outputs"][0].update({
            "output_id": "emergency_map", "name": "emergency_map", "kind": "file",
            "format": "png", "geometry": "not_applicable",
            "spatial_reference": "not_applicable", "required_fields": [], "destination": "default",
        })
        value["requirements"] = [{
            "requirement_id": "export_map",
            "predicate": {
                "kind": "artifact_export", "subject": "emergency_map",
                "action": "export_map_png",
            },
            "evidence": command,
        }]

        with self.assertRaisesRegex(TaskContractError, "action.*closed vocabulary"):
            parse_task_contract(value, command)
        jsonschema.validate(
            {"task_contract": task_contract_model_view(value)}, TASK_CONTRACT.schema,
        )

    def test_task_contract_rejects_fields_that_a_passthrough_export_source_cannot_supply(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract

        command = "将道路导出为 roads.shp"
        value = {
            "input_entities": [{
                "entity_id": "input:roads", "role": "source", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": "道路",
            }],
            "outputs": [{
                "output_id": "output:roads", "kind": "feature_class", "name": "roads",
                "format": "shp", "geometry": "polyline", "required_fields": ["SITE_ID"],
                "spatial_reference": "EPSG:3857", "destination": "default", "evidence": "roads.shp",
            }],
            "requirements": [{
                "requirement_id": "export", "evidence": command,
                "predicate": {
                    "kind": "artifact_export", "subject": "output:roads",
                    "target": "input:roads", "action": "export_selected_features",
                    "selected_only": True,
                },
            }],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }

        with self.assertRaisesRegex(TaskContractError, "required_fields.*export source"):
            parse_task_contract(value, command, CONTEXT)

    def test_task_contract_derives_non_spatial_map_snapshot_dimensions(self):
        from gateway_py3.task_contract import parse_task_contract
        command = "导出当前道路安全图"
        value = task_contract(command)
        value["outputs"][0].update({
            "output_id": "road_safety_map", "name": "road_safety_map", "kind": "file",
            "format": "png", "geometry": "polygon",
            "spatial_reference": "EPSG:32650", "required_fields": [], "destination": "default",
        })
        value["requirements"] = [{
            "requirement_id": "export_map", "evidence": command,
            "predicate": {"kind": "artifact_export", "subject": "road_safety_map", "action": "map_png"},
        }]

        output = parse_task_contract(value, command)["outputs"][0]

        self.assertEqual("not_applicable", output["geometry"])
        self.assertEqual("not_applicable", output["spatial_reference"])

    def test_task_contract_binds_condition_literals_to_context_field_types(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract

        command = "筛选 RISK_LVL 不低于4且名称为004的道路"
        context = {"layers": [{
            "layer_ref": "layer:roads", "name": "roads",
            "fields": [
                {"name": "RISK_LVL", "type": "Integer"},
                {"name": "ROAD_CODE", "type": "String"},
            ],
        }]}
        value = {
            "input_entities": [{
                "entity_id": "roads", "role": "target", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": command,
            }],
            "outputs": [],
            "requirements": [{
                "requirement_id": "filter", "evidence": command,
                "predicate": {
                    "kind": "attribute_filter", "subject": "roads", "target": "roads",
                    "selection_type": "new_selection",
                    "where": {"op": "and", "conditions": [
                        {"field": "RISK_LVL", "op": "gte", "value": "4"},
                        {"field": "ROAD_CODE", "op": "eq", "value": "004"},
                    ]},
                },
            }],
            "allowed_side_effects": ["changes_map"], "clarifications": [],
        }

        parsed = parse_task_contract(value, command, context)

        conditions = parsed["requirements"][0]["predicate"]["where"]["conditions"]
        self.assertEqual(4, conditions[0]["value"])
        self.assertEqual("004", conditions[1]["value"])
        invalid = copy.deepcopy(value)
        invalid["requirements"][0]["predicate"]["where"]["conditions"][0]["value"] = "four"
        with self.assertRaisesRegex(TaskContractError, "RISK_LVL.*integer"):
            parse_task_contract(invalid, command, context)

    def test_task_contract_canonicalizes_kilometers_to_meters(self):
        from gateway_py3.task_contract import parse_task_contract
        command = "建立2公里缓冲区"
        value = task_contract(command)
        value["input_entities"] = [{
            "entity_id": "source", "role": "source", "kind": "feature_layer",
            "reference": "layer:source", "evidence": command,
        }]
        value["outputs"][0].update({
            "output_id": "buffer", "name": "buffer", "kind": "feature_class",
            "format": "shp", "geometry": "polygon",
            "spatial_reference": "EPSG:32650", "destination": "default",
        })
        value["requirements"] = [{
            "requirement_id": "buffer",
            "predicate": {
                "kind": "buffer", "subject": "buffer", "source": "source",
                "distance": {"value": 2, "unit": "Kilometers"},
            },
            "evidence": command,
        }]

        predicate = parse_task_contract(value, command)["requirements"][0]["predicate"]

        self.assertEqual({"value": 2000, "unit": "meters"}, predicate["distance"])

    def test_task_contract_derives_fixed_export_format_and_optional_selection_default(self):
        from gateway_py3.task_contract import parse_task_contract
        command = "导出道路属性表"
        value = task_contract(command)
        value["input_entities"] = [{
            "entity_id": "roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": command,
        }]
        value["outputs"][0].update({
            "output_id": "roads_csv", "name": "roads_csv", "kind": "file",
            "format": "csv", "geometry": "not_applicable",
            "spatial_reference": "not_applicable", "required_fields": ["RID"], "destination": "default",
        })
        value["requirements"] = [{
            "requirement_id": "csv", "evidence": command,
            "predicate": {"kind": "artifact_export", "subject": "roads_csv", "target": "roads", "action": "table_csv"},
        }]

        predicate = parse_task_contract(value, command)["requirements"][0]["predicate"]

        self.assertEqual("csv", predicate["output_format"])
        self.assertFalse(predicate["selected_only"])

    def test_model_catalog_never_exposes_export_output_format(self):
        from gateway_py3.semantic_domain import task_predicate_catalog

        variants = [
            item for item in task_predicate_catalog()["variants"]
            if item["kind"] == "artifact_export"
        ]

        self.assertTrue(variants)
        self.assertTrue(all("output_format" not in item["fields"] for item in variants))
        self.assertNotIn("gdb_or_shp", repr(variants))

    def test_model_view_strips_server_derived_export_format(self):
        import json

        command = "导出道路属性表"
        value = task_contract(command)
        value["requirements"][0]["predicate"] = {
            "kind": "artifact_export", "subject": "o1", "target": "o1",
            "action": "table_csv", "selected_only": False, "output_format": "csv",
        }

        predicate = json.loads(task_contract_model_view(value)["requirements"][0]["predicate_json"])

        self.assertNotIn("output_format", predicate)

    def test_selected_feature_export_format_is_derived_from_declared_output(self):
        from gateway_py3.task_contract import bind_model_task_contract

        command = "导出道路为 roads_export"
        value = task_contract(command)
        value["input_entities"] = [{
            "entity_id": "roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": command,
        }]
        value["outputs"][0].update({
            "output_id": "roads_export", "name": "roads_export", "kind": "feature_class",
            "format": "shp", "geometry": "polyline", "spatial_reference": "EPSG:3857",
            "destination": "default",
        })
        value["requirements"] = [{
            "requirement_id": "export", "evidence": command,
            "predicate": {
                "kind": "artifact_export", "subject": "roads_export", "target": "roads",
                "action": "export_selected_features", "selected_only": True,
            },
        }]
        wire = task_contract_model_view(value)

        parsed = parse_task_contract(bind_model_task_contract(wire, command, CONTEXT), command, CONTEXT)

        self.assertEqual("shp", parsed["requirements"][0]["predicate"]["output_format"])

    def test_model_provided_legacy_export_format_is_rejected(self):
        from gateway_py3.task_contract import bind_model_task_contract

        command = "导出道路属性表"
        value = task_contract(command)
        value["requirements"][0]["predicate"]["output_format"] = "csv"
        wire = task_contract_model_view(value)
        predicate = '{"kind":"artifact_export","subject":"o1","action":"write_file","output_format":"csv"}'
        wire["requirements"][0]["predicate_json"] = predicate

        with self.assertRaisesRegex(TaskContractError, "invalid or cross-kind fields"):
            parse_task_contract(bind_model_task_contract(wire, command), command)

    def test_export_action_must_match_declared_output_format(self):
        command = "导出当前地图为 PDF"
        value = task_contract(command)
        value["outputs"][0].update({
            "kind": "file", "format": "csv", "geometry": "not_applicable",
            "spatial_reference": "not_applicable", "required_fields": [],
            "destination": "default",
        })
        value["requirements"][0]["predicate"] = {
            "kind": "artifact_export", "subject": "o1", "action": "map_pdf",
        }

        with self.assertRaisesRegex(TaskContractError, "requires output format pdf"):
            parse_task_contract(value, command)

    def test_follow_on_csv_inherits_the_source_output_basename_when_unnamed(self):
        from gateway_py3.task_contract import parse_task_contract
        command = "生成 roads_a 并导出其属性表"
        value = task_contract(command)
        value["input_entities"] = [{
            "entity_id": "roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": "roads",
        }]
        value["outputs"] = [{
            "output_id": "roads_a", "name": "roads_a", "kind": "feature_class",
            "format": "gdb", "geometry": "polyline",
            "required_fields": ["RID"], "spatial_reference": "EPSG:3857",
            "destination": "default", "evidence": "roads_a",
        }, {
            "output_id": "roads_a_csv", "name": "roads_a_csv", "kind": "file",
            "format": "csv", "geometry": "not_applicable",
            "required_fields": ["RID"], "spatial_reference": "not_applicable",
            "destination": "default", "evidence": "导出其属性表",
        }]
        value["requirements"] = [{
            "requirement_id": "export_features", "evidence": "roads_a",
            "predicate": {"kind": "artifact_export", "subject": "roads_a", "target": "roads", "action": "export_selected_features", "selected_only": True},
        }, {
            "requirement_id": "export_csv", "evidence": "导出其属性表",
            "predicate": {"kind": "artifact_export", "subject": "roads_a_csv", "target": "roads_a", "action": "table_csv"},
        }]

        parsed = parse_task_contract(value, command)

        self.assertEqual("roads_a", parsed["outputs"][1]["name"])

    def test_task_contract_rejects_invalid_condition_protocol_tree(self):
        from gateway_py3.task_contract import TaskContractError, parse_task_contract
        command = "字段比较"
        value = task_contract(command)
        value["requirements"] = [{"requirement_id": "r", "predicate": {"kind": "attribute_filter", "subject": "o1", "where": {"op": "gt", "field": "a", "value": 1, "value_field": "b"}, "selection_type": "new_selection"}, "evidence": command}]
        with self.assertRaises(TaskContractError):
            parse_task_contract(value, command)

    def test_condition_protocol_normalizes_operator_alias(self):
        from arcmap_runtime_py2.condition_protocol import validate_condition_tree
        self.assertEqual({"op": "eq", "field": "a", "value": 1}, validate_condition_tree({"operator": "=", "field": "a", "value": 1}))

    def test_condition_protocol_rejects_extra_leaf_key(self):
        from arcmap_runtime_py2.condition_protocol import validate_condition_tree
        with self.assertRaises(ValueError): validate_condition_tree({"op": "eq", "field": "a", "value": 1, "extra": True})

    def test_condition_protocol_rejects_invalid_between(self):
        from arcmap_runtime_py2.condition_protocol import validate_condition_tree
        with self.assertRaises(ValueError): validate_condition_tree({"op": "between", "field": "a", "values": [1]})

    def test_condition_protocol_rejects_invalid_logical_shape(self):
        from arcmap_runtime_py2.condition_protocol import validate_condition_tree
        with self.assertRaises(ValueError): validate_condition_tree({"op": "and", "conditions": [{"op": "eq", "field": "a", "value": 1}]})

    def test_condition_protocol_accepts_field_to_field_comparison(self):
        from arcmap_runtime_py2.condition_protocol import validate_condition_tree
        self.assertEqual("right", validate_condition_tree({"op": "gt", "field": "left", "value_field": "right"})["value_field"])


class WorkflowVerifierTests(unittest.TestCase):
    def setUp(self):
        self.verifier = WorkflowVerifier(OperationCatalog())

    def test_public_verifier_compiles_declared_entity_id_to_context_reference(self):
        task = task_contract("筛选道路")
        task["input_entities"] = [{
            "entity_id": "roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": "筛选道路",
        }]
        workflow = {
            "action": "execute", "summary": "筛选道路", "steps": [{
                "id": "select_roads", "operation": "selection.select_by_attribute",
                "arguments": {"layer": "roads", "selection_type": "NEW_SELECTION",
                              "where": {"field": "CLASS", "op": "eq", "value": "A"}},
                "reason": "筛选道路",
            }],
        }
        report = self.verifier.verify(workflow, CONTEXT, task)
        self.assertFalse(any(item["code"] == "dependency.unresolved" for item in report["hard_violations"]))

    def test_public_verifier_rejects_two_steps_writing_the_same_artifact(self):
        command = "生成唯一的道路裁剪成果"
        workflow = {
            "action": "execute", "summary": command, "steps": [
                {
                    "id": "wrong_clip", "operation": "analysis.clip",
                    "arguments": {
                        "input_layer": "roads", "clip_layer": "zones",
                        "output_name": "roads_result",
                    },
                    "reason": command,
                },
                {
                    "id": "duplicate_export", "operation": "selection.export_selected_features",
                    "arguments": {"layer": "roads", "output_name": "roads_result"},
                    "reason": command,
                },
            ],
        }

        report = self.verifier.verify(workflow, CONTEXT, task_contract(command))

        self.assertFalse(report["ok"])
        self.assertIn("output destination collision", report["hard_violations"][0]["message"])

    def test_public_verifier_rejects_missing_output_location_before_unsaved_mxd_execution(self):
        command = "生成道路缓冲区"
        workflow = {
            "action": "execute", "summary": command, "steps": [{
                "id": "buffer", "operation": "analysis.buffer",
                "arguments": {
                    "input_layer": "roads", "distance": "500 meters",
                    "output_name": "roads_buffer", "output_format": "shp",
                },
                "reason": command,
            }],
        }
        context = copy.deepcopy(CONTEXT)
        context["is_saved"] = False

        report = self.verifier.verify(
            workflow, context,
            self._task(command, "roads_buffer", ["RID", "CLASS"], "polygon", "shp"),
        )

        self.assertFalse(report["ok"])
        self.assertTrue(
            any(
                item["code"] == "workflow_structure"
                and "输出位置还不明确" in item.get("message", "")
                for item in report["hard_violations"]
            ),
            report,
        )

    def test_output_location_clarification_preempts_invalid_unsaved_execution_draft(self):
        command = "生成 roads_buffer.shp"
        task = self._task(command, "roads_buffer", ["RID", "CLASS"], "polygon", "shp")
        task["clarifications"] = [{
            "clarification_id": "output-location", "question": "请指定输出文件夹。",
            "evidence": command,
        }]
        workflow = {
            "action": "execute", "summary": command, "steps": [{
                "id": "buffer", "operation": "analysis.buffer",
                "arguments": {
                    "input_layer": "roads", "distance": "500 meters",
                    "output_name": "roads_buffer", "output_format": "shp",
                },
                "reason": command,
            }],
        }
        context = copy.deepcopy(CONTEXT)
        context["is_saved"] = False

        report = self.verifier.verify(workflow, context, task)

        self.assertEqual([], report["hard_violations"])
        self.assertEqual("task.clarification", report["blocking_clarifications"][0]["code"])

    def test_output_destination_is_part_of_the_verified_task_contract(self):
        with tempfile.TemporaryDirectory() as requested_folder, tempfile.TemporaryDirectory() as wrong_folder:
            command = "生成 roads_buffer.shp 并写入 " + requested_folder
            task = self._task(command, "roads_buffer", ["RID", "CLASS"], "polygon", "shp")
            task["outputs"][0]["destination"] = requested_folder
            task["input_entities"] = [{
                "entity_id": "roads", "role": "source", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": command,
            }]
            task["requirements"] = [{
                "requirement_id": "buffer", "evidence": command,
                "predicate": {
                    "kind": "buffer", "subject": "o1", "source": "roads",
                    "distance": {"value": 500, "unit": "meters"},
                },
            }]
            workflow = {
                "action": "execute", "summary": command, "steps": [{
                    "id": "buffer", "operation": "analysis.buffer",
                    "arguments": {
                        "input_layer": "roads", "distance": "500 meters",
                        "output_name": "roads_buffer", "output_format": "shp",
                    },
                    "reason": command,
                }],
            }

            missing = self.verifier.verify(workflow, CONTEXT, task)
            exact_workflow = copy.deepcopy(workflow)
            exact_workflow["steps"][0]["arguments"]["output_folder"] = requested_folder
            exact = self.verifier.verify(exact_workflow, CONTEXT, task)
            wrong_workflow = copy.deepcopy(workflow)
            wrong_workflow["steps"][0]["arguments"]["output_folder"] = wrong_folder
            wrong = self.verifier.verify(wrong_workflow, CONTEXT, task)

            self.assertFalse(missing["ok"], missing)
            self.assertTrue(exact["ok"], exact)
            self.assertFalse(wrong["ok"], wrong)
            self.assertEqual(
                ["output.destination"],
                [item["code"] for item in missing["hard_violations"]],
            )
            self.assertEqual(
                ["output.destination"],
                [item["code"] for item in wrong["hard_violations"]],
            )

    def test_spatial_filter_proof_binds_the_selectors_prior_attribute_selection(self):
        command = "保留距离A类道路500米以内的分区"
        task = {
            "input_entities": [
                {"entity_id": "roads", "role": "selector", "kind": "feature_layer", "reference": "layer:roads", "evidence": command},
                {"entity_id": "zones", "role": "target", "kind": "feature_layer", "reference": "layer:zones", "evidence": command},
            ],
            "outputs": [],
            "requirements": [
                {
                    "requirement_id": "filter_roads", "evidence": command,
                    "predicate": {
                        "kind": "attribute_filter", "subject": "roads", "target": "roads",
                        "where": {"field": "CLASS", "op": "eq", "value": "A"},
                        "selection_type": "new_selection",
                    },
                },
                {
                    "requirement_id": "select_zones", "evidence": command,
                    "predicate": {
                        "kind": "spatial_filter", "subject": "zones", "target": "zones",
                        "selector": "roads", "overlap_type": "within_a_distance",
                        "search_distance": {"value": 500, "unit": "meters"},
                        "selection_type": "new_selection",
                    },
                },
            ],
            "allowed_side_effects": ["changes_map"],
            "clarifications": [],
        }
        workflow = {
            "action": "execute", "summary": command, "steps": [
                {
                    "id": "filter_roads", "operation": "selection.select_by_attribute",
                    "arguments": {
                        "layer": "roads", "selection_type": "NEW_SELECTION",
                        "where": {"field": "CLASS", "op": "eq", "value": "A"},
                    },
                    "reason": command,
                },
                {
                    "id": "select_zones", "operation": "selection.select_by_location",
                    "arguments": {
                        "target_layer": "zones", "select_layer": "roads",
                        "overlap_type": "WITHIN_A_DISTANCE", "search_distance": "500 meters",
                        "selection_type": "NEW_SELECTION",
                    },
                    "reason": command,
                },
            ],
        }

        report = self.verifier.verify(workflow, CONTEXT, task)

        proofs = dict((item["requirement_id"], item.get("proof")) for item in report["requirements"])
        selector_proof = proofs["select_zones"]["selector_selection"]
        self.assertEqual("filter_roads", selector_proof["requirement_id"])
        self.assertEqual("filter_roads", selector_proof["step_id"])
        self.assertEqual("attribute_filter", selector_proof["semantic_fact"]["kind"])

        cleared_workflow = copy.deepcopy(workflow)
        cleared_workflow["steps"].insert(1, {
            "id": "clear_roads", "operation": "selection.clear_selection",
            "arguments": {"layer": "roads"}, "reason": command,
        })

        cleared_report = self.verifier.verify(cleared_workflow, CONTEXT, task)

        self.assertFalse(cleared_report["ok"])
        violations = [
            item for item in cleared_report["hard_violations"]
            if item["code"] == "requirement.selector_selection_invalidated"
        ]
        self.assertEqual(1, len(violations))
        self.assertEqual("select_zones", violations[0]["requirement_id"])
        self.assertEqual("clear_roads", violations[0]["step_id"])
        self.assertEqual(
            {"selector_reference": "layer:roads", "producer_step_id": "filter_roads"},
            violations[0]["expected"],
        )
        self.assertEqual(
            {"invalidator_kind": "map_change", "invalidator_action": "clear_selection"},
            violations[0]["actual"],
        )
        requirements = {
            item["requirement_id"]: item for item in cleared_report["requirements"]
        }
        self.assertFalse(requirements["select_zones"]["satisfied"])

        replaced_workflow = copy.deepcopy(workflow)
        replaced_workflow["steps"].insert(1, {
            "id": "replace_roads", "operation": "selection.select_by_attribute",
            "arguments": {
                "layer": "roads", "selection_type": "NEW_SELECTION",
                "where": {"field": "CLASS", "op": "eq", "value": "B"},
            },
            "reason": command,
        })

        replaced_report = self.verifier.verify(replaced_workflow, CONTEXT, task)

        self.assertFalse(replaced_report["ok"])
        replacement = next(
            item for item in replaced_report["hard_violations"]
            if item["code"] == "requirement.selector_selection_invalidated"
        )
        self.assertEqual("replace_roads", replacement["step_id"])
        self.assertEqual({"invalidator_kind": "attribute_filter"}, replacement["actual"])

    def test_verifier_resolves_stable_reference_when_context_exposes_only_an_alias_key(self):
        from gateway_py3.workflow_verifier import Fact
        fact = Fact("layer:roads", "feature_class", "polyline", frozenset({"CLASS"}), "EPSG:3857", "existing", "all", "published", "roads", None)
        resolved = self.verifier._fact_for_reference({"roads": fact}, "layer:roads")
        self.assertIs(fact, resolved)

    def test_write_authorization_allows_edit_only_for_a_run_owned_output(self):
        task = task_contract("处理成果")
        task["allowed_side_effects"] = ["writes_data"]
        contract = {"side_effects": "edits_data", "inputs": [{"parameter": "layer"}]}
        self.assertTrue(self.verifier._allows_owned_output_mutation(
            {"arguments": {"layer": "from_step:export"}}, contract, task))
        self.assertFalse(self.verifier._allows_owned_output_mutation(
            {"arguments": {"layer": "layer:roads"}}, contract, task))

    @staticmethod
    def _task(command, name, fields, geometry, fmt="gdb"):
        value = task_contract(command)
        value["outputs"][0].update({"kind": "feature_class", "name": name, "required_fields": fields, "geometry": geometry, "spatial_reference": "EPSG:3857", "format": fmt, "destination": "default"})
        return value

    def _matrix_case(self, label, context, task, wrong, correct, requirement_ids):
        baseline = self.verifier.verify(wrong, context, task)
        candidate = self.verifier.verify(correct, context, task)
        conflicts = [item for item in baseline["hard_violations"] if item["code"] == "requirement.semantic_conflict"]
        self.assertTrue(conflicts, label + " baseline must conflict: " + repr(baseline))
        satisfied = {item["requirement_id"]: item for item in candidate["requirements"]}
        self.assertTrue(all(satisfied[item]["satisfied"] and "proof" in satisfied[item] for item in requirement_ids), label + " candidate must prove every requirement")
        gate = DominanceGate().admit({"baseline_verifier_report": baseline}, candidate, [{"claim": {"proof_id": item["violation_id"]}} for item in conflicts])
        self.assertTrue(gate["accepted"], label + " gate: " + repr(gate))

    def test_nine_semantic_behavior_matrix(self):
        command = "matrix"
        context = {"is_saved": True, "layers": [
            {"layer_ref": "layer:target-1", "name": "target_name", "geometry_type": "Polyline", "spatial_reference": "EPSG:3857", "fields": [{"name":"a"},{"name":"b"},{"name":"CLASS"}]},
            {"layer_ref": "layer:selector-2", "name": "selector_name", "geometry_type": "Polygon", "spatial_reference": "EPSG:3857", "fields": [{"name":"z"}]},
        ]}
        entities = [{"entity_id":"entity_target","role":"target","kind":"feature_layer","reference":"target_name","evidence":command}, {"entity_id":"entity_selector","role":"selector","kind":"feature_layer","reference":"selector_name","evidence":command}]
        def task_for(kind, predicate, name, output_kind="feature_class", geometry="polyline", fmt="gdb"):
            return {"input_entities": entities, "outputs":[{"output_id":"o1","kind":output_kind,"name":name,"format":fmt,"geometry":geometry,"required_fields":[],"spatial_reference":"EPSG:3857" if output_kind == "feature_class" else "not_applicable","destination":"not_applicable" if output_kind == "map_state" else "default","evidence":command}], "requirements":[{"requirement_id":"r1","predicate":predicate,"evidence":command}], "allowed_side_effects":["changes_map","writes_data"],"clarifications":[]}
        # Attribute literal and field-to-field use the same production where grammar.
        for label, where, wrong_where in (("attribute literal", {"op":"eq","field":"a","value":1}, {"op":"eq","field":"a","value":2}), ("attribute value_field", {"op":"gt","field":"a","value_field":"b"}, {"op":"gt","field":"b","value_field":"a"})):
            predicate={"kind":"attribute_filter","subject":"o1","target":"entity_target","where":where,"selection_type":"new_selection"}
            t=task_for("attribute_filter",predicate,"selection", "map_state", "not_applicable", "map")
            base={"action":"execute","summary":command,"steps":[{"id":"s","operation":"selection.select_by_attribute","arguments":{"layer":"target_name","where":wrong_where,"selection_type":"NEW_SELECTION"},"reason":command}]}
            good=copy.deepcopy(base); good["steps"][0]["arguments"]["where"]=where
            self._matrix_case(label,context,t,base,good,["r1"])
        # buffer, spatial join, aggregate, project and selected export.
        cases=[
            ("buffer", {"kind":"buffer","subject":"o1","source":"entity_target","distance":{"value":10,"unit":"meters"}}, "analysis.buffer", {"input_layer":"target_name","distance":"20 meters","output_name":"buffered"}, {"input_layer":"target_name","distance":"10 meters","output_name":"buffered"}, "buffered", "feature_class", "polygon", "gdb"),
            ("spatial join", {"kind":"spatial_join","subject":"o1","target":"entity_target","join":"entity_selector"}, "analysis.spatial_join", {"target_layer":"selector_name","join_layer":"target_name","output_name":"joined"}, {"target_layer":"target_name","join_layer":"selector_name","output_name":"joined"}, "joined", "feature_class", "polyline", "gdb"),
            ("aggregate", {"kind":"aggregate","subject":"o1","source":"entity_target","dissolve_fields":["CLASS"]}, "analysis.dissolve", {"input_layer":"target_name","dissolve_fields":["a"],"output_name":"agg"}, {"input_layer":"target_name","dissolve_fields":["CLASS"],"output_name":"agg"}, "agg", "feature_class", "polyline", "gdb"),
            ("project", {"kind":"project","subject":"o1","source":"entity_target","spatial_reference":"EPSG:4326"}, "analysis.project", {"input_layer":"target_name","spatial_reference":"EPSG:3857","output_name":"projected"}, {"input_layer":"target_name","spatial_reference":"EPSG:4326","output_name":"projected"}, "projected", "feature_class", "polyline", "gdb"),
            ("selected export", {"kind":"artifact_export","subject":"o1","action":"table_csv","target":"entity_target","selected_only":True}, "export.table_csv", {"layer":"target_name","selected_only":False,"output_name":"out"}, {"layer":"target_name","selected_only":True,"output_name":"out"}, "out", "file", "not_applicable", "csv"),
        ]
        for label,predicate,operation,bad_args,good_args,name,kind,geometry,fmt in cases:
            t=task_for(label,predicate,name,kind,geometry,fmt)
            if label == "aggregate": t["outputs"][0]["required_fields"] = ["CLASS"]
            if label == "project": t["outputs"][0]["spatial_reference"] = "EPSG:4326"
            case_context = copy.deepcopy(context)
            if label == "selected export":
                case_context["layers"][0]["selected_count"] = 1
            bad={"action":"execute","summary":command,"steps":[{"id":"x","operation":operation,"arguments":bad_args,"reason":command}]}; good=copy.deepcopy(bad); good["steps"][0]["arguments"]=good_args; self._matrix_case(label,case_context,t,bad,good,["r1"])
        # Spatial filter: relation, search distance and target/selector roles are one fact.
        predicate={"kind":"spatial_filter","subject":"o1","target":"entity_target","selector":"entity_selector","overlap_type":"intersect","selection_type":"new_selection"}
        t=task_for("spatial",predicate,"selection", "map_state", "not_applicable", "map")
        t["outputs"][0]["spatial_reference"] = "not_applicable"
        bad={"action":"execute","summary":command,"steps":[{"id":"spatial","operation":"selection.select_by_location","arguments":{"target_layer":"target_name","select_layer":"selector_name","overlap_type":"WITHIN","search_distance":"0 meters","selection_type":"NEW_SELECTION"},"reason":command}]}
        good=copy.deepcopy(bad); good["steps"][0]["arguments"]["overlap_type"]="INTERSECT"
        self._matrix_case("spatial filter",context,t,bad,good,["r1"])
        # Continuous chain: buffer output is the semantic target of its export.
        chain={"input_entities":[{"entity_id":"chain_source","role":"source","kind":"feature_layer","reference":"target_name","evidence":command}],"outputs":[
            {"output_id":"o1","kind":"feature_class","name":"chain_buffer","format":"gdb","geometry":"polygon","required_fields":[],"spatial_reference":"EPSG:3857","destination":"default","evidence":command},
            {"output_id":"o2","kind":"file","name":"chain_csv","format":"csv","geometry":"not_applicable","required_fields":[],"spatial_reference":"not_applicable","destination":"default","evidence":command}],
            "requirements":[{"requirement_id":"buffer_requirement","predicate":{"kind":"buffer","subject":"o1","source":"chain_source","distance":{"value":10,"unit":"meters"}},"evidence":command},{"requirement_id":"export_requirement","predicate":{"kind":"artifact_export","subject":"o2","action":"table_csv","target":"o1","selected_only":False},"evidence":command}],"allowed_side_effects":["writes_data"],"clarifications":[]}
        bad={"action":"execute","summary":command,"steps":[{"id":"chain_buffer","operation":"analysis.buffer","arguments":{"input_layer":"target_name","distance":"20 meters","output_name":"chain_buffer"},"reason":command},{"id":"chain_export","operation":"export.table_csv","arguments":{"layer":"from_step:chain_buffer","selected_only":False,"output_name":"chain_csv"},"reason":command}]}
        good=copy.deepcopy(bad); good["steps"][0]["arguments"]["distance"]="10 meters"
        self._matrix_case("continuous chain",context,chain,bad,good,["buffer_requirement","export_requirement"])

    def test_spatial_join_propagates_join_count_and_checks_output_contract(self):
        command = "对道路和分区进行空间连接"
        workflow = {"action": "execute", "summary": command, "steps": [{"id": "join", "operation": "analysis.spatial_join", "arguments": {"target_layer": "roads", "join_layer": "zones", "output_name": "joined"}, "reason": command}]}
        report = self.verifier.verify(workflow, CONTEXT, self._task(command, "joined", ["RID", "CLASS", "Join_Count"], "polyline"))
        self.assertTrue(report["ok"])

    def test_file_exports_prove_exact_format_and_csv_source_fields(self):
        command = "导出道路属性表和当前地图"
        task = {
            "input_entities": [{
                "entity_id": "roads", "role": "source", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": command,
            }],
            "outputs": [
                {
                    "output_id": "roads_csv", "kind": "file", "name": "roads_table.csv",
                    "format": "csv", "geometry": "not_applicable",
                    "required_fields": ["RID", "CLASS"],
                    "spatial_reference": "not_applicable", "destination": "default",
                    "evidence": command,
                },
                {
                    "output_id": "roads_map", "kind": "file", "name": "roads_map.png",
                    "format": "png", "geometry": "not_applicable",
                    "required_fields": [], "spatial_reference": "not_applicable",
                    "destination": "default", "evidence": command,
                },
            ],
            "requirements": [
                {
                    "requirement_id": "csv", "evidence": command,
                    "predicate": {"kind": "artifact_export", "subject": "roads_csv", "target": "roads", "action": "table_csv", "selected_only": False, "output_format": "csv"},
                },
                {
                    "requirement_id": "map", "evidence": command,
                    "predicate": {"kind": "artifact_export", "subject": "roads_map", "action": "map_png", "output_format": "png"},
                },
            ],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }
        workflow = {
            "action": "execute", "summary": command, "steps": [
                {"id": "csv", "operation": "export.table_csv", "arguments": {"layer": "roads", "output_name": "roads_table"}, "reason": command},
                {"id": "map", "operation": "export.map_png", "arguments": {"output_name": "roads_map"}, "reason": command},
            ],
        }

        report = self.verifier.verify(workflow, CONTEXT, task)

        self.assertTrue(report["ok"], report)

    def test_csv_of_selected_feature_artifact_proves_selected_source_export(self):
        command = "筛选道路生成 roads_selected，并导出 roads_selected.csv"
        task = {
            "input_entities": [{
                "entity_id": "roads", "role": "source", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": command,
            }],
            "outputs": [
                {
                    "output_id": "roads_selected", "kind": "feature_class", "name": "roads_selected",
                    "format": "shp", "geometry": "polyline",
                    "required_fields": ["RID", "CLASS"], "spatial_reference": "EPSG:3857",
                    "destination": "default", "evidence": "roads_selected",
                },
                {
                    "output_id": "roads_csv", "kind": "file", "name": "roads_selected",
                    "format": "csv", "geometry": "not_applicable",
                    "required_fields": ["RID", "CLASS"], "spatial_reference": "not_applicable",
                    "destination": "default", "evidence": "roads_selected.csv",
                },
            ],
            "requirements": [
                {
                    "requirement_id": "selected_features", "evidence": command,
                    "predicate": {
                        "kind": "artifact_export", "subject": "roads_selected", "target": "roads",
                        "action": "export_selected_features", "selected_only": True, "output_format": "shp",
                    },
                },
                {
                    "requirement_id": "selected_csv", "evidence": command,
                    "predicate": {
                        "kind": "artifact_export", "subject": "roads_csv", "target": "roads",
                        "action": "table_csv", "selected_only": True, "output_format": "csv",
                    },
                },
            ],
            "allowed_side_effects": ["changes_map", "writes_data"], "clarifications": [],
        }
        workflow = {
            "action": "execute", "summary": command, "steps": [
                {
                    "id": "select_roads", "operation": "selection.select_by_attribute",
                    "arguments": {
                        "layer": "roads", "selection_type": "NEW_SELECTION",
                        "where": {"field": "CLASS", "op": "eq", "value": "A"},
                    },
                    "reason": command,
                },
                {
                    "id": "export_selected", "operation": "selection.export_selected_features",
                    "arguments": {"layer": "roads", "output_name": "roads_selected", "output_format": "shp"},
                    "reason": command,
                },
                {
                    "id": "export_csv", "operation": "export.table_csv",
                    "arguments": {"layer": "from_step:export_selected", "selected_only": False, "output_name": "roads_selected"},
                    "reason": command,
                },
            ],
        }

        report = self.verifier.verify(workflow, CONTEXT, task)

        self.assertTrue(report["ok"], report)
        proof = next(item for item in report["requirements"] if item["requirement_id"] == "selected_csv")["proof"]
        self.assertEqual("layer:roads", proof["semantic_fact"]["target"])
        self.assertTrue(proof["semantic_fact"]["selected_only"])

    def test_selected_export_requires_a_live_selection(self):
        command = "导出 roads 当前选中的要素"
        task = {
            "input_entities": [{
                "entity_id": "roads", "role": "source", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": command,
            }],
            "outputs": [{
                "output_id": "roads_selected", "kind": "feature_class",
                "name": "roads_selected", "format": "gdb", "geometry": "polyline",
                "required_fields": ["RID", "CLASS"], "spatial_reference": "EPSG:3857",
                "destination": "default", "evidence": "roads_selected",
            }],
            "requirements": [{
                "requirement_id": "selected_features", "evidence": command,
                "predicate": {
                    "kind": "artifact_export", "subject": "roads_selected", "target": "roads",
                    "action": "export_selected_features", "selected_only": True,
                    "output_format": "gdb",
                },
            }],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }
        workflow = {
            "action": "execute", "summary": command, "steps": [{
                "id": "export_selected", "operation": "selection.export_selected_features",
                "arguments": {"layer": "roads", "output_name": "roads_selected"},
                "reason": command,
            }],
        }

        report = self.verifier.verify(workflow, CONTEXT, task)

        self.assertFalse(report["ok"])
        self.assertTrue(any(
            item["code"] == "input.selection_required"
            and item.get("step_id") == "export_selected"
            for item in report["hard_violations"]
        ), report)

    def test_selected_export_accepts_selection_created_earlier_in_the_workflow(self):
        command = "筛选 A 类道路并导出 roads_selected"
        task = task_contract(command)
        task["input_entities"] = [{
            "entity_id": "roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": command,
        }]
        task["outputs"][0].update({
            "output_id": "roads_selected", "name": "roads_selected", "format": "gdb",
            "kind": "feature_class", "geometry": "polyline",
            "required_fields": ["RID", "CLASS"], "spatial_reference": "EPSG:3857",
            "destination": "default", "evidence": "roads_selected",
        })
        task["requirements"] = [{
            "requirement_id": "selected_features", "evidence": command,
            "predicate": {
                "kind": "artifact_export", "subject": "roads_selected", "target": "roads",
                "action": "export_selected_features", "selected_only": True,
                "output_format": "gdb",
            },
        }]
        workflow = {
            "action": "execute", "summary": command, "steps": [
                {
                    "id": "select_roads", "operation": "selection.select_by_attribute",
                    "arguments": {
                        "layer": "roads", "selection_type": "NEW_SELECTION",
                        "where": {"field": "CLASS", "op": "eq", "value": "A"},
                    },
                    "reason": command,
                },
                {
                    "id": "export_selected", "operation": "selection.export_selected_features",
                    "arguments": {"layer": "roads", "output_name": "roads_selected"},
                    "reason": command,
                },
            ],
        }

        report = self.verifier.verify(workflow, CONTEXT, task)

        self.assertFalse(any(
            item["code"] == "input.selection_required"
            for item in report["hard_violations"]
        ), report)

    def test_selected_only_csv_rejects_a_new_artifact_without_a_live_selection(self):
        command = "筛选 A 类道路，生成 roads_selected，并导出属性表"
        task = {
            "input_entities": [{
                "entity_id": "roads", "role": "source", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": command,
            }],
            "outputs": [
                {
                    "output_id": "roads_selected", "kind": "feature_class",
                    "name": "roads_selected", "format": "gdb", "geometry": "polyline",
                    "required_fields": ["RID", "CLASS"], "spatial_reference": "EPSG:3857",
                    "destination": "default", "evidence": "roads_selected",
                },
                {
                    "output_id": "roads_csv", "kind": "file", "name": "roads_selected",
                    "format": "csv", "geometry": "not_applicable",
                    "required_fields": ["RID", "CLASS"], "spatial_reference": "not_applicable",
                    "destination": "default", "evidence": "roads_selected.csv",
                },
            ],
            "requirements": [
                {
                    "requirement_id": "selected_features", "evidence": command,
                    "predicate": {
                        "kind": "artifact_export", "subject": "roads_selected", "target": "roads",
                        "action": "export_selected_features", "selected_only": True,
                        "output_format": "gdb",
                    },
                },
                {
                    "requirement_id": "selected_csv", "evidence": command,
                    "predicate": {
                        "kind": "artifact_export", "subject": "roads_csv", "target": "roads_selected",
                        "action": "table_csv", "selected_only": True, "output_format": "csv",
                    },
                },
            ],
            "allowed_side_effects": ["changes_map", "writes_data"], "clarifications": [],
        }
        workflow = {
            "action": "execute", "summary": command, "steps": [
                {
                    "id": "select_roads", "operation": "selection.select_by_attribute",
                    "arguments": {
                        "layer": "roads", "selection_type": "NEW_SELECTION",
                        "where": {"field": "CLASS", "op": "eq", "value": "A"},
                    },
                    "reason": command,
                },
                {
                    "id": "export_selected", "operation": "selection.export_selected_features",
                    "arguments": {"layer": "roads", "output_name": "roads_selected"},
                    "reason": command,
                },
                {
                    "id": "export_csv", "operation": "export.table_csv",
                    "arguments": {
                        "layer": "from_step:export_selected", "selected_only": True,
                        "output_name": "roads_selected",
                    },
                    "reason": command,
                },
            ],
        }

        report = self.verifier.verify(workflow, CONTEXT, task)

        self.assertFalse(report["ok"])
        self.assertTrue(any(
            item["code"] == "input.selection_required"
            and item.get("step_id") == "export_csv"
            for item in report["hard_violations"]
        ), report)

    def test_subset_selection_requires_and_consumes_live_selection_state(self):
        command = "先选择 A 类道路，再缩小到 RID 大于 10 的道路"
        task = task_contract(command)
        task["allowed_side_effects"] = ["changes_map"]
        subset = {
            "id": "subset_roads", "operation": "selection.select_by_attribute",
            "arguments": {
                "layer": "roads", "selection_type": "SUBSET_SELECTION",
                "where": {"field": "RID", "op": "gt", "value": 10},
            },
            "reason": command,
        }

        invalid = self.verifier.verify(
            {"action": "execute", "summary": command, "steps": [subset]},
            CONTEXT, task,
        )
        self.assertTrue(any(
            item["code"] == "input.selection_required"
            and item.get("step_id") == "subset_roads"
            for item in invalid["hard_violations"]
        ), invalid)

        valid = self.verifier.verify(
            {
                "action": "execute", "summary": command, "steps": [
                    {
                        "id": "select_roads", "operation": "selection.select_by_attribute",
                        "arguments": {
                            "layer": "roads", "selection_type": "NEW_SELECTION",
                            "where": {"field": "CLASS", "op": "eq", "value": "A"},
                        },
                        "reason": command,
                    },
                    subset,
                ],
            },
            CONTEXT, task,
        )
        self.assertFalse(any(
            item["code"] == "input.selection_required"
            for item in valid["hard_violations"]
        ), valid)

    def test_dissolve_semantics_and_in_place_field_updates_are_proved(self):
        command = "按 CLASS 融合道路"
        workflow = {"action": "execute", "summary": command, "steps": [
            {"id": "add", "operation": "table.add_field", "arguments": {"layer": "roads", "field_name": "TEMP", "field_type": "TEXT"}, "reason": "add"},
            {"id": "drop", "operation": "table.delete_field", "arguments": {"layer": "roads", "field_name": "TEMP"}, "reason": "drop"},
            {"id": "dissolve", "operation": "analysis.dissolve", "arguments": {"input_layer": "roads", "output_name": "roads_by_class", "dissolve_fields": ["CLASS"]}, "reason": command},
        ]}
        report = self.verifier.verify(workflow, CONTEXT, self._task(command, "roads_by_class", ["CLASS"], "polyline"))
        self.assertTrue(report["ok"])
        self.assertNotIn("TEMP", report["facts"][1]["in_place_update"]["fields"])

    def test_dissolve_without_group_fields_proves_one_reduced_output(self):
        command = "将全部道路融合为一个成果"
        task = self._task(command, "roads_reduced", [], "polyline")
        task["input_entities"] = [{
            "entity_id": "roads", "role": "source", "kind": "feature_layer",
            "reference": "layer:roads", "evidence": command,
        }]
        task["requirements"] = [{
            "requirement_id": "dissolve_all", "evidence": command,
            "predicate": {
                "kind": "aggregate", "subject": "o1", "source": "roads",
                "dissolve_fields": [],
            },
        }]
        workflow = {
            "action": "execute", "summary": command, "steps": [{
                "id": "dissolve_all", "operation": "analysis.dissolve",
                "arguments": {"input_layer": "roads", "output_name": "roads_reduced"},
                "reason": command,
            }],
        }

        report = self.verifier.verify(workflow, CONTEXT, task)

        self.assertTrue(report["ok"], report)
        self.assertEqual("reduced", report["facts"][0]["output"]["cardinality"])
        self.assertEqual([], report["facts"][0]["output"]["fields"])
        self.assertTrue(report["requirements"][0]["satisfied"])

    def test_unresolved_context_is_an_obligation_and_format_mismatch_is_a_violation(self):
        command = "按 CLASS 融合道路"
        workflow = {"action": "execute", "summary": command, "steps": [{"id": "dissolve", "operation": "analysis.dissolve", "arguments": {"input_layer": "roads", "output_name": "roads_by_class", "dissolve_fields": ["CLASS"]}, "reason": command}]}
        unresolved = copy.deepcopy(CONTEXT)
        del unresolved["layers"][0]["spatial_reference"]
        report = self.verifier.verify(workflow, unresolved, self._task(command, "roads_by_class", ["CLASS"], "polyline"))
        self.assertEqual("output.spatial_reference_unresolved", report["review_obligations"][0]["code"])
        mismatch = self.verifier.verify(workflow, CONTEXT, self._task(command, "roads_by_class", ["CLASS"], "polyline", fmt="shp"))
        self.assertEqual("output.format", mismatch["hard_violations"][0]["code"])

    def test_unauthorized_effect_is_a_stable_violation(self):
        command = "按 CLASS 融合道路"
        task = self._task(command, "roads_by_class", ["CLASS"], "polyline")
        task["allowed_side_effects"] = ["read_only"]
        workflow = {"action": "execute", "summary": command, "steps": [{"id": "dissolve", "operation": "analysis.dissolve", "arguments": {"input_layer": "roads", "output_name": "roads_by_class", "dissolve_fields": ["CLASS"]}, "reason": command}]}
        report = self.verifier.verify(workflow, CONTEXT, task)
        self.assertEqual("authorization.side_effect", report["hard_violations"][0]["code"])
        self.assertIn("violation_id", report["hard_violations"][0])

    def test_structured_aggregate_requirement_is_proved_and_candidate_eliminates_conflict(self):
        command = "按分类字段聚合线要素"
        context = {"is_saved": True, "layers": [{"layer_ref": "layer:source_lines", "name": "source_lines", "geometry_type": "Polyline", "spatial_reference": "EPSG:3857", "fields": [{"name": "category"}]}]}
        task = self._task(command, "grouped_lines", ["category"], "polyline")
        task["input_entities"] = [{"entity_id": "aggregate_source", "role": "source", "kind": "feature_layer", "reference": "layer:source_lines", "evidence": command}]
        task["requirements"] = [{"requirement_id": "r_group", "predicate": {"kind": "aggregate", "subject": "o1", "source": "aggregate_source", "dissolve_fields": ["category"]}, "evidence": command}]
        wrong = {"action": "execute", "summary": command, "steps": [{"id": "aggregate", "operation": "analysis.dissolve", "arguments": {"input_layer": "source_lines", "output_name": "grouped_lines", "dissolve_fields": ["other_category"]}, "reason": command}]}
        correct = copy.deepcopy(wrong)
        correct["steps"][0]["arguments"]["dissolve_fields"] = ["category"]
        baseline = self.verifier.verify(wrong, context, task)
        candidate = self.verifier.verify(correct, context, task)
        self.assertEqual("requirement.semantic_conflict", baseline["hard_violations"][-1]["code"])
        self.assertTrue(candidate["requirements"][0]["satisfied"])
        proof = candidate["requirements"][0]["proof"]
        self.assertEqual("aggregate", proof["semantic_fact"]["kind"])
        self.assertTrue(DominanceGate().admit({"baseline_verifier_report": baseline}, candidate, [{"claim": {"proof_id": baseline["hard_violations"][-1]["violation_id"]}}])["accepted"])

    def test_spatial_join_roles_are_not_interchangeable(self):
        command = "连接目标与连接图层"
        context = {"is_saved": True, "layers": [
            {"layer_ref": "layer:linear_source", "name": "linear_source", "geometry_type": "Polyline", "spatial_reference": "EPSG:3857", "fields": [{"name": "source_id"}]},
            {"layer_ref": "layer:area_lookup", "name": "area_lookup", "geometry_type": "Polygon", "spatial_reference": "EPSG:3857", "fields": [{"name": "lookup_id"}]},
        ]}
        task = self._task(command, "joined_result", ["source_id", "Join_Count"], "polyline")
        task["input_entities"] = [
            {"entity_id": "target_entity", "role": "target", "kind": "feature_layer", "reference": "layer:linear_source", "evidence": command},
            {"entity_id": "join_entity", "role": "join", "kind": "feature_layer", "reference": "layer:area_lookup", "evidence": command},
        ]
        task["requirements"] = [{"requirement_id": "r_join", "predicate": {"kind": "spatial_join", "subject": "o1", "target": "target_entity", "join": "join_entity"}, "evidence": command}]
        wrong = {"action": "execute", "summary": command, "steps": [{"id": "join", "operation": "analysis.spatial_join", "arguments": {"target_layer": "area_lookup", "join_layer": "linear_source", "output_name": "joined_result"}, "reason": command}]}
        correct = copy.deepcopy(wrong)
        correct["steps"][0]["arguments"].update({"target_layer": "linear_source", "join_layer": "area_lookup"})
        baseline, candidate = self.verifier.verify(wrong, context, task), self.verifier.verify(correct, context, task)
        violation = [item for item in baseline["hard_violations"] if item["code"] == "requirement.semantic_conflict"][0]
        self.assertTrue(candidate["requirements"][0]["satisfied"])
        self.assertTrue(DominanceGate().admit({"baseline_verifier_report": baseline}, candidate, [{"claim": {"proof_id": violation["violation_id"]}}])["accepted"])

    def test_buffer_uses_context_canonical_reference_and_normalized_distance(self):
        command = "buffer 10 meters"
        context = {"is_saved": True, "layers": [{"layer_ref": "layer:src-42", "name": "alpha_name", "geometry_type": "Polyline", "spatial_reference": "EPSG:3857", "fields": []}]}
        task = self._task(command, "buf", [], "polygon")
        task["input_entities"] = [{"entity_id": "entity_A", "role": "source", "kind": "feature_layer", "reference": "alpha_name", "evidence": command}]
        task["requirements"] = [{"requirement_id": "buffer", "predicate": {"kind": "buffer", "subject": "o1", "source": "entity_A", "distance": {"value": 10, "unit": "meters"}}, "evidence": command}]
        workflow = {"action": "execute", "summary": command, "steps": [{"id": "buffer", "operation": "analysis.buffer", "arguments": {"input_layer": "alpha_name", "distance": "10 Meters", "output_name": "buf"}, "reason": command}]}
        report = self.verifier.verify(workflow, context, task)
        self.assertTrue(report["ok"])
        proof = report["requirements"][0]["proof"]["semantic_fact"]
        self.assertEqual("layer:src-42", proof["source"])
        self.assertEqual({"value": 10.0, "unit": "meters"}, proof["distance"])

    def test_overlay_compiles_a_list_parameter_to_a_flat_source_set(self):
        command = "相交道路与分区"
        task = self._task(command, "roads_in_zones", [], "polyline")
        task["input_entities"] = [
            {"entity_id": "roads", "role": "source", "kind": "feature_layer", "reference": "layer:roads", "evidence": command},
            {"entity_id": "zones", "role": "source", "kind": "feature_layer", "reference": "layer:zones", "evidence": command},
        ]
        task["requirements"] = [{
            "requirement_id": "intersect",
            "predicate": {"kind": "overlay", "subject": "o1", "sources": ["roads", "zones"], "method": "intersect"},
            "evidence": command,
        }]
        workflow = {"action": "execute", "summary": command, "steps": [{
            "id": "intersect", "operation": "analysis.intersect",
            "arguments": {"input_layers": ["roads", "zones"], "output_name": "roads_in_zones"},
            "reason": command,
        }]}

        report = self.verifier.verify(workflow, CONTEXT, task)

        self.assertTrue(report["requirements"][0]["satisfied"], report)
        self.assertEqual(
            ["layer:roads", "layer:zones"],
            report["requirements"][0]["proof"]["semantic_fact"]["sources"],
        )

    def test_intersect_geometry_uses_lowest_input_dimension_regardless_of_order(self):
        command = "相交道路与分区"
        for input_entities, input_layers in (
            (("zones", "roads"), ("zones", "roads")),
            (("roads", "zones"), ("roads", "zones")),
        ):
            with self.subTest(input_layers=input_layers):
                task = self._task(command, "roads_in_zones", [], "polyline")
                task["input_entities"] = [
                    {
                        "entity_id": entity,
                        "role": "source",
                        "kind": "feature_layer",
                        "reference": "layer:" + entity,
                        "evidence": command,
                    }
                    for entity in input_entities
                ]
                task["requirements"] = [{
                    "requirement_id": "intersect",
                    "predicate": {
                        "kind": "overlay",
                        "subject": "o1",
                        "sources": list(input_entities),
                        "method": "intersect",
                    },
                    "evidence": command,
                }]
                workflow = {
                    "action": "execute",
                    "summary": command,
                    "steps": [{
                        "id": "intersect",
                        "operation": "analysis.intersect",
                        "arguments": {
                            "input_layers": list(input_layers),
                            "output_name": "roads_in_zones",
                        },
                        "reason": command,
                    }],
                }

                report = self.verifier.verify(workflow, CONTEXT, task)

                self.assertTrue(report["ok"], report)

    def test_identity_binds_geometry_and_fields_to_their_declared_sources(self):
        command = "把分区属性标识到道路结果"
        task = self._task(command, "roads_identity", ["RID", "ZID"], "polyline")
        task["input_entities"] = [
            {
                "entity_id": "roads",
                "role": "source",
                "kind": "feature_layer",
                "reference": "layer:roads",
                "evidence": command,
            },
            {
                "entity_id": "zones",
                "role": "source",
                "kind": "feature_layer",
                "reference": "layer:zones",
                "evidence": command,
            },
        ]
        task["requirements"] = [{
            "requirement_id": "identity",
            "predicate": {
                "kind": "overlay",
                "subject": "o1",
                "sources": ["roads", "zones"],
                "method": "identity",
            },
            "evidence": command,
        }]
        workflow = {
            "action": "execute",
            "summary": command,
            "steps": [{
                "id": "identity",
                "operation": "analysis.identity",
                "arguments": {
                    "input_layer": "roads",
                    "identity_layer": "zones",
                    "output_name": "roads_identity",
                },
                "reason": command,
            }],
        }

        report = self.verifier.verify(workflow, CONTEXT, task)

        self.assertTrue(report["ok"], report)
        output = report["facts"][0]["output"]
        self.assertEqual("polyline", output["geometry"])
        self.assertEqual(["CLASS", "RID", "ZID"], output["fields"])
