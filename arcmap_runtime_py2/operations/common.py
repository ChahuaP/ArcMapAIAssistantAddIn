# -*- coding: utf-8 -*-
from __future__ import absolute_import

import csv
import re
import uuid

import arcpy

try:
    import path_utils
    import execution_session
    import arcmap_desktop_selection
except ImportError:
    from .. import path_utils
    from .. import execution_session
    from .. import arcmap_desktop_selection


try:
    unicode
except NameError:
    unicode = str


ARCPY_EXECUTE_ERROR = getattr(arcpy, "ExecuteError", RuntimeError)
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
        snapshot_layer = _context_layer_by_ref(context, raw)
        if snapshot_layer is not None:
            return _find_live_snapshot_layer(snapshot_layer)
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
    if not layer_ref.startswith("layer:"):
        live_match = _find_live_layer_exact(raw)
        if live_match is not None:
            return live_match
        raise OperationError(u"Layer metadata is not executable: %s" % raw)
    return _find_live_snapshot_layer(matches[0])


def _context_layer_by_ref(context, layer_ref):
    matches = [
        layer for layer in context.get("layers", [])
        if layer.get("layer_ref") == layer_ref
    ]
    if len(matches) > 1:
        raise OperationError(u"Layer reference is ambiguous: %s" % layer_ref)
    return matches[0] if matches else None


def _find_live_snapshot_layer(snapshot_layer):
    for identity in (
        snapshot_layer.get("dataSource"),
        snapshot_layer.get("longName"),
        snapshot_layer.get("name"),
    ):
        if identity:
            layer = _find_live_layer_exact(identity)
            if layer is not None:
                return layer
    raise OperationError(u"Layer no longer exists: %s" % snapshot_layer.get("layer_ref", ""))


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
            if not path_utils.isdir(workspace):
                raise OperationError(u"Output folder not found: %s" % workspace)
            gdb = path_utils.join_path(workspace, "ArcMapAI_Output.gdb")
        folder = path_utils.dirname(gdb)
        name = path_utils.basename(gdb)
        if not folder or not path_utils.isdir(folder):
            raise OperationError(u"Output workspace folder not found: %s" % folder)
        if not arcpy.Exists(gdb):
            arcpy.CreateFileGDB_management(folder, name)
        return gdb

    mxd_path = context.get("mxd_path")
    if not mxd_path:
        raise OperationError(u"当前 MXD 未保存。请指定输出 GDB，或先保存 MXD。")
    folder = path_utils.dirname(mxd_path)
    gdb = path_utils.join_path(folder, "ArcMapAI_Output.gdb")
    if not arcpy.Exists(gdb):
        arcpy.CreateFileGDB_management(folder, "ArcMapAI_Output.gdb")
    return gdb


def output_directory(context, output_folder=None):
    if output_folder:
        folder = _path_text(output_folder)
        if not path_utils.isdir(folder):
            raise OperationError(u"Output folder not found: %s" % folder)
        return folder

    mxd_path = context.get("mxd_path")
    if not mxd_path:
        raise OperationError(u"当前 MXD 未保存。请指定输出文件夹，或先保存 MXD。")
    folder = path_utils.join_path(path_utils.dirname(mxd_path), "ArcMapAI_Output")
    if not path_utils.isdir(folder):
        path_utils.makedirs(folder)
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
    path = path_utils.join_path(gdb, name)
    if arcpy.Exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


def output_shapefile(context, output_name, output_folder=None):
    folder = output_directory(context, output_folder)
    name = safe_output_name(output_name)
    path = path_utils.join_path(folder, name + ".shp")
    if arcpy.Exists(path) or path_utils.exists(path):
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
    path = path_utils.join_path(folder, name + extension)
    if path_utils.exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


def _folder_workspace(output_workspace):
    if not output_workspace:
        return None
    workspace = _path_text(output_workspace)
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


def export_table_to_csv(layer, path, selected_only):
    fields = [f.name for f in arcpy.ListFields(layer) if f.type not in ("Geometry", "Raster", "Blob")]
    with path_utils.open_binary(path, "wb") as f:
        writer = csv.writer(f)
        writer.writerow([field.encode("utf-8") for field in fields])
        with read_layer(layer, selected_only) as cursor_layer:
            with arcpy.da.SearchCursor(cursor_layer, fields) as cursor:
                for row in cursor:
                    writer.writerow([_csv_value(value) for value in row])


def read_layer(layer, selected_only=False, where_clause=None):
    return _ReadLayer(layer, selected_only, where_clause)


class _ReadLayer(object):
    def __init__(self, layer, selected_only=False, where_clause=None):
        self.layer = layer
        self.selected_only = bool(selected_only)
        self.where_clause = where_clause
        self.temp_layer = None

    def __enter__(self):
        if self.selected_only:
            require_selection(self.layer)
            if not self.where_clause:
                return self.layer
            self.temp_layer = "arcmap_ai_selected_%s" % uuid.uuid4().hex
            arcpy.MakeFeatureLayer_management(self.layer, self.temp_layer, self.where_clause)
            return self.temp_layer

        self.temp_layer = "arcmap_ai_read_%s" % uuid.uuid4().hex
        arcpy.MakeFeatureLayer_management(self.layer, self.temp_layer, self.where_clause)
        clear_layer_selection(self.temp_layer)
        return self.temp_layer

    def __exit__(self, exc_type, exc, tb):
        if self.temp_layer:
            delete_layer(self.temp_layer)
        return False


def require_selection(layer):
    try:
        selected = arcmap_desktop_selection.has_selection(layer)
    except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError) as exc:
        raise OperationError(u"无法读取当前图层选择集：%s" % _text(exc))
    if not selected:
        raise OperationError(u"当前图层没有已选要素。")


def clear_layer_selection(layer):
    arcmap_desktop_selection.restore_oids(layer, [])


def delete_layer(layer):
    try:
        arcpy.Delete_management(layer)
    except (ARCPY_EXECUTE_ERROR, RuntimeError):
        pass


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
    return _path_text(output_workspace).strip()


def _find_layer_from_step(step_id, step_outputs):
    step_id = _text(step_id)
    result = step_outputs.get(step_id)
    if not isinstance(result, dict):
        raise OperationError(u"Step output not found: %s" % step_id)
    layer_path = result.get("layer_path")
    if layer_path:
        layer = _find_live_layer_exact(_text(layer_path))
        if layer is None:
            raise OperationError(u"Layer added by step is no longer in the map: %s" % step_id)
        return layer
    source = result.get("output")
    if not source:
        raise OperationError(u"Step has no layer output: %s" % step_id)
    session = execution_session.current()
    if session is None:
        raise OperationError(u"from_step requires an active execution session: %s" % step_id)
    return session.layer_for_output(step_id, source)


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
            return _path_text(layer.dataSource)
    except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError):
        pass
    return None


def _normalize_path(path):
    return path_utils.normcase(path_utils.normpath(path))


def _path_text(value):
    return path_utils.to_unicode_path(value)
