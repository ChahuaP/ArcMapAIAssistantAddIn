# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from operations import common


def buffer(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"])
    output = common.output_feature_class(context, arguments["output_name"])
    arcpy.Buffer_analysis(layer, output, arguments["distance"])
    common.add_output_layer(output)
    return {"output": output}


def clip(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"])
    clip_layer = common.find_layer(context, arguments["clip_layer"])
    output = common.output_feature_class(context, arguments["output_name"])
    arcpy.Clip_analysis(input_layer, clip_layer, output)
    common.add_output_layer(output)
    return {"output": output}


def intersect(context, arguments, step_outputs):
    layers = [common.find_layer(context, layer_value) for layer_value in arguments["input_layers"]]
    output = common.output_feature_class(context, arguments["output_name"])
    arcpy.Intersect_analysis(layers, output)
    common.add_output_layer(output)
    return {"output": output}


def dissolve(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"])
    output = common.output_feature_class(context, arguments["output_name"])
    fields = arguments.get("dissolve_fields") or []
    arcpy.Dissolve_management(layer, output, fields)
    common.add_output_layer(output)
    return {"output": output}


def project(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"])
    output = common.output_feature_class(context, arguments["output_name"])
    spatial_reference = arcpy.SpatialReference(arguments["spatial_reference"])
    arcpy.Project_management(layer, output, spatial_reference)
    common.add_output_layer(output)
    return {"output": output}


def spatial_join(context, arguments, step_outputs):
    target = common.find_layer(context, arguments["target_layer"])
    join = common.find_layer(context, arguments["join_layer"])
    output = common.output_feature_class(context, arguments["output_name"])
    arcpy.SpatialJoin_analysis(target, join, output)
    common.add_output_layer(output)
    return {"output": output}
