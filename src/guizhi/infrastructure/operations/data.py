"""Versioned dataset and selection operations."""
from typing import Literal

import numpy as np
import pandas as pd
from shapely import STRtree

from guizhi.infrastructure.operations.common import (
    Mode, Selection, Unit, compatible, concatenate, condition, distance_in_crs,
    select, selection_fids, valid,
)


def describe(ctx):
    frame, info = ctx.frames[0], ctx.info[0]
    bounds = [float(n) for n in frame.total_bounds]
    return {"statistics": {"feature_count": len(frame)}, "revision": ctx.selection_hashes[0],
            "layer": ctx.request.inputs[0].layer, "bounds": bounds if np.isfinite(bounds).all() else None,
            "crs": frame.crs.to_string() if frame.crs else None, "geometry_type": info["geometry_type"]}


def list_fields(ctx):
    info = ctx.info[0]
    return {"fields": [{"name": name, "type": kind} for name, kind in
                       zip(info["fields"].tolist(), info["dtypes"].tolist(), strict=True)]}


def copy(ctx):
    ctx.publish(ctx.frames[0], geometry_type=ctx.info[0]["geometry_type"])


def merge(ctx):
    ctx.publish(concatenate(ctx.frames), geometry_type=ctx.info[0]["geometry_type"] if all(f.empty for f in ctx.frames) else None)


def append(ctx, schema: Literal["strict", "map"] = "strict"):
    base, *sources = ctx.frames
    columns = list(base.drop(columns=base.geometry.name))
    dtype = dict(zip(ctx.info[0]["fields"], ctx.info[0]["dtypes"], strict=True))
    compatible(ctx.frames)
    target_family = ctx.info[0]["geometry_type"].replace("Multi", "", 1)
    for info in ctx.info[1:]:
        if info["geometry_type"].replace("Multi", "", 1) != target_family:
            raise ValueError("Append requires matching target and source geometry types")
    prepared = [base]
    for index, source in enumerate(sources, 1):
        attrs = source.drop(columns=source.geometry.name)
        source_types = dict(zip(ctx.info[index]["fields"], ctx.info[index]["dtypes"], strict=True))
        if schema == "strict" and source_types != dtype:
            raise ValueError("Append requires matching field names and types")
        if any(source_types[name] != kind for name, kind in dtype.items() if name in source_types):
            raise ValueError("Append field types differ")
        mapped = source[[name for name in columns if name in attrs] + [source.geometry.name]].copy()
        for name in columns:
            kind = dtype[name]
            if kind.startswith(("int", "uint")):
                nullable = kind.capitalize()
                mapped[name] = mapped[name].astype(nullable) if name in mapped else pd.array([None] * len(mapped), dtype=nullable)
            elif name not in mapped:
                mapped[name] = pd.Series(index=mapped.index, dtype=base[name].dtype)
        prepared.append(mapped)
    result = concatenate(prepared)
    ctx.publish(result, geometry_type=ctx.info[0]["geometry_type"] if result.empty else None)
    return {"changeset": {"target_revision": ctx.selection_hashes[0], "appended": len(result) - len(base)}}


def select_by_attribute(ctx, where: dict, mode: Mode = "new_selection", previous: Selection | None = None):
    frame = ctx.frames[0]
    chosen = set(map(int, frame.index[condition(frame, where)]))
    return select(ctx, chosen, mode, previous)


def select_by_location(
    ctx, predicate: Literal["intersect", "contain", "within", "touch", "overlap", "cross", "within_a_distance"],
    distance: float | None = None, unit: Unit = "meters",
    mode: Mode = "new_selection", previous: Selection | None = None,
):
    frame, query = ctx.frames
    crs = compatible(ctx.frames)
    for item in ctx.frames:
        valid(item)
    translation = {"intersect": "intersects", "contain": "contains", "touch": "touches",
                   "overlap": "overlaps", "cross": "crosses", "within_a_distance": "dwithin"}
    spatial = translation.get(predicate, predicate)
    if (distance is not None) != (spatial == "dwithin"):
        raise ValueError("Supply distance only for within_a_distance")
    length = distance_in_crs(crs, distance, unit) if distance is not None else None
    pairs = STRtree(query.geometry.to_numpy()).query(frame.geometry.to_numpy(), predicate=spatial, distance=length)
    return select(ctx, set(map(int, frame.index[pairs[0]])), mode, previous)


def selection_count(ctx, selection: Selection):
    return {"statistics": {"selected_count": len(selection_fids(ctx, selection))}}


def clear_selection(ctx):
    return select(ctx, set(), "new_selection", None)


def export_selection(ctx, selection: Selection):
    frame = ctx.frames[0]
    ids = selection_fids(ctx, selection)
    ctx.publish(frame.loc[frame.index.isin(ids)], geometry_type=ctx.info[0]["geometry_type"])
