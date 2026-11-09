"""Independent vector operators with explicit units and topology checks."""
from typing import Literal

import geopandas
import shapely

from guizhi.infrastructure.operations.common import (
    Unit, compatible, concatenate, distance_in_crs, field, separate_fields, valid,
)


def buffer(ctx, distance: float, unit: Unit):
    frame = ctx.frames[0]
    valid(frame)
    width = distance_in_crs(frame.crs, distance, unit)
    result = frame.set_geometry(shapely.buffer(frame.geometry.to_numpy(), width, quad_segs=24))
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
    if how == "intersection":
        frames.sort(key=lambda frame: shapely.get_dimensions(frame.geometry.to_numpy()).max(initial=2)
                    if frame.empty else shapely.get_dimensions(frame.geometry.to_numpy()).min())
    left, *rest = frames
    for other in rest:
        ctx.check()
        left = geopandas.overlay(left, other, how=how, keep_geom_type=True, make_valid=False)
    ctx.publish(left, geometry_type=ctx.info[0]["geometry_type"] if left.empty else None)


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
