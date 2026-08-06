# -*- coding: utf-8 -*-
from __future__ import absolute_import

try:
    import context_reader
except ImportError:
    from .. import context_reader

from . import common


def list_layers(context, arguments, step_outputs):
    live_context = context_reader.read_context()
    return {"layers": [
        {"layer_ref": layer["layer_ref"], "name": layer["name"], "visible": layer["visible"]}
        for layer in live_context.get("layers", [])
    ]}


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
    return {"extent": context_reader.read_context().get("extent")}


def get_spatial_reference(context, arguments, step_outputs):
    return {"spatial_reference": context_reader.read_context().get("spatial_reference")}


def _layer_info(context, layer_value, step_outputs=None):
    layer = common.find_layer(context, layer_value, step_outputs)
    for item in context_reader.read_context().get("layers", []):
        if item["name"] == layer.name or item["longName"] == getattr(layer, "longName", layer.name):
            return item
    raise common.OperationError("Layer metadata not found: %s" % layer_value)
