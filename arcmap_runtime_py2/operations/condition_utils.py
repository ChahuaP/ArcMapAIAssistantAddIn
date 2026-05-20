# -*- coding: utf-8 -*-
from __future__ import absolute_import

import uuid

import arcpy

from operations import common


try:
    unicode
except NameError:
    unicode = str


TEXT_TYPES = set(["String", "Guid", "GlobalID"])
NUMBER_TYPES = set(["SmallInteger", "Integer", "Single", "Double", "OID"])


def compile_where(layer, condition):
    if not isinstance(condition, dict) or not condition:
        raise common.OperationError(u"where 条件必须是结构化对象。")
    return _compile_node(layer, condition)


def condition_fields(condition):
    if not isinstance(condition, dict):
        return []
    op = _operator(condition)
    if op in ("and", "or"):
        fields = []
        for child in condition.get("conditions") or []:
            fields.extend(condition_fields(child))
        return _unique(fields)
    if op == "not":
        return condition_fields(condition.get("condition"))
    field = condition.get("field")
    return [common._text(field)] if field else []


def count_where(layer, condition):
    where_clause = compile_where(layer, condition)
    temp_name = "arcmap_ai_count_%s" % uuid.uuid4().hex
    try:
        arcpy.MakeFeatureLayer_management(layer, temp_name, where_clause)
        result = arcpy.GetCount_management(temp_name)
        return int(result.getOutput(0))
    finally:
        try:
            arcpy.Delete_management(temp_name)
        except Exception:
            pass


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

    if op in ("eq", "="):
        return "%s = %s" % (field_sql, _literal(field, _value(condition)))
    if op in ("ne", "!=", "<>"):
        return "%s <> %s" % (field_sql, _literal(field, _value(condition)))
    if op in ("gt", ">"):
        return "%s > %s" % (field_sql, _literal(field, _value(condition)))
    if op in ("gte", ">="):
        return "%s >= %s" % (field_sql, _literal(field, _value(condition)))
    if op in ("lt", "<"):
        return "%s < %s" % (field_sql, _literal(field, _value(condition)))
    if op in ("lte", "<="):
        return "%s <= %s" % (field_sql, _literal(field, _value(condition)))
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
        return "%s LIKE %s" % (field_sql, _literal(field, _value(condition)))
    if op == "is_null":
        return "%s IS NULL" % field_sql
    if op == "is_not_null":
        return "%s IS NOT NULL" % field_sql

    raise common.OperationError(u"不支持的条件操作符：%s" % op)


def _operator(condition):
    op = condition.get("op", condition.get("operator"))
    if op is None:
        raise common.OperationError(u"条件缺少 op。")
    op = common._text(op).strip().lower()
    aliases = {
        u"等于": "eq",
        u"不等于": "ne",
        u"大于": "gt",
        u"大于等于": "gte",
        u"小于": "lt",
        u"小于等于": "lte",
        u"之间": "between",
        u"包含于": "in",
        u"模糊匹配": "like",
        u"为空": "is_null",
        u"非空": "is_not_null"
    }
    return aliases.get(op, op)


def _value(condition):
    if "value" not in condition:
        raise common.OperationError(u"条件缺少 value。")
    return condition["value"]


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
    except Exception:
        return '"%s"' % field_name


def _literal(field, value):
    field_type = getattr(field, "type", "")
    if value is None:
        return "NULL"
    if field_type in NUMBER_TYPES:
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
