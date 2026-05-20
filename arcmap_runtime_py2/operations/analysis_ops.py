# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from operations import common


def buffer(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.Buffer_analysis(layer, output, arguments["distance"])
    common.add_output_layer(output)
    return {"output": output}


def clip(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    clip_layer = common.find_layer(context, arguments["clip_layer"], step_outputs)
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.Clip_analysis(input_layer, clip_layer, output)
    common.add_output_layer(output)
    return {"output": output}


def intersect(context, arguments, step_outputs):
    layers = [common.find_layer(context, layer_value, step_outputs) for layer_value in arguments["input_layers"]]
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.Intersect_analysis(layers, output)
    common.add_output_layer(output)
    return {"output": output}


def dissolve(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    fields = arguments.get("dissolve_fields") or []
    arcpy.Dissolve_management(layer, output, fields)
    common.add_output_layer(output)
    return {"output": output}


def project(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    spatial_reference = arcpy.SpatialReference(arguments["spatial_reference"])
    arcpy.Project_management(layer, output, spatial_reference)
    common.add_output_layer(output)
    return {"output": output}


def spatial_join(context, arguments, step_outputs):
    target = common.find_layer(context, arguments["target_layer"], step_outputs)
    join = common.find_layer(context, arguments["join_layer"], step_outputs)
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.SpatialJoin_analysis(target, join, output)
    common.add_output_layer(output)
    return {"output": output}


def erase(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    erase_layer = common.find_layer(context, arguments["erase_layer"], step_outputs)
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.Erase_analysis(input_layer, erase_layer, output)
    common.add_output_layer(output)
    return {"output": output}


def identity(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    identity_layer = common.find_layer(context, arguments["identity_layer"], step_outputs)
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.Identity_analysis(input_layer, identity_layer, output)
    common.add_output_layer(output)
    return {"output": output}


def union(context, arguments, step_outputs):
    layers = [common.find_layer(context, layer_value, step_outputs) for layer_value in arguments["input_layers"]]
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.Union_analysis(layers, output)
    common.add_output_layer(output)
    return {"output": output}


def symmetrical_difference(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    update_layer = common.find_layer(context, arguments["update_layer"], step_outputs)
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.SymDiff_analysis(input_layer, update_layer, output)
    common.add_output_layer(output)
    return {"output": output}


def update_overlay(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    update_layer = common.find_layer(context, arguments["update_layer"], step_outputs)
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.Update_analysis(input_layer, update_layer, output)
    common.add_output_layer(output)
    return {"output": output}


def merge(context, arguments, step_outputs):
    layers = [common.find_layer(context, layer_value, step_outputs) for layer_value in arguments["input_layers"]]
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.Merge_management(layers, output)
    common.add_output_layer(output)
    return {"output": output}


def append(context, arguments, step_outputs):
    inputs = [common.find_layer(context, layer_value, step_outputs) for layer_value in arguments["input_layers"]]
    target = common.find_layer(context, arguments["target_layer"], step_outputs)
    schema_type = arguments.get("schema_type", "NO_TEST")
    arcpy.Append_management(inputs, target, schema_type)
    common.refresh()
    return {"target_layer": target.name, "appended_layers": len(inputs)}


def estimate_append(context, arguments, step_outputs):
    target = common.find_layer(context, arguments["target_layer"], step_outputs)
    return {"summary": u"将直接修改图层 %s：把 %s 个输入图层追加进去。" % (target.name, len(arguments["input_layers"]))}
