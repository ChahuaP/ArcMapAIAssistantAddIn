"""Plan a geometry edit without changing the saved layer."""
import numpy as np
import shapely
from shapely.geometry import MultiLineString, MultiPoint, MultiPolygon, mapping, shape
from shapely.ops import split


def sketch(payload):
    items = payload.get("geometries")
    mode = payload.get("mode")
    if not isinstance(items, list) or not 1 <= len(items) <= 500 or mode not in {"merge", "split", "multipart", "explode"}:
        raise ValueError("请选择 1 至 500 个待编辑要素")
    geometries = [shape(item) for item in items]
    family = geometries[0].geom_type.removeprefix("Multi")
    if mode in {"multipart", "explode"}:
        constructors = {"Point": MultiPoint, "LineString": MultiLineString, "Polygon": MultiPolygon}
        if family not in constructors or any(geom.geom_type.removeprefix("Multi") != family or geom.is_empty
                or not geom.is_valid or geom.has_m or geom.has_z != geometries[0].has_z for geom in geometries):
            raise ValueError("请选择坐标维度一致的有效点、线或面要素")
        if mode == "explode":
            if not any(geom.geom_type.startswith("Multi") for geom in geometries):
                raise ValueError("所选要素均为单部件，无需拆分")
            return {"parts": [[mapping(part) for part in shapely.get_parts(geom)] for geom in geometries]}
        if len(geometries) < 2:
            raise ValueError("至少选择两个要素")
        merged = constructors[family](list(shapely.get_parts(geometries)))
        if not merged.is_valid:
            raise ValueError("所选图形不能组成有效的多部件几何，重叠或共边的面可先使用「合并要素」")
        return {"geometry": mapping(merged)}
    if family not in {"LineString", "Polygon"} or any(
            geom.geom_type.removeprefix("Multi") != family or geom.has_z != geometries[0].has_z
            or geom.has_m or geom.is_empty or not geom.is_valid
            for geom in geometries):
        raise ValueError("请选择坐标维度一致的有效线或面要素")
    def valid_z(geometry):
        if geometries[0].has_z and (not geometry.has_z or
                not np.isfinite(shapely.get_coordinates(geometry, include_z=True)[:, 2]).all()):
            raise ValueError("无法确定新顶点的高程，请调整分割线或选择其他要素")
        return mapping(geometry)

    if mode == "merge":
        if len(geometries) < 2:
            raise ValueError("至少选择两个要素")
        merged = shapely.union_all(geometries)
        if family == "LineString":
            merged = shapely.line_merge(merged)
        if merged.geom_type.removeprefix("Multi") != family or merged.is_empty or not merged.is_valid:
            raise ValueError("合并未产生有效几何")
        return {"geometry": valid_z(merged)}

    cutter = shape(payload["line"])
    if cutter.geom_type != "LineString" or cutter.is_empty or not cutter.is_valid or not cutter.is_simple:
        raise ValueError("请绘制一条不自交的分割线")
    parts = []
    for geom in geometries:
        try:
            pieces = tuple(split(geom, cutter).geoms)
        except ValueError as error:
            raise ValueError("分割线与要素重合，请让分割线穿过要素") from error
        if len(pieces) <= shapely.get_num_geometries(geom):
            parts.append([])
        else:
            parts.append([valid_z(piece) for piece in pieces])
    if not any(parts):
        raise ValueError("分割线未切开选中要素，请绘制穿过要素的线")
    return {"parts": parts}
