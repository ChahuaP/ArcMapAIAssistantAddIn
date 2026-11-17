"""Read distinct attribute values and evaluate typed conditions and arithmetic."""
from math import isfinite
from operator import add, mul, sub, truediv
from typing import Literal

from guizhi.infrastructure.map_attributes import unique_values as read_values
from guizhi.infrastructure.operations.data import AttributeFilter
from guizhi.infrastructure.operations.sql_expression import attribute_predicate


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


def condition(ctx, where: AttributeFilter):
    info = ctx.info[0]
    attribute_predicate(where, dict(zip(info["fields"], info["dtypes"], strict=True)))
    return {"value": where.model_dump(exclude_none=True)}


def calculate(ctx, operator: Literal["add", "subtract", "multiply", "divide"], left: float, right: float):
    if operator == "divide" and right == 0:
        raise ValueError("除数不能为 0")
    value = {"add": add, "subtract": sub, "multiply": mul, "divide": truediv}[operator](left, right)
    if not isfinite(value):
        raise ValueError("计算结果超出有效数值范围")
    return {"value": value}
