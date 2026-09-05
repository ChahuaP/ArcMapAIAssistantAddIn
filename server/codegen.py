"""Catalog-to-tool code generation: one native MCP tool per operation.

Each catalog operation becomes a first-class tool the model can see and call
directly (B structure): the tool list IS the capability list, so "we don't
support that" hallucinations are structurally impossible. All tools share one
execution pipeline; this module only builds names, schemas and docstrings.

Schema policy: every field is Optional so a missing required argument still
reaches the boundary pre-check, which answers with askable ``unresolved``
obligations instead of a hard validation error.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import create_model

_JSON_TO_PYTHON = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}

_SIDE_EFFECT_LABEL = {
    1: "只读",
    2: "修改地图",
    3: "写入数据",
    4: "编辑源数据",
}


def tool_name(operation_id: str) -> str:
    """`layer.add_layer` -> `layer__add_layer` (category__operation suffix)."""
    category, _, suffix = operation_id.partition(".")
    return "%s__%s" % (category.replace(".", "_"), suffix.replace(".", "_"))


def tool_description(card: Dict[str, Any], level: int) -> str:
    schema = card.get("parameters_schema", {}) or {}
    lines = [card.get("summary", card.get("id", "")) + "。"]
    lines.append("副作用等级：%s。执行缺必填参数时返回 unresolved 待澄清问题。"
                 % _SIDE_EFFECT_LABEL.get(level, "只读"))
    properties = schema.get("properties", {}) or {}
    if properties:
        lines.append("参数：")
        for name, spec in properties.items():
            required = name in (schema.get("required") or [])
            description = (spec or {}).get("description")
            title = (spec or {}).get("title") or name
            marker = "必填" if required else "可选"
            detail = ("——" + description) if description else ""
            lines.append("- %s（%s%s）%s" % (title, marker,
                                             _type_label(spec), detail))
    return "\n".join(lines)


def _type_label(spec: Dict[str, Any]) -> str:
    kind = spec.get("type", "值")
    enum = spec.get("enum")
    if enum:
        return "%s，取值：%s" % (kind, "/".join(str(item) for item in enum))
    return kind


def build_arguments_model(operation_id: str,
                          card: Dict[str, Any]) -> Tuple[Type, List[str]]:
    """Generate a pydantic model from the operation's JSON Schema.

    Returns (model, required_names). Every field is Optional-with-None so the
    three-state pre-check owns requiredness.
    """
    schema = card.get("parameters_schema", {}) or {}
    properties: Dict[str, Any] = schema.get("properties", {}) or {}
    required = list(schema.get("required") or [])
    fields: Dict[str, Tuple[Any, Any]] = {}
    for name, spec in properties.items():
        spec = spec or {}
        fields[name] = (Optional[_json_type(spec)], None)
    model = create_model(
        "Args__%s" % tool_name(operation_id).title().replace("_", ""),
        __config__={"extra": "allow"},
        **fields,
    ) if fields else None
    return model, required


def _json_type(spec: Dict[str, Any]) -> Any:
    """Map a JSON Schema type (possibly a union list) to a Python type."""
    declared = spec.get("type", "string")
    if isinstance(declared, list):
        declared = next((item for item in declared if item != "null"), "string")
    return _JSON_TO_PYTHON.get(declared, Any)
