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


INVALID_OUTPUT_NAME_RE = re.compile(u'[<>:"/\\\\|?*\\x00-\\x1f]')
SAFE_EXTENSION_RE = re.compile(r"^\.[A-Za-z0-9]{1,12}$")


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


def find_layer(context, layer_value, step_outputs=None):
    if not layer_value:
        raise OperationError("Layer is required.")
    raw = _text(layer_value)
    if raw.startswith(u"layer_ref:"):
        raw = raw[len(u"layer_ref:"):]
    if raw.startswith(u"layer:"):
        return _find_layer_by_ref(raw)
    if raw.startswith(u"from_step:"):
        return _find_layer_from_step(raw[len(u"from_step:"):], step_outputs or {})

    matches = []
    for layer in context.get("layers", []):
        if raw in (layer.get("layer_ref"), layer.get("name"), layer.get("longName"), layer.get("dataSource")):
            matches.append(layer)

    if len(matches) != 1:
        live_match = _find_live_layer_exact(raw)
        if live_match is not None:
            return live_match
        if not matches:
            raise OperationError(u"Layer not found: %s" % raw)
        raise OperationError(u"Layer is ambiguous: %s" % raw)

    layer_ref = matches[0].get("layer_ref", "")
    if layer_ref.startswith("from_step:"):
        return _find_layer_from_step(layer_ref[len("from_step:"):], step_outputs or {})
    if not layer_ref.startswith("layer:"):
        live_match = _find_live_layer_exact(raw)
        if live_match is not None:
            return live_match
        raise OperationError(u"Layer metadata is not executable: %s" % raw)
    return _find_layer_by_ref(layer_ref)


def _find_layer_by_ref(layer_ref):
    index = int(layer_ref.split(":")[1])
    mxd = current_mxd()
    df = active_data_frame(mxd)
    layers = arcpy.mapping.ListLayers(mxd, "", df)
    if index >= len(layers):
        raise OperationError("Layer index no longer exists: %s" % layer_ref)
    return layers[index]


def output_gdb(context, output_workspace=None):
    if output_workspace:
        workspace = _resolve_output_workspace(context, output_workspace)
        if workspace.lower().endswith(u".gdb"):
            gdb = workspace
        else:
            if not os.path.isdir(workspace):
                raise OperationError(u"Output folder not found: %s" % workspace)
            gdb = os.path.join(workspace, "ArcMapAI_Output.gdb")
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
    text = _text(name).strip() if name else u""
    if (
        not text
        or text != _text(name)
        or text in (u".", u"..")
        or u"." in text
        or INVALID_OUTPUT_NAME_RE.search(text)
    ):
        raise OperationError("Invalid output_name: %s" % name)
    return text


def output_feature_class(context, output_name, output_workspace=None):
    gdb = output_gdb(context, output_workspace)
    name = safe_output_name(output_name)
    path = os.path.join(gdb, name)
    if arcpy.Exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


def output_shapefile(context, output_name, output_folder=None):
    folder = output_directory(context, output_folder)
    name = safe_output_name(output_name)
    path = os.path.join(folder, name + ".shp")
    if arcpy.Exists(path) or os.path.exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


def output_feature_dataset(context, output_name, output_workspace=None, output_folder=None, output_format=None):
    fmt = _normalize_output_format(output_format)
    if not fmt and output_folder:
        fmt = "shp"
    if fmt in ("", "gdb"):
        return output_feature_class(context, output_name, output_workspace)
    if fmt == "shp":
        folder = output_folder or _folder_workspace(output_workspace)
        return output_shapefile(context, output_name, folder)
    raise OperationError(u"Unsupported feature output format: %s" % output_format)


def output_dataset(context, output_name, output_policy, output_workspace=None, output_folder=None, output_format=None):
    policy = output_policy if isinstance(output_policy, dict) else {}
    output_type = _output_policy_type(policy)
    if output_type == "feature_class":
        fmt = output_format or (None if output_folder else policy.get("default_format"))
        return output_feature_dataset(context, output_name, output_workspace, output_folder, fmt)
    if output_type == "file":
        return output_file(context, output_name, _policy_extension(policy, output_format), output_folder)
    if output_type == "raster":
        return output_file(context, output_name, _raster_extension(policy, output_format), output_folder)
    raise OperationError(u"Unsupported output_policy.type: %s" % output_type)


def output_file(context, output_name, extension, output_folder=None):
    folder = output_directory(context, output_folder)
    name = safe_output_name(output_name)
    extension = _normalize_extension(extension)
    path = os.path.join(folder, name + extension)
    if os.path.exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


def _folder_workspace(output_workspace):
    if not output_workspace:
        return None
    workspace = _text(output_workspace)
    if workspace.lower().endswith(u".gdb"):
        raise OperationError(u"Shapefile output requires an output folder, not a geodatabase: %s" % workspace)
    return workspace


def _output_policy_type(policy):
    value = policy.get("type")
    if not value:
        return "feature_class"
    text = _text(value).strip().lower()
    if text in ("vector", "feature", "featureclass"):
        return "feature_class"
    return text


def _policy_extension(policy, output_format=None):
    extension = policy.get("extension")
    if extension:
        return _normalize_extension(extension)
    fmt = _normalize_output_format(output_format or policy.get("default_format"))
    if fmt:
        return _normalize_extension("." + fmt)
    raise OperationError("File output_policy requires extension.")


def _raster_extension(policy, output_format=None):
    fmt = _normalize_output_format(output_format or policy.get("default_format") or "tif")
    if fmt == "tiff":
        fmt = "tif"
    if fmt not in ("tif",):
        raise OperationError(u"Unsupported raster output format: %s" % fmt)
    return "." + fmt


def _normalize_output_format(value):
    if not value:
        return ""
    text = _text(value).strip().lower().lstrip(".")
    if text == "shapefile":
        return "shp"
    if text in ("geodatabase", "file_gdb", "feature_class"):
        return "gdb"
    return text


def _normalize_extension(extension):
    text = _text(extension).strip()
    if not text.startswith("."):
        text = "." + text
    if not SAFE_EXTENSION_RE.match(text):
        raise OperationError(u"Invalid output extension: %s" % extension)
    return text.lower()


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


class auto_add_outputs_disabled(object):
    def __init__(self):
        self._env = None
        self._original = None
        self._enabled = False

    def __enter__(self):
        self._env = getattr(arcpy, "env", None)
        if self._env is not None and hasattr(self._env, "addOutputsToMap"):
            self._original = self._env.addOutputsToMap
            self._env.addOutputsToMap = False
            self._enabled = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._enabled:
            self._env.addOutputsToMap = self._original
        return False


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


def _resolve_output_workspace(context, output_workspace):
    return _text(output_workspace).strip()


def _layer_source_exists(mxd, df, path):
    expected = _normalize_path(path)
    for layer in arcpy.mapping.ListLayers(mxd, "", df):
        source = _safe_data_source(layer)
        if source and _normalize_path(source) == expected:
            return True
    return False


def _find_layer_from_step(step_id, step_outputs):
    result = _step_output(step_outputs, step_id)
    if not isinstance(result, dict):
        raise OperationError(u"Step output not found: %s" % step_id)
    source = result.get("output") or result.get("added_layer")
    if not source:
        raise OperationError(u"Step has no layer output: %s" % step_id)
    mxd = current_mxd()
    df = active_data_frame(mxd)
    layers = arcpy.mapping.ListLayers(mxd, "", df)
    match = _find_live_layer_exact(_text(source), layers)
    if match is not None:
        return match
    name = result.get("layer_name")
    if name:
        match = _find_live_layer_exact(_text(name), layers)
        if match is not None:
            return match
    output_name = result.get("output_name")
    if output_name:
        match = _find_live_layer_exact(_text(output_name), layers)
        if match is not None:
            return match
    raise OperationError(u"Layer from step not found in map: %s" % step_id)


def _step_output(step_outputs, step_id):
    if step_id in step_outputs:
        return step_outputs.get(step_id)
    expected = _text(step_id)
    for key, value in step_outputs.items():
        if _text(key) == expected:
            return value
    return None


def _find_live_layer_exact(raw, layers=None):
    value = _text(raw)
    expected_path = _normalize_path(value)
    if layers is None:
        mxd = current_mxd()
        df = active_data_frame(mxd)
        layers = arcpy.mapping.ListLayers(mxd, "", df)
    matches = []
    for layer in layers:
        layer_name = getattr(layer, "name", "")
        long_name = getattr(layer, "longName", layer_name)
        source = _safe_data_source(layer)
        if value in (layer_name, long_name):
            matches.append(layer)
            continue
        if source and _normalize_path(source) == expected_path:
            matches.append(layer)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise OperationError(u"Layer is ambiguous: %s" % value)
    return None


def _safe_data_source(layer):
    try:
        if layer.supports("DATASOURCE"):
            return layer.dataSource
    except Exception:
        pass
    return None


def _normalize_path(path):
    return os.path.normcase(os.path.normpath(_text(path)))
