# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os

import arcpy

from operations import common


RASTER_RGB_EXTENSIONS = (".tif", ".tiff")
RGB_BAND_INDEX = "1;2;3"


def add_layer(context, arguments, step_outputs):
    path = arguments["path"]
    if not os.path.exists(path) and not arcpy.Exists(path):
        raise common.OperationError("Layer path not found: %s" % path)
    mxd = common.current_mxd()
    df = common.active_data_frame(mxd)
    layer, needs_add = _layer_for_path(path, mxd, df)
    if needs_add:
        arcpy.mapping.AddLayer(df, layer, "TOP")
    common.refresh()
    return {"added_layer": path, "layer_name": getattr(layer, "name", os.path.splitext(os.path.basename(path))[0])}


def _layer_for_path(path, mxd, df):
    if _is_rgb_raster_path(path):
        layer_name = os.path.basename(path)
        before_count = _matching_layer_count(mxd, df, path, None)
        result = _make_raster_layer(path, layer_name)
        layer = _layer_from_result(result)
        if _matching_layer_count(mxd, df, path, layer) > before_count:
            return _last_matching_layer(mxd, df, path, layer) or layer, False
        return layer, True
    return arcpy.mapping.Layer(path), True


def _make_raster_layer(path, layer_name):
    add_outputs_to_map = arcpy.env.addOutputsToMap
    arcpy.env.addOutputsToMap = False
    try:
        return arcpy.MakeRasterLayer_management(path, layer_name, "", "", RGB_BAND_INDEX)
    finally:
        arcpy.env.addOutputsToMap = add_outputs_to_map


def _is_rgb_raster_path(path):
    if not _looks_like_rgb_raster(path):
        return False
    desc = arcpy.Describe(path)
    band_count = getattr(desc, "bandCount", None)
    if band_count is None:
        return False
    return int(band_count) >= 3


def _looks_like_rgb_raster(path):
    return os.path.splitext(path)[1].lower() in RASTER_RGB_EXTENSIONS


def _layer_from_result(result):
    output = result.getOutput(0) if hasattr(result, "getOutput") else result
    if hasattr(output, "supports") or hasattr(output, "name"):
        return output
    return arcpy.mapping.Layer(output)


def _matching_layer_count(mxd, df, path, layer):
    return len(_matching_layers(mxd, df, path, layer))


def _last_matching_layer(mxd, df, path, layer):
    matches = _matching_layers(mxd, df, path, layer)
    return matches[-1] if matches else None


def _matching_layers(mxd, df, path, layer):
    layers = arcpy.mapping.ListLayers(mxd, "", df)
    return [item for item in layers if _matches_layer(item, path, layer)]


def _matches_layer(item, path, layer):
    if layer is not None and item is layer:
        return True
    expected_path = common._normalize_path(path)
    source = common._safe_data_source(item)
    if source and common._normalize_path(source) == expected_path:
        return True
    if layer is not None and getattr(item, "name", "") == getattr(layer, "name", None):
        return True
    return getattr(item, "name", "") == os.path.basename(path)


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
