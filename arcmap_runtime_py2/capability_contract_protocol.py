# -*- coding: utf-8 -*-
"""Shared closed rules for resolving parameter-dependent capability outputs."""
from __future__ import absolute_import


OUTPUT_CARDINALITIES = frozenset((
    "not_applicable", "one", "one_snapshot", "one_per_input_feature",
    "one_or_more_per_input_feature", "one_per_target_feature",
    "one_per_aggregate_group", "selected_feature_count",
    "one_per_distinct_field_value", "in_place", "reduced",
))

_GEOMETRY_DIMENSIONS = {
    "point": 0,
    "polyline": 1,
    "polygon": 2,
}

CARDINALITY_DESCRIPTOR_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["rule", "value"],
            "properties": {
                "rule": {"const": "fixed"},
                "value": {"enum": sorted(OUTPUT_CARDINALITIES)},
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["rule", "parameter", "when_empty", "otherwise"],
            "properties": {
                "rule": {"const": "parameter_array_empty"},
                "parameter": {"type": "string", "minLength": 1},
                "when_empty": {"enum": sorted(OUTPUT_CARDINALITIES)},
                "otherwise": {"enum": sorted(OUTPUT_CARDINALITIES)},
            },
        },
    ],
}


def _raise(error_type, path, message):
    raise error_type("%s %s" % (path, message))


def validate_output_cardinality(descriptor, parameters_schema, error_type=ValueError,
                                path="outputs.cardinality"):
    if not isinstance(descriptor, dict):
        _raise(error_type, path, "must be a closed descriptor object.")
    rule = descriptor.get("rule")
    if rule == "fixed":
        if set(descriptor) != set(("rule", "value")):
            _raise(error_type, path, "fixed rule has invalid fields.")
        if descriptor.get("value") not in OUTPUT_CARDINALITIES:
            _raise(error_type, path, "fixed value is invalid.")
        return descriptor
    if rule != "parameter_array_empty":
        _raise(error_type, path, "rule is invalid.")
    if set(descriptor) != set(("rule", "parameter", "when_empty", "otherwise")):
        _raise(error_type, path, "parameter_array_empty rule has invalid fields.")
    parameter = descriptor.get("parameter")
    properties = (parameters_schema or {}).get("properties") or {}
    parameter_schema = properties.get(parameter)
    if not isinstance(parameter_schema, dict) or parameter_schema.get("type") != "array":
        _raise(error_type, path, "parameter must bind an executable array parameter.")
    required = (parameters_schema or {}).get("required") or []
    if parameter not in required and "default" not in parameter_schema:
        _raise(error_type, path, "optional parameter requires an executable default.")
    if "default" in parameter_schema and not isinstance(parameter_schema["default"], list):
        _raise(error_type, path, "parameter default must be an array.")
    for key in ("when_empty", "otherwise"):
        if descriptor.get(key) not in OUTPUT_CARDINALITIES:
            _raise(error_type, path, "%s value is invalid." % key)
    if descriptor["when_empty"] == descriptor["otherwise"]:
        _raise(error_type, path, "branches must describe different cardinalities.")
    return descriptor


def resolve_output_cardinality(descriptor, arguments, parameters_schema,
                               error_type=ValueError, path="outputs.cardinality"):
    validate_output_cardinality(descriptor, parameters_schema, error_type, path)
    if descriptor["rule"] == "fixed":
        return descriptor["value"]
    parameter = descriptor["parameter"]
    if parameter in arguments:
        value = arguments[parameter]
    else:
        value = parameters_schema["properties"][parameter].get("default")
    if not isinstance(value, list):
        _raise(error_type, path, "bound parameter value must be an array.")
    return descriptor["when_empty"] if not value else descriptor["otherwise"]


def resolve_lowest_dimension_geometry(geometries, error_type=ValueError,
                                      path="outputs.geometry"):
    """Resolve ArcGIS overlay geometry from the lowest input dimension."""
    if not isinstance(geometries, (list, tuple)) or not geometries:
        _raise(error_type, path, "requires at least one observed input geometry.")
    normalized = []
    for geometry in geometries:
        try:
            value = unicode(geometry).strip().lower()
        except NameError:
            value = str(geometry).strip().lower()
        if value not in _GEOMETRY_DIMENSIONS:
            _raise(error_type, path, "contains an unsupported input geometry.")
        normalized.append(value)
    return min(normalized, key=lambda value: _GEOMETRY_DIMENSIONS[value])
