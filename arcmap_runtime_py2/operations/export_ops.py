# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os
import re
import uuid
import zipfile

import arcpy

from operations import common
from operations import condition_utils


def export_map_png(context, arguments, step_outputs):
    mxd = common.current_mxd()
    output = common.output_file(context, arguments["output_name"], ".png", arguments.get("output_folder"))
    resolution = int(arguments.get("resolution", 150))
    arcpy.mapping.ExportToPNG(mxd, output, resolution=resolution)
    return {"output": output}


def export_map_pdf(context, arguments, step_outputs):
    mxd = common.current_mxd()
    output = common.output_file(context, arguments["output_name"], ".pdf", arguments.get("output_folder"))
    resolution = int(arguments.get("resolution", 150))
    arcpy.mapping.ExportToPDF(mxd, output, resolution=resolution)
    return {"output": output}


def export_table_csv(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    output = common.output_file(context, arguments["output_name"], ".csv", arguments.get("output_folder"))
    common.export_table_to_csv(layer, output, bool(arguments.get("selected_only", False)))
    return {"output": output}


def export_layer_kml(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    output = common.output_file(context, arguments["output_name"], ".kmz", arguments.get("output_folder"))
    selected_only = bool(arguments.get("selected_only", False))
    if _is_feature_layer(layer):
        source = _feature_kml_source(layer, selected_only)
        feature_count = _feature_count(source)
        if feature_count <= 0:
            raise common.OperationError(u"KML 导出的图层没有要素，已停止生成空 KMZ。")
        written_count = _write_feature_kmz(source, getattr(layer, "name", "layer"), output)
        return {
            "output": output,
            "selected_only": selected_only,
            "format": "kmz",
            "feature_count": written_count
        }
    scale = _kml_output_scale(arguments)
    is_composite = arguments.get("is_composite", "NO_COMPOSITE")
    arcpy.LayerToKML_conversion(layer, output, scale, is_composite)
    return {
        "output": output,
        "selected_only": selected_only,
        "format": "kmz"
    }


def split_by_field(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    field = condition_utils.require_field(layer, arguments["field"])
    include_null = bool(arguments.get("include_null", True))
    max_outputs = int(arguments.get("max_outputs", 200))
    if max_outputs <= 0:
        raise common.OperationError(u"max_outputs 必须大于 0。")

    values = _unique_field_values(layer, field.name, include_null, bool(arguments.get("selected_only", False)))
    if not values:
        raise common.OperationError(u"字段 %s 没有可导出的取值。" % field.name)
    if len(values) > max_outputs:
        raise common.OperationError(u"字段 %s 有 %s 个不同取值，超过当前上限 %s。请缩小范围或提高 max_outputs。" % (field.name, len(values), max_outputs))

    output_format = _output_format(arguments)
    prefix = common.safe_output_name(arguments["output_name"])
    output_base = _output_base(context, arguments, output_format)
    input_source = layer if bool(arguments.get("selected_only", False)) else (common._safe_data_source(layer) or layer)
    outputs = []
    output_items = []
    used_names = set()
    for index, value in enumerate(values, 1):
        name = _output_name_for_value(prefix, value, index, used_names, output_format, arguments.get("name_template"))
        output = _output_path(output_base, name, output_format)
        _ensure_output_available(output)
        where_clause = _where_for_value(layer, field.name, value)
        if output_format == "kmz":
            feature_count = _write_feature_kmz(input_source, name, output, where_clause)
            outputs.append(output)
            output_items.append({"value": common._text(value), "output": output, "feature_count": feature_count})
        else:
            temp_layer = "arcmap_ai_split_%s" % uuid.uuid4().hex
            try:
                with common.auto_add_outputs_disabled():
                    arcpy.MakeFeatureLayer_management(input_source, temp_layer, where_clause)
                    arcpy.CopyFeatures_management(temp_layer, output)
                outputs.append(output)
                output_items.append({"value": common._text(value), "output": output})
            finally:
                try:
                    arcpy.Delete_management(temp_layer)
                except Exception:
                    pass

    common.refresh()
    return {"outputs": outputs, "output_items": output_items, "count": len(outputs), "output_format": output_format}


def _kml_output_scale(arguments):
    if "layer_output_scale" in arguments:
        return int(arguments["layer_output_scale"])
    try:
        mxd = common.current_mxd()
        df = common.active_data_frame(mxd)
        scale = int(round(float(getattr(df, "scale", 0))))
    except Exception as exc:
        raise common.OperationError(u"KML 栅格导出无法读取当前地图比例尺，请提供 layer_output_scale：%s" % common._text(exc))
    if scale <= 0:
        raise common.OperationError(u"KML 栅格导出需要有效地图比例尺，请提供 layer_output_scale。")
    return scale


def _is_feature_layer(layer):
    desc = arcpy.Describe(layer)
    data_type = common._text(getattr(desc, "dataType", "")).lower()
    if data_type in ("featurelayer", "feature class", "shapefile"):
        return True
    return bool(getattr(desc, "shapeType", None))


def _feature_kml_source(layer, selected_only):
    if selected_only:
        _require_selection(layer)
        return layer
    return common._safe_data_source(layer) or layer


def _require_selection(layer):
    desc = arcpy.Describe(layer)
    fid_set = getattr(desc, "FIDSet", None)
    if fid_set is not None and not common._text(fid_set).strip():
        raise common.OperationError(u"当前图层没有已选要素，不能按 selected_only 导出 KML。")


def _feature_count(layer):
    result = arcpy.GetCount_management(layer)
    value = result.getOutput(0) if hasattr(result, "getOutput") else result
    return int(value)


def _write_feature_kmz(source, layer_name, output, where_clause=None):
    document_name = _xml_text(layer_name or "layer")
    spatial_reference = _require_spatial_reference(source)
    wgs84 = arcpy.SpatialReference(4326)
    fields = _kml_attribute_fields(source)
    field_names = [field.name for field in fields]
    parts = [
        u'<?xml version="1.0" encoding="UTF-8"?>',
        u'<kml xmlns="http://www.opengis.net/kml/2.2">',
        u"<Document>",
        u"<name>%s</name>" % document_name,
        u'<Style id="feature_style">',
        u"<LineStyle><color>ff6e6e6e</color><width>1.2</width></LineStyle>",
        u"<PolyStyle><color>7d5ca6ff</color><outline>1</outline></PolyStyle>",
        u"</Style>"
    ]
    written_count = 0
    cursor_fields = ["SHAPE@"] + field_names
    cursor_args = [source, cursor_fields]
    if where_clause:
        cursor_args.append(where_clause)
    with arcpy.da.SearchCursor(*cursor_args) as cursor:
        for index, row in enumerate(cursor, 1):
            geometry = _project_geometry(row[0], spatial_reference, wgs84)
            if geometry is None:
                continue
            geometry_xml = _geometry_kml(geometry)
            if not geometry_xml:
                continue
            values = row[1:]
            name = _feature_name(fields, values, index)
            parts.append(u"<Placemark>")
            parts.append(u"<name>%s</name>" % _xml_text(name))
            parts.append(u"<styleUrl>#feature_style</styleUrl>")
            parts.append(_extended_data_kml(fields, values))
            parts.append(geometry_xml)
            parts.append(u"</Placemark>")
            written_count += 1
    if written_count <= 0:
        raise common.OperationError(u"KML 导出的图层没有可写入的几何，已停止生成空 KMZ。")
    parts.append(u"</Document>")
    parts.append(u"</kml>")
    kml_text = u"\n".join(parts).encode("utf-8")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", kml_text)
    return written_count


def _require_spatial_reference(source):
    desc = arcpy.Describe(source)
    spatial_reference = getattr(desc, "spatialReference", None)
    name = common._text(getattr(spatial_reference, "name", "") if spatial_reference else "")
    if not spatial_reference or not name or name.lower() == "unknown":
        raise common.OperationError(u"KML 导出要求图层有明确空间参考。请先定义投影。")
    return spatial_reference


def _kml_attribute_fields(source):
    excluded = set(["Geometry", "Blob", "Raster"])
    return [field for field in arcpy.ListFields(source) if field.type not in excluded]


def _project_geometry(geometry, spatial_reference, wgs84):
    if geometry is None or bool(getattr(geometry, "isEmpty", False)):
        return None
    factory_code = getattr(spatial_reference, "factoryCode", None)
    name = common._text(getattr(spatial_reference, "name", ""))
    if factory_code == 4326 or name == u"GCS_WGS_1984":
        return geometry
    return geometry.projectAs(wgs84)


def _geometry_kml(geometry):
    data = json.loads(common._text(geometry.JSON))
    if "curveRings" in data or "curvePaths" in data:
        raise common.OperationError(u"KML 导出暂不支持曲线几何，请先转为普通要素。")
    if "x" in data and "y" in data:
        return _point_kml([data.get("x"), data.get("y"), data.get("z")])
    if "points" in data:
        return _multi_geometry([_point_kml(point) for point in data.get("points") or []])
    if "paths" in data:
        return _multi_geometry([_line_kml(path) for path in data.get("paths") or []])
    if "rings" in data:
        return _polygon_kml(data.get("rings") or [])
    raise common.OperationError(u"KML 导出遇到不支持的几何 JSON。")


def _point_kml(point):
    return u"<Point><coordinates>%s</coordinates></Point>" % _coordinate_text(point)


def _line_kml(path):
    coordinates = _coordinates_text(path, False)
    if not coordinates:
        return u""
    return u"<LineString><tessellate>1</tessellate><coordinates>%s</coordinates></LineString>" % coordinates


def _polygon_kml(rings):
    groups = _polygon_ring_groups(rings)
    polygons = []
    for group in groups:
        outer = _coordinates_text(group["outer"], True)
        if not outer:
            continue
        parts = [
            u"<Polygon><tessellate>1</tessellate>",
            u"<outerBoundaryIs><LinearRing><coordinates>%s</coordinates></LinearRing></outerBoundaryIs>" % outer
        ]
        for hole in group["holes"]:
            inner = _coordinates_text(hole, True)
            if inner:
                parts.append(u"<innerBoundaryIs><LinearRing><coordinates>%s</coordinates></LinearRing></innerBoundaryIs>" % inner)
        parts.append(u"</Polygon>")
        polygons.append(u"".join(parts))
    return _multi_geometry(polygons)


def _multi_geometry(items):
    items = [item for item in items if item]
    if not items:
        return u""
    if len(items) == 1:
        return items[0]
    return u"<MultiGeometry>%s</MultiGeometry>" % u"".join(items)


def _polygon_ring_groups(rings):
    valid_rings = [_closed_ring(ring) for ring in rings if len(ring or []) >= 3]
    if len(valid_rings) <= 1:
        return [{"outer": ring, "holes": []} for ring in valid_rings]
    groups = []
    holes = []
    for ring in valid_rings:
        if _signed_area(ring) < 0:
            groups.append({"outer": ring, "holes": []})
        else:
            holes.append(ring)
    if not groups:
        return [{"outer": ring, "holes": []} for ring in valid_rings]
    for hole in holes:
        owner = _containing_group(hole, groups)
        if owner is None:
            groups.append({"outer": hole, "holes": []})
        else:
            owner["holes"].append(hole)
    return groups


def _containing_group(ring, groups):
    point = _first_xy(ring)
    if point is None:
        return None
    containers = [group for group in groups if _point_in_ring(point, group["outer"])]
    if not containers:
        return None
    return sorted(containers, key=lambda group: abs(_signed_area(group["outer"])))[0]


def _closed_ring(ring):
    points = [point for point in ring if point and len(point) >= 2]
    if points and _xy(points[0]) != _xy(points[-1]):
        points.append(points[0])
    return points


def _coordinates_text(points, close_ring):
    items = _closed_ring(points) if close_ring else [point for point in points if point and len(point) >= 2]
    return u" ".join([_coordinate_text(point) for point in items])


def _coordinate_text(point):
    z = point[2] if len(point) > 2 and point[2] is not None else 0
    return u"%.12g,%.12g,%.12g" % (float(point[0]), float(point[1]), float(z))


def _signed_area(ring):
    area = 0.0
    points = _closed_ring(ring)
    for index in range(len(points) - 1):
        x1, y1 = _xy(points[index])
        x2, y2 = _xy(points[index + 1])
        area += x1 * y2 - x2 * y1
    return area / 2.0


def _point_in_ring(point, ring):
    x, y = point
    inside = False
    points = _closed_ring(ring)
    for index in range(len(points) - 1):
        x1, y1 = _xy(points[index])
        x2, y2 = _xy(points[index + 1])
        intersects = ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1)
        if intersects:
            inside = not inside
    return inside


def _first_xy(ring):
    for point in ring:
        if point and len(point) >= 2:
            return _xy(point)
    return None


def _xy(point):
    return (float(point[0]), float(point[1]))


def _feature_name(fields, values, index):
    for expected in ("NAME", "Name", "name"):
        for field, value in zip(fields, values):
            if field.name == expected and value not in (None, ""):
                return common._text(value)
    for value in values:
        if value not in (None, ""):
            return common._text(value)
    return u"feature_%s" % index


def _extended_data_kml(fields, values):
    if not fields:
        return u""
    parts = [u"<ExtendedData>"]
    for field, value in zip(fields, values):
        parts.append(
            u'<Data name="%s"><value>%s</value></Data>'
            % (_xml_text(field.name), _xml_text(_value_text(value)))
        )
    parts.append(u"</ExtendedData>")
    return u"".join(parts)


def _value_text(value):
    if value is None:
        return u""
    return common._text(value)


def _xml_text(value):
    text = common._text(value)
    return (
        text.replace(u"&", u"&amp;")
        .replace(u"<", u"&lt;")
        .replace(u">", u"&gt;")
        .replace(u'"', u"&quot;")
        .replace(u"'", u"&apos;")
    )


def _unique_field_values(layer, field_name, include_null, selected_only):
    source = layer if selected_only else (common._safe_data_source(layer) or layer)
    values = []
    seen = set()
    with arcpy.da.SearchCursor(source, [field_name]) as cursor:
        for row in cursor:
            value = row[0]
            if value is None and not include_null:
                continue
            key = _value_key(value)
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
    return sorted(values, key=lambda item: common._text(item) if item is not None else u"")


def _where_for_value(layer, field_name, value):
    if value is None:
        return condition_utils.compile_where(layer, {"field": field_name, "op": "is_null"})
    return condition_utils.compile_where(layer, {"field": field_name, "op": "eq", "value": value})


def _output_format(arguments):
    value = common._text(arguments.get("output_format", "")).strip().lower()
    if value:
        return value
    workspace = common._text(arguments.get("output_workspace", "")).strip().lower()
    if workspace.endswith(u".gdb"):
        return "gdb"
    return "shp"


def _output_base(context, arguments, output_format):
    if output_format == "gdb":
        return common.output_gdb(context, arguments.get("output_workspace") or arguments.get("output_folder"))
    if output_format in ("shp", "kmz"):
        folder = arguments.get("output_folder") or arguments.get("output_workspace")
        if folder and common._text(folder).strip().lower().endswith(u".gdb"):
            raise common.OperationError(u"导出 %s 时输出位置必须是普通文件夹，不能是 GDB。" % output_format)
        return common.output_directory(context, folder)
    raise common.OperationError(u"output_format 只支持 shp、gdb 或 kmz。")


def _output_path(output_base, output_name, output_format):
    if output_format == "gdb":
        return os.path.join(output_base, output_name)
    if output_format == "kmz":
        return os.path.join(output_base, output_name + ".kmz")
    return os.path.join(output_base, output_name + ".shp")


def _ensure_output_available(path):
    if arcpy.Exists(path) or os.path.exists(path):
        raise common.OperationError("Output already exists: %s" % path)


def _output_name_for_value(prefix, value, index, used_names, output_format, name_template=None):
    if output_format == "kmz":
        if name_template:
            raw_name = _render_name_template(name_template, prefix, value, index)
        else:
            raw_name = u"%s_%s" % (common._text(prefix), _safe_file_name_part(value))
        return _unique_file_name(raw_name, used_names)

    suffix = _safe_name_part(value)
    base = _trim_name("%s_%s" % (prefix, suffix))
    name = base
    counter = 2
    while name.lower() in used_names:
        name = _trim_name("%s_%02d" % (base, counter))
        counter += 1
    used_names.add(name.lower())
    return common.safe_output_name(name)


def _render_name_template(template, prefix, value, index):
    text = common._text(template)
    replacements = {
        u"{prefix}": common._text(prefix),
        u"{value}": _value_text_for_name(value),
        u"{value_base}": _value_base_for_name(value),
        u"{index}": u"%03d" % index,
        u"{index_number}": common._text(index)
    }
    for token, replacement in replacements.items():
        text = text.replace(token, replacement)
    return text


def _unique_file_name(raw_name, used_names):
    base = _safe_file_name(raw_name)
    name = base
    counter = 2
    while name.lower() in used_names:
        name = _safe_file_name(u"%s_%02d" % (base, counter))
        counter += 1
    used_names.add(name.lower())
    return name


def _safe_file_name_part(value):
    if value is None:
        return u"null"
    raw_text = _value_text_for_name(value)
    try:
        return _safe_file_name(raw_text)
    except common.OperationError:
        raise common.OperationError(u"字段值无法生成输出文件名：%s" % raw_text)


def _safe_file_name(value):
    text = common._text(value).strip()
    text = re.sub(u'[<>:"/\\\\|?\\x00-\\x1f]+', u"_", text)
    text = re.sub(u"\\s+", u" ", text).strip(u" .")
    if not text:
        raise common.OperationError(u"输出文件名不能为空。")
    upper = text.upper()
    if upper in (u"CON", u"PRN", u"AUX", u"NUL") or re.match(u"^(COM|LPT)[1-9]$", upper):
        text = u"_" + text
    text = text[:120].rstrip(u" ._")
    if not text:
        raise common.OperationError(u"输出文件名不能为空。")
    return text


def _value_text_for_name(value):
    if value is None:
        return u"null"
    return common._text(value).strip()


def _value_base_for_name(value):
    text = _value_text_for_name(value)
    suffixes = (
        u"社区村委会",
        u"社区居委会",
        u"村委会",
        u"居委会",
        u"居民委员会",
        u"村民委员会",
        u"社区",
        u"行政村",
        u"自然村",
        u"村"
    )
    for suffix in suffixes:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[:-len(suffix)].strip()
    return text


def _safe_name_part(value):
    if value is None:
        return "null"
    text = common._text(value)
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text).strip("_")
    if not text:
        raise common.OperationError(u"字段值无法生成 ArcGIS 输出名称：%s" % common._text(value))
    if text[0].isdigit():
        text = "v_" + text
    return _trim_name(text)


def _trim_name(value):
    text = value[:120].rstrip("_")
    if not text:
        raise common.OperationError(u"输出名称不能为空。")
    return text


def _value_key(value):
    if value is None:
        return "__NULL__"
    return "%s:%s" % (type(value).__name__, common._text(value))
