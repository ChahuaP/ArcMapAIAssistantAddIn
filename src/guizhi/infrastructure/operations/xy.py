"""Convert tabular coordinates to a point layer without changing the source table."""
import csv
import io
import math
from contextlib import contextmanager
from datetime import date, time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from openpyxl import load_workbook

from guizhi.infrastructure.crs_options import resolve_definition
from guizhi.infrastructure.documents import checked_office, read_text_data


@contextmanager
def tables(source):
    path = Path(source)
    if path.stat().st_size > 50 << 20:
        raise ValueError("坐标表大小不得超过 50 MB")
    if path.suffix.lower() == ".xlsx":
        checked_office(path)
        book = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        try:
            yield {s.title: s.iter_rows(values_only=True) for s in book.worksheets}
        finally:
            book.close()
    elif path.suffix.lower() == ".csv":
        yield {None: csv.reader(io.StringIO(read_text_data(path), newline=""))}
    else:
        raise ValueError("请选择 CSV 或 XLSX 坐标表")


def header(rows):
    names = [str(v).strip() if v is not None else "" for v in next(rows, ())]
    while names and not names[-1]:
        names.pop()
    if not names or not all(names) or len({n.casefold() for n in names}) != len(names):
        raise ValueError("请在首行填写完整且不重复的字段名称")
    return names


def table_headers(source):
    result = []
    with tables(source) as sheets:
        for title, rows in sheets.items():
            try:
                result.append({"sheet": title, "fields": header(rows)})
            except ValueError as error:
                result.append({"sheet": title, "fields": [], "issue": str(error)})
    return result


def inspect(ctx):
    return {"fields": table_headers(ctx.documents[0][0])}


def points(ctx, x_field: str, y_field: str, source_crs: str, sheet: str | None = None):
    target = resolve_definition(ctx.workspace, source_crs)
    with tables(ctx.documents[0][0]) as sheets:
        if sheet is None and len(sheets) == 1:
            sheet = next(iter(sheets))
        if sheet not in sheets:
            raise ValueError("请选择坐标表中的工作表")
        rows = sheets[sheet]
        names = header(rows)
        if x_field == y_field or x_field not in names or y_field not in names:
            raise ValueError("请选择两个不同的横、纵坐标字段")
        xi, yi = names.index(x_field), names.index(y_field)
        records, xs, ys = [], [], []
        limit = math.pi / target.axis_info[0].unit_conversion_factor if target.is_geographic else None
        for line, values in enumerate(rows, 2):
            if all(v is None or v == "" for v in values):
                continue
            if any(v is not None and v != "" for v in values[len(names):]):
                raise ValueError(f"第 {line} 行有内容缺少字段名称")
            record = list(values[:len(names)]) + [None] * max(0, len(names) - len(values))
            try:
                x, y = float(record[xi]), float(record[yi])
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError()
            except (TypeError, ValueError) as error:
                raise ValueError(f"第 {line} 行横、纵坐标缺失或不是有效数字") from error
            if limit and (abs(x) > limit or abs(y) > limit / 2):
                raise ValueError(f"第 {line} 行坐标超出经纬度范围，请检查字段和坐标系")
            records.append(record)
            xs.append(x)
            ys.append(y)
            if line % 1000 == 0:
                ctx.check()
    if not records:
        raise ValueError("所选坐标表没有数据行")
    frame = pd.DataFrame(records, columns=names)
    for name in names:
        series = frame[name]
        if series.dtype == object or pd.api.types.is_datetime64_any_dtype(series):
            frame[name] = series.map(lambda v: None if pd.isna(v) else
                                     v.isoformat() if isinstance(v, (date, time)) else str(v))
    geometry = "geometry"
    while geometry.casefold() in {name.casefold() for name in names}:
        geometry += "_"
    frame[geometry] = gpd.points_from_xy(xs, ys, crs=target)
    ctx.publish(gpd.GeoDataFrame(frame, geometry=geometry, crs=target), geometry_type="Point")
