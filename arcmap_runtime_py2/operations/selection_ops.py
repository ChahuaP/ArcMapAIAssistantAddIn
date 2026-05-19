# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from operations import common


def select_by_attribute(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"])
    selection_type = arguments.get("selection_type", "NEW_SELECTION")
    arcpy.SelectLayerByAttribute_management(layer, selection_type, arguments["where_clause"])
    common.refresh()
    return {"layer": layer.name, "selection_type": selection_type}


def select_by_location(context, arguments, step_outputs):
    target = common.find_layer(context, arguments["target_layer"])
    select_layer = common.find_layer(context, arguments["select_layer"])
    selection_type = arguments.get("selection_type", "NEW_SELECTION")
    arcpy.SelectLayerByLocation_management(target, arguments["overlap_type"], select_layer, "", selection_type)
    common.refresh()
    return {"target_layer": target.name, "select_layer": select_layer.name}


def clear_selection(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"])
    arcpy.SelectLayerByAttribute_management(layer, "CLEAR_SELECTION")
    common.refresh()
    return {"layer": layer.name, "cleared": True}


def export_selected_features(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"])
    output = common.output_feature_class(context, arguments["output_name"])
    arcpy.CopyFeatures_management(layer, output)
    common.add_output_layer(output)
    return {"output": output}
