"""Apply explicit desktop edits to a sealed vector layer."""
from typing import Annotated, Literal

import geopandas
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from shapely.geometry import shape

from guizhi.infrastructure.operations.common import valid


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["add", "update", "delete"]
    fid: StrictInt | None = None
    geometry: dict | None = None
    fields: dict = Field(default_factory=dict)


def changes(ctx, changes: Annotated[list[Change], Field(min_length=1, max_length=500)]):
    original = ctx.frames[0]
    frame = original.copy()
    geometry = frame.geometry.name
    fields = frame.columns.drop(geometry)
    if frame.crs is None:
        raise ValueError("请先为图层定义坐标系")
    if frame.geometry.has_z.any() or frame.geometry.has_m.any():
        raise ValueError("地图编辑适用于二维图层")
    geom_type = ctx.info[0]["geometry_type"]
    expected = geom_type.removeprefix("Multi")
    if expected not in {"Point", "LineString", "Polygon"}:
        raise ValueError("请选择点、线或面图层")
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
                if value is None and pd.api.types.is_integer_dtype(frame[name].dtype):
                    frame[name] = frame[name].astype("Int64")
                frame.at[change.fid, name] = value
            if candidate is not None:
                frame.at[change.fid, geometry] = candidate
    if additions:
        incoming = geopandas.GeoDataFrame(additions, geometry=geometry, crs=frame.crs)
        for name in fields:
            dtype = original[name].dtype
            if pd.api.types.is_integer_dtype(dtype) and incoming[name].isna().any():
                dtype = "Int64"
            elif pd.api.types.is_bool_dtype(dtype) and incoming[name].isna().any():
                dtype = "boolean"
            incoming[name] = incoming[name].astype(dtype)
        frame = geopandas.GeoDataFrame(pd.concat([frame, incoming], ignore_index=True), geometry=geometry, crs=frame.crs)
    valid(frame)
    ctx.publish(frame, geometry_type=geom_type if frame.empty else None)
    ctx.json("edits.json", {"revision": ctx.selection_hashes[0],
        "changes": [change.model_dump(mode="json") for change in changes]}, "edits")
    return {"changeset": {"target_revision": ctx.selection_hashes[0], "count": len(changes)}}


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
