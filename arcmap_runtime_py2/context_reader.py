# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os

import arcpy
import context_fingerprint


try:
    unicode
except NameError:
    unicode = str


VALUE_PROFILE_EXCLUDED_TYPES = set(["Geometry", "Raster", "Blob"])
MAX_VALUE_PROFILE_FIELDS = 30
MAX_VALUE_PROFILE_ROWS = 250
MAX_FIELD_VALUE_SAMPLES = 20
MAX_VALUE_TEXT_LENGTH = 120
ARCPY_EXECUTE_ERROR = getattr(arcpy, "ExecuteError", RuntimeError)


def read_context():
    mxd = arcpy.mapping.MapDocument("CURRENT")
    data_frames = arcpy.mapping.ListDataFrames(mxd)
    data_frame = data_frames[0] if data_frames else None
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
    context["context_hash"] = context_hash(context)
    return context


def context_hash(context):
    return context_fingerprint.context_hash(context)


def _mxd_path(mxd):
    path = getattr(mxd, "filePath", None)
    if path and os.path.exists(path):
        return path
    return ""


def _default_geodatabase(mxd):
    default_gdb = getattr(mxd, "defaultGeodatabase", None)
    if default_gdb:
        return default_gdb
    env = getattr(arcpy, "env", None)
    workspace = getattr(env, "workspace", None)
    if workspace and unicode(workspace).lower().endswith(u".gdb"):
        return workspace
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
        "XMin": extent.XMin,
        "YMin": extent.YMin,
        "XMax": extent.XMax,
        "YMax": extent.YMax
    }


def _layer_info(layer, index):
    info = {
        "layer_ref": "layer:%s" % index,
        "name": layer.name,
        "longName": getattr(layer, "longName", layer.name),
        "visible": bool(getattr(layer, "visible", False)),
        "isFeatureLayer": bool(getattr(layer, "isFeatureLayer", False)),
        "dataSource": _safe_support(layer, "DATASOURCE", "dataSource"),
        "fields": [],
        "selected_count": 0,
        "geometry_type": None
    }
    if info["isFeatureLayer"]:
        try:
            desc = arcpy.Describe(layer)
            info["geometry_type"] = getattr(desc, "shapeType", None)
            fid_set = getattr(desc, "FIDSet", "") or ""
            info["selected_count"] = len([item for item in fid_set.split(";") if item])
            info["selection_hash"] = context_fingerprint.selection_hash(fid_set)
        except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError) as exc:
            _layer_warning(info, u"describe_failed: %s" % _sample_text(exc))
        try:
            fields = arcpy.ListFields(layer)
            for field in fields:
                info["fields"].append({"name": field.name, "type": field.type})
            _attach_field_value_samples(layer, info, fields)
        except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError) as exc:
            _layer_warning(info, u"field_read_failed: %s" % _sample_text(exc))
    return info


def _attach_field_value_samples(layer, layer_info, fields):
    profiled_fields = [field for field in fields if getattr(field, "type", None) not in VALUE_PROFILE_EXCLUDED_TYPES]
    profiled_fields = profiled_fields[:MAX_VALUE_PROFILE_FIELDS]
    if not profiled_fields:
        return
    field_names = [field.name for field in profiled_fields]
    samples = dict((field.name, []) for field in profiled_fields)
    seen = dict((field.name, set()) for field in profiled_fields)
    try:
        cursor = arcpy.da.SearchCursor(layer, field_names)
    except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError) as exc:
        _layer_warning(layer_info, u"value_sample_failed: %s" % _sample_text(exc))
        return
    try:
        row_count = 0
        for row in cursor:
            row_count += 1
            for index, raw_value in enumerate(row):
                if raw_value is None:
                    continue
                field_name = field_names[index]
                if len(samples[field_name]) >= MAX_FIELD_VALUE_SAMPLES:
                    continue
                value = _sample_text(raw_value)
                if not value or value in seen[field_name]:
                    continue
                seen[field_name].add(value)
                samples[field_name].append(value)
            if row_count >= MAX_VALUE_PROFILE_ROWS or _all_sample_lists_full(samples):
                break
    finally:
        del cursor
    for field_info in layer_info.get("fields", []):
        values = samples.get(field_info.get("name"))
        if values:
            field_info["value_samples"] = values
    if len(fields) > len(profiled_fields):
        layer_info["value_profile_truncated"] = True


def _all_sample_lists_full(samples):
    for values in samples.values():
        if len(values) < MAX_FIELD_VALUE_SAMPLES:
            return False
    return True


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
            return getattr(layer, attr_name)
    except (RuntimeError, AttributeError, TypeError):
        return None
    return None


def _layer_warning(info, message):
    warnings = info.setdefault("warnings", [])
    warnings.append(message)
