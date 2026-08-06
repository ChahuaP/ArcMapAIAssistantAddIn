"""Closed, executable capability contracts for every GeoPilot operation."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict
from arcmap_runtime_py2.capability_contract_protocol import (
    CARDINALITY_DESCRIPTOR_SCHEMA,
    validate_output_cardinality,
)
from .semantic_domain import validate_capability_effect, effect_schema


class CapabilityContractError(ValueError):
    pass


_CONTRACT_KEYS = {
    "inputs", "parameters_schema", "outputs", "semantic_effects", "side_effects", "authorization", "postconditions",
}
_INPUT_KEYS = {"parameter", "cardinality", "data_kind", "geometry", "required_fields", "selection"}
_OUTPUT_KEYS = {"kind", "geometry", "fields", "spatial_reference", "cardinality", "selection_state", "map_publication"}
_GEOMETRY_KEYS = {"rule", "value"}
_FIELDS_TARGET_KEYS = {"effect", "target", "static_fields", "parameter_field"}
_FIELDS_SOURCE_KEYS = {"effect", "sources", "static_fields", "parameter_field"}
_SPATIAL_REFERENCE_KEYS = {"rule", "input"}
_AUTHORIZATION_KEYS = {"required", "scope"}
_POSTCONDITION_KEYS = {"kind", "target", "expectation"}
_EXPECTATION_KEYS = {"kind", "geometry", "fields", "spatial_reference", "cardinality", "selection_state", "map_publication"}

_SELECTION_REQUIREMENT_SCHEMA = {
    "oneOf": [
        {
            "type": "object", "additionalProperties": False, "required": ["rule"],
            "properties": {"rule": {"enum": ["any", "requires_selected"]}},
        },
        {
            "type": "object", "additionalProperties": False,
            "required": ["rule", "parameter", "values"],
            "properties": {
                "rule": {"const": "parameter_values_require_selected"},
                "parameter": {"type": "string", "minLength": 1},
                "values": {"type": "array", "minItems": 1, "uniqueItems": True},
            },
        },
    ],
}

# Shared by built-in and custom-operation ingress.  Runtime registration below
# remains the authoritative semantic validation; this schema rejects incomplete
# contracts before they can enter a draft.
CAPABILITY_CONTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": sorted(_CONTRACT_KEYS),
    "properties": {
        "inputs": {"type": "array", "items": {"type":"object", "additionalProperties":False, "required":sorted(_INPUT_KEYS), "properties": {"parameter":{"type":"string"}, "cardinality":{"enum":["one","many"]}, "data_kind":{"type":"array"}, "geometry":{"type":"array"}, "required_fields":{"type":"array"}, "selection":_SELECTION_REQUIREMENT_SCHEMA}}}, "parameters_schema": {"type": "object"},
        "outputs": {"type":"object", "additionalProperties":False, "required":sorted(_OUTPUT_KEYS), "properties":{"kind":{"type":"string"}, "geometry":{"type":"object", "additionalProperties":False, "required":["rule","value"], "properties":{"rule":{"type":"string"},"value":{"type":"string"}}}, "fields":{"type":"object", "additionalProperties":False, "required":["effect","static_fields","parameter_field"], "properties":{"effect":{"type":"string"},"target":{"type":"string"},"sources":{"type":"array","minItems":1,"uniqueItems":True,"items":{"type":"string","minLength":1}},"static_fields":{"type":"array"},"parameter_field":{"type":"string"}}, "oneOf":[{"required":["target"],"not":{"required":["sources"]}},{"required":["sources"],"not":{"required":["target"]}}]}, "spatial_reference":{"type":"object", "additionalProperties":False, "required":["rule","input"], "properties":{"rule":{"type":"string"},"input":{"type":"string"}}}, "cardinality":CARDINALITY_DESCRIPTOR_SCHEMA,"selection_state":{"type":"string"},"map_publication":{"type":"string"}}}, "semantic_effects": {"type": "array", "minItems": 1, "items": effect_schema()},
        "side_effects": {"type": "string", "enum": ["read_only", "changes_map", "writes_data", "edits_data"]},
        "authorization": {"type": "object", "required": ["required", "scope"], "additionalProperties": False,
                          "properties": {"required": {"type": "boolean"}, "scope": {"type": "string"}}},
        "postconditions": {"type":"array", "minItems":1, "items":{"type":"object", "additionalProperties":False, "required":sorted(_POSTCONDITION_KEYS), "properties":{"kind":{"type":"string"},"target":{"type":"string"},"expectation":{"type":"object", "additionalProperties":False, "required":sorted(_EXPECTATION_KEYS), "properties":{key:{"type":"object", "additionalProperties":False, "required":["ref"], "properties":{"ref":{"const":"outputs."+key}}} for key in _EXPECTATION_KEYS}}}}},
    },
}

_CARDINALITIES = {"one", "many"}
_DATA_KINDS = {"feature_layer", "raster_layer", "table_view", "coordinate_sequence", "feature_definition"}
_GEOMETRIES = {"point", "polyline", "polygon", "raster", "not_applicable"}
_SELECTION_RULES = {"any", "requires_selected", "parameter_values_require_selected"}
_OUTPUT_KINDS = {"none", "map_state", "file", "file_collection", "feature_class", "raster", "table"}
_GEOMETRY_RULES = {"fixed", "inherit", "lowest_dimension", "not_applicable"}
_FIELD_EFFECTS = {"not_applicable", "inherit_input", "inherit_tabular_fields", "inherit_target_merge_join", "merge_inputs", "aggregate_by_parameter_fields", "static_generated", "add_static_fields", "add_parameter_field", "delete_parameter_field", "in_place_update"}
_SPATIAL_REFERENCE_RULES = {"inherit", "from_parameter", "from_parameter_or_map", "not_applicable"}
_SELECTION_OUTPUTS = {"not_applicable", "selection_preserved", "applied", "cleared"}
_PUBLICATIONS = {"none", "published", "map_state_updated"}
_SIDE_EFFECTS = {"read_only", "changes_map", "writes_data", "edits_data"}
_AUTHORIZATION_SCOPES = {"none", "read_current_map", "modify_map", "write_data", "write_file", "edit_data"}


def _exact(value: Any, expected: set[str], path: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise CapabilityContractError("%s must have exactly: %s." % (path, ", ".join(sorted(expected))))
    return value


def _enum(value: Any, values: set[str], path: str) -> None:
    if value not in values:
        raise CapabilityContractError("%s is invalid: %r." % (path, value))


class CapabilityRegistry:
    def __init__(self, operations):
        self._operations: Dict[str, Dict[str, Any]] = {}
        for operation in operations:
            self._register(operation)

    def _register(self, operation: Dict[str, Any]) -> None:
        operation_id = operation.get("id", "<missing>")
        contract = _exact(operation.get("capability_contract"), _CONTRACT_KEYS, operation_id + ".capability_contract")
        if contract["parameters_schema"] != operation.get("parameters_schema"):
            raise CapabilityContractError("%s contract parameters_schema diverges from the executable schema." % operation_id)
        if contract["side_effects"] != operation.get("side_effects"):
            raise CapabilityContractError("%s contract side_effects diverges from the executable specification." % operation_id)
        _enum(contract["side_effects"], _SIDE_EFFECTS, operation_id + ".side_effects")
        self._validate_inputs(contract["inputs"], operation_id, operation["parameters_schema"])
        self._validate_outputs(
            contract["outputs"], operation_id, operation["parameters_schema"],
            {item["parameter"]: item for item in contract["inputs"]},
        )
        output_format = self._derive_output_format(operation)
        self._validate_semantic_effects(contract["semantic_effects"], operation_id, operation["parameters_schema"], contract["outputs"], output_format)
        authorization = _exact(contract["authorization"], _AUTHORIZATION_KEYS, operation_id + ".authorization")
        if not isinstance(authorization["required"], bool):
            raise CapabilityContractError(operation_id + ".authorization.required must be boolean.")
        _enum(authorization["scope"], _AUTHORIZATION_SCOPES, operation_id + ".authorization.scope")
        self._validate_postconditions(
            contract["postconditions"],
            operation_id,
            set(operation["parameters_schema"].get("properties", {})),
            contract["outputs"],
        )
        registered = deepcopy(contract)
        registered["outputs"]["format"] = output_format
        self._operations[operation_id] = registered

    @staticmethod
    def _derive_output_format(operation: Dict[str, Any]) -> Dict[str, Any]:
        """Close the executable output policy into one deterministic format fact."""
        operation_id = operation["id"]
        output = operation["capability_contract"]["outputs"]
        if output["kind"] == "none":
            return {"rule": "not_applicable", "value": "not_applicable"}
        if output["kind"] == "map_state":
            return {"rule": "fixed", "value": "map"}
        policy = operation.get("output_policy")
        if not isinstance(policy, dict):
            raise CapabilityContractError(operation_id + ".output_policy must describe its declared output.")
        properties = operation["parameters_schema"].get("properties", {})
        if "output_format" in properties:
            schema = properties["output_format"]
            default = schema.get("default") or policy.get("default_format")
            if not isinstance(default, str) or not default:
                raise CapabilityContractError(operation_id + ".output_format requires one executable default.")
            allowed = schema.get("enum")
            if not isinstance(allowed, list) or default not in allowed:
                raise CapabilityContractError(operation_id + ".output_format default must belong to its enum.")
            return {"rule": "from_parameter", "parameter": "output_format", "default": default}
        extension = policy.get("extension")
        if isinstance(extension, str) and extension.startswith(".") and len(extension) > 1:
            return {"rule": "fixed", "value": extension[1:].lower()}
        raise CapabilityContractError(operation_id + ".output_policy cannot prove an exact output format.")

    @staticmethod
    def _validate_semantic_effects(effects: Any, operation_id: str, parameters: Dict[str, Any], outputs: Dict[str, Any], output_format: Dict[str, Any]) -> None:
        if not isinstance(effects, list) or not effects:
            raise CapabilityContractError(operation_id + ".semantic_effects must be a non-empty array.")
        required = set(parameters.get("required", []))
        properties = parameters.get("properties", {})
        for index, effect in enumerate(effects):
            validate_capability_effect(effect, parameters, outputs["kind"], "%s.semantic_effects[%d]" % (operation_id, index), CapabilityContractError)
            if effect["kind"] == "artifact_export" and output_format["rule"] != "not_applicable":
                expected = ({"const": output_format["value"]} if output_format["rule"] == "fixed"
                            else {"parameter": output_format["parameter"]})
                if effect.get("output_format") != expected:
                    raise CapabilityContractError(
                        "%s.semantic_effects[%d].output_format must bind the exact executable output format."
                        % (operation_id, index)
                    )
            for parameter in CapabilityRegistry._semantic_parameters(effect):
                if parameter not in required and "default" not in properties.get(parameter, {}):
                    raise CapabilityContractError(
                        "%s.semantic_effects[%d] optional parameter %s requires an executable default."
                        % (operation_id, index, parameter)
                    )

    @staticmethod
    def _semantic_parameters(value: Any) -> set[str]:
        if isinstance(value, dict):
            if set(value) == {"parameter"} and isinstance(value["parameter"], str):
                return {value["parameter"]}
            result = set()
            for item in value.values():
                result.update(CapabilityRegistry._semantic_parameters(item))
            return result
        if isinstance(value, list):
            result = set()
            for item in value:
                result.update(CapabilityRegistry._semantic_parameters(item))
            return result
        return set()

    @staticmethod
    def _validate_inputs(inputs: Any, operation_id: str, parameters_schema: Dict[str, Any]) -> None:
        if not isinstance(inputs, list):
            raise CapabilityContractError(operation_id + ".inputs must be an array.")
        parameter_specs = parameters_schema.get("properties", {})
        parameters = set(parameter_specs)
        seen = set()
        for index, item in enumerate(inputs):
            item = _exact(item, _INPUT_KEYS, "%s.inputs[%d]" % (operation_id, index))
            if not isinstance(item["parameter"], str) or not item["parameter"] or item["parameter"] in seen:
                raise CapabilityContractError("%s.inputs[%d].parameter must be unique and non-empty." % (operation_id, index))
            seen.add(item["parameter"])
            if item["parameter"] not in parameters:
                raise CapabilityContractError("%s.inputs[%d].parameter is not executable." % (operation_id, index))
            _enum(item["cardinality"], _CARDINALITIES, "%s.inputs[%d].cardinality" % (operation_id, index))
            for key, allowed in (("data_kind", _DATA_KINDS), ("geometry", _GEOMETRIES)):
                values = item[key]
                if not isinstance(values, list) or not values or any(value not in allowed for value in values):
                    raise CapabilityContractError("%s.inputs[%d].%s is invalid." % (operation_id, index, key))
            selection = item["selection"]
            if not isinstance(selection, dict):
                raise CapabilityContractError("%s.inputs[%d].selection must be an object." % (operation_id, index))
            rule = selection.get("rule")
            _enum(rule, _SELECTION_RULES, "%s.inputs[%d].selection.rule" % (operation_id, index))
            expected_keys = ({"rule", "parameter", "values"}
                             if rule == "parameter_values_require_selected" else {"rule"})
            _exact(selection, expected_keys, "%s.inputs[%d].selection" % (operation_id, index))
            if rule == "parameter_values_require_selected":
                parameter = selection["parameter"]
                parameter_spec = parameter_specs.get(parameter)
                values = selection["values"]
                if not isinstance(parameter_spec, dict) or not isinstance(values, list) or not values:
                    raise CapabilityContractError(
                        "%s.inputs[%d].selection must bind an executable parameter and non-empty values."
                        % (operation_id, index)
                    )
                allowed = parameter_spec.get("enum")
                if isinstance(allowed, list) and any(value not in allowed for value in values):
                    raise CapabilityContractError(
                        "%s.inputs[%d].selection.values must belong to the parameter enum."
                        % (operation_id, index)
                    )
                expected_type = parameter_spec.get("type")
                if expected_type == "boolean" and any(not isinstance(value, bool) for value in values):
                    raise CapabilityContractError(
                        "%s.inputs[%d].selection.values must match the boolean parameter."
                        % (operation_id, index)
                    )
                if expected_type == "string" and any(not isinstance(value, str) for value in values):
                    raise CapabilityContractError(
                        "%s.inputs[%d].selection.values must match the string parameter."
                        % (operation_id, index)
                    )
            if not isinstance(item["required_fields"], list) or any(not isinstance(value, str) for value in item["required_fields"]):
                raise CapabilityContractError("%s.inputs[%d].required_fields is invalid." % (operation_id, index))

    @staticmethod
    def _validate_outputs(outputs: Any, operation_id: str, parameters_schema: Dict[str, Any], input_specs: Dict[str, Dict[str, Any]]) -> None:
        outputs = _exact(outputs, _OUTPUT_KEYS, operation_id + ".outputs")
        parameters = set(parameters_schema.get("properties", {}))
        input_parameters = set(input_specs)
        _enum(outputs["kind"], _OUTPUT_KINDS, operation_id + ".outputs.kind")
        geometry = _exact(outputs["geometry"], _GEOMETRY_KEYS, operation_id + ".outputs.geometry")
        _enum(geometry["rule"], _GEOMETRY_RULES, operation_id + ".outputs.geometry.rule")
        if not isinstance(geometry["value"], str) or not geometry["value"]:
            raise CapabilityContractError(operation_id + ".outputs.geometry.value is invalid.")
        if geometry["rule"] in {"inherit", "lowest_dimension"} and geometry["value"] not in input_parameters:
            raise CapabilityContractError(operation_id + ".outputs.geometry.value must bind an input parameter.")
        if geometry["rule"] == "lowest_dimension":
            source = input_specs[geometry["value"]]
            if source["cardinality"] != "many":
                raise CapabilityContractError(operation_id + ".outputs.geometry.value must bind a many-valued input parameter.")
            if any(value not in {"point", "polyline", "polygon"} for value in source["geometry"]):
                raise CapabilityContractError(operation_id + ".outputs.geometry.value contains an unsupported topological geometry.")
        if geometry["rule"] == "not_applicable" and geometry["value"] != "not_applicable":
            raise CapabilityContractError(operation_id + ".outputs.geometry.value must use not_applicable.")
        raw_fields = outputs["fields"]
        if not isinstance(raw_fields, dict):
            raise CapabilityContractError(operation_id + ".outputs.fields must be an object.")
        _enum(raw_fields.get("effect"), _FIELD_EFFECTS, operation_id + ".outputs.fields.effect")
        field_keys = _FIELDS_SOURCE_KEYS if raw_fields["effect"] == "merge_inputs" else _FIELDS_TARGET_KEYS
        fields = _exact(raw_fields, field_keys, operation_id + ".outputs.fields")
        if not isinstance(fields["parameter_field"], str) or not fields["parameter_field"] or not isinstance(fields["static_fields"], list):
            raise CapabilityContractError(operation_id + ".outputs.fields is invalid.")
        if fields["effect"] == "merge_inputs":
            sources = fields["sources"]
            if (not isinstance(sources, list) or not sources or len(set(sources)) != len(sources)
                    or any(not isinstance(value, str) or value not in input_parameters for value in sources)):
                raise CapabilityContractError(operation_id + ".outputs.fields.sources must bind unique input parameters.")
        elif not isinstance(fields["target"], str) or not fields["target"]:
            raise CapabilityContractError(operation_id + ".outputs.fields.target is invalid.")
        if fields["effect"] in {"inherit_input", "inherit_tabular_fields", "inherit_target_merge_join", "in_place_update", "add_static_fields", "add_parameter_field", "delete_parameter_field"} and fields["target"] not in input_parameters:
            raise CapabilityContractError(operation_id + ".outputs.fields.target must bind an input parameter.")
        if fields["effect"] in {"add_parameter_field", "delete_parameter_field"} and fields["parameter_field"] not in parameters:
            raise CapabilityContractError(operation_id + ".outputs.fields.parameter_field must bind a parameter.")
        if fields["effect"] == "not_applicable" and (fields["target"] != "not_applicable" or fields["parameter_field"] != "not_applicable"):
            raise CapabilityContractError(operation_id + ".outputs.fields must use not_applicable sentinels.")
        spatial = _exact(outputs["spatial_reference"], _SPATIAL_REFERENCE_KEYS, operation_id + ".outputs.spatial_reference")
        _enum(spatial["rule"], _SPATIAL_REFERENCE_RULES, operation_id + ".outputs.spatial_reference.rule")
        if not isinstance(spatial["input"], str) or not spatial["input"]:
            raise CapabilityContractError(operation_id + ".outputs.spatial_reference.input is invalid.")
        if spatial["rule"] == "inherit" and spatial["input"] not in input_parameters:
            raise CapabilityContractError(operation_id + ".outputs.spatial_reference.input must bind an input parameter.")
        if spatial["rule"] in {"from_parameter", "from_parameter_or_map"} and spatial["input"] not in parameters:
            raise CapabilityContractError(operation_id + ".outputs.spatial_reference.input must bind a parameter.")
        if spatial["rule"] == "not_applicable" and spatial["input"] != "not_applicable":
            raise CapabilityContractError(operation_id + ".outputs.spatial_reference.input must use not_applicable.")
        validate_output_cardinality(
            outputs["cardinality"], parameters_schema, CapabilityContractError,
            operation_id + ".outputs.cardinality",
        )
        _enum(outputs["selection_state"], _SELECTION_OUTPUTS, operation_id + ".outputs.selection_state")
        _enum(outputs["map_publication"], _PUBLICATIONS, operation_id + ".outputs.map_publication")

    @staticmethod
    def _validate_postconditions(postconditions: Any, operation_id: str, parameters: set[str], outputs: Dict[str, Any]) -> None:
        if not isinstance(postconditions, list) or not postconditions:
            raise CapabilityContractError(operation_id + ".postconditions must be a non-empty array.")
        for index, condition in enumerate(postconditions):
            condition = _exact(condition, _POSTCONDITION_KEYS, "%s.postconditions[%d]" % (operation_id, index))
            if not isinstance(condition["kind"], str) or not condition["kind"] or not isinstance(condition["target"], str) or not condition["target"]:
                raise CapabilityContractError("%s.postconditions[%d] is invalid." % (operation_id, index))
            if condition["target"] not in parameters and condition["target"] != "map":
                raise CapabilityContractError("%s.postconditions[%d].target cannot be resolved." % (operation_id, index))
            expectation = _exact(condition["expectation"], _EXPECTATION_KEYS, "%s.postconditions[%d].expectation" % (operation_id, index))
            for key, reference in expectation.items():
                if reference != {"ref": "outputs." + key}:
                    raise CapabilityContractError("%s.postconditions[%d].expectation.%s must reference outputs.%s." % (operation_id, index, key, key))

    def get(self, capability_id: str) -> Dict[str, Any]:
        if capability_id not in self._operations:
            raise CapabilityContractError("unknown capability: %s" % capability_id)
        return self._operations[capability_id]

    def planning_card(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        contract = self.get(operation["id"])
        return {"id": operation["id"], "summary": operation["summary"], "examples": operation.get("examples", [])[:2], **contract}

