# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from operations import common
from operations import condition_utils


FIELD_TYPE_MAP = {
    "text": "TEXT",
    "string": "TEXT",
    "short": "SHORT",
    "long": "LONG",
    "integer": "LONG",
    "float": "FLOAT",
    "double": "DOUBLE",
    "date": "DATE"
}


def add_field(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    field_name = common.safe_output_name(arguments["field_name"])
    if condition_utils.field_exists(layer, field_name):
        raise common.OperationError(u"字段已存在：%s" % field_name)
    field_type = _field_type(arguments.get("field_type", "TEXT"))
    field_length = arguments.get("field_length")
    arcpy.AddField_management(layer, field_name, field_type, "", "", field_length or "")
    return {"layer": layer.name, "field_name": field_name, "field_type": field_type}


def delete_field(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    field_name = arguments["field_name"]
    condition_utils.require_field(layer, field_name)
    arcpy.DeleteField_management(layer, field_name)
    return {"layer": layer.name, "deleted_field": field_name}


def update_rows(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    where_clause = condition_utils.compile_where(layer, arguments["where"])
    assignments = _assignments(arguments)
    fields = list(assignments.keys())
    for field in fields:
        condition_utils.require_field(layer, field)
    count = 0
    with arcpy.da.UpdateCursor(layer, fields, where_clause) as cursor:
        for row in cursor:
            for index, field in enumerate(fields):
                row[index] = assignments[field]
            cursor.updateRow(row)
            count += 1
    return {"layer": layer.name, "updated": count, "fields": list(fields)}


def delete_rows(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    where_clause = condition_utils.compile_where(layer, arguments["where"])
    oid_field = arcpy.Describe(layer).OIDFieldName
    count = 0
    with arcpy.da.UpdateCursor(layer, [oid_field], where_clause) as cursor:
        for _row in cursor:
            cursor.deleteRow()
            count += 1
    return {"layer": layer.name, "deleted": count}


def estimate_add_field(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    return {"summary": u"将直接修改图层 %s：添加字段 %s。" % (layer.name, arguments["field_name"])}


def estimate_delete_field(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    return {"summary": u"将直接修改图层 %s：删除字段 %s。" % (layer.name, arguments["field_name"])}


def estimate_update_rows(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    count = condition_utils.count_where(layer, arguments["where"])
    return {"summary": u"将直接修改图层 %s：更新 %s 条要素。" % (layer.name, count), "count": count}


def estimate_delete_rows(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    count = condition_utils.count_where(layer, arguments["where"])
    return {"summary": u"将直接修改图层 %s：删除 %s 条要素。" % (layer.name, count), "count": count}


def _field_type(value):
    key = common._text(value).strip().lower()
    return FIELD_TYPE_MAP.get(key, common._text(value).strip().upper())


def _assignments(arguments):
    assignments = arguments.get("assignments")
    if not isinstance(assignments, dict) or not assignments:
        raise common.OperationError(u"update_rows 需要 assignments。")
    normalized = {}
    for field, value in assignments.items():
        normalized[common._text(field)] = value
    return normalized
