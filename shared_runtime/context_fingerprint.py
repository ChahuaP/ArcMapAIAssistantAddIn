# -*- coding: utf-8 -*-
from __future__ import absolute_import

import hashlib
import json
import math
import ntpath


def context_hash(context):
    payload = json.dumps(
        execution_fingerprint(context),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":")
    )
    if not isinstance(payload, bytes):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def execution_fingerprint(context):
    return {
        "mxd_path": _normalize_path(context.get("mxd_path")),
        "is_saved": bool(context.get("is_saved")),
        "data_frame": context.get("data_frame"),
        "spatial_reference": _spatial_reference(context.get("spatial_reference")),
        "extent": context.get("extent"),
        "active_view": context.get("active_view"),
        "edit_session_state": context.get("edit_session_state"),
        "layers": [_layer_fingerprint(layer) for layer in context.get("layers", [])]
    }


def selection_hash(fid_set):
    ids = [item.strip() for item in (fid_set or "").split(";") if item.strip()]
    ids.sort(key=_selection_sort_key)
    payload = ";".join(ids)
    if not isinstance(payload, bytes):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest() if ids else ""


def canonical_coordinate(value):
    """Remove binary floating noise while retaining twelve significant digits."""
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        raise ValueError("ArcMap extent coordinates must be finite.")
    result = float("%.12g" % number)
    return 0.0 if result == 0.0 else result


def _layer_fingerprint(layer):
    return {
        "layer_ref": layer.get("layer_ref"),
        "name": layer.get("name"),
        "long_name": layer.get("long_name"),
        "visible": bool(layer.get("visible")),
        "is_feature_layer": bool(layer.get("is_feature_layer")),
        "data_source": _normalize_path(layer.get("data_source")),
        "geometry_type": layer.get("geometry_type"),
        "spatial_reference": layer.get("spatial_reference"),
        "fields": [_field_fingerprint(field) for field in layer.get("fields", [])],
        "selected_count": int(layer.get("selected_count") or 0),
        "selection_hash": layer.get("selection_hash") or ""
    }


def _field_fingerprint(field):
    return {
        "name": field.get("name"),
        "type": field.get("type")
    }


def _spatial_reference(value):
    if not isinstance(value, dict):
        return None
    return {
        "name": value.get("name"),
        "factoryCode": value.get("factoryCode")
    }


def _normalize_path(value):
    if not value:
        return ""
    return ntpath.normcase(ntpath.normpath(value))


def _selection_sort_key(value):
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, value)
