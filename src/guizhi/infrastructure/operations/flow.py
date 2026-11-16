"""Typed reusable values produced by map workflow nodes."""
from math import isfinite
from operator import add, mul, sub, truediv
from typing import Literal

from guizhi.infrastructure.map_attributes import unique_values as read_values


def unique_values(ctx, field: str, query: str = ""):
    file = ctx.directory / ctx.evidence[0]["path"]
    values = []
    for page in range(10):
        ctx.check()
        result = read_values(file, ctx.request.inputs[0].layer, {"field": field, "query": query, "page": page})
        values.extend(result["values"])
        if not result["more"]:
            return {"value": values, "statistics": {"distinct_count": len(values)}}
    raise ValueError("匹配的唯一值超过 500 个，请缩小搜索范围")


def expression(ctx, text: str):
    text = text.strip()
    if not 1 <= len(text) <= 4096:
        raise ValueError("请填写 1 至 4096 字的 SQL 表达式")
    return {"value": text}


def calculate(ctx, operator: Literal["add", "subtract", "multiply", "divide"], left: float, right: float):
    if operator == "divide" and right == 0:
        raise ValueError("除数不能为 0")
    value = {"add": add, "subtract": sub, "multiply": mul, "divide": truediv}[operator](left, right)
    if not isfinite(value):
        raise ValueError("计算结果超出有效数值范围")
    return {"value": value}
