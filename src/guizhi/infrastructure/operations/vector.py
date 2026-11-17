"""Independent vector operators with explicit units and topology checks."""
from typing import Literal

import geopandas
import shapely

from guizhi.infrastructure.operations.common import (
    Unit, compatible, concatenate, condition, distance_in_crs, field, separate_fields, valid,
)
from guizhi.infrastructure.operations.data import AttributeFilter


def area(ctx, where: AttributeFilter | None = None, method: Literal["union", "sum"] = "union",
         area_field: str | None = None):
    frame = ctx.frames[0]
    filters = where.model_dump(mode="json", exclude_none=True) if where is not None else None
    if filters is not None:
        frame = frame.loc[condition(frame, filters)]
    if area_field is not None:
        from math import fsum, isfinite
        from pandas.api.types import is_bool_dtype, is_numeric_dtype

        values = field(frame, area_field)
        if not is_numeric_dtype(values) or is_bool_dtype(values) or any(not isfinite(n) or n < 0 for n in values.dropna()):
            raise ValueError("请选择存有有效非负面积的数值字段")
        valid_count = int(values.count())
        area_m2 = fsum(values.dropna()) if valid_count else (0.0 if frame.empty else None)
    else:
        crs = frame.crs
        if crs is None or not crs.is_projected or len(crs.axis_info) != 2:
            raise ValueError("请先将图层转换到适用的投影坐标系，或选择已知单位为平方米的面积字段")
        if not frame.empty:
            valid(frame, polygon=True)
        scale = crs.axis_info[0].unit_conversion_factor * crs.axis_info[1].unit_conversion_factor
        area_m2 = float((shapely.area(frame.geometry).sum() if method == "sum" else
                         shapely.area(shapely.union_all(frame.geometry))) * scale)
        valid_count = len(frame)
    stats = {"feature_count": len(frame)}
    if where is not None or method == "sum" or area_field is not None:
        stats["area_summary"] = {"area_m2": area_m2, "layer": ctx.request.inputs[0].layer,
                                 "where": filters, "count": len(frame), "valid": valid_count, "field": area_field}
    elif area_m2 is not None:
        stats["area_m2"] = area_m2
    return {"statistics": stats, "value": area_m2}


def buffer(ctx, distance: float, unit: Unit, cap: Literal["round", "flat", "square"] = "round",
           join: Literal["round", "mitre", "bevel"] = "round", dissolve: bool = False):
    frame = ctx.frames[0]
    valid(frame)
    width = distance_in_crs(frame.crs, distance, unit)
    shapes = shapely.buffer(frame.geometry.to_numpy(), width, quad_segs=24, cap_style=cap, join_style=join)
    result = (geopandas.GeoDataFrame(geometry=[shapely.union_all(shapes)], crs=frame.crs) if dissolve and len(shapes)
              else frame.set_geometry(shapes))
    ctx.publish(result, geometry_type="Polygon" if result.empty else None)
    return {"distance": {"value": distance, "unit": unit, "map_units": width}}


def clip(ctx):
    left, mask = ctx.frames
    compatible(ctx.frames)
    valid(left)
    valid(mask, polygon=True)
    result = geopandas.clip(left, mask, keep_geom_type=True)
    ctx.publish(result, geometry_type=ctx.info[0]["geometry_type"] if result.empty else None)


def dissolve(ctx, fields: list[str] | None = None):
    frame = ctx.frames[0]
    valid(frame)
    for name in fields or []:
        field(frame, name)
    if len(set(fields or [])) != len(fields or []):
        raise ValueError("Dissolve fields must be unique")
    if frame.empty:
        ctx.publish(frame[[*(fields or []), frame.geometry.name]], geometry_type=ctx.info[0]["geometry_type"])
        return
    subset = frame[[*(fields or []), frame.geometry.name]]
    ctx.publish(subset.dissolve(by=fields, dropna=False, as_index=False))


def overlay(ctx, how, polygons=False):
    compatible(ctx.frames)
    for frame in ctx.frames:
        valid(frame, polygon=polygons)
    frames = separate_fields(ctx.frames)
    order = list(range(len(frames)))
    if how == "intersection":
        order.sort(key=lambda index: shapely.get_dimensions(frames[index].geometry.to_numpy()).min(initial=2))
    left, *rest = [frames[index] for index in order]
    for other in rest:
        ctx.check()
        left = geopandas.overlay(left, other, how=how, keep_geom_type=True, make_valid=False)
    ctx.publish(left, geometry_type=ctx.info[order[0]]["geometry_type"] if left.empty else None)


def intersect(ctx):
    overlay(ctx, "intersection")


def erase(ctx):
    overlay(ctx, "difference")


def identity(ctx):
    overlay(ctx, "identity", polygons=True)


def union(ctx):
    overlay(ctx, "union", polygons=True)


def symmetric_difference(ctx):
    overlay(ctx, "symmetric_difference", polygons=True)


def update_overlay(ctx):
    compatible(ctx.frames)
    left, right = ctx.frames
    valid(left, polygon=True)
    valid(right, polygon=True)
    kept = geopandas.overlay(left, right, how="difference", keep_geom_type=True, make_valid=False)
    ctx.publish(concatenate([kept, right]))


def spatial_join(ctx, mode: Literal["one_to_one", "one_to_many"] = "one_to_one"):
    compatible(ctx.frames)
    for item in ctx.frames:
        valid(item)
    left, right = separate_fields(ctx.frames)
    if {"join_count", "join_fid", "index_right"} & set(left.columns) or {"join_count", "join_fid", "index_right"} & set(right.columns):
        raise ValueError("Spatial join requires unused join_count, join_fid and index_right names")
    joined = geopandas.sjoin(left.rename_axis(None), right.rename_axis("join_fid"),
                             how="left", predicate="intersects")
    joined["join_count"] = joined.groupby(level=0)["join_fid"].transform("count")
    if mode == "one_to_one":
        joined = joined.sort_values("join_fid", kind="stable")
        joined = joined.loc[~joined.index.duplicated()].sort_index()
    ctx.publish(joined, geometry_type=ctx.info[0]["geometry_type"] if joined.empty else None)
