# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from operations import common


def copy_features(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    output = _output(context, arguments)
    arcpy.CopyFeatures_management(layer, output)
    return {"output": output, "feature_count": _feature_count(output)}


def multipart_to_singlepart(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    output = _output(context, arguments)
    arcpy.MultipartToSinglepart_management(layer, output)
    return {"output": output, "feature_count": _feature_count(output)}


def repair_geometry(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    delete_null = arguments.get("delete_null") or "DELETE_NULL"
    arcpy.RepairGeometry_management(layer, delete_null)
    return {"layer": getattr(layer, "name", common._text(arguments["layer"])), "delete_null": delete_null}


def estimate_repair_geometry(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    return {"summary": u"将直接修复图层 %s 的源数据几何。" % getattr(layer, "name", common._text(arguments["layer"]))}


def define_projection(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    spatial_reference = arcpy.SpatialReference(int(arguments["wkid"]))
    arcpy.DefineProjection_management(layer, spatial_reference)
    return {
        "layer": getattr(layer, "name", common._text(arguments["layer"])),
        "wkid": int(arguments["wkid"]),
        "spatial_reference": common._text(getattr(spatial_reference, "name", ""))
    }


def estimate_define_projection(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    return {"summary": u"将直接修改图层 %s 的坐标系元数据为 WKID %s。" % (getattr(layer, "name", common._text(arguments["layer"])), int(arguments["wkid"]))}


def add_xy(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    arcpy.AddXY_management(layer)
    return {"layer": getattr(layer, "name", common._text(arguments["layer"]))}


def estimate_add_xy(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    return {"summary": u"将直接给图层 %s 的源数据添加或更新 POINT_X、POINT_Y 字段。" % getattr(layer, "name", common._text(arguments["layer"]))}


def _output(context, arguments):
    return common.output_feature_dataset(
        context,
        arguments["output_name"],
        arguments.get("output_workspace"),
        arguments.get("output_folder"),
        arguments.get("output_format")
    )


def _feature_count(layer_or_path):
    result = arcpy.GetCount_management(layer_or_path)
    value = result.getOutput(0) if hasattr(result, "getOutput") else result
    return int(value)
