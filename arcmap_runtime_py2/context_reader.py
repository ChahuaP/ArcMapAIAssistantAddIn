# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os

import arcpy
import context_fingerprint


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
        except Exception:
            pass
        try:
            for field in arcpy.ListFields(layer):
                info["fields"].append({"name": field.name, "type": field.type})
        except Exception:
            pass
    return info


def _safe_support(layer, support_name, attr_name):
    try:
        if layer.supports(support_name):
            return getattr(layer, attr_name)
    except Exception:
        pass
    return None
