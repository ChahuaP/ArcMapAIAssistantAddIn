# -*- coding: utf-8 -*-
from __future__ import absolute_import

import csv
import os
import re

import arcpy


SAFE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class OperationError(Exception):
    pass


def current_mxd():
    return arcpy.mapping.MapDocument("CURRENT")


def active_data_frame(mxd=None):
    mxd = mxd or current_mxd()
    frames = arcpy.mapping.ListDataFrames(mxd)
    if not frames:
        raise OperationError("Current MXD has no data frame.")
    return frames[0]


def find_layer(context, layer_value):
    if not layer_value:
        raise OperationError("Layer is required.")
    raw = _text(layer_value)
    if raw.startswith(u"layer_ref:"):
        raw = raw[len(u"layer_ref:"):]

    matches = []
    for layer in context.get("layers", []):
        if raw == layer.get("layer_ref") or raw == layer.get("name") or raw == layer.get("longName"):
            matches.append(layer)
    if not matches:
        lowered = raw.lower()
        for layer in context.get("layers", []):
            if lowered == (layer.get("name") or "").lower() or lowered == (layer.get("longName") or "").lower():
                matches.append(layer)
    if len(matches) != 1:
        if not matches:
            raise OperationError(u"Layer not found: %s" % raw)
        raise OperationError(u"Layer is ambiguous: %s" % raw)

    mxd = current_mxd()
    df = active_data_frame(mxd)
    index = int(matches[0]["layer_ref"].split(":")[1])
    layers = arcpy.mapping.ListLayers(mxd, "", df)
    if index >= len(layers):
        raise OperationError("Layer index no longer exists: %s" % matches[0]["layer_ref"])
    return layers[index]


def output_gdb(context):
    mxd_path = context.get("mxd_path")
    if not mxd_path:
        raise OperationError("Save the MXD before writing output.")
    folder = os.path.dirname(mxd_path)
    gdb = os.path.join(folder, "ArcMapAI_Output.gdb")
    if not arcpy.Exists(gdb):
        arcpy.CreateFileGDB_management(folder, "ArcMapAI_Output.gdb")
    return gdb


def output_folder(context):
    mxd_path = context.get("mxd_path")
    if not mxd_path:
        raise OperationError("Save the MXD before writing output.")
    folder = os.path.join(os.path.dirname(mxd_path), "ArcMapAI_Output")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    return folder


def safe_output_name(name):
    if not name or not SAFE_NAME_RE.match(name):
        raise OperationError("Invalid output_name: %s" % name)
    return name


def output_feature_class(context, output_name):
    gdb = output_gdb(context)
    name = safe_output_name(output_name)
    path = os.path.join(gdb, name)
    if arcpy.Exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


def output_file(context, output_name, extension):
    folder = output_folder(context)
    name = safe_output_name(output_name)
    path = os.path.join(folder, name + extension)
    if os.path.exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


def add_output_layer(path):
    mxd = current_mxd()
    df = active_data_frame(mxd)
    layer = arcpy.mapping.Layer(path)
    arcpy.mapping.AddLayer(df, layer, "TOP")
    refresh()


def refresh():
    arcpy.RefreshTOC()
    arcpy.RefreshActiveView()


def export_table_to_csv(layer, path, selected_only):
    fields = [f.name for f in arcpy.ListFields(layer) if f.type not in ("Geometry", "Raster", "Blob")]
    with open(path, "wb") as f:
        writer = csv.writer(f)
        writer.writerow([field.encode("utf-8") for field in fields])
        cursor_layer = layer
        if selected_only:
            cursor_layer = layer
        with arcpy.da.SearchCursor(cursor_layer, fields) as cursor:
            for row in cursor:
                writer.writerow([_csv_value(value) for value in row])


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, unicode):
        return value.encode("utf-8")
    return value


def _text(value):
    if isinstance(value, unicode):
        return value
    if isinstance(value, str):
        return value.decode("utf-8", "replace")
    return unicode(value)
