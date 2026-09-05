# -*- coding: utf-8 -*-
from __future__ import absolute_import
try:
    basestring
except NameError:  # Python 3 (syntax gate)
    basestring = str


import os
import re
import uuid

import arcpy
from shared_runtime.output_contract import OutputContractError, output_policy_type, validate_output_policy

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


class OperationError(Exception):
    pass


def current_mxd():
    return arcpy.mapping.MapDocument("CURRENT")


def active_data_frame(mxd=None):
    mxd = mxd or current_mxd()
    frames = arcpy.mapping.ListDataFrames(mxd)
    if not frames:
        raise OperationError("Current MXD has no data frame.")
    data_frame = getattr(mxd, "activeDataFrame", None)
    if data_frame is None:
        raise OperationError("Current MXD has no active data frame.")
    if data_frame not in frames:
        raise OperationError("Current MXD active data frame is not in this document.")
    return data_frame


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
        if raw in (layer.get("layer_ref"), layer.get("name"), layer.get("long_name"), layer.get("data_source")):
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
        snapshot_layer.get("data_source"),
        snapshot_layer.get("long_name"),
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


def _assert_within_staging(context, path):
    """Reject output paths that escape the task staging directory (§6.8).

    Py2 execution must only stage outputs; writing to a user-named absolute
    path outside staging would bypass Gateway acceptance + atomic publish.
    Every output is derived beneath the server-issued staging_root.
    Uses a separator-aware prefix check so ``/foo/bar`` does not allow
    ``/foo/bar-evil`` (a plain ``startswith`` would).
    """
    staging_root = context.get("staging_root")
    if not staging_root:
        raise OperationError(u"缺少任务 staging 目录，拒绝输出。请确认运行租约已签发。")
    try:
        resolved = path_utils.abspath(path)
        staging_abs = path_utils.abspath(staging_root)
        sep = os.sep
        inside = (
            resolved.lower() == staging_abs.lower()
            or resolved.lower().startswith(staging_abs.lower() + sep)
        )
        if not inside:
            raise OperationError(
                u"输出路径必须在任务 staging 目录内（%s），拒绝写入：%s" % (staging_abs, resolved))
    except (TypeError, ValueError):
        raise OperationError(u"输出路径无效：%s" % path)


def output_gdb(context):
    # Outputs must land in the per-run staging directory (§6.8): fail closed
    # if it is missing — writing to the MXD folder would bypass Gateway
    # acceptance + atomic publish.
    staging_root = context.get("staging_root")
    if not staging_root:
        raise OperationError(u"缺少任务 staging 目录，拒绝输出。请确认运行租约已签发。")
    if not path_utils.isdir(staging_root):
        path_utils.makedirs(staging_root)
    gdb = path_utils.join_path(staging_root, "staging.gdb")
    if not arcpy.Exists(gdb):
        arcpy.CreateFileGDB_management(staging_root, "staging.gdb")
    return gdb


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


def dataset(layer_or_path):
    """Resolve a live Layer object to its datasource path for analysis tools.

    GP analysis on a live MxDocument-bound Layer object crashed ArcMap
    natively (observed 0x800706BE during Buffer on the UI thread); the same
    buffer on the datasource path runs clean standalone. Analysis operates
    on data, not on map state, so hand the tools the path.
    """
    data_source = getattr(layer_or_path, "dataSource", None)
    if isinstance(data_source, basestring) and data_source:
        return data_source
    if hasattr(layer_or_path, "name"):
        # A Layer object without a resolvable datasource (e.g. a layer whose
        # source is broken) must not silently stringify to its display name —
        # the GP subprocess would then receive "schools" as a path.
        raise OperationError(
            u"Layer has no readable datasource: %s" % layer_or_path.name)
    return layer_or_path


def output_feature_class(context, output_name):
    gdb = output_gdb(context)
    name = safe_output_name(output_name)
    path = path_utils.join_path(gdb, name)
    if arcpy.Exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


def output_dataset(context, output_name, output_policy):
    try:
        policy = validate_output_policy(output_policy, "writes_data")
        output_policy_type(policy)
    except OutputContractError as exc:
        raise OperationError(str(exc))
    if output_policy_type(policy) == "file":
        return output_file(context, output_name, policy["default_format"])
    return output_feature_class(context, output_name)


def output_file(context, output_name, output_format):
    """Derive one run-private file beside the staging FileGDB."""
    text = _text(output_name).strip() if output_name else u""
    extension = u"." + _text(output_format).lower()
    if (not text or text != _text(output_name) or path_utils.basename(text) != text
            or text in (u".", u"..") or not text.lower().endswith(extension)
            or text.count(u".") != 1 or INVALID_OUTPUT_NAME_RE.search(text)):
        raise OperationError("Invalid %s output_name: %s" % (output_format, output_name))
    gdb = output_gdb(context)
    directory = path_utils.join_path(path_utils.dirname(gdb), "files")
    if not path_utils.isdir(directory):
        path_utils.makedirs(directory)
    path = path_utils.join_path(directory, text)
    if path_utils.isfile(path) or arcpy.Exists(path):
        raise OperationError("Output already exists: %s" % path)
    return path


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
        session = execution_session.current()
        detached_path = session.registered_path_for_detached_layer(self.layer) if session is not None else None
        source = detached_path if detached_path is not None else self.layer
        arcpy.MakeFeatureLayer_management(source, self.temp_layer, self.where_clause)
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


def _text(value):
    if isinstance(value, unicode):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return unicode(value)




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
