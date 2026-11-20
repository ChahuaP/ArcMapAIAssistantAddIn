"""Export attributes from a processed vector dataset."""
import csv
from itertools import chain
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

import pandas as pd

from guizhi.infrastructure.map_kml import Symbol
from guizhi.infrastructure.operations.common import field


def export_table(ctx, fields: list[str] | None = None, format: Literal["csv", "xlsx"] = "csv",
                 name: str = "table", sequence: bool = False, total_fields: list[str] | None = None):
    """按指定顺序导出属性表，可添加序号列和数值列合计。"""
    from numbers import Real

    frame = ctx.frames[0]
    attributes = frame.drop(columns=frame.geometry.name)
    columns = list(attributes.columns if fields is None else fields)
    if not columns or len(set(columns)) != len(columns) or any(column not in attributes for column in columns):
        raise ValueError("请选择存在且不重复的属性字段")
    totals = total_fields or []
    if len(set(totals)) != len(totals) or any(column not in columns for column in totals):
        raise ValueError("合计字段必须是已选择且不重复的字段")
    if sequence and "序号" in columns:
        raise ValueError("已选择的字段包含序号，请取消自动序号或移除同名字段")
    row = []
    if totals:
        for column in columns:
            values = attributes[column].dropna().tolist() if column in totals else []
            if any(isinstance(value, bool) or not isinstance(value, Real) for value in values):
                raise ValueError(f"字段 {column} 不是数值字段")
            row.append(sum(values) if column in totals else None)
        if sequence:
            row.insert(0, "合计")
        elif len(totals) < len(columns):
            row[next(index for index, column in enumerate(columns) if column not in totals)] = "合计"

    def rows():
        for index, values in enumerate(attributes[columns].itertuples(index=False, name=None), 1):
            if index % 1024 == 0:
                ctx.check()
            values = [None if pd.isna(value) else value for value in values]
            yield [index, *values] if sequence else values
        if row:
            yield row

    headers = ["序号", *columns] if sequence else columns
    file = ctx.path(f"{export_name(name)}.{format}")
    expected = len(attributes) + bool(row)
    if format == "xlsx":
        from openpyxl import Workbook, load_workbook
        from openpyxl.cell import WriteOnlyCell

        if expected >= 1_048_576:
            raise ValueError("内容超出 Excel 单个工作表的行数限制")
        workbook = Workbook(write_only=True)
        sheet = workbook.create_sheet("属性表")
        for values in chain([headers], rows()):
            cells = []
            for value in values:
                cell = WriteOnlyCell(sheet, value=value)
                if isinstance(value, str):
                    cell.data_type = "s"
                cells.append(cell)
            sheet.append(cells)
        workbook.save(file)
        checked = load_workbook(file, read_only=True)
        try:
            actual = checked.active.values
            if list(next(actual)) != headers or sum(1 for _ in actual) != expected:
                raise ValueError("Excel 导出校验失败")
        finally:
            checked.close()
    else:
        with file.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows())
        with file.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            if next(reader) != headers or sum(1 for _ in reader) != expected:
                raise ValueError("CSV 导出校验失败")
    ctx.record("table", file)
    return {"statistics": {"row_count": len(attributes), "format": format, "file": file.name}}


def export_name(name):
    if not name or len(name) > 100 or name != name.strip(" .") or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name):
        raise ValueError("请输入有效的文件名称，不含目录和特殊字符")
    if Path(name + ".tmp").is_reserved():
        raise ValueError("文件名称为系统保留名称")
    return name


def export_grouped_kml(ctx, group_field: str, name: str = "分组导出",
                       naming: Literal["sequence", "group"] = "sequence", style: Symbol | None = None):
    """按属性字段分组，每组导出独立 KML，并将清单和文件打包。"""
    import json
    import pyogrio
    from guizhi.infrastructure.map_kml import export_kml

    frame = ctx.frames[0]
    field(frame, group_field)
    if frame.crs is None:
        raise ValueError("请先定义图层坐标系")
    groups = frame.groupby(group_field, sort=True, dropna=False, observed=True)
    if not 1 <= groups.ngroups <= 500:
        raise ValueError("分组数量须在 1 到 500 之间")
    archive = ctx.path(export_name(name) + ".zip")
    manifest = []
    used = set()
    with TemporaryDirectory(dir=archive.parent) as folder, ZipFile(archive, "x", ZIP_DEFLATED) as bundle:
        for index, (value, rows) in enumerate(groups, 1):
            ctx.check()
            label = "空值" if pd.isna(value) else str(value)
            stem = str(index) if naming == "sequence" else export_name(label)
            if stem.casefold() in used:
                raise ValueError("组名会产生重复文件名，请使用序号命名")
            used.add(stem.casefold())
            source = Path(folder) / "group.gpkg"
            output = Path(folder) / (stem + ".kml")
            pyogrio.write_dataframe(rows.reset_index(drop=True), source, layer="features", driver="GPKG")
            symbol = style.model_dump(exclude_unset=True) if style else {"color": "#1785db", "fill": "#1785db",
                "opacity": 100, "fillOpacity": 100, "noOutline": False, "width": 1.5}
            export_kml(source, "features", output, {"name": label, "style": symbol})
            bundle.write(output, output.name)
            manifest.append({"group": label, "file": output.name, "count": len(rows)})
            source.unlink()
            output.unlink()
        bundle.writestr("导出清单.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    ctx.record("package", archive)
    return {"statistics": {"groups": manifest, "file": archive.name}}


class LayoutPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=.065, le=1)
    height: float = Field(ge=.055, le=1)

    @model_validator(mode="after")
    def on_paper(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("版面元素超出纸张边界")
        return self


class MapFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layers: list[str] | None = Field(default=None, min_length=1, max_length=32)
    extent: Literal["current", "layers"] = "current"
    caption: str = Field(default="", max_length=80)
    legend: bool = True
    scale: bool = True
    north: bool = False


def map_layout(ctx, title: str | None = None, format: Literal["png", "jpg", "pdf"] | None = None,
               legend: bool | None = None, scale: bool | None = None, border: bool | None = None,
               paper: Literal["a4", "a3"] | None = None, orientation: Literal["landscape", "portrait"] | None = None,
               positions: dict[Literal["frame", "frame2", "title", "legend", "scale", "north", "note"], LayoutPosition] | None = None,
               frames: dict[Literal["frame", "frame2"], MapFrame] | None = None,
               note: Annotated[str, Field(max_length=160)] | None = None,
               monochrome: bool | None = None, update: bool = False, export: bool = False):
    """生成地图版面设置。export=true 时在桌面应用版面后导出。"""
    if title is not None and not 1 <= len(title.strip()) <= 80:
        raise ValueError("图名应为 1 至 80 字")
    layout = {key: value for key, value in {"title": title.strip() if title else None, "format": format,
              "legend": legend, "scale": scale, "border": border, "paper": paper, "orientation": orientation,
              "note": note, "monochrome": monochrome}.items() if value is not None}
    layout.update(update=update, export=export)
    if positions:
        layout["positions"] = {name: box.model_dump(mode="json") for name, box in positions.items()}
    if frames is not None:
        if "frame" not in frames:
            raise ValueError("版面需要主地图框")
        layout["frames"] = {name: frame.model_dump(mode="json") for name, frame in frames.items()}
    ctx.json("layout.json", layout, "layout")
    return {"presentation": layout}


Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class MapAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["zoom", "visibility", "symbol", "classify", "table", "background"]
    layer: Annotated[str, StringConstraints(min_length=1, max_length=255)] | None = None
    color: Color | None = None
    fill: Color | None = None
    fill_opacity: Annotated[int, Field(ge=0, le=100)] | None = None
    visible: bool | None = None
    field: str | None = None
    direction: Literal["ascending", "descending"] = "ascending"


def map_view(ctx, action: Literal["zoom", "visibility", "symbol", "classify", "table", "background"],
             layer: str | None = None, color: Color | None = None, fill: Color | None = None,
             fill_opacity: int | None = None, visible: bool | None = None, field: str | None = None,
             direction: Literal["ascending", "descending"] = "ascending"):
    if (action == "background" and not color or action in {"visibility", "symbol", "classify", "table"} and not layer or
            action == "classify" and not field or
            action == "visibility" and visible is None or
            action == "symbol" and color is None and fill is None and fill_opacity is None):
        raise ValueError("请提供地图操作所需的图层和参数")
    value = MapAction(action=action, layer=layer, color=color, fill=fill, fill_opacity=fill_opacity,
                      visible=visible, field=field, direction=direction)
    ctx.json("map-actions.json", [value.model_dump(mode="json", exclude_none=True)], "map_actions")
