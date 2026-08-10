# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy
from shared_runtime import context_fingerprint

try:
    import path_utils
except ImportError:
    from . import path_utils

try:
    import arcmap_desktop_selection
except ImportError:
    from . import arcmap_desktop_selection


try:
    unicode
except NameError:
    unicode = str

try:
    INTEGER_TYPES = (int, long)
except NameError:
    INTEGER_TYPES = (int,)


MAX_VALUE_TEXT_LENGTH = 120
ARCPY_EXECUTE_ERROR = getattr(arcpy, "ExecuteError", RuntimeError)


def read_context():
    mxd = arcpy.mapping.MapDocument("CURRENT")
    data_frames = arcpy.mapping.ListDataFrames(mxd)
    data_frame = getattr(mxd, "activeDataFrame", None)
    if data_frames and data_frame is None:
        raise RuntimeError("ArcMap has data frames but no active data frame.")
    if data_frame is not None and data_frame not in data_frames:
        raise RuntimeError("ArcMap active data frame is not in this document.")
    layers = []
    if data_frame is not None:
        for index, layer in enumerate(arcpy.mapping.ListLayers(mxd, "", data_frame)):
            layers.append(_layer_info(layer, index))

    context = {
        "mxd_path": _mxd_path(mxd),
        "is_saved": bool(_mxd_path(mxd)),
        "default_gdb": _default_geodatabase(mxd),
        "active_view": getattr(mxd, "activeView", None),
        "data_frame": data_frame.name if data_frame is not None else None,
        "spatial_reference": _spatial_reference(data_frame),
        "extent": _extent(data_frame),
        "layers": layers
    }
    context["edit_session_state"] = _edit_session_state(layers)
    context["content_hash"] = context_hash(context)
    return context


def context_hash(context):
    return context_fingerprint.context_hash(context)


def sample_values(layer_ref, field_names, max_rows, max_samples):
    if not isinstance(layer_ref, unicode) or not layer_ref.startswith(u"layer:"):
        raise ValueError("sample layer_ref is invalid")
    index = int(layer_ref.split(u":", 1)[1])
    if index < 0 or not isinstance(field_names, list) or not field_names or max_rows <= 0 or max_samples <= 0:
        raise ValueError("sample request is invalid")
    mxd = arcpy.mapping.MapDocument("CURRENT")
    frames = arcpy.mapping.ListDataFrames(mxd)
    if not frames:
        raise RuntimeError("ArcMap has no active data frame.")
    data_frame = getattr(mxd, "activeDataFrame", None)
    if data_frame is None:
        raise RuntimeError("ArcMap has data frames but no active data frame.")
    if data_frame not in frames:
        raise RuntimeError("ArcMap active data frame is not in this document.")
    layers = arcpy.mapping.ListLayers(mxd, "", data_frame)
    if index >= len(layers):
        raise RuntimeError("sample layer_ref does not exist.")
    layer = layers[index]
    available = set(field.name for field in arcpy.ListFields(layer))
    if any(name not in available for name in field_names):
        raise RuntimeError("sample field does not exist.")
    values = dict((name, []) for name in field_names)
    seen = dict((name, set()) for name in field_names)
    with arcpy.da.SearchCursor(layer, field_names) as cursor:
        for row_number, row in enumerate(cursor):
            if row_number >= max_rows:
                break
            for number, raw in enumerate(row):
                if raw is None:
                    continue
                name = field_names[number]
                text = _sample_text(raw)
                if text and text not in seen[name] and len(values[name]) < max_samples:
                    seen[name].add(text)
                    values[name].append(text)
    return values


def _mxd_path(mxd):
    path = getattr(mxd, "filePath", None)
    if path and path_utils.exists(path):
        return path_utils.to_unicode_path(path)
    return ""


def _default_geodatabase(mxd):
    default_gdb = getattr(mxd, "defaultGeodatabase", None)
    if default_gdb:
        return path_utils.to_unicode_path(default_gdb)
    env = getattr(arcpy, "env", None)
    workspace = getattr(env, "workspace", None)
    if workspace and unicode(workspace).lower().endswith(u".gdb"):
        return path_utils.to_unicode_path(workspace)
    return ""


def _spatial_reference(data_frame):
    if data_frame is None:
        return None
    sr = getattr(data_frame, "spatialReference", None)
    if sr is None:
        return None
    return {"name": getattr(sr, "name", ""), "factoryCode": getattr(sr, "factoryCode", None)}


def _extent(data_frame):
    if data_frame is None:
        return None
    extent = getattr(data_frame, "extent", None)
    if extent is None:
        return None
    return {
        "XMin": context_fingerprint.canonical_coordinate(extent.XMin),
        "YMin": context_fingerprint.canonical_coordinate(extent.YMin),
        "XMax": context_fingerprint.canonical_coordinate(extent.XMax),
        "YMax": context_fingerprint.canonical_coordinate(extent.YMax)
    }


def _edit_session_state(layers):
    workspaces = set()
    for layer in layers:
        source = layer.get("data_source")
        if source:
            workspaces.add(path_utils.dirname(source))
    if not workspaces:
        return "none"
    states = []
    for workspace in sorted(workspaces):
        try:
            states.append(bool(arcpy.da.Editor(workspace).isEditing))
        except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError):
            return "unknown"
    if all(not state for state in states):
        return "none"
    if all(states):
        return "single"
    return "mixed"


def _layer_info(layer, index):
    info = {
        "layer_ref": "layer:%s" % index,
        "name": layer.name,
        "long_name": getattr(layer, "longName", layer.name),
        "visible": bool(getattr(layer, "visible", False)),
        "is_feature_layer": bool(getattr(layer, "isFeatureLayer", False)),
        "data_source": _safe_support(layer, "DATASOURCE", "dataSource"),
        "layer_type": _layer_type(layer),
        "fields": [],
        "selected_count": 0,
        "geometry_type": None,
        "spatial_reference": None
    }
    if info["is_feature_layer"]:
        desc = arcpy.Describe(layer)
        info["geometry_type"] = getattr(desc, "shapeType", None)
        info["spatial_reference"] = _layer_spatial_reference(desc)
        selection_oids = arcmap_desktop_selection.capture_oids(layer, desc)
        info["selected_count"] = len(selection_oids)
        info["selection_hash"] = context_fingerprint.selection_hash(
            u";".join(unicode(oid) for oid in selection_oids))
        try:
            fields = arcpy.ListFields(layer)
            for field in fields:
                info["fields"].append({"name": field.name, "type": field.type})
        except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError) as exc:
            _layer_warning(info, u"field_read_failed: %s" % _sample_text(exc))
    return info


def _layer_type(layer):
    if bool(getattr(layer, "isFeatureLayer", False)):
        return "FeatureLayer"
    if bool(getattr(layer, "isRasterLayer", False)):
        return "RasterLayer"
    if bool(getattr(layer, "isGroupLayer", False)):
        return "GroupLayer"
    return "Layer"


def _layer_spatial_reference(description):
    spatial_reference = getattr(description, "spatialReference", None)
    name = getattr(spatial_reference, "name", None) if spatial_reference is not None else None
    factory_code = getattr(spatial_reference, "factoryCode", None) if spatial_reference is not None else None
    if isinstance(factory_code, INTEGER_TYPES) and factory_code > 0:
        return "EPSG:%d" % factory_code
    return name if name else None


def _sample_text(value):
    try:
        text = value if isinstance(value, unicode) else unicode(value)
    except (UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError):
        if hasattr(value, "decode"):
            try:
                text = value.decode("utf-8", "ignore")
            except (UnicodeDecodeError, UnicodeEncodeError, TypeError, AttributeError):
                text = u""
        else:
            text = u""
    text = text.strip()
    if len(text) > MAX_VALUE_TEXT_LENGTH:
        text = text[:MAX_VALUE_TEXT_LENGTH]
    return text


def _safe_support(layer, support_name, attr_name):
    try:
        if layer.supports(support_name):
            value = getattr(layer, attr_name)
            if attr_name == "dataSource" and value:
                return path_utils.to_unicode_path(value)
            return value
    except (RuntimeError, AttributeError, TypeError):
        return None
    return None


def _layer_warning(info, message):
    warnings = info.setdefault("warnings", [])
    warnings.append(message)
