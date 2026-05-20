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
    return {"added_layer": path, "layer_name": getattr(layer, "name", os.path.splitext(os.path.basename(path))[0])}


def set_layer_visibility(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    layer.visible = bool(arguments["visible"])
    common.refresh()
    return {"layer": layer.name, "visible": bool(layer.visible)}


def remove_layer(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    name = layer.name
    arcpy.mapping.RemoveLayer(df, layer)
    common.refresh()
    return {"removed_layer": name}


def clear_layers(context, arguments, step_outputs):
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    layers = list(arcpy.mapping.ListLayers(mxd, "", df))
    for layer in layers:
        arcpy.mapping.RemoveLayer(df, layer)
    common.refresh()
    return {"removed_count": len(layers)}


def move_layer(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    position = arguments["position"].upper()
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    layers = list(arcpy.mapping.ListLayers(mxd, "", df))
    if layer not in layers:
        raise common.OperationError(u"Layer not found in current data frame: %s" % layer.name)
    index = layers.index(layer)

    if position == "TOP":
        reference = layers[0]
        insert_position = "BEFORE"
    elif position == "BOTTOM":
        reference = layers[-1]
        insert_position = "AFTER"
    elif position == "UP":
        if index == 0:
            common.refresh()
            return {"layer": layer.name, "position": position, "moved": False}
        reference = layers[index - 1]
        insert_position = "BEFORE"
    elif position == "DOWN":
        if index == len(layers) - 1:
            common.refresh()
            return {"layer": layer.name, "position": position, "moved": False}
        reference = layers[index + 1]
        insert_position = "AFTER"
    elif position in ("BEFORE", "AFTER"):
        reference = common.find_layer(context, arguments.get("reference_layer"), step_outputs)
        insert_position = position
    else:
        raise common.OperationError(u"Unsupported layer move position: %s" % position)

    if reference is layer:
        common.refresh()
        return {"layer": layer.name, "position": position, "moved": False}
    arcpy.mapping.MoveLayer(df, reference, layer, insert_position)
    common.refresh()
    return {"layer": layer.name, "position": position, "moved": True}


def zoom_to_layer(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    df.extent = layer.getExtent()
    common.refresh()
    return {"zoomed_to": layer.name}


def zoom_to_selection(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    df.extent = layer.getSelectedExtent()
    common.refresh()
    return {"zoomed_to_selection": layer.name}


def refresh_view(context, arguments, step_outputs):
    common.refresh()
    return {"refreshed": True}
