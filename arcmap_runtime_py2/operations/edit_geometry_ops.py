# -*- coding: utf-8 -*-
from __future__ import absolute_import

import math
import os

import arcpy

from operations import common


def create_point_features(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    output = _create_feature_class(context, arguments, "POINT", spatial_reference)
    _add_name_field(output)
    rows = []
    for index, item in enumerate(arguments["points"], 1):
        x, y = _point_xy(item)
        name = _feature_name(item, "point_%s" % index)
        geometry = arcpy.PointGeometry(arcpy.Point(x, y), spatial_reference)
        rows.append((geometry, name))
    _insert_rows(output, rows)
    common.add_output_layer(output)
    return {"output": output, "feature_count": len(rows), "geometry_type": "Point"}


def create_polyline_feature(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    points = _points(arguments["coordinates"], min_count=2)
    output = _create_feature_class(context, arguments, "POLYLINE", spatial_reference)
    _add_name_field(output)
    geometry = arcpy.Polyline(arcpy.Array([arcpy.Point(x, y) for x, y in points]), spatial_reference)
    _insert_rows(output, [(geometry, arguments.get("name") or arguments["output_name"])])
    common.add_output_layer(output)
    return {"output": output, "feature_count": 1, "geometry_type": "Polyline"}


def create_polygon_feature(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    points = _closed_ring(_points(arguments["coordinates"], min_count=3))
    return _create_polygon_output(context, arguments, spatial_reference, points)


def create_regular_polygon(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    sides = int(arguments["sides"])
    if sides < 3:
        raise common.OperationError(u"正多边形 sides 必须大于等于 3。")
    radius = _distance_to_map_units(arguments["radius"], arguments["radius_unit"], spatial_reference)
    if radius <= 0:
        raise common.OperationError(u"radius 必须大于 0。")
    start_angle = float(arguments.get("start_angle_degrees", -90.0))
    points = _radial_points(
        float(arguments["center_x"]),
        float(arguments["center_y"]),
        [radius] * sides,
        start_angle,
        360.0 / sides
    )
    return _create_polygon_output(context, arguments, spatial_reference, _closed_ring(points))


def create_star_polygon(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    point_count = int(arguments.get("point_count", 5))
    if point_count < 3:
        raise common.OperationError(u"星形 point_count 必须大于等于 3。")
    outer = _distance_to_map_units(arguments["outer_radius"], arguments["outer_radius_unit"], spatial_reference)
    if outer <= 0:
        raise common.OperationError(u"outer_radius 必须大于 0。")
    inner_unit = arguments.get("inner_radius_unit") or arguments["outer_radius_unit"]
    inner_value = arguments.get("inner_radius")
    if inner_value is None:
        inner = outer * 0.38196601125
    else:
        inner = _distance_to_map_units(inner_value, inner_unit, spatial_reference)
    if inner <= 0 or inner >= outer:
        raise common.OperationError(u"inner_radius 必须大于 0 且小于 outer_radius。")
    radii = []
    for index in range(point_count * 2):
        radii.append(outer if index % 2 == 0 else inner)
    points = _radial_points(
        float(arguments["center_x"]),
        float(arguments["center_y"]),
        radii,
        float(arguments.get("start_angle_degrees", -90.0)),
        180.0 / point_count
    )
    return _create_polygon_output(context, arguments, spatial_reference, _closed_ring(points))


def _create_polygon_output(context, arguments, spatial_reference, points):
    output = _create_feature_class(context, arguments, "POLYGON", spatial_reference)
    _add_name_field(output)
    geometry = arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in points]), spatial_reference)
    _insert_rows(output, [(geometry, arguments.get("name") or arguments["output_name"])])
    common.add_output_layer(output)
    return {"output": output, "feature_count": 1, "geometry_type": "Polygon"}


def _create_feature_class(context, arguments, geometry_type, spatial_reference):
    output = common.output_feature_dataset(
        context,
        arguments["output_name"],
        arguments.get("output_workspace"),
        arguments.get("output_folder"),
        arguments.get("output_format")
    )
    workspace = os.path.dirname(output)
    name = os.path.basename(output)
    arcpy.CreateFeatureclass_management(workspace, name, geometry_type, "", "DISABLED", "DISABLED", spatial_reference)
    return output


def _add_name_field(output):
    arcpy.AddField_management(output, "NAME", "TEXT", "", "", 255)


def _insert_rows(output, rows):
    with arcpy.da.InsertCursor(output, ["SHAPE@", "NAME"]) as cursor:
        for geometry, name in rows:
            cursor.insertRow([geometry, common._text(name)[:255]])


def _spatial_reference(context, arguments, step_outputs):
    if arguments.get("spatial_reference_layer"):
        layer = common.find_layer(context, arguments["spatial_reference_layer"], step_outputs)
        spatial_reference = getattr(arcpy.Describe(layer), "spatialReference", None)
    elif arguments.get("wkid"):
        spatial_reference = arcpy.SpatialReference(int(arguments["wkid"]))
    else:
        spatial_reference = getattr(common.active_data_frame(), "spatialReference", None)
    _require_known_spatial_reference(spatial_reference)
    return spatial_reference


def _require_known_spatial_reference(spatial_reference):
    name = common._text(getattr(spatial_reference, "name", "") if spatial_reference else "")
    if not spatial_reference or not name or name.lower().startswith("unknown"):
        raise common.OperationError(u"创建几何要素需要明确坐标系：请提供 spatial_reference_layer 或 wkid，或给当前数据框设置坐标系。")


def _points(items, min_count):
    points = [_point_xy(item) for item in items]
    if len(points) < min_count:
        raise common.OperationError(u"坐标点数量不足。")
    return points


def _point_xy(item):
    if isinstance(item, dict):
        return float(item["x"]), float(item["y"])
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return float(item[0]), float(item[1])
    raise common.OperationError(u"坐标必须是 {x, y} 对象。")


def _feature_name(item, default):
    if isinstance(item, dict) and item.get("name"):
        return item["name"]
    return default


def _closed_ring(points):
    if points[0] != points[-1]:
        points = points + [points[0]]
    return points


def _radial_points(center_x, center_y, radii, start_angle_degrees, step_degrees):
    points = []
    for index, radius in enumerate(radii):
        angle = math.radians(start_angle_degrees + step_degrees * index)
        points.append((center_x + math.cos(angle) * radius, center_y + math.sin(angle) * radius))
    return points


def _distance_to_map_units(value, unit, spatial_reference):
    distance = float(value)
    unit = common._text(unit).strip().lower()
    if unit == "map_units":
        return distance
    sr_type = common._text(getattr(spatial_reference, "type", "")).strip().lower()
    if unit == "degrees":
        if sr_type and sr_type != "geographic":
            raise common.OperationError(u"degrees 只能用于地理坐标系；投影坐标系请使用 meters 或 map_units。")
        return distance
    if unit == "meters":
        if sr_type == "geographic":
            raise common.OperationError(u"地理坐标系不能直接用 meters 创建坐标偏移；请改用 degrees，或先投影到米单位坐标系。")
        meters_per_unit = getattr(spatial_reference, "metersPerUnit", None)
        if meters_per_unit is None:
            unit_name = common._text(getattr(spatial_reference, "linearUnitName", "")).lower()
            if "meter" in unit_name or "metre" in unit_name:
                meters_per_unit = 1.0
        if not meters_per_unit:
            raise common.OperationError(u"当前坐标系无法把 meters 转换为地图单位；请改用 map_units。")
        return distance / float(meters_per_unit)
    raise common.OperationError(u"Unsupported distance unit: %s" % unit)
