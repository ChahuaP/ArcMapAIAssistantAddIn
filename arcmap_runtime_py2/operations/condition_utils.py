# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

try:
    import condition_protocol
    from operations import common
except ImportError:
    from .. import condition_protocol
    from . import common


try:
    unicode
except NameError:
    unicode = str


TEXT_TYPES = condition_protocol.TEXT_FIELD_TYPES
NUMBER_TYPES = condition_protocol.NUMBER_FIELD_TYPES
ARCPY_EXECUTE_ERROR = getattr(arcpy, "ExecuteError", RuntimeError)


def compile_where(layer, condition):
    if not isinstance(condition, dict) or not condition:
        raise common.OperationError(u"where 条件必须是结构化对象。")
    condition = condition_protocol.normalize_condition_tree(condition)
    return _compile_node(layer, condition)


def condition_fields(condition):
    if not isinstance(condition, dict):
        return []
    condition = condition_protocol.normalize_condition_tree(condition)
    op = _operator(condition)
    if op in ("and", "or"):
        fields = []
        for child in condition.get("conditions") or []:
            fields.extend(condition_fields(child))
        return _unique(fields)
    if op == "not":
        return condition_fields(condition.get("condition"))
    fields = []
    for name in ("field", "value_field"):
        field = condition.get(name)
        if field:
            fields.append(common._text(field))
    return fields


def count_where(layer, condition):
    where_clause = compile_where(layer, condition)
    with common.read_layer(layer, False, where_clause) as source:
        result = arcpy.GetCount_management(source)
        return int(result.getOutput(0))


def field_exists(layer, field_name):
    try:
        _field(layer, field_name)
        return True
    except common.OperationError:
        return False


def require_field(layer, field_name):
    return _field(layer, field_name)


def _compile_node(layer, condition):
    op = _operator(condition)
    if op in ("and", "or"):
        children = condition.get("conditions")
        if not isinstance(children, list) or not children:
            raise common.OperationError(u"%s 条件必须包含 conditions。" % op)
        joined = (" %s " % op.upper()).join(["(%s)" % _compile_node(layer, child) for child in children])
        return joined
    if op == "not":
        child = condition.get("condition")
        if not isinstance(child, dict):
            raise common.OperationError(u"not 条件必须包含 condition。")
        return "NOT (%s)" % _compile_node(layer, child)

    field = _field(layer, condition.get("field"))
    field_sql = _field_sql(layer, field.name)

    if op == "eq":
        return "%s = %s" % (field_sql, _comparison_operand(layer, field, condition, op))
    if op == "ne":
        return "%s <> %s" % (field_sql, _comparison_operand(layer, field, condition, op))
    if op == "gt":
        return "%s > %s" % (field_sql, _comparison_operand(layer, field, condition, op))
    if op == "gte":
        return "%s >= %s" % (field_sql, _comparison_operand(layer, field, condition, op))
    if op == "lt":
        return "%s < %s" % (field_sql, _comparison_operand(layer, field, condition, op))
    if op == "lte":
        return "%s <= %s" % (field_sql, _comparison_operand(layer, field, condition, op))
    if op == "between":
        values = condition.get("values")
        if not isinstance(values, list) or len(values) != 2:
            raise common.OperationError(u"between 条件必须提供两个值。")
        return "%s BETWEEN %s AND %s" % (field_sql, _literal(field, values[0]), _literal(field, values[1]))
    if op == "in":
        values = condition.get("values")
        if not isinstance(values, list) or not values:
            raise common.OperationError(u"in 条件必须提供值列表。")
        return "%s IN (%s)" % (field_sql, ", ".join([_literal(field, value) for value in values]))
    if op == "like":
        if getattr(field, "type", "") not in TEXT_TYPES:
            raise common.OperationError(u"like 条件只能用于文本字段：%s" % field.name)
        return "%s LIKE %s" % (field_sql, _literal(field, _value(condition)))
    if op == "is_null":
        return "%s IS NULL" % field_sql
    if op == "is_not_null":
        return "%s IS NOT NULL" % field_sql

    raise common.OperationError(u"不支持的条件操作符：%s" % op)


def _operator(condition):
    return condition_protocol.canonical_operator(condition, error_cls=common.OperationError, missing_message=u"条件缺少 op。")


def _value(condition):
    if "value" not in condition:
        raise common.OperationError(u"条件缺少 value。")
    return condition["value"]


def _comparison_operand(layer, left_field, condition, op):
    has_value = "value" in condition
    has_value_field = "value_field" in condition
    if has_value == has_value_field:
        raise common.OperationError(u"%s 条件必须且只能提供 value 或 value_field 其中一个。" % op)
    if has_value:
        return _literal(left_field, condition["value"])
    if op not in condition_protocol.FIELD_COMPARISON_OPERATORS:
        raise common.OperationError(u"%s 条件不能使用 value_field。" % op)
    right_field = _field(layer, condition["value_field"])
    left_family = condition_protocol.field_type_family(getattr(left_field, "type", ""))
    right_family = condition_protocol.field_type_family(getattr(right_field, "type", ""))
    if left_family and right_family and left_family != right_family:
        raise common.OperationError(
            u"字段比较类型不兼容：%s(%s) 与 %s(%s)。"
            % (left_field.name, left_field.type, right_field.name, right_field.type)
        )
    return _field_sql(layer, right_field.name)


def _field(layer, field_name):
    if not field_name:
        raise common.OperationError(u"条件缺少字段名。")
    requested = common._text(field_name)
    matches = []
    for field in arcpy.ListFields(layer):
        if field.name == requested or field.name.lower() == requested.lower():
            matches.append(field)
    if len(matches) != 1:
        if not matches:
            raise common.OperationError(u"字段不存在：%s" % requested)
        raise common.OperationError(u"字段名不唯一：%s" % requested)
    return matches[0]


def _field_sql(layer, field_name):
    try:
        desc = arcpy.Describe(layer)
        workspace = getattr(desc, "path", "")
        return arcpy.AddFieldDelimiters(workspace, field_name)
    except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError):
        return '"%s"' % field_name


def _literal(field, value):
    field_type = getattr(field, "type", "")
    if value is None:
        return "NULL"
    if field_type in NUMBER_TYPES:
        if not condition_protocol.is_number_value(value):
            raise common.OperationError(u"数值字段条件值不是数字：%s" % common._text(value))
        return common._text(value)
    text = common._text(value).replace("'", "''")
    if field_type == "Date":
        return "date '%s'" % text
    return "'%s'" % text


def _unique(values):
    result = []
    seen = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
