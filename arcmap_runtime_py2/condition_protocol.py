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
FIELD_COMPARISON_OPERATORS = set(["eq", "ne", "gt", "gte", "lt", "lte"])
CONDITION_OPERATOR_HELP = "eq, ne, gt, gte, lt, lte, between, in, like, is_null, is_not_null, and, or, not"

TEXT_FIELD_TYPES = set(["String", "Guid", "GlobalID"])
NUMBER_FIELD_TYPES = set(["SmallInteger", "Integer", "Single", "Double", "OID"])
DATE_FIELD_TYPES = set(["Date"])


def field_type_family(field_type):
    if field_type in TEXT_FIELD_TYPES:
        return "text"
    if field_type in NUMBER_FIELD_TYPES:
        return "number"
    if field_type in DATE_FIELD_TYPES:
        return "date"
    return ""


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


def validate_condition_tree(condition, error_cls=ValueError):
    """Normalize and strictly validate the one production condition grammar."""
    condition = normalize_condition_tree(condition)
    if not isinstance(condition, dict) or not condition:
        raise error_cls("where must be a non-empty structured object.")
    op = canonical_operator(condition, error_cls=error_cls)
    if op in ("and", "or"):
        if set(condition) != set(["op", "conditions"]) or not isinstance(condition["conditions"], list) or len(condition["conditions"]) < 2:
            raise error_cls("%s requires exactly two or more conditions." % op)
        condition["conditions"] = [validate_condition_tree(item, error_cls) for item in condition["conditions"]]
        return condition
    if op == "not":
        if set(condition) != set(["op", "condition"]) or not isinstance(condition["condition"], dict):
            raise error_cls("not requires exactly one condition.")
        condition["condition"] = validate_condition_tree(condition["condition"], error_cls)
        return condition
    if op not in LEAF_CONDITION_OPERATORS or not isinstance(condition.get("field"), unicode) or not condition["field"].strip():
        raise error_cls("condition leaf is invalid.")
    allowed = set(["op", "field"])
    if op in VALUE_CONDITION_OPERATORS:
        allowed.update(["value", "value_field"])
        has_value, has_value_field = "value" in condition, "value_field" in condition
        if has_value == has_value_field or (has_value_field and op not in FIELD_COMPARISON_OPERATORS):
            raise error_cls("condition value/value_field is invalid.")
        if has_value_field and (not isinstance(condition["value_field"], unicode) or not condition["value_field"].strip()):
            raise error_cls("condition value_field is invalid.")
    elif op == "between":
        allowed.add("values")
        if not isinstance(condition.get("values"), list) or len(condition["values"]) != 2:
            raise error_cls("between requires exactly two values.")
    elif op == "in":
        allowed.add("values")
        if not isinstance(condition.get("values"), list) or not condition["values"]:
            raise error_cls("in requires non-empty values.")
    if set(condition) - allowed:
        raise error_cls("condition has invalid keys.")
    return condition


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
