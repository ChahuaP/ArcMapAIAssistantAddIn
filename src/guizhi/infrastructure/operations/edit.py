"""Apply explicit desktop edits to a sealed vector layer."""
import json
from typing import Annotated, Literal

import geopandas
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, StrictInt
import shapely
from shapely.geometry import mapping, shape

from guizhi.infrastructure.operations.common import Selection, selection_fids, valid


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["add", "update", "delete"]
    fid: StrictInt | None = None
    geometry: dict | None = None
    fields: dict = Field(default_factory=dict)


def editable(frame, info):
    if frame.crs is None:
        raise ValueError("请先为图层定义坐标系")
    if frame.geometry.has_z.any() or frame.geometry.has_m.any():
        raise ValueError("地图编辑适用于二维图层")
    kind = info["geometry_type"]
    if kind.removeprefix("Multi") not in {"Point", "LineString", "Polygon"}:
        raise ValueError("请选择点、线或面图层")
    return kind


def publish_edit(ctx, frame, kind, **options):
    valid(frame)
    kinds = frame.geom_type.dropna().unique()
    ctx.publish(frame, geometry_type=kinds[0] if len(kinds) == 1 else kind, **options)


def changes(ctx, changes: Annotated[list[Change], Field(min_length=1, max_length=500)]):
    original = ctx.frames[0]
    frame = original.copy()
    geometry = frame.geometry.name
    fields = frame.columns.drop(geometry)
    geom_type = editable(frame, ctx.info[0])
    expected = geom_type.removeprefix("Multi")
    seen = set()
    additions = []
    for change in changes:
        ctx.check()
        if change.fid in seen or (change.action != "add" and change.fid not in frame.index):
            raise ValueError("有重复或不存在的要素，请重新打开图层")
        if change.fid is not None:
            seen.add(change.fid)
        if change.action == "delete":
            if change.geometry is not None or change.fields:
                raise ValueError("删除操作只接受要素编号")
            frame = frame.drop(index=change.fid)
            continue
        if set(change.fields) - set(fields):
            raise ValueError("修改包含图层中不存在的字段")
        updates = {name: field_value(original[name], value) for name, value in change.fields.items()}
        candidate = shape(change.geometry) if change.geometry else None
        if candidate is not None and (candidate.is_empty or not candidate.is_valid or candidate.has_z or candidate.has_m or
                candidate.geom_type.removeprefix("Multi") != expected):
            raise ValueError("几何无效或与图层类型不一致")
        if change.action == "add":
            if change.fid is not None or candidate is None:
                raise ValueError("新建要素需要有效几何")
            additions.append({**{name: updates.get(name) for name in fields}, geometry: candidate})
        else:
            for name, value in updates.items():
                if value is None:
                    dtype = frame[name].dtype
                    if pd.api.types.is_bool_dtype(dtype):
                        frame[name] = frame[name].astype("boolean")
                    elif pd.api.types.is_integer_dtype(dtype):
                        frame[name] = frame[name].astype("Int64")
                frame.at[change.fid, name] = value
            if candidate is not None:
                frame.at[change.fid, geometry] = candidate
    if additions:
        incoming = geopandas.GeoDataFrame(additions, geometry=geometry, crs=frame.crs)
        for name in fields:
            dtype = original[name].dtype
            if pd.api.types.is_integer_dtype(dtype) and (incoming[name].isna().any() or frame[name].isna().any()):
                dtype = str(dtype).capitalize()
                frame[name] = frame[name].astype(dtype)
            elif pd.api.types.is_bool_dtype(dtype) and incoming[name].isna().any():
                dtype = "boolean"
                frame[name] = frame[name].astype(dtype)
            incoming[name] = incoming[name].astype(dtype)
        frame = geopandas.GeoDataFrame(pd.concat([frame, incoming], ignore_index=True), geometry=geometry, crs=frame.crs)
    publish_edit(ctx, frame, geom_type)
    ctx.json("edits.json", {"revision": ctx.selection_hashes[0],
        "changes": [change.model_dump(mode="json") for change in changes]}, "edits")
    return {"changeset": {"target_revision": ctx.selection_hashes[0], "count": len(changes)}}


def transfer_rows(ctx, selection):
    from guizhi.infrastructure.crs_options import projection

    source, target = ctx.frames
    source_info, target_info = ctx.info
    source_kind, target_kind = editable(source, source_info), editable(target, target_info)
    if source_kind.removeprefix("Multi") != target_kind.removeprefix("Multi"):
        raise ValueError("源图层和目标图层的几何类型必须一致")
    fids = sorted(selection_fids(ctx, selection))
    if not 1 <= len(fids) <= 500:
        raise ValueError("请选择 1 至 500 个要素")
    selected = source.loc[fids]
    geom = selected.geometry.array
    if source.crs != target.crs:
        transform = projection(source.crs, target.crs, selected.total_bounds)
        geom = shapely.transform(geom, lambda x, y: transform.transform(x, y, errcheck=True), interleaved=False)

    target_fields = dict(zip(target_info["fields"], target_info["dtypes"], strict=True))
    source_fields = {name.casefold(): (name, kind) for name, kind in zip(source_info["fields"], source_info["dtypes"], strict=True)}
    attrs = {}
    base = target.copy()
    for name, kind in target_fields.items():
        match = source_fields.get(name.casefold())
        values = list(selected[match[0]]) if match and match[1] == kind else [None] * len(selected)
        if kind.startswith(("int", "uint")):
            dtype = kind.capitalize()
            base[name] = base[name].astype(dtype)
        elif kind == "bool":
            dtype = "boolean"
            base[name] = base[name].astype(dtype)
        else:
            dtype = target[name].dtype
        attrs[name] = pd.Series(values, dtype=dtype)
    geometry = target.geometry.name
    incoming = geopandas.GeoDataFrame({**attrs, geometry: geom}, geometry=geometry, crs=target.crs)
    return base, incoming, source_kind, target_kind, fids


def transfer_plan(ctx, selection: Selection):
    _, incoming, _, _, _ = transfer_rows(ctx, selection)
    rows = json.loads(incoming.drop(columns=incoming.geometry.name).to_json(orient="records", date_format="iso", double_precision=15))
    for row in rows:
        for key, value in row.items():
            if type(value) is int and abs(value) > 2**53 - 1:
                row[key] = str(value)
    return {"features": [{"geometry": mapping(geom), "properties": row}
                         for geom, row in zip(incoming.geometry, rows, strict=True)]}


def transfer(ctx, selection: Selection, mode: Literal["copy", "cut"]):
    base, incoming, source_kind, target_kind, fids = transfer_rows(ctx, selection)
    source = ctx.frames[0]
    geometry = base.geometry.name
    target_crs = base.crs
    added = geopandas.GeoDataFrame(pd.concat([base, incoming], ignore_index=True), geometry=geometry, crs=target_crs)
    publish_edit(ctx, added, target_kind)
    if mode == "cut":
        publish_edit(ctx, source.drop(index=fids), source_kind, artifact="transfer-source", filename="source.gpkg")
    return {"changeset": {"mode": mode, "moved": len(fids), "source_revision": ctx.selection_hashes[0],
                          "target_revision": ctx.selection_hashes[1], "fids": fids}}


def field_value(series, value):
    if value is None:
        return None
    try:
        if pd.api.types.is_bool_dtype(series.dtype):
            if value in (True, False, "true", "false", "1", "0"):
                return value in (True, "true", "1")
            raise ValueError
        if pd.api.types.is_integer_dtype(series.dtype):
            if isinstance(value, (int, str)) and not isinstance(value, bool):
                return int(value)
            raise ValueError
        if pd.api.types.is_float_dtype(series.dtype):
            number = float(value)
            if pd.notna(number) and abs(number) != float("inf"):
                return number
            raise ValueError
        if pd.api.types.is_datetime64_any_dtype(series.dtype):
            return pd.Timestamp(value)
        if isinstance(value, str):
            return value
        raise ValueError
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError(f"字段 {series.name} 的值不符合原字段类型") from error
