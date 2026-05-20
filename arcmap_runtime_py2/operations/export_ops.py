# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import re
import uuid

import arcpy

from operations import common
from operations import condition_utils


def export_map_png(context, arguments, step_outputs):
    mxd = common.current_mxd()
    output = common.output_file(context, arguments["output_name"], ".png", arguments.get("output_folder"))
    resolution = int(arguments.get("resolution", 150))
    arcpy.mapping.ExportToPNG(mxd, output, resolution=resolution)
    return {"output": output}


def export_map_pdf(context, arguments, step_outputs):
    mxd = common.current_mxd()
    output = common.output_file(context, arguments["output_name"], ".pdf", arguments.get("output_folder"))
    resolution = int(arguments.get("resolution", 150))
    arcpy.mapping.ExportToPDF(mxd, output, resolution=resolution)
    return {"output": output}


def export_table_csv(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    output = common.output_file(context, arguments["output_name"], ".csv", arguments.get("output_folder"))
    common.export_table_to_csv(layer, output, bool(arguments.get("selected_only", False)))
    return {"output": output}


def split_by_field(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    field = condition_utils.require_field(layer, arguments["field"])
    include_null = bool(arguments.get("include_null", True))
    max_outputs = int(arguments.get("max_outputs", 200))
    if max_outputs <= 0:
        raise common.OperationError(u"max_outputs 必须大于 0。")

    values = _unique_field_values(layer, field.name, include_null, bool(arguments.get("selected_only", False)))
    if not values:
        raise common.OperationError(u"字段 %s 没有可导出的取值。" % field.name)
    if len(values) > max_outputs:
        raise common.OperationError(u"字段 %s 有 %s 个不同取值，超过当前上限 %s。请缩小范围或提高 max_outputs。" % (field.name, len(values), max_outputs))

    output_format = _output_format(arguments)
    prefix = common.safe_output_name(arguments["output_name"])
    output_base = _output_base(context, arguments, output_format)
    input_source = layer if bool(arguments.get("selected_only", False)) else (common._safe_data_source(layer) or layer)
    outputs = []
    used_names = set()
    for index, value in enumerate(values, 1):
        name = _output_name_for_value(prefix, value, index, used_names)
        output = _output_path(output_base, name, output_format)
        _ensure_output_available(output)
        temp_layer = "arcmap_ai_split_%s" % uuid.uuid4().hex
        try:
            where_clause = _where_for_value(layer, field.name, value)
            arcpy.MakeFeatureLayer_management(input_source, temp_layer, where_clause)
            arcpy.CopyFeatures_management(temp_layer, output)
            outputs.append(output)
        finally:
            try:
                arcpy.Delete_management(temp_layer)
            except Exception:
                pass

    common.refresh()
    return {"outputs": outputs, "count": len(outputs), "output_format": output_format}


def _unique_field_values(layer, field_name, include_null, selected_only):
    source = layer if selected_only else (common._safe_data_source(layer) or layer)
    values = []
    seen = set()
    with arcpy.da.SearchCursor(source, [field_name]) as cursor:
        for row in cursor:
            value = row[0]
            if value is None and not include_null:
                continue
            key = _value_key(value)
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
    return sorted(values, key=lambda item: common._text(item) if item is not None else u"")


def _where_for_value(layer, field_name, value):
    if value is None:
        return condition_utils.compile_where(layer, {"field": field_name, "op": "is_null"})
    return condition_utils.compile_where(layer, {"field": field_name, "op": "eq", "value": value})


def _output_format(arguments):
    value = common._text(arguments.get("output_format", "")).strip().lower()
    if value:
        return value
    workspace = common._text(arguments.get("output_workspace", "")).strip().lower()
    if workspace.endswith(u".gdb"):
        return "gdb"
    return "shp"


def _output_base(context, arguments, output_format):
    if output_format == "gdb":
        return common.output_gdb(context, arguments.get("output_workspace") or arguments.get("output_folder"))
    if output_format == "shp":
        folder = arguments.get("output_folder") or arguments.get("output_workspace")
        if folder and common._text(folder).strip().lower().endswith(u".gdb"):
            raise common.OperationError(u"导出 shp 时输出位置必须是普通文件夹，不能是 GDB。")
        return common.output_directory(context, folder)
    raise common.OperationError(u"output_format 只支持 shp 或 gdb。")


def _output_path(output_base, output_name, output_format):
    if output_format == "gdb":
        return os.path.join(output_base, output_name)
    return os.path.join(output_base, output_name + ".shp")


def _ensure_output_available(path):
    if arcpy.Exists(path) or os.path.exists(path):
        raise common.OperationError("Output already exists: %s" % path)


def _output_name_for_value(prefix, value, index, used_names):
    suffix = _safe_name_part(value, "group_%03d" % index)
    base = _trim_name("%s_%s" % (prefix, suffix))
    name = base
    counter = 2
    while name.lower() in used_names:
        name = _trim_name("%s_%02d" % (base, counter))
        counter += 1
    used_names.add(name.lower())
    return common.safe_output_name(name)


def _safe_name_part(value, fallback):
    if value is None:
        return "null"
    text = common._text(value)
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    if not text:
        text = fallback
    if text[0].isdigit():
        text = "v_" + text
    return _trim_name(text)


def _trim_name(value):
    return value[:120].rstrip("_") or "group"


def _value_key(value):
    if value is None:
        return "__NULL__"
    return "%s:%s" % (type(value).__name__, common._text(value))
