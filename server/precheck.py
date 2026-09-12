"""Per-operation three-state pre-checks (proven / unresolved / violated).

The boundary re-checks every tool call against the operation card and the
live map context immediately before dispatch. ``unresolved`` carries askable
obligations back to the agent, which clarifies with the user; ``violated``
hard-rejects. This replaces the old whole-plan ProofGraph with per-op checks
at the door.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

_TYPE_NAMES = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "object": dict, "array": list,
}

_UNIT_ALIASES = {
    "m": "meters", "meter": "meters", "metre": "meters", "米": "meters", "公尺": "meters",
    "km": "kilometers", "千米": "kilometers", "公里": "kilometers",
    "sq_m": "square_meters", "平方米": "square_meters",
    "km2": "square_kilometers", "平方公里": "square_kilometers",
    "ha": "hectares", "公顷": "hectares",
}


def _fill_semantics(value: Any, spec: Any) -> Any:
    """Complete x-geopilot-semantic payloads the Py2 ABI validates strictly.

    Quantity requires exactly {value, unit, dimension, tolerance, crs} with
    canonical units; FieldSpec requires all seven keys. Models send partial
    dicts with aliased units — the boundary completes them so the contract
    sees well-formed values instead of fail-closing on shape.
    """
    if not isinstance(spec, dict) or not isinstance(value, dict):
        return value
    semantic = spec.get("x-geopilot-semantic")
    _CANONICAL_UNITS = {"meters", "kilometers", "map_units", "degrees",
                        "square_meters", "hectares", "square_kilometers",
                        "map_units_squared", "square_degrees"}
    if semantic == "quantity":
        unit = value.get("unit")
        if isinstance(unit, str):
            lowered = unit.strip().lower()
            if lowered in _CANONICAL_UNITS:
                value["unit"] = lowered
            else:
                value["unit"] = _UNIT_ALIASES.get(lowered, unit.strip())
        value.setdefault("tolerance", 0.0)
        value.setdefault("crs", None)
        dimension_spec = (spec.get("properties") or {}).get("dimension") or {}
        if dimension_spec.get("const") is not None:
            value["dimension"] = dimension_spec["const"]
        else:
            value.setdefault("dimension", "length")
        if isinstance(value.get("tolerance"), str):
            try:
                value["tolerance"] = float(value["tolerance"])
            except ValueError:
                value["tolerance"] = 0.0
    elif semantic == "field_spec":
        value.setdefault("nullable", True)
        value.setdefault("precision", 0)
        value.setdefault("scale", 0)
        value.setdefault("domain", [])
        if value.get("length") in (None, "", "null"):
            value["length"] = 50 if value.get("type") == "string" else 0
    return value


def resolve_layer_references(schema: Dict[str, Any],
                              arguments: Dict[str, Any],
                              context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Map layer NAMES to layer:N refs for every layer-kind parameter.

    The runtime executor addresses layers by layer_ref; models naturally say
    "hospitals". Resolve against the live captured context so move_layer's
    reference_layer etc. accept names.
    """
    if not context:
        return arguments
    by_name = {}
    for layer in context.get("layers", []):
        if isinstance(layer, dict) and layer.get("layer_ref"):
            by_name[layer.get("name")] = layer["layer_ref"]
    if not by_name:
        return arguments
    properties = (schema or {}).get("properties", {}) or {}
    resolved = dict(arguments)
    for name, spec in properties.items():
        if not isinstance(spec, dict) or spec.get("x-geopilot-kind") != "layer":
            continue
        value = resolved.get(name)
        if isinstance(value, str) and value in by_name:
            resolved[name] = by_name[value]
    return resolved


def coerce_arguments(schema: Dict[str, Any],
                     arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively coerce stringly-typed argument values to schema types.

    Models routinely emit ``"true"``/``"50"`` strings, ``"null"``/empty
    placeholders, and ``{"item": [...]}`` array wrappers; the Py2 semantic
    ABI fail-closes on all of them. Coerce before pre-check so the contract
    sees well-typed values; coerced-away fields are dropped entirely.
    """
    coerced = _coerce_object(arguments, schema.get("properties", {}) or {})
    return coerced


def _declared_type(spec: Any) -> Optional[str]:
    if not isinstance(spec, dict):
        return None
    declared = spec.get("type")
    if isinstance(declared, list):
        return next((item for item in declared if item != "null"), None)
    return declared


def _coerce_object(value: Dict[str, Any],
                    properties: Dict[str, Any]) -> Dict[str, Any]:
    result = {}
    for name, item in value.items():
        spec = properties.get(name)
        coerced = _coerce_value(item, spec)
        if coerced is not None:
            result[name] = coerced
    return result


def _coerce_value(value: Any, spec: Any) -> Any:
    declared = _declared_type(spec)
    if isinstance(value, dict):
        inner = spec.get("properties", {}) if isinstance(spec, dict) else {}
        coerced = _coerce_object(value, inner or {})
        return _fill_semantics(coerced, spec)
    if declared == "array":
        return _coerce_array(value, spec)
    if isinstance(value, str):
        stripped = value.strip()
        if declared == "boolean":
            if stripped.lower() == "true":
                return True
            if stripped.lower() == "false":
                return False
        elif declared == "integer":
            try:
                return int(stripped)
            except ValueError:
                return None
        elif declared == "number":
            try:
                return float(stripped)
            except ValueError:
                return None
        elif declared is None and stripped.isdigit():
            # Schema-silent digit strings (e.g. the catalog's bare `length`)
            # must still arrive as integers for the Py2 semantic ABI.
            return int(stripped)
        if stripped.lower() == "null" or not stripped:
            return None
        return value
    if declared == "integer" and isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _coerce_array(value: Any, spec: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"item"}:
        value = value["item"]
    if isinstance(value, str):
        # Required array fields must arrive as arrays, not placeholders.
        return [] if (not value.strip() or value.strip().lower() == "null") else value
    if not isinstance(value, list):
        return value
    item_spec = spec.get("items") if isinstance(spec, dict) else None
    coerced = [_coerce_value(item, item_spec) for item in value]
    return [item for item in coerced if item is not None]


def check(card: Dict[str, Any], arguments: Dict[str, Any],
          context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the three-state outcome document for one operation call."""
    operation_id = card.get("operation_id") or card.get("id", "")
    schema = card.get("parameters_schema", {}) or {}
    properties = schema.get("properties", {}) or {}
    unresolved: List[Dict[str, Any]] = []
    violations: List[Dict[str, Any]] = []

    if not isinstance(arguments, dict):
        return _violated(["arguments 必须是对象。"])

    for name in schema.get("required", []):
        if name not in arguments or arguments.get(name) in (None, "", [], {}):
            property_schema = properties.get(name, {}) or {}
            label = property_schema.get("title") or name
            unresolved.append({
                "proof_id": "%s.%s" % (operation_id, name),
                "question": "参数「%s」未提供：%s" % (
                    label, property_schema.get("description", "请补充该参数。")),
            })

    for name, value in arguments.items():
        property_schema = properties.get(name)
        if property_schema is None:
            violations.append("未知参数 %s：不在能力 %s 的参数 schema 内。" % (name, operation_id))
            continue
        violation = _type_violation(name, value, property_schema)
        if violation:
            violations.append(violation)

    if context is not None:
        violations.extend(_layer_violations(arguments, properties, context, operation_id))

    if violations:
        return _violated(violations)
    if unresolved:
        return {"status": "unresolved", "obligations": unresolved}
    return {"status": "proven"}


def _layer_violations(arguments: Dict[str, Any], properties: Dict[str, Any],
                      context: Dict[str, Any], operation_id: str) -> List[str]:
    """Layer references must resolve against the live captured context."""
    layers = context.get("layers", [])
    known = {layer.get("layer_ref") for layer in layers}
    known |= {layer.get("name") for layer in layers}
    violations = []
    for name, property_schema in properties.items():
        if (property_schema or {}).get("x-geopilot-kind") != "layer":
            continue
        value = arguments.get(name)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and item not in known:
                violations.append(
                    "参数 %s 引用的图层「%s」不在当前地图上下文中（能力 %s）。"
                    "可用图层：%s" % (name, item, operation_id,
                                     "、".join(sorted(str(k) for k in known if k)) or "（空地图）"))
    return violations


def _type_violation(name: str, value: Any, property_schema: Dict[str, Any]) -> Optional[str]:
    expected = property_schema.get("type")
    if isinstance(expected, list):
        expected = next((item for item in expected if item != "null"), None)
    if expected is None or value is None:
        return None
    python_type = _TYPE_NAMES.get(expected)
    if python_type is None:
        return None
    if expected == "number" and isinstance(value, bool):
        return "参数 %s 必须是数字。" % name
    if expected == "integer" and isinstance(value, bool):
        return "参数 %s 必须是整数。" % name
    if not isinstance(value, python_type):
        return "参数 %s 必须是 %s。" % (name, expected)
    enum = property_schema.get("enum")
    if enum and value not in enum:
        return "参数 %s 的取值必须是：%s。" % (name, "、".join(str(item) for item in enum))
    return None


def _violated(messages: List[str]) -> Dict[str, Any]:
    return {"status": "violated", "violations": messages}
