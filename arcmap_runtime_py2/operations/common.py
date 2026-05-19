# -*- coding: utf-8 -*-
from __future__ import absolute_import

import csv
import os
import re

import arcpy


try:
    unicode
except NameError:
    unicode = str


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


def output_gdb(context, output_workspace=None):
    if output_workspace:
        gdb = _text(output_workspace)
        if not gdb.lower().endswith(u".gdb"):
            raise OperationError(u"Output workspace must be a file geodatabase: %s" % gdb)
        folder = os.path.dirname(gdb)
        name = os.path.basename(gdb)
        if not folder or not os.path.isdir(folder):
            raise OperationError(u"Output workspace folder not found: %s" % folder)
        if not arcpy.Exists(gdb):
            arcpy.CreateFileGDB_management(folder, name)
        return gdb

    mxd_path = context.get("mxd_path")
    if not mxd_path:
        raise OperationError(u"当前 MXD 未保存。请指定输出 GDB，或先保存 MXD。")
    folder = os.path.dirname(mxd_path)
    gdb = os.path.join(folder, "ArcMapAI_Output.gdb")
    if not arcpy.Exists(gdb):
        arcpy.CreateFileGDB_management(folder, "ArcMapAI_Output.gdb")
    return gdb


def output_directory(context, output_folder=None):
    if output_folder:
        folder = _text(output_folder)
        if not os.path.isdir(folder):
            raise OperationError(u"Output folder not found: %s" % folder)
        return folder

    mxd_path = context.get("mxd_path")
    if not mxd_path:
        raise OperationError(u"当前 MXD 未保存。请指定输出文件夹，或先保存 MXD。")
    folder = os.path.join(os.path.dirname(mxd_path), "ArcMapAI_Output")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    return folder


def safe_output_name(name):
    if not name or not SAFE_NAME_RE.match(name):
        raise OperationError("Invalid output_name: %s" % name)
    return name


def output_feature_class(context, output_name, output_workspace=None):
    gdb = output_gdb(context, output_workspace)
    name = safe_output_name(output_name)
    path = os.path.join(gdb, name)
    if arcpy.Exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


def output_file(context, output_name, extension, output_folder=None):
    folder = output_directory(context, output_folder)
    name = safe_output_name(output_name)
    path = os.path.join(folder, name + extension)
    if os.path.exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


def add_output_layer(path):
    mxd = current_mxd()
    df = active_data_frame(mxd)
    if _layer_source_exists(mxd, df, path):
        refresh()
        return {"already_visible": True}
    layer = arcpy.mapping.Layer(path)
    arcpy.mapping.AddLayer(df, layer, "TOP")
    refresh()
    return {"added": True}


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
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return unicode(value)


def _layer_source_exists(mxd, df, path):
    expected = _normalize_path(path)
    for layer in arcpy.mapping.ListLayers(mxd, "", df):
        source = _safe_data_source(layer)
        if source and _normalize_path(source) == expected:
            return True
    return False


def _safe_data_source(layer):
    try:
        if layer.supports("DATASOURCE"):
            return layer.dataSource
    except Exception:
        pass
    return None


def _normalize_path(path):
    return os.path.normcase(os.path.normpath(_text(path)))
