# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from operations import common
from operations import condition_utils


def select_by_attribute(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    selection_type = arguments.get("selection_type", "NEW_SELECTION")
    where_clause = condition_utils.compile_where(layer, arguments["where"])
    arcpy.SelectLayerByAttribute_management(layer, selection_type, where_clause)
    common.refresh()
    return {"layer": layer.name, "selection_type": selection_type}


def select_by_location(context, arguments, step_outputs):
    target = common.find_layer(context, arguments["target_layer"], step_outputs)
    select_layer = common.find_layer(context, arguments["select_layer"], step_outputs)
    selection_type = arguments.get("selection_type", "NEW_SELECTION")
    search_distance = arguments.get("search_distance", "")
    arcpy.SelectLayerByLocation_management(target, arguments["overlap_type"], select_layer, search_distance, selection_type)
    common.refresh()
    return {"target_layer": target.name, "select_layer": select_layer.name}


def clear_selection(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    arcpy.SelectLayerByAttribute_management(layer, "CLEAR_SELECTION")
    common.refresh()
    return {"layer": layer.name, "cleared": True}


def export_selected_features(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    output = common.output_feature_class(context, arguments["output_name"], arguments.get("output_workspace"))
    arcpy.CopyFeatures_management(layer, output)
    common.add_output_layer(output)
    return {"output": output}
