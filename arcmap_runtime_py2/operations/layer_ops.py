# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os

import arcpy

from operations import common


def add_layer(context, arguments, step_outputs):
    path = arguments["path"]
    if not os.path.exists(path) and not arcpy.Exists(path):
        raise common.OperationError("Layer path not found: %s" % path)
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    layer = arcpy.mapping.Layer(path)
    arcpy.mapping.AddLayer(df, layer, "TOP")
    common.refresh()
    return {"added_layer": path}


def set_layer_visibility(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"])
    layer.visible = bool(arguments["visible"])
    common.refresh()
    return {"layer": layer.name, "visible": bool(layer.visible)}


def zoom_to_layer(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"])
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    df.extent = layer.getExtent()
    common.refresh()
    return {"zoomed_to": layer.name}


def zoom_to_selection(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"])
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    df.extent = layer.getSelectedExtent()
    common.refresh()
    return {"zoomed_to_selection": layer.name}


def refresh_view(context, arguments, step_outputs):
    common.refresh()
    return {"refreshed": True}
