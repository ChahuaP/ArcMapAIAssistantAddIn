# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from operations import common


def list_layers(context, arguments, step_outputs):
    return {"layers": [{"layer_ref": layer["layer_ref"], "name": layer["name"], "visible": layer["visible"]} for layer in context.get("layers", [])]}


def describe_layer(context, arguments, step_outputs):
    layer_info = _layer_info(context, arguments["layer"], step_outputs)
    return layer_info


def list_fields(context, arguments, step_outputs):
    layer_info = _layer_info(context, arguments["layer"], step_outputs)
    return {"layer": layer_info["name"], "fields": layer_info.get("fields", [])}


def get_selection_count(context, arguments, step_outputs):
    layer_info = _layer_info(context, arguments["layer"], step_outputs)
    return {"layer": layer_info["name"], "selected_count": layer_info.get("selected_count", 0)}


def get_map_extent(context, arguments, step_outputs):
    return {"extent": context.get("extent")}


def get_spatial_reference(context, arguments, step_outputs):
    return {"spatial_reference": context.get("spatial_reference")}


def _layer_info(context, layer_value, step_outputs=None):
    layer = common.find_layer(context, layer_value, step_outputs)
    for item in context.get("layers", []):
        if item["name"] == layer.name or item["longName"] == getattr(layer, "longName", layer.name):
            return item
    raise common.OperationError("Layer metadata not found: %s" % layer_value)
