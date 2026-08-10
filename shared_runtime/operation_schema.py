# -*- coding: utf-8 -*-
from __future__ import absolute_import


class OperationSchemaError(Exception):
    pass


try:
    _STRING_TYPES = (basestring,)
except NameError:
    _STRING_TYPES = (str,)


def validate_parameter_schema(schema):
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise OperationSchemaError("parameters_schema must be a JSON Schema object.")
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise OperationSchemaError("parameters_schema must declare properties and required.")
    if schema.get("additionalProperties") is not False:
        raise OperationSchemaError("parameters_schema.additionalProperties must be false.")
    if any(not isinstance(name, _STRING_TYPES) for name in required):
        raise OperationSchemaError("parameters_schema.required must contain only strings.")
    unknown_required = sorted(set(required) - set(properties))
    if unknown_required:
        raise OperationSchemaError(
            "parameters_schema.required contains unknown fields: %s."
            % ", ".join(unknown_required)
        )
    for name, property_schema in properties.items():
        if not isinstance(name, _STRING_TYPES) or not isinstance(property_schema, dict):
            raise OperationSchemaError("parameter schemas must be named objects.")
        kind = property_schema.get("x-geopilot-kind")
        if kind is not None and kind not in ("layer", "path"):
            raise OperationSchemaError("x-geopilot-kind must be layer or path.")
    return schema
