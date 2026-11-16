"""Plan a geometry edit without changing the saved layer."""
import shapely
from shapely.geometry import mapping, shape
from shapely.ops import split


def sketch(payload):
    items = payload.get("geometries")
    mode = payload.get("mode")
    if not isinstance(items, list) or not 1 <= len(items) <= 500 or mode not in {"merge", "split"}:
        raise ValueError("请选择 1 至 500 个待编辑要素")
    geometries = [shape(item) for item in items]
    family = geometries[0].geom_type.removeprefix("Multi")
    if family not in {"LineString", "Polygon"} or any(
            geom.geom_type.removeprefix("Multi") != family or geom.has_z or geom.has_m or geom.is_empty or not geom.is_valid
            for geom in geometries):
        raise ValueError("请选择同类型的有效二维线或面要素")
    if mode == "merge":
        if len(geometries) < 2:
            raise ValueError("至少选择两个要素")
        merged = shapely.union_all(geometries)
        if family == "LineString":
            merged = shapely.line_merge(merged)
        if merged.geom_type.removeprefix("Multi") != family or merged.is_empty or not merged.is_valid:
            raise ValueError("合并未产生有效几何")
        return {"geometry": mapping(merged)}

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
            parts.append([mapping(piece) for piece in pieces])
    if not any(parts):
        raise ValueError("分割线未切开选中要素，请绘制穿过要素的线")
    return {"parts": parts}
