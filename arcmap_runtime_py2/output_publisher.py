# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

try:
    import path_utils
    import arcmap_desktop_selection
except ImportError:
    from . import path_utils
    from . import arcmap_desktop_selection


def publish(plan, mxd=None):
    items = _unique_items(plan.items)
    if not items:
        return {"published": 0, "already_visible": 0}

    mxd = mxd or arcpy.mapping.MapDocument("CURRENT")
    frames = arcpy.mapping.ListDataFrames(mxd)
    if not frames:
        raise RuntimeError("Current MXD has no data frame.")
    data_frame = frames[0]
    published = 0
    already_visible = 0
    for item in items:
        existing = _find_source_layer(mxd, data_frame, item.path)
        if existing is not None:
            _apply_state(existing, item)
            already_visible += 1
            continue
        layer = item.layer if item.layer is not None else arcpy.mapping.Layer(item.path)
        arcpy.mapping.AddLayer(data_frame, layer, "TOP")
        live_layer = _find_source_layer(mxd, data_frame, item.path)
        if live_layer is None:
            raise RuntimeError("Added ArcMap Desktop layer cannot be resolved by data source: %s" % item.path)
        _apply_state(live_layer, item)
        published += 1
    return {"published": published, "already_visible": already_visible}


def _unique_items(items):
    result = []
    seen = set()
    for item in items:
        path = path_utils.to_unicode_path(item.path)
        key = path_utils.normcase(path_utils.normpath(path))
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _find_source_layer(mxd, data_frame, path):
    expected = path_utils.normcase(path_utils.normpath(path))
    matches = []
    for layer in arcpy.mapping.ListLayers(mxd, "", data_frame):
        try:
            if not layer.supports("DATASOURCE"):
                continue
            source = path_utils.to_unicode_path(layer.dataSource)
        except (arcpy.ExecuteError, RuntimeError, AttributeError, TypeError) as exc:
            raise RuntimeError(
                "ArcMap Desktop layer data source cannot be read while publishing %s: %s"
                % (path, exc))
        if path_utils.normcase(path_utils.normpath(source)) == expected:
            matches.append(layer)
    if len(matches) > 1:
        raise RuntimeError("ArcMap Desktop data source resolves to multiple live layers: %s" % path)
    return matches[0] if matches else None


def _apply_state(layer, item):
    if item.visible is not None:
        layer.visible = bool(item.visible)
    if item.selection_oids is not None:
        arcmap_desktop_selection.restore_oids(layer, item.selection_oids)
