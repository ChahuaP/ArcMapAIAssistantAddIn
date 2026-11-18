"""Shared geometry, field and selection invariants."""
import operator
from functools import reduce
import re
from math import isfinite
from typing import Literal

import geopandas
import pandas as pd
import shapely
from pydantic import BaseModel, ConfigDict
from guizhi.domain.diagnostics import Fault

Unit = Literal["meters", "kilometers", "map_units", "degrees"]
Mode = Literal["new_selection", "add_to_selection", "remove_from_selection", "select_subset"]


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layer: str
    revision: str
    fids: list[int]


def compatible(frames):
    crs = frames[0].crs
    if crs is None or any(frame.crs != crs for frame in frames):
        raise ValueError("All inputs must have the same defined CRS")
    return crs


def valid(frame, *, polygon=False):
    geometry = frame.geometry
    invalid = geometry.isna() | ~geometry.is_valid
    if invalid.any():
        raise Fault("data", "invalid_geometry", "Invalid or null geometry; repair the input explicitly",
                    invalid_count=int(invalid.sum()))
    if polygon and not geometry.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("Operation requires polygon data")


def distance_in_crs(crs, value, unit):
    if not isfinite(value) or value < 0:
        raise Fault("parameter", "invalid_distance", "Distance must be finite and nonnegative", unit=unit)
    if crs is None or len(crs.axis_info) != 2:
        raise ValueError("Distance requires a defined 2D CRS")
    if unit == "degrees":
        if not crs.is_geographic:
            raise ValueError("Degree distance requires a geographic CRS")
        return value
    if not crs.is_projected:
        raise ValueError("Linear distance requires a projected CRS or an explicit degree unit")
    first, second = (a.unit_conversion_factor for a in crs.axis_info)
    if first != second or first <= 0:
        raise ValueError("The horizontal axes must use the same linear unit")
    return value if unit == "map_units" else value * (1000 if unit == "kilometers" else 1) / first


def field(frame, name):
    if name not in frame or name == frame.geometry.name:
        raise ValueError(f"Unknown attribute field: {name}")
    return frame[name]


def like_regex(pattern):
    return "".join(".*" if c == "%" else "." if c == "_" else re.escape(c) for c in pattern)


def condition(frame, value):
    return condition_mask(frame, value).fillna(False).to_numpy(dtype=bool)


def condition_mask(frame, value, depth=0):
    if depth >= 16 or not isinstance(value, dict):
        raise ValueError("Attribute expression is invalid or too deeply nested")
    op = value.get("op")
    comparisons = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt, "gte": operator.ge,
                   "lt": operator.lt, "lte": operator.le}
    if op in {"and", "or"}:
        items = value.get("conditions")
        if set(value) != {"op", "conditions"} or not isinstance(items, list) or not 1 <= len(items) <= 32:
            raise ValueError("AND/OR requires a nonempty conditions list")
        combine = operator.and_ if op == "and" else operator.or_
        return reduce(combine, [condition_mask(frame, item, depth + 1) for item in items])
    if op == "not":
        if set(value) != {"op", "condition"}:
            raise ValueError("NOT requires one condition")
        return ~condition_mask(frame, value["condition"], depth + 1)
    series = field(frame, value.get("field"))
    keys = set(value) - {"op", "field"}
    if op in {"is_null", "is_not_null"} and not keys:
        return (series.isna() if op == "is_null" else series.notna()).astype("boolean")
    missing = series.isna()
    if op in comparisons and keys in ({"value"}, {"value_field"}):
        other = field(frame, value["value_field"]) if "value_field" in value else value["value"]
        if other is None or isinstance(other, (dict, list)):
            raise ValueError("Use is_null for NULL and scalar values for comparisons")
        mask = comparisons[op](series, other)
        if isinstance(other, pd.Series):
            missing |= other.isna()
    elif op in {"contains", "like"} and keys == {"value"} and isinstance(value["value"], str):
        if not pd.api.types.is_string_dtype(series):
            raise ValueError("LIKE requires a text field")
        mask = (series.str.contains(value["value"], regex=False, na=False) if op == "contains" else
                series.str.fullmatch(like_regex(value["value"]), na=False, flags=re.DOTALL))
    elif op == "in" and keys == {"values"} and isinstance(value["values"], list) and value["values"]:
        mask = series.isin(value["values"]) & series.notna()
    elif op == "between" and keys == {"values"} and isinstance(value["values"], list) and len(value["values"]) == 2:
        mask = series.between(*value["values"])
    else:
        raise ValueError(f"Invalid attribute condition: {op}")
    return mask.astype("boolean").mask(missing)


def selection_fids(ctx, selection):
    if selection.layer != ctx.request.inputs[0].layer or selection.revision != ctx.selection_hashes[0]:
        raise Fault("association", "selection_revision_mismatch", "Selection belongs to a different dataset revision",
                    expected_revision=ctx.selection_hashes[0], selected_revision=selection.revision,
                    same_layer=selection.layer == ctx.request.inputs[0].layer)
    fids = set(selection.fids)
    if len(fids) != len(selection.fids) or not fids.issubset(ctx.frames[0].index):
        raise ValueError("Selection contains duplicate or unknown feature IDs")
    return fids


def combine_selection(chosen, mode, previous):
    if mode == "new_selection" and previous is None:
        return chosen
    if previous is not None:
        if mode == "add_to_selection":
            return previous | chosen
        if mode == "remove_from_selection":
            return previous - chosen
        if mode == "select_subset":
            return previous & chosen
    raise ValueError("选择方式与已有选区不匹配")


def select(ctx, chosen, mode, previous):
    chosen = combine_selection(chosen, mode, selection_fids(ctx, previous) if previous is not None else None)
    result = Selection(layer=ctx.request.inputs[0].layer, revision=ctx.selection_hashes[0],
                       fids=sorted(chosen)).model_dump()
    ctx.json("selection.json", result, "selection")
    return {"selection": result, "statistics": {"selected_count": len(chosen)}}


def concatenate(frames):
    crs = compatible(frames)
    normalized = [f if f.geometry.name == "geometry" else f.rename_geometry("geometry") for f in frames]
    return geopandas.GeoDataFrame(pd.concat(normalized, ignore_index=True), geometry="geometry", crs=crs)


def separate_fields(frames):
    seen = {"geometry"}
    result = []
    for i, frame in enumerate(frames, 1):
        frame = frame if frame.geometry.name == "geometry" else frame.rename_geometry("geometry")
        renames = {}
        for field_name in frame.drop(columns="geometry"):
            new = field_name
            while new.casefold() in seen:
                new += f"_{i}"
            seen.add(new.casefold())
            if new != field_name:
                renames[field_name] = new
        result.append(frame.rename(columns=renames))
    return result
