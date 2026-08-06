"""The single closed vocabulary for task predicates and capability effects.

This module deliberately owns both shapes.  Callers may only supply entity
identifiers (tasks) or explicit binding objects (capabilities); neither layer
is allowed to maintain a shadow table of semantic fields.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from arcmap_runtime_py2.condition_protocol import (
    LOGICAL_CONDITION_OPERATORS,
    normalize_condition_tree,
)

DISTANCE_UNITS = frozenset(("meters", "kilometers", "map_units", "degrees"))
SELECTION_TYPES = frozenset(("new_selection", "add_to_selection", "remove_from_selection", "select_subset"))
ARCMAP_SELECTION_TYPES = {
    "NEW_SELECTION": "new_selection",
    "ADD_TO_SELECTION": "add_to_selection",
    "REMOVE_FROM_SELECTION": "remove_from_selection",
    "SUBSET_SELECTION": "select_subset",
}
OVERLAP_TYPES = frozenset(("intersect", "contain", "within", "touch", "overlap", "cross", "within_a_distance"))
ARTIFACT_EXPORT_ACTIONS = frozenset((
    "export_pdf", "export_png", "export_selected_features", "layer_kml",
    "map_pdf", "map_png", "split_by_field", "table_csv", "write_file",
))

# Required fields are intentionally variant-specific where an operation has
# genuinely different behaviour.  `action` prevents map/layout/export facts
# from collapsing into a subject-only assertion.
_SPECS = {
 "inspect": (({"subject"}, {}), ({"subject", "target"}, {})),
 "source_preserved": (({"subject"}, {}),),
 "map_change": (({"subject", "action"}, {"action": "string"}), ({"subject", "target", "action"}, {"action": "string"})),
 "layout_change": (({"subject", "action"}, {"action": "string"}),),
 "attribute_filter": (({"subject", "where", "selection_type"}, {"where": "condition", "selection_type": "selection"}), ({"subject", "target", "where", "selection_type"}, {"where": "condition", "selection_type": "selection"})),
 "spatial_filter": (({"subject", "target", "selector", "overlap_type", "search_distance", "selection_type"}, {"overlap_type":"overlap", "search_distance":"distance_or_null", "selection_type":"selection"}),),
 "buffer": (({"subject", "source", "distance"}, {"distance":"distance"}),),
 "overlay": (({"subject", "sources", "method"}, {"sources":"entities", "method":"string"}),),
 "spatial_join": (({"subject", "target", "join"}, {}),),
 "aggregate": (({"subject", "source", "dissolve_fields"}, {"dissolve_fields":"strings"}),),
 "project": (({"subject", "source", "spatial_reference"}, {"spatial_reference":"string"}),),
 "merge": (({"subject", "sources"}, {"sources":"entities"}),),
 "append": (({"subject", "sources", "target", "schema_type"}, {"sources":"entities", "schema_type":"string"}),),
 "field_add": (({"subject", "target", "field_name", "field_type", "field_length"}, {"field_name":"string", "field_type":"string", "field_length":"integer_or_null"}),),
 "field_delete": (({"subject", "target", "field_name"}, {"field_name":"string"}),),
 "field_update": (({"subject", "target", "where", "assignments"}, {"where":"condition", "assignments":"object_or_null"}), ({"subject", "target", "where"}, {"where":"condition"})),
 "feature_create": (({"subject", "action"}, {"action":"string"}),),
 "feature_append": (({"subject", "target", "action"}, {"action":"string"}),),
 "copy": (({"subject", "source"}, {}),), "repair": (({"subject", "target"}, {}),),
 "define_projection": (({"subject", "target", "spatial_reference"}, {"spatial_reference":"string"}),),
 "add_xy": (({"subject", "target"}, {}),),
 "artifact_export": (
     ({"subject", "action"}, {"action":"artifact_export_action"}),
     ({"subject", "action", "output_format"}, {"action":"artifact_export_action", "output_format":"string"}),
     ({"subject", "action", "target", "selected_only"}, {"action":"artifact_export_action", "selected_only":"boolean"}),
     ({"subject", "action", "target", "selected_only", "output_format"}, {"action":"artifact_export_action", "selected_only":"boolean", "output_format":"string"}),
 ),
}
# A task predicate describes a user-visible obligation.  `inspect.target` in a
# capability is an ArcMap layer binding, whereas natural-language planners
# repeatedly put a field name there (for example `Join_Count`).  Those are
# different domains.  Keep the capability form for catalogue validation, but
# do not expose an entity-shaped target slot in the task grammar.
_TASK_SPECS = dict(_SPECS)
_TASK_SPECS.pop("inspect")
_TASK_SPECS["spatial_filter"] = (
    (
        {"subject", "target", "selector", "overlap_type", "selection_type"},
        {"overlap_type": "overlap_without_distance", "selection_type": "selection"},
    ),
    (
        {"subject", "target", "selector", "overlap_type", "search_distance", "selection_type"},
        {
            "overlap_type": "overlap_with_distance",
            "search_distance": "distance",
            "selection_type": "selection",
        },
    ),
)
_TASK_SPECS["artifact_export"] = (
    ({"subject", "action"}, {"action": "const:map_png"}),
    ({"subject", "action"}, {"action": "const:map_pdf"}),
    ({"subject", "action"}, {"action": "const:export_png"}),
    ({"subject", "action"}, {"action": "const:export_pdf"}),
    ({"subject", "action"}, {"action": "const:write_file"}),
    (
        {"subject", "target", "action", "selected_only"},
        {"action": "const:table_csv", "selected_only": "boolean"},
    ),
    (
        {"subject", "target", "action", "selected_only"},
        {"action": "const:layer_kml", "selected_only": "boolean"},
    ),
    (
        {"subject", "target", "action", "selected_only"},
        {"action": "const:export_selected_features", "selected_only": "const:true"},
    ),
    (
        {"subject", "target", "action", "selected_only"},
        {"action": "const:split_by_field", "selected_only": "boolean"},
    ),
)
KINDS = frozenset(_SPECS)
_ENTITY_FIELDS = frozenset(("subject", "source", "target", "selector", "join"))
_LIST_ENTITY_FIELDS = frozenset(("sources",))
CONDITION_SCHEMA_REF = "#/definitions/condition"
_INTEGER_FIELD_TYPES = frozenset(("smallinteger", "integer", "oid", "biginteger"))
_FLOAT_FIELD_TYPES = frozenset(("single", "double"))
_TEXT_FIELD_TYPES = frozenset(("string", "guid", "globalid", "date"))
_INTEGER_LITERAL = re.compile(r"[+-]?\d+")
_FLOAT_LITERAL = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")

def _fail(error, path, message):
    raise error(path + message)


def bind_condition_field_types(value, fields, path="condition", error=ValueError):
    """Canonicalize condition literals from authoritative ArcMap field types."""
    field_types = {
        str(field["name"]).casefold(): str(field.get("type") or "").casefold()
        for field in fields or []
        if isinstance(field, dict) and field.get("name")
    }
    return _bind_condition_node(deepcopy(value), field_types, path, error)


def _bind_condition_node(value, field_types, path, error):
    if not isinstance(value, dict):
        return value
    op = value.get("op")
    if op in LOGICAL_CONDITION_OPERATORS:
        if op == "not":
            value["condition"] = _bind_condition_node(
                value.get("condition"), field_types, path + ".condition", error,
            )
        else:
            value["conditions"] = [
                _bind_condition_node(item, field_types, "%s.conditions[%d]" % (path, index), error)
                for index, item in enumerate(value.get("conditions", []))
            ]
        return value
    field_name = value.get("field")
    field_type = field_types.get(str(field_name).casefold()) if field_name is not None else None
    if not field_type or "value_field" in value:
        return value
    if "value" in value:
        value["value"] = _bind_field_literal(
            value["value"], field_name, field_type, path + ".value", error,
        )
    if "values" in value and isinstance(value["values"], list):
        value["values"] = [
            _bind_field_literal(item, field_name, field_type, "%s.values[%d]" % (path, index), error)
            for index, item in enumerate(value["values"])
        ]
    return value


def _bind_field_literal(value, field_name, field_type, path, error):
    if field_type in _INTEGER_FIELD_TYPES:
        if isinstance(value, bool):
            _fail(error, path, " for field %s must be an integer." % field_name)
        if isinstance(value, int):
            return value
        if isinstance(value, float) and math.isfinite(value) and value.is_integer():
            return int(value)
        if isinstance(value, str) and _INTEGER_LITERAL.fullmatch(value.strip()):
            return int(value.strip())
        _fail(error, path, " for field %s must be an integer." % field_name)
    if field_type in _FLOAT_FIELD_TYPES:
        if isinstance(value, bool):
            _fail(error, path, " for field %s must be numeric." % field_name)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            return float(value)
        if isinstance(value, str) and _FLOAT_LITERAL.fullmatch(value.strip()):
            result = float(value.strip())
            if math.isfinite(result):
                return result
        _fail(error, path, " for field %s must be numeric." % field_name)
    if field_type in _TEXT_FIELD_TYPES and not isinstance(value, str):
        _fail(error, path, " for field %s must be text." % field_name)
    return value

def exact_fields(value, required, path, error):
    if not isinstance(value, dict) or set(value) != set(required): _fail(error, path, " has invalid fields.")
    return value

def normalize_distance(value, path="distance", error=ValueError):
    if isinstance(value, str):
        match = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*(meters|kilometers|map_units|degrees)\s*",
            value,
            flags=re.IGNORECASE,
        )
        if not match: _fail(error, path, " is ambiguous or invalid.")
        value = {"value": float(match.group(1)), "unit": match.group(2).lower()}
    if isinstance(value, dict) and isinstance(value.get("unit"), str):
        value = dict(value, unit=value["unit"].lower())
    if not isinstance(value, dict) or set(value) != {"value", "unit"} or isinstance(value["value"], bool) or not isinstance(value["value"], (int, float)) or value["unit"] not in DISTANCE_UNITS:
        _fail(
            error,
            path,
            ' must be exactly {"value": number, "unit": one of "meters", '
            '"kilometers", "map_units", "degrees"}.',
        )
    if value["unit"] == "kilometers":
        return {"value": value["value"] * 1000, "unit": "meters"}
    return {"value": value["value"], "unit": value["unit"]}

def distance(value, path, error): return normalize_distance(value, path, error)

def _valid_value(value, kind, path, error):
    if kind == "string": ok = isinstance(value, str) and bool(value)
    elif kind == "artifact_export_action":
        if value not in ARTIFACT_EXPORT_ACTIONS:
            _fail(error, path, " must use the closed vocabulary for artifact export actions.")
        return
    elif kind == "boolean": ok = isinstance(value, bool)
    elif kind == "integer_or_null": ok = value is None or (isinstance(value, int) and not isinstance(value, bool))
    elif kind == "object_or_null": ok = value is None or isinstance(value, dict)
    elif kind == "strings": ok = isinstance(value, list) and all(isinstance(x, str) and x for x in value)
    elif kind == "entities": ok = isinstance(value, list) and bool(value)
    elif kind == "distance": normalize_distance(value, path, error); return
    elif kind == "distance_or_null":
        if value is None: return
        normalize_distance(value, path, error); return
    elif kind == "selection": ok = value in SELECTION_TYPES
    elif kind == "overlap": ok = value in OVERLAP_TYPES
    elif kind == "overlap_without_distance": ok = value in OVERLAP_TYPES - {"within_a_distance"}
    elif kind == "overlap_with_distance": ok = value == "within_a_distance"
    elif kind.startswith("const:"):
        expected = kind.split(":", 1)[1]
        if expected == "true": expected = True
        ok = value == expected
    elif kind == "condition": ok = isinstance(value, dict)
    else: raise RuntimeError("unknown semantic type " + kind)
    if not ok: _fail(error, path, " has invalid value.")

def _canonical_value(value, type_name, path, error):
    if type_name in ("distance", "distance_or_null"):
        return None if value is None and type_name == "distance_or_null" else normalize_distance(value, path, error)
    if type_name == "selection" and isinstance(value, str):
        normalized = value.strip()
        value = ARCMAP_SELECTION_TYPES.get(normalized.upper(), normalized.lower())
    if type_name in ("overlap", "overlap_without_distance", "overlap_with_distance") and isinstance(value, str):
        value = value.strip().lower()
    if type_name.startswith("const:") and isinstance(value, str):
        value = value.strip().lower()
    if type_name == "condition": value = normalize_condition_tree(value)
    if type_name == "string" and path.endswith("spatial_reference"):
        if isinstance(value, int): value = "EPSG:%d" % value
        elif isinstance(value, str) and value.isdigit(): value = "EPSG:" + value
    _valid_value(value, type_name, path, error)
    return value

def _spec(kind, fields, path, error):
    for required, types in _SPECS[kind]:
        if set(fields) == {"kind"} | required: return types
    _fail(error, path, " has invalid or cross-kind fields.")

def _task_spec(kind, fields, path, error):
    if kind == "artifact_export" and "action" in fields:
        _valid_value(fields["action"], "artifact_export_action", path + ".action", error)
    matches = []
    for required, types in _TASK_SPECS[kind]:
        if set(fields) != {"kind"} | required:
            continue
        if all(
            not type_name.startswith("const:")
            or fields[field] == (True if type_name == "const:true" else type_name.split(":", 1)[1])
            for field, type_name in types.items()
        ):
            matches.append(types)
    if len(matches) == 1:
        return matches[0]
    _fail(error, path, " has invalid or cross-kind fields.")

def parse_task_predicate(value, entity_ids, path, error):
    if not isinstance(value, dict) or value.get("kind") not in _TASK_SPECS: _fail(error, path, ".kind is invalid.")
    value = _task_predicate_defaults(value, path, error)
    types = _task_spec(value["kind"], value, path, error)
    for field in _ENTITY_FIELDS & set(value):
        if not isinstance(value[field], str) or value[field] not in entity_ids: _fail(error, path + "." + field, " must bind an entity.")
    if "sources" in value and (not isinstance(value["sources"], list) or not value["sources"] or any(not isinstance(x, str) or x not in entity_ids for x in value["sources"])): _fail(error, path + ".sources", " must bind entities.")
    result = deepcopy(value)
    for field, type_name in types.items(): result[field] = _canonical_value(result[field], type_name, path + "." + field, error)
    return result


def _task_predicate_defaults(value, path, error):
    result = deepcopy(value)
    if result.get("kind") != "artifact_export":
        return result
    action = result.get("action")
    if action in {"table_csv", "layer_kml", "split_by_field"} and "target" in result:
        result.setdefault("selected_only", False)
    return result

def canonicalize_semantic_fact(value, path="semantic_fact", error=ValueError):
    """Canonicalize executable bindings into the same values used by tasks."""
    if not isinstance(value, dict) or value.get("kind") not in KINDS: _fail(error, path, " has invalid kind.")
    fields = {key: item for key, item in value.items() if key != "step_id"}
    types = _spec(fields["kind"], fields, path, error)
    result = deepcopy(value)
    for field, type_name in types.items(): result[field] = _canonical_value(result[field], type_name, path + "." + field, error)
    return result

def _binding(value, parameters, output_kind, field, path, error):
    if not isinstance(value, dict) or len(value) != 1: _fail(error, path, " binding cannot be resolved: explicit binding object required.")
    key, payload = next(iter(value.items()))
    if field == "artifact_export_action" and key != "const":
        _fail(error, path, " must use a const from the closed vocabulary for artifact export actions.")
    if key == "output":
        if payload is not True or output_kind == "none": _fail(error, path, " cannot bind a missing formal output.")
        return
    if key == "parameter":
        if not isinstance(payload, str) or payload not in parameters: _fail(error, path, " references an unknown parameter.")
        schema = parameters[payload]
        actual = schema.get("type") if isinstance(schema, dict) else None
        actual_types = set(actual if isinstance(actual, list) else [actual])
        compatible = {
            "entities": bool(actual_types & {"array", "string"}),
            "boolean": "boolean" in actual_types,
            "integer_or_null": bool(actual_types & {"integer", "null"}),
            "strings": "array" in actual_types,
            "string": bool(actual_types & {"string", "integer"}),
            "selection": "string" in actual_types,
            "overlap": "string" in actual_types,
            "condition": "object" in actual_types,
            "distance": bool(actual_types & {"string", "object"}),
            "distance_or_null": bool(actual_types & {"string", "object", "null"}),
            "object_or_null": bool(actual_types & {"object", "null"}),
        }
        # Entity bindings are runtime layer references, represented by strings
        # in the executable schema but marked with x-geopilot-kind.
        if field == "entity" and "string" not in actual_types:
            _fail(error, path, " parameter type is incompatible with semantic entity.")
        if field != "entity" and not compatible.get(field, True):
            _fail(error, path, " parameter type is incompatible with semantic field.")
        return
    if key == "const":
        _valid_value(payload, field, path + ".const", error)
        return
    _fail(error, path, " must use parameter, output, or const.")

def validate_capability_effect(effect, parameters_schema, output_kind, path, error):
    if not isinstance(effect, dict) or effect.get("kind") not in KINDS: _fail(error, path, " has invalid kind.")
    fields = dict(effect); fields.pop("result", None)
    preserves = fields.pop("preserves", None)
    # capabilities may omit subject: it is always the formal output/map subject.
    fields.setdefault("subject", {"output": True})
    types = _spec(effect["kind"], fields, path, error)
    if preserves is not None:
        if (not isinstance(preserves, list) or not preserves
                or len(set(preserves)) != len(preserves)
                or any(kind not in KINDS for kind in preserves)):
            _fail(error, path + ".preserves", " must contain unique semantic kinds.")
        if "source" not in fields:
            _fail(error, path + ".preserves", " requires an explicit source binding.")
    parameters = parameters_schema.get("properties", {}) if isinstance(parameters_schema, dict) else {}
    for field in fields:
        if field in {"kind", "subject"}: continue
        type_name = types.get(field, "entities" if field == "sources" else "entity" if field in _ENTITY_FIELDS else None)
        if type_name is None: raise RuntimeError("semantic field lacks a declared type: " + field)
        binding = effect[field]
        if type_name == "entities" and isinstance(binding, list):
            if not binding: _fail(error, path + "." + field, " cannot be empty.")
            for item in binding: _binding(item, parameters, output_kind, type_name, path + "." + field, error)
        else: _binding(binding, parameters, output_kind, type_name, path + "." + field, error)
    if "result" in effect: _binding(effect["result"], parameters, output_kind, "string", path + ".result", error)
    return effect

def condition_definition():
    """Return the exact recursive JSON schema for the production condition grammar."""
    scalar = {}
    variants = []
    for op in sorted({"eq", "ne", "gt", "gte", "lt", "lte"}):
        variants.extend((
            {
                "type": "object",
                "properties": {"op": {"const": op}, "field": {"type": "string", "minLength": 1}, "value": scalar},
                "required": ["field", "op", "value"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"op": {"const": op}, "field": {"type": "string", "minLength": 1}, "value_field": {"type": "string", "minLength": 1}},
                "required": ["field", "op", "value_field"],
                "additionalProperties": False,
            },
        ))
    variants.extend((
        {
            "type": "object",
            "properties": {"op": {"const": "like"}, "field": {"type": "string", "minLength": 1}, "value": scalar},
            "required": ["field", "op", "value"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"op": {"const": "between"}, "field": {"type": "string", "minLength": 1}, "values": {"type": "array", "minItems": 2, "maxItems": 2, "items": scalar}},
            "required": ["field", "op", "values"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"op": {"const": "in"}, "field": {"type": "string", "minLength": 1}, "values": {"type": "array", "minItems": 1, "items": scalar}},
            "required": ["field", "op", "values"],
            "additionalProperties": False,
        },
    ))
    for op in ("is_null", "is_not_null"):
        variants.append({
            "type": "object",
            "properties": {"op": {"const": op}, "field": {"type": "string", "minLength": 1}},
            "required": ["field", "op"],
            "additionalProperties": False,
        })
    for op in ("and", "or"):
        variants.append({
            "type": "object",
            "properties": {
                "op": {"const": op},
                "conditions": {"type": "array", "minItems": 2, "items": {"$ref": CONDITION_SCHEMA_REF}},
            },
            "required": ["conditions", "op"],
            "additionalProperties": False,
        })
    variants.append({
        "type": "object",
        "properties": {"op": {"const": "not"}, "condition": {"$ref": CONDITION_SCHEMA_REF}},
        "required": ["condition", "op"],
        "additionalProperties": False,
    })
    return {"oneOf": variants}


def predicate_schema():
    distance_schema = {
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "unit": {"type": "string", "enum": sorted(DISTANCE_UNITS)},
        },
        "required": ["unit", "value"],
        "additionalProperties": False,
    }
    variants=[]
    for kind, specs in _TASK_SPECS.items():
        for required, types in specs:
            props={"kind":{"const":kind}}
            for field in required:
                if field in _ENTITY_FIELDS:
                    props[field]={"type":"string", "minLength":1}
                    if kind in {"attribute_filter", "spatial_filter"} and field == "subject":
                        props[field]["description"] = (
                            "The existing input entity being filtered, or a declared map_state output."
                        )
                    if kind in {"attribute_filter", "spatial_filter"} and field == "target":
                        props[field]["description"] = (
                            "The existing input entity being filtered; it must equal subject when subject is an input."
                        )
                    if kind == "artifact_export" and field == "subject":
                        props[field]["description"] = (
                            "The declared output entity created by this export. For current-map PNG/PDF exports, "
                            "use the PNG/PDF output here and do not invent a current-map input."
                        )
                    if kind == "artifact_export" and field == "target":
                        props[field]["description"] = (
                            "The declared input or prior output being exported; never put the exported output here."
                        )
                elif field == "sources": props[field]={"type":"array", "minItems":1, "items":{"type":"string", "minLength":1}}
                else:
                    type_name = types.get(field)
                    if type_name == "boolean": props[field] = {"type": "boolean"}
                    elif type_name == "condition": props[field] = {"$ref": CONDITION_SCHEMA_REF}
                    elif type_name == "selection":
                        props[field] = {
                            "type": "string",
                            "enum": sorted(SELECTION_TYPES),
                            "description": (
                                "new_selection creates or replaces a selection; select_subset only narrows "
                                "a non-empty selection that already exists on the target layer."
                            ),
                        }
                    elif type_name == "overlap": props[field] = {"type": "string", "enum": sorted(OVERLAP_TYPES)}
                    elif type_name == "overlap_without_distance":
                        props[field] = {"type": "string", "enum": sorted(OVERLAP_TYPES - {"within_a_distance"})}
                    elif type_name == "overlap_with_distance": props[field] = {"const": "within_a_distance"}
                    elif type_name == "distance": props[field] = deepcopy(distance_schema)
                    elif type_name == "distance_or_null":
                        props[field] = deepcopy(distance_schema)
                        props[field]["type"] = ["object", "null"]
                    elif type_name == "strings": props[field] = {"type": "array", "items": {"type": "string", "minLength": 1}}
                    elif type_name == "integer_or_null": props[field] = {"type": ["integer", "null"]}
                    elif type_name == "object_or_null": props[field] = {"type": ["object", "null"]}
                    elif type_name == "string": props[field] = {"type": "string", "minLength": 1}
                    elif type_name == "artifact_export_action":
                        props[field] = {"type": "string", "enum": sorted(ARTIFACT_EXPORT_ACTIONS)}
                    elif isinstance(type_name, str) and type_name.startswith("const:"):
                        constant = type_name.split(":", 1)[1]
                        props[field] = {"const": True if constant == "true" else constant}
                    else: raise RuntimeError("unknown semantic type " + str(type_name))
            variants.append({"type":"object","properties":props,"required":sorted({"kind"}|required),"additionalProperties":False})
    return {"oneOf":variants}


def task_predicate_catalog():
    """Return the compact, closed grammar shown to task-contract models.

    The provider tool deliberately accepts each predicate as an opaque JSON
    string.  This catalog communicates the canonical grammar without embedding
    the large union or recursive condition schema in provider tool arguments.
    The server remains authoritative: ``parse_task_predicate`` validates the
    decoded object against ``_TASK_SPECS`` before planning can continue.
    """
    field_types = {
        "boolean": "boolean",
        "condition": "condition",
        "distance": "distance",
        "integer_or_null": "integer_or_null",
        "object_or_null": "object_or_null",
        "string": "non_empty_string",
        "strings": "non_empty_string_array",
    }
    variants = []
    for kind, specs in _TASK_SPECS.items():
        for required, types in specs:
            fields = {}
            for field in sorted(required):
                if field in _ENTITY_FIELDS:
                    fields[field] = "declared_entity_id"
                elif field in _LIST_ENTITY_FIELDS:
                    fields[field] = "non_empty_declared_entity_id_array"
                else:
                    type_name = types[field]
                    if type_name == "selection":
                        fields[field] = sorted(SELECTION_TYPES)
                    elif type_name == "overlap_without_distance":
                        fields[field] = sorted(OVERLAP_TYPES - {"within_a_distance"})
                    elif type_name == "overlap_with_distance":
                        fields[field] = "within_a_distance"
                    elif type_name == "artifact_export_action":
                        fields[field] = sorted(ARTIFACT_EXPORT_ACTIONS)
                    elif type_name.startswith("const:"):
                        constant = type_name.split(":", 1)[1]
                        fields[field] = True if constant == "true" else constant
                    else:
                        fields[field] = field_types[type_name]
            variants.append({"kind": kind, "fields": fields})
    return {
        "rule": "predicate object has exactly kind plus one variant's fields",
        "distance": {
            "fields": ["unit", "value"],
            "unit": sorted(DISTANCE_UNITS),
            "value": "number",
        },
        "condition": [
            {"op": sorted({"eq", "ne", "gt", "gte", "lt", "lte", "like"}), "fields": ["field", "op", "value"]},
            {"op": sorted({"eq", "ne", "gt", "gte", "lt", "lte"}), "fields": ["field", "op", "value_field"]},
            {"op": "between", "fields": ["field", "op", "values"], "values": "exactly_2_scalars"},
            {"op": "in", "fields": ["field", "op", "values"], "values": "non_empty_scalar_array"},
            {"op": ["is_not_null", "is_null"], "fields": ["field", "op"]},
            {"op": ["and", "or"], "fields": ["conditions", "op"], "conditions": "at_least_2_conditions"},
            {"op": "not", "fields": ["condition", "op"], "condition": "one_condition"},
        ],
        "variants": variants,
    }

def effect_schema():
    binding = {"type":"object", "minProperties":1, "maxProperties":1,
               "properties":{"parameter":{"type":"string","minLength":1}, "output":{"const":True}, "const":{}},
               "additionalProperties":False}
    export_action_binding = {
        "type": "object", "additionalProperties": False,
        "properties": {"const": {"type": "string", "enum": sorted(ARTIFACT_EXPORT_ACTIONS)}},
        "required": ["const"],
    }
    variants=[]
    for kind, specs in _SPECS.items():
        for required, types in specs:
            props={
                "kind":{"const":kind}, "result":binding,
                "preserves": {
                    "type": "array", "minItems": 1, "uniqueItems": True,
                    "items": {"type": "string", "enum": sorted(KINDS)},
                },
            }
            for field in required - {"subject"}:
                if types.get(field) == "artifact_export_action":
                    props[field] = export_action_binding
                else:
                    props[field]={"type":"array","minItems":1,"items":binding} if field == "sources" else binding
            variants.append({"type":"object","properties":props,"required":sorted({"kind"}|(required-{"subject"})),"additionalProperties":False})
    return {"oneOf":variants}
