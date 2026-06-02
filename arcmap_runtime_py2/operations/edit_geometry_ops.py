# -*- coding: utf-8 -*-
from __future__ import absolute_import

import math

import arcpy

from operations import common

try:
    import path_utils
except ImportError:
    from .. import path_utils


def create_empty_feature_layer(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    geometry_type = _feature_class_geometry_type(arguments["geometry_type"])
    output = _create_feature_class(context, arguments, geometry_type, spatial_reference)
    _add_name_field(output)
    common.add_output_layer(output)
    return {"output": output, "feature_count": 0, "geometry_type": _geometry_type_label(geometry_type)}


def create_point_features(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    output = _create_feature_class(context, arguments, "POINT", spatial_reference)
    _add_name_field(output)
    rows = _point_rows(arguments["points"], spatial_reference)
    names_written = _insert_rows(output, rows)
    common.add_output_layer(output)
    return {"output": output, "feature_count": len(rows), "geometry_type": "Point", "names_written": names_written}


def append_point_features(context, arguments, step_outputs):
    target, spatial_reference = _target_layer(context, arguments, step_outputs, "Point")
    rows = _point_rows(arguments["points"], spatial_reference)
    names_written = _insert_rows(target, rows)
    common.refresh()
    return {
        "target_layer": _layer_name(target),
        "feature_count": len(rows),
        "geometry_type": "Point",
        "names_written": names_written
    }


def create_polyline_feature(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    output = _create_feature_class(context, arguments, "POLYLINE", spatial_reference)
    _add_name_field(output)
    rows = _polyline_rows(arguments, spatial_reference)
    names_written = _insert_rows(output, rows)
    common.add_output_layer(output)
    return {"output": output, "feature_count": len(rows), "geometry_type": "Polyline", "names_written": names_written}


def append_polyline_features(context, arguments, step_outputs):
    target, spatial_reference = _target_layer(context, arguments, step_outputs, "Polyline")
    rows = _polyline_rows(arguments, spatial_reference)
    names_written = _insert_rows(target, rows)
    common.refresh()
    return {
        "target_layer": _layer_name(target),
        "feature_count": len(rows),
        "geometry_type": "Polyline",
        "names_written": names_written
    }


def create_polygon_feature(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    rows = _polygon_rows(arguments, spatial_reference)
    return _create_polygon_output(context, arguments, spatial_reference, rows)


def create_rectangle_polygon(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    rows = _rectangle_polygon_rows(arguments, spatial_reference)
    return _create_polygon_output(context, arguments, spatial_reference, rows)


def append_polygon_features(context, arguments, step_outputs):
    target, spatial_reference = _target_layer(context, arguments, step_outputs, "Polygon")
    rows = _polygon_rows(arguments, spatial_reference)
    names_written = _insert_rows(target, rows)
    common.refresh()
    return {
        "target_layer": _layer_name(target),
        "feature_count": len(rows),
        "geometry_type": "Polygon",
        "names_written": names_written
    }


def create_regular_polygon(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    rows = _regular_polygon_rows(arguments, spatial_reference)
    return _create_polygon_output(context, arguments, spatial_reference, rows)


def append_regular_polygons(context, arguments, step_outputs):
    target, spatial_reference = _target_layer(context, arguments, step_outputs, "Polygon")
    rows = _regular_polygon_rows(arguments, spatial_reference)
    names_written = _insert_rows(target, rows)
    common.refresh()
    return {
        "target_layer": _layer_name(target),
        "feature_count": len(rows),
        "geometry_type": "Polygon",
        "names_written": names_written
    }


def _regular_polygon_geometry(arguments, spatial_reference):
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
    return _polygon_geometry(_closed_ring(points), spatial_reference)


def create_star_polygon(context, arguments, step_outputs):
    spatial_reference = _spatial_reference(context, arguments, step_outputs)
    rows = _star_polygon_rows(arguments, spatial_reference)
    return _create_polygon_output(context, arguments, spatial_reference, rows)


def append_star_polygons(context, arguments, step_outputs):
    target, spatial_reference = _target_layer(context, arguments, step_outputs, "Polygon")
    rows = _star_polygon_rows(arguments, spatial_reference)
    names_written = _insert_rows(target, rows)
    common.refresh()
    return {
        "target_layer": _layer_name(target),
        "feature_count": len(rows),
        "geometry_type": "Polygon",
        "names_written": names_written
    }


def _star_polygon_geometry(arguments, spatial_reference):
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
    return _polygon_geometry(_closed_ring(points), spatial_reference)


def _create_polygon_output(context, arguments, spatial_reference, rows):
    output = _create_feature_class(context, arguments, "POLYGON", spatial_reference)
    _add_name_field(output)
    names_written = _insert_rows(output, rows)
    common.add_output_layer(output)
    return {"output": output, "feature_count": len(rows), "geometry_type": "Polygon", "names_written": names_written}


def _create_feature_class(context, arguments, geometry_type, spatial_reference):
    output = common.output_feature_dataset(
        context,
        arguments["output_name"],
        arguments.get("output_workspace"),
        arguments.get("output_folder"),
        arguments.get("output_format")
    )
    workspace = path_utils.dirname(output)
    name = path_utils.basename(output)
    arcpy.CreateFeatureclass_management(workspace, name, geometry_type, "", "DISABLED", "DISABLED", spatial_reference)
    return output


def _feature_class_geometry_type(value):
    text = common._text(value).strip().lower()
    mapping = {
        "point": "POINT",
        "multipoint": "MULTIPOINT",
        "polyline": "POLYLINE",
        "line": "POLYLINE",
        "polygon": "POLYGON",
        "面": "POLYGON",
        "线": "POLYLINE",
        "点": "POINT",
    }
    if text not in mapping:
        raise common.OperationError(u"geometry_type 必须是 point、polyline 或 polygon。")
    return mapping[text]


def _geometry_type_label(value):
    text = common._text(value).upper()
    if text == "POINT":
        return "Point"
    if text == "POLYLINE":
        return "Polyline"
    if text == "POLYGON":
        return "Polygon"
    return text


def _add_name_field(output):
    if not _name_field(output):
        arcpy.AddField_management(output, "NAME", "TEXT", "", "", 255)


def _insert_rows(output, rows):
    name_field = _name_field(output)
    fields = ["SHAPE@"]
    if name_field:
        fields.append(name_field)
    with arcpy.da.InsertCursor(output, fields) as cursor:
        for geometry, name in rows:
            if name_field:
                cursor.insertRow([geometry, common._text(name)[:255]])
            else:
                cursor.insertRow([geometry])
    return bool(name_field)


def _name_field(output):
    for field in arcpy.ListFields(output):
        if common._text(getattr(field, "name", "")).lower() == "name":
            return getattr(field, "name")
    return None


def _target_layer(context, arguments, step_outputs, expected_shape_type):
    target = common.find_layer(context, arguments["target_layer"], step_outputs)
    description = arcpy.Describe(target)
    shape_type = common._text(getattr(description, "shapeType", ""))
    if shape_type.lower() != expected_shape_type.lower():
        raise common.OperationError(u"目标图层几何类型是 %s，不能追加 %s 要素。" % (shape_type, expected_shape_type))
    spatial_reference = getattr(description, "spatialReference", None)
    _require_known_spatial_reference(spatial_reference)
    return target, spatial_reference


def _layer_name(layer):
    return common._text(getattr(layer, "longName", getattr(layer, "name", "")))


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


def _point_rows(items, spatial_reference):
    rows = []
    for index, item in enumerate(items, 1):
        x, y = _point_xy(item)
        name = _feature_name(item, "point_%s" % index)
        geometry = arcpy.PointGeometry(arcpy.Point(x, y), spatial_reference)
        rows.append((geometry, name))
    if not rows:
        raise common.OperationError(u"至少需要 1 个点要素。")
    return rows


def _polyline_rows(arguments, spatial_reference):
    features = arguments.get("features")
    rows = []
    if features is not None:
        _require_non_empty_features(features)
        for index, item in enumerate(features, 1):
            points = _closed_if_needed(_points(_feature_argument(item, "coordinates"), min_count=2), close=False)
            rows.append((_polyline_geometry(points, spatial_reference), _feature_name(item, "line_%s" % index)))
        return rows
    if "coordinates" not in arguments:
        raise common.OperationError(u"请提供 coordinates，或提供 features 数组。")
    points = _points(arguments["coordinates"], min_count=2)
    return [(_polyline_geometry(points, spatial_reference), arguments.get("name") or arguments.get("output_name") or "line_1")]


def _polygon_rows(arguments, spatial_reference):
    features = arguments.get("features")
    rows = []
    if features is not None:
        _require_non_empty_features(features)
        for index, item in enumerate(features, 1):
            points = _closed_ring(_points(_feature_argument(item, "coordinates"), min_count=3))
            rows.append((_polygon_geometry(points, spatial_reference), _feature_name(item, "polygon_%s" % index)))
        return rows
    if "coordinates" not in arguments:
        raise common.OperationError(u"请提供 coordinates，或提供 features 数组。")
    points = _closed_ring(_points(arguments["coordinates"], min_count=3))
    return [(_polygon_geometry(points, spatial_reference), arguments.get("name") or arguments.get("output_name") or "polygon_1")]


def _rectangle_polygon_rows(arguments, spatial_reference):
    left = float(arguments["left"])
    top = float(arguments["top"])
    right = float(arguments["right"])
    bottom = float(arguments["bottom"])
    if left >= right:
        raise common.OperationError(u"矩形 left 必须小于 right。")
    if bottom >= top:
        raise common.OperationError(u"矩形 bottom 必须小于 top。")
    points = _closed_ring([
        (left, top),
        (right, top),
        (right, bottom),
        (left, bottom)
    ])
    name = arguments.get("name") or arguments.get("output_name") or "rectangle_1"
    return [(_polygon_geometry(points, spatial_reference), name)]


def _regular_polygon_rows(arguments, spatial_reference):
    features = arguments.get("features")
    rows = []
    if features is not None:
        _require_non_empty_features(features)
        for index, item in enumerate(features, 1):
            feature_arguments = _merge_feature_arguments(arguments, item, [
                "center_x",
                "center_y",
                "radius",
                "radius_unit",
                "sides",
                "start_angle_degrees"
            ])
            rows.append((_regular_polygon_geometry(feature_arguments, spatial_reference), _feature_name(item, "regular_polygon_%s" % index)))
        return rows
    _require_arguments(arguments, ["center_x", "center_y", "radius", "radius_unit", "sides"])
    return [(_regular_polygon_geometry(arguments, spatial_reference), arguments.get("name") or arguments.get("output_name") or "regular_polygon_1")]


def _star_polygon_rows(arguments, spatial_reference):
    features = arguments.get("features")
    rows = []
    if features is not None:
        _require_non_empty_features(features)
        for index, item in enumerate(features, 1):
            feature_arguments = _merge_feature_arguments(arguments, item, [
                "center_x",
                "center_y",
                "outer_radius",
                "outer_radius_unit",
                "inner_radius",
                "inner_radius_unit",
                "point_count",
                "start_angle_degrees"
            ])
            rows.append((_star_polygon_geometry(feature_arguments, spatial_reference), _feature_name(item, "star_%s" % index)))
        return rows
    _require_arguments(arguments, ["center_x", "center_y", "outer_radius", "outer_radius_unit"])
    return [(_star_polygon_geometry(arguments, spatial_reference), arguments.get("name") or arguments.get("output_name") or "star_1")]


def _polyline_geometry(points, spatial_reference):
    return arcpy.Polyline(arcpy.Array([arcpy.Point(x, y) for x, y in points]), spatial_reference)


def _polygon_geometry(points, spatial_reference):
    return arcpy.Polygon(arcpy.Array([arcpy.Point(x, y) for x, y in points]), spatial_reference)


def _require_non_empty_features(features):
    if not isinstance(features, list) or not features:
        raise common.OperationError(u"features 必须是非空数组。")


def _feature_argument(item, name):
    if not isinstance(item, dict) or name not in item:
        raise common.OperationError(u"features 中每个要素都必须包含 %s。" % name)
    return item[name]


def _merge_feature_arguments(arguments, item, names):
    if not isinstance(item, dict):
        raise common.OperationError(u"features 中每个要素必须是对象。")
    result = {}
    for name in names:
        if name in item:
            result[name] = item[name]
        elif name in arguments:
            result[name] = arguments[name]
    return result


def _require_arguments(arguments, names):
    missing = [name for name in names if name not in arguments]
    if missing:
        raise common.OperationError(u"缺少参数：%s。" % u"、".join(missing))


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


def _closed_if_needed(points, close):
    if close:
        return _closed_ring(points)
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


def estimate_append_point_features(context, arguments, step_outputs):
    return _append_estimate(arguments, "点", len(arguments.get("points") or []))


def estimate_append_polyline_features(context, arguments, step_outputs):
    return _append_estimate(arguments, "线", _argument_feature_count(arguments))


def estimate_append_polygon_features(context, arguments, step_outputs):
    return _append_estimate(arguments, "面", _argument_feature_count(arguments))


def estimate_append_regular_polygons(context, arguments, step_outputs):
    return _append_estimate(arguments, "正多边形面", _argument_feature_count(arguments))


def estimate_append_star_polygons(context, arguments, step_outputs):
    return _append_estimate(arguments, "星形面", _argument_feature_count(arguments))


def _argument_feature_count(arguments):
    features = arguments.get("features")
    if isinstance(features, list):
        return len(features)
    return 1


def _append_estimate(arguments, label, count):
    layer = common._text(arguments.get("target_layer", ""))
    return {
        "summary": u"将向已有图层 %s 追加 %s 个%s要素。此操作会直接修改目标图层数据，是否继续？" % (
            layer,
            count,
            label
        )
    }
