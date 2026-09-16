"""Per-operation three-state pre-checks (proven / unresolved / violated).

The boundary re-checks every tool call against the operation card and the
live map context immediately before dispatch. ``unresolved`` carries askable
obligations back to the agent, which clarifies with the user; ``violated``
hard-rejects. This replaces the old whole-plan ProofGraph with per-op checks
at the door.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from jsonschema import Draft202012Validator
import math
from shared_runtime import semantic_abi, condition_contract
from server.tool_contract import omit_optional_nulls

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
    refs = {layer.get('layer_ref') for layer in context.get('layers', [])}
    for layer in context.get("layers", []):
        if isinstance(layer, dict) and layer.get("layer_ref"):
            by_name.setdefault(layer.get("name"), []).append(layer["layer_ref"])
    if not by_name:
        return arguments
    properties = (schema or {}).get("properties", {}) or {}
    resolved = dict(arguments)
    for name, spec in properties.items():
        if not isinstance(spec, dict) or spec.get("x-geopilot-kind") != "layer":
            continue
        value = resolved.get(name)
        if name not in resolved:
            continue
        def resolve(item):
            matches = by_name.get(item, []) if isinstance(item, str) else []
            return matches[0] if isinstance(item, str) and item not in refs and len(matches) == 1 else item
        resolved[name] = [resolve(item) for item in value] if isinstance(value, list) else resolve(value)
    return resolved


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
    arguments = omit_optional_nulls(arguments, schema)

    for name in schema.get("required", []):
        if name not in arguments or (arguments.get(name) == "" and name != 'text'):
            property_schema = properties.get(name, {}) or {}
            label = property_schema.get("title") or name
            unresolved.append({
                "proof_id": "%s.%s" % (operation_id, name),
                "question": "参数「%s」未提供：%s" % (
                    label, property_schema.get("description", "请补充该参数。")),
            })

    validator = Draft202012Validator(schema)
    for error in validator.iter_errors(arguments):
        path = ".".join(str(part) for part in error.absolute_path) or "arguments"
        if error.validator == "required":
            missing = [key for key in error.validator_value if key not in error.instance]
            if not error.absolute_path:
                continue  # already reported above, with user-facing labels
            for key in missing:
                unresolved.append({"proof_id": "%s.%s.%s" % (operation_id, path, key),
                                   "question": "请补充 %s.%s。" % (path, key)})
        else:
            if error.validator in ('oneOf', 'anyOf') and unresolved:
                continue
            if error.validator in ('oneOf', 'anyOf') and isinstance(error.instance, dict):
                choices = [part.get('required', []) for part in error.validator_value]
                if choices and all(choice and not set(choice) <= set(error.instance) for choice in choices):
                    alternatives = ['、'.join(name for name in choice if name not in error.instance) for choice in choices]
                    unresolved.append({'proof_id': operation_id + '.' + path,
                                       'question': '请提供其中一组参数：' + ' 或 '.join(alternatives) + '。'})
                    continue
            violations.append("参数 %s 不符合工具定义：%s" % (path, error.message))
    violations.extend(_finite_violations(arguments))
    if not violations and not unresolved:
        violations.extend(_semantic_violations(arguments, schema))
        violations.extend(_operation_violations(operation_id, arguments))

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
    refs = {layer.get('layer_ref') for layer in layers}
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
            matches = [layer for layer in layers if layer.get('name') == item]
            if isinstance(item, str) and item not in refs and len(matches) > 1:
                violations.append("图层名称「%s」不唯一，请使用明确的 layer_ref：%s。" %
                                  (item, '、'.join(layer['layer_ref'] for layer in matches)))
                continue
            if isinstance(item, str) and item not in known:
                violations.append(
                    "参数 %s 引用的图层「%s」不在当前地图上下文中（能力 %s）。"
                    "可用图层：%s" % (name, item, operation_id,
                                     "、".join(sorted(str(k) for k in known if k)) or "（空地图）"))
    return violations


def _violated(messages: List[str]) -> Dict[str, Any]:
    return {"status": "violated", "violations": messages}


def _finite_violations(value, path="arguments"):
    if isinstance(value, float) and not math.isfinite(value):
        return ["参数 %s 必须是有限数值。" % path]
    if isinstance(value, dict):
        return [error for name, item in value.items() for error in _finite_violations(item, path + "." + name)]
    if isinstance(value, list):
        return [error for index, item in enumerate(value) for error in _finite_violations(item, path + "." + str(index))]
    return []


def _semantic_violations(value, schema):
    errors = []
    if not isinstance(value, (dict, list)):
        return errors
    semantic = schema.get("x-geopilot-semantic")
    try:
        if semantic in ("quantity", "angle"):
            semantic_abi.quantity(value)
        elif semantic == "field_spec":
            semantic_abi.field_spec(value)
    except (ValueError, TypeError) as exc:
        errors.append(str(exc))
    if isinstance(value, dict):
        for name, item in value.items():
            errors.extend(_semantic_violations(item, schema.get("properties", {}).get(name, {})))
    else:
        for item in value:
            errors.extend(_semantic_violations(item, schema.get("items", {})))
    return errors


def _operation_violations(operation_id, arguments):
    errors = []
    if "where" in arguments:
        try:
            condition_contract.validate_condition_tree(arguments["where"])
        except ValueError as exc:
            errors.append("where 条件无效：%s" % exc)
    if operation_id == "layer.move_layer" and arguments.get("position") in ("BEFORE", "AFTER"):
        if not arguments.get("reference_layer"):
            errors.append("BEFORE/AFTER 必须提供 reference_layer。")
        elif arguments.get("layer") == arguments.get("reference_layer"):
            errors.append("不能相对于自身移动图层。")
    if operation_id == "selection.select_by_location":
        distance = arguments.get("search_distance")
        if arguments.get("overlap_type") == "within_a_distance" and distance is None:
            errors.append("within_a_distance 必须提供 search_distance。")
        elif arguments.get("overlap_type") != "within_a_distance" and distance is not None:
            errors.append("仅 within_a_distance 接受 search_distance。")
    if operation_id == "edit.create_rectangle_polygon":
        if arguments["left"] >= arguments["right"] or arguments["bottom"] >= arguments["top"]:
            errors.append("矩形必须满足 left < right 且 bottom < top。")
    return errors
