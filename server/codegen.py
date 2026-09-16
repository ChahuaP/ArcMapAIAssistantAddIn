"""Catalog-to-tool code generation: one native MCP tool per operation.

Each catalog operation has one public business schema and one execution
pipeline. Required fields are advertised as required. The callable accepts
missing fields so the boundary can return structured clarification; it never
coerces types independently of the public JSON Schema validator.
"""
from __future__ import annotations

from inspect import Parameter, Signature
from typing import Any, Callable, Dict
from server.tool_contract import model_schema

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
    schema = model_schema(card.get("parameters_schema", {}) or {})
    lines = [card.get("summary", card.get("id", "")).rstrip("。") + "。"]
    lines.append("副作用等级：%s。执行缺必填参数时返回 unresolved 待澄清问题。"
                 % _SIDE_EFFECT_LABEL.get(level, "只读"))
    if card.get('id', '').startswith('edit.create_'):
        lines.append('必须明确坐标系：提供 wkid（用户指定的 EPSG 代码）或 spatial_reference_layer，二选一。')
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


def build_operation_runner(operation_id: str, card: Dict[str, Any],
                           execute: Callable[[str, Dict[str, Any]], dict]):
    """Expose catalog fields as native keyword parameters to FastMCP/Pydantic.

    Names match the public schema. Type and nested constraint validation belongs
    to the common boundary, so Pydantic cannot silently coerce business values.
    """
    properties = model_schema(card.get("parameters_schema") or {}).get("properties") or {}
    parameters = [
        Parameter(name, kind=Parameter.KEYWORD_ONLY, default=None, annotation=Any)
        for name, spec in properties.items()
    ]

    def run(**arguments):
        return execute(operation_id, {name: value for name, value in arguments.items()
                                      if value is not None})

    run.__signature__ = Signature(parameters, return_annotation=dict)
    run.__annotations__ = {parameter.name: parameter.annotation for parameter in parameters}
    run.__annotations__["return"] = dict
    run.__name__ = tool_name(operation_id)
    run.__qualname__ = run.__name__
    return run


