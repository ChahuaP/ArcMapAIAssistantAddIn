# -*- coding: utf-8 -*-
from __future__ import absolute_import

import math


try:
    unicode
except NameError:
    unicode = str

try:
    long
except NameError:
    long = int


CONDITION_OPERATOR_ALIASES = {
    u"等于": "eq",
    "=": "eq",
    "==": "eq",
    u"不等于": "ne",
    "!=": "ne",
    "<>": "ne",
    u"大于": "gt",
    ">": "gt",
    u"大于等于": "gte",
    ">=": "gte",
    u"小于": "lt",
    "<": "lt",
    u"小于等于": "lte",
    "<=": "lte",
    u"之间": "between",
    u"包含于": "in",
    u"模糊匹配": "like",
    u"为空": "is_null",
    u"非空": "is_not_null",
}

LOGICAL_CONDITION_OPERATORS = set(["and", "or", "not"])
LEAF_CONDITION_OPERATORS = set([
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "in",
    "like",
    "is_null",
    "is_not_null",
])
VALUE_CONDITION_OPERATORS = set(["eq", "ne", "gt", "gte", "lt", "lte", "like"])
CONDITION_OPERATOR_HELP = "eq, ne, gt, gte, lt, lte, between, in, like, is_null, is_not_null, and, or, not"

TEXT_FIELD_TYPES = set(["String", "Guid", "GlobalID"])
NUMBER_FIELD_TYPES = set(["SmallInteger", "Integer", "Single", "Double", "OID"])
DATE_FIELD_TYPES = set(["Date"])


def canonical_operator(condition, strict=True, error_cls=ValueError, missing_message=u"属性条件缺少 op。"):
    op = condition.get("op", condition.get("operator"))
    if op is None:
        if strict:
            raise error_cls(missing_message)
        return ""
    text = unicode(op).strip().lower()
    return CONDITION_OPERATOR_ALIASES.get(text, text)


def normalize_condition_tree(condition):
    if not isinstance(condition, dict):
        return condition

    shorthand_keys = [key for key in ("and", "or", "not") if key in condition]
    if "op" not in condition and "operator" not in condition and len(shorthand_keys) == 1 and len(condition) == 1:
        key = shorthand_keys[0]
        value = condition[key]
        if key in ("and", "or") and isinstance(value, list):
            return {"op": key, "conditions": [normalize_condition_tree(child) for child in value]}
        if key == "not" and isinstance(value, dict):
            return {"op": key, "condition": normalize_condition_tree(value)}
        return dict(condition)

    result = dict(condition)
    op = canonical_operator(result, strict=False)
    if op:
        result.pop("operator", None)
        result["op"] = op
    if op in ("and", "or") and isinstance(result.get("conditions"), list):
        result["conditions"] = [normalize_condition_tree(child) for child in result["conditions"]]
    elif op == "not" and isinstance(result.get("condition"), dict):
        result["condition"] = normalize_condition_tree(result["condition"])
    return result


def is_number_value(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, long, float)):
        try:
            return math.isfinite(float(value))
        except AttributeError:
            return not (math.isinf(float(value)) or math.isnan(float(value)))
    if isinstance(value, unicode):
        text = value.strip()
        if not text:
            return False
        try:
            number = float(text)
        except ValueError:
            return False
        try:
            return math.isfinite(number)
        except AttributeError:
            return not (math.isinf(number) or math.isnan(number))
    return False
