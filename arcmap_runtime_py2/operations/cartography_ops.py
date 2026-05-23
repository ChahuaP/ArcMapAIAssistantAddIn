# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os

import arcpy

from operations import common
from operations import condition_utils


def apply_symbology_from_layer(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["target_layer"], step_outputs)
    source_layer = _source_symbology_layer(context, arguments, step_outputs)
    symbology_only = bool(arguments.get("symbology_only", True))
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    arcpy.mapping.UpdateLayer(df, layer, source_layer, symbology_only)
    common.refresh()
    return {"layer": layer.name, "symbology_only": symbology_only}


def set_unique_values(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    field = condition_utils.require_field(layer, arguments["field"])
    _require_symbology_type(layer, "UNIQUE_VALUES")
    symbology = layer.symbology
    symbology.valueField = field.name
    if "class_values" in arguments:
        symbology.classValues = [common._text(value) for value in arguments["class_values"]]
        if "class_labels" in arguments:
            labels = [common._text(value) for value in arguments["class_labels"]]
            _require_equal_length(labels, symbology.classValues, "class_labels", "class_values")
            symbology.classLabels = labels
    else:
        symbology.addAllValues()
    if "show_other_values" in arguments:
        symbology.showOtherValues = bool(arguments["show_other_values"])
    common.refresh()
    return {"layer": layer.name, "field": field.name, "renderer": "UNIQUE_VALUES"}


def set_graduated_colors(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    field = condition_utils.require_field(layer, arguments["field"])
    _require_symbology_type(layer, "GRADUATED_COLORS")
    symbology = layer.symbology
    symbology.valueField = field.name
    _set_normalization(layer, symbology, arguments.get("normalization_field"))
    _set_classes(symbology, arguments)
    if bool(arguments.get("reclassify", False)) and hasattr(symbology, "reclassify"):
        symbology.reclassify()
    common.refresh()
    return {"layer": layer.name, "field": field.name, "renderer": "GRADUATED_COLORS"}


def set_raster_classified(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    _require_symbology_type(layer, "RASTER_CLASSIFIED")
    symbology = layer.symbology
    if arguments.get("value_field"):
        symbology.valueField = common._text(arguments["value_field"])
    _set_normalization(layer, symbology, arguments.get("normalization_field"))
    if "excluded_values" in arguments:
        symbology.excludedValues = common._text(arguments["excluded_values"])
    _set_classes(symbology, arguments)
    if bool(arguments.get("reclassify", False)) and hasattr(symbology, "reclassify"):
        symbology.reclassify()
    common.refresh()
    return {"layer": layer.name, "renderer": "RASTER_CLASSIFIED"}


def set_raster_bands(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    source = common._safe_data_source(layer)
    if not source:
        raise common.OperationError(u"图层没有可用的数据源，无法重新指定波段：%s" % layer.name)
    band_text = _band_index_text(source, arguments["band_indices"])
    layer_name = common._text(arguments.get("layer_name") or layer.name)
    result = arcpy.MakeRasterLayer_management(source, layer_name, "", "", band_text)
    new_layer = _layer_from_result(result)
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    arcpy.mapping.AddLayer(df, new_layer, "TOP")
    if arguments.get("replace", True):
        arcpy.mapping.RemoveLayer(df, layer)
    common.refresh()
    return {"layer": getattr(new_layer, "name", layer_name), "source": source, "band_index": band_text}


def set_transparency(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    value = int(arguments["transparency"])
    if value < 0 or value > 100:
        raise common.OperationError(u"透明度必须在 0 到 100 之间。")
    layer.transparency = value
    common.refresh()
    return {"layer": layer.name, "transparency": value}


def save_layer_file(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    output = common.output_file(context, arguments["output_name"], ".lyr", arguments.get("output_folder"))
    is_relative_path = arguments.get("is_relative_path", "ABSOLUTE")
    arcpy.SaveToLayerFile_management(layer, output, is_relative_path)
    return {"output": output, "layer": layer.name, "format": "lyr"}


def _require_symbology_type(layer, expected):
    actual = getattr(layer, "symbologyType", None)
    if actual != expected:
        raise common.OperationError(
            u"%s 当前渲染器是 %s，不是 %s。请先用 .lyr 样式模板切换渲染器。"
            % (layer.name, actual or u"未知", expected)
        )


def _source_symbology_layer(context, arguments, step_outputs):
    if arguments.get("source_layer"):
        return common.find_layer(context, arguments["source_layer"], step_outputs)
    template_path = common._text(arguments.get("template_path", ""))
    if not template_path:
        raise common.OperationError(u"必须提供 template_path 或 source_layer。")
    if not os.path.exists(template_path) and not arcpy.Exists(template_path):
        raise common.OperationError(u"样式模板不存在：%s" % template_path)
    return arcpy.mapping.Layer(template_path)


def _set_normalization(layer, symbology, normalization_field):
    if normalization_field is None:
        return
    if common._text(normalization_field).strip() == "":
        symbology.normalization = None
        return
    field = condition_utils.require_field(layer, normalization_field)
    symbology.normalization = field.name


def _set_classes(symbology, arguments):
    if "class_break_values" in arguments:
        values = [float(value) for value in arguments["class_break_values"]]
        _require_sorted(values, "class_break_values")
        symbology.classBreakValues = values
        if "class_break_labels" in arguments:
            labels = [common._text(value) for value in arguments["class_break_labels"]]
            if len(labels) != len(values) - 1:
                raise common.OperationError(u"class_break_labels 数量必须比 class_break_values 少 1。")
            symbology.classBreakLabels = labels
        return
    if "num_classes" in arguments:
        count = int(arguments["num_classes"])
        if count <= 0:
            raise common.OperationError(u"num_classes 必须大于 0。")
        symbology.numClasses = count


def _band_index_text(source, band_indices):
    if not isinstance(band_indices, list) or not band_indices:
        raise common.OperationError(u"band_indices 必须是非空整数数组。")
    band_count = _band_count(source)
    values = []
    for value in band_indices:
        index = int(value)
        if index <= 0:
            raise common.OperationError(u"波段编号必须从 1 开始。")
        if band_count and index > band_count:
            raise common.OperationError(u"波段 %s 超过栅格实际波段数 %s。" % (index, band_count))
        values.append(str(index))
    return ";".join(values)


def _band_count(source):
    desc = arcpy.Describe(source)
    value = getattr(desc, "bandCount", None)
    return int(value) if value is not None else 0


def _layer_from_result(result):
    output = result.getOutput(0) if hasattr(result, "getOutput") else result
    if hasattr(output, "supports") or hasattr(output, "name"):
        return output
    return arcpy.mapping.Layer(output)


def _require_sorted(values, name):
    if values != sorted(values):
        raise common.OperationError(u"%s 必须按从小到大排序。" % name)


def _require_equal_length(left, right, left_name, right_name):
    if len(left) != len(right):
        raise common.OperationError(u"%s 数量必须等于 %s。" % (left_name, right_name))
