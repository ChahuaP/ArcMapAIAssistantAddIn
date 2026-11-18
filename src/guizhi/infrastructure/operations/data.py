"""Versioned dataset and selection operations."""
import re
from typing import Literal

from typing import Annotated, Any

import numpy as np
import pandas as pd
from shapely import STRtree
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from guizhi.infrastructure.operations.common import (
    Mode, Selection, Unit, compatible, concatenate, distance_in_crs,
    select, selection_fids, valid,
)


def describe(ctx):
    """读取要素数、几何类型、范围和源坐标系。返回统计与元数据。"""
    frame, info = ctx.frames[0], ctx.info[0]
    bounds = [float(n) for n in info["total_bounds"]] if info["total_bounds"] else None
    crs = ctx.datasets[0]["crs_wkt"]
    from guizhi.infrastructure.crs_options import describe_crs

    coordinates = describe_crs(crs)
    if crs:
        from pyproj import CRS

        crs = CRS(crs).to_string()
    return {"value": info["features"], "statistics": {"feature_count": info["features"], "coordinate_system": coordinates,
                           "layer": ctx.request.inputs[0].layer}, "revision": ctx.selection_hashes[0],
            "layer": ctx.request.inputs[0].layer, "bounds": bounds if bounds and np.isfinite(bounds).all() else None,
            "crs": crs, "crs_name": info.get("source_crs_name"),
            "geometry_type": info["geometry_type"],
            "geometry_types": sorted(frame.geom_type.dropna().unique().tolist()) if frame is not None else []}


def list_fields(ctx):
    """读取字段名与类型，属性值需另行查询。"""
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


AttributeValue = str | int | float | bool | None


class AttributeFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["and", "or", "not", "eq", "ne", "gt", "gte", "lt", "lte", "is_null", "is_not_null",
                "contains", "like", "in", "between"]
    field: str | None = None
    value: AttributeValue = None
    value_field: str | None = None
    values: list[Any] | None = None
    conditions: Annotated[list["AttributeFilter"], Field(min_length=1, max_length=32)] | None = None
    condition: "AttributeFilter | None" = None


def predicate_sql(expression):
    # A query clause belongs to the enclosing SELECT produced by the executor.
    if (not expression.strip() or len(expression) > 4000 or "\0" in expression or
            re.match(r"(?:\s|\(|--[^\n]*(?:\n|$)|/\*.*?\*/)*(?:SELECT|WITH|WHERE|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|PRAGMA)\b",
                     expression, re.IGNORECASE | re.DOTALL)):
        raise ValueError("sql 只填写 WHERE 后的布尔条件，例如 DLMC = '草地'。字段、运算符和值也可用 where 结构化传入")
    return expression


PredicateSQL = Annotated[str, Field(min_length=1, max_length=4000), AfterValidator(predicate_sql)]


def select_by_attribute(ctx, where: AttributeFilter | None = None, mode: Mode = "new_selection",
                        previous: Selection | None = None, sql: PredicateSQL | None = None):
    """输入待选择的图层。返回选区和匹配数。交付 selection 将标记原图层；只求数量可仅交付 answer。"""
    if (where is None) == (sql is None):
        raise ValueError("请提供一组属性查询条件")
    from guizhi.infrastructure.operations.sql_expression import attribute_predicate, evaluate

    info = ctx.info[0]
    fields = dict(zip(info["fields"], info["dtypes"], strict=True))
    expression, arguments = attribute_predicate(where, fields) if where is not None else (sql, None)
    rows = evaluate(ctx.directory / ctx.evidence[0]["path"], ctx.request.inputs[0].layer,
                    info["fid_column"], fields, expression, predicate=True, arguments=arguments)
    return select(ctx, {row[0] for row in rows}, mode, previous)


def select_by_location(
    ctx, predicate: Literal["intersect", "contain", "within", "touch", "overlap", "cross", "within_a_distance"],
    distance: float | None = None, unit: Unit = "meters",
    mode: Mode = "new_selection", previous: Selection | None = None,
):
    """输入顺序为目标图层、空间参照图层。返回目标图层中匹配的要素选区和数量。"""
    frame, query = ctx.frames
    crs = frame.crs
    if crs is None or query.crs is None:
        raise ValueError("按位置选择前，请为目标图层和参照图层定义坐标系")
    for item in ctx.frames:
        try:
            valid(item)
        except ValueError as error:
            raise ValueError("存在空或无效的几何，请先检查图层") from error
    query = query.loc[~query.geometry.is_empty]
    if crs != query.crs and not query.empty:
        from guizhi.infrastructure.crs_options import projection
        import shapely

        tx = projection(query.crs, crs, query.total_bounds)
        query = query.set_geometry(shapely.transform(query.geometry.to_numpy(),
            lambda x, y, z=None: tx.transform(x, y, z, errcheck=True), interleaved=False, include_z=None))
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
    """输入原图层与同一版本的选择集，输出只含所选要素的新图层。"""
    from guizhi.infrastructure.operations.table import identifier, table_copy

    frame = ctx.frames[0]
    ids = selection_fids(ctx, selection)
    remaining = frame.loc[frame.index.isin(ids)]
    index = identifier(ctx.info[0]["fid_column"])

    def keep(db, table, _):
        db.execute("CREATE TEMP TABLE selected (id INTEGER PRIMARY KEY)")
        db.executemany("INSERT INTO selected VALUES (?)", ((fid,) for fid in sorted(ids)))
        db.execute(f"DELETE FROM {table} WHERE {index} NOT IN (SELECT id FROM selected)")
        bounds = [None] * 4 if remaining.empty or remaining.geometry.is_empty.all() else remaining.total_bounds.tolist()
        db.execute("UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=? WHERE table_name=?",
                   (*bounds, ctx.request.inputs[0].layer))
        return {"deleted": len(frame) - len(remaining)}

    return table_copy(ctx, keep, edit_source=False)
