"""Export attributes from a processed vector dataset."""
import csv
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

import pandas as pd

from guizhi.infrastructure.operations.common import field


def export_table(ctx, fields: list[str] | None = None):
    frame = ctx.frames[0]
    if fields is not None:
        if not fields or len(set(fields)) != len(fields):
            raise ValueError("Fields must be a nonempty unique list")
        for name in fields:
            field(frame, name)
    attributes = frame[fields] if fields else frame.drop(columns=frame.geometry.name)
    if not len(attributes.columns):
        raise ValueError("Layer has no attribute columns to export")
    file = ctx.path("table.csv")
    with file.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(attributes.columns)
        for row in attributes.itertuples(index=False, name=None):
            writer.writerow([None if pd.isna(value) else value for value in row])
    with file.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        if next(reader) != list(attributes.columns) or sum(1 for _ in reader) != len(attributes):
            raise ValueError("CSV failed round-trip verification")
    ctx.record("table", file)
    return {"statistics": {"row_count": len(attributes)}}


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


def map_layout(ctx, title: str, format: Literal["png", "jpg"] = "png",
               legend: bool = True, scale: bool = True, border: bool = True,
               paper: Literal["a4", "a3"] = "a4", orientation: Literal["landscape", "portrait"] = "landscape",
               positions: dict[Literal["frame", "title", "legend", "scale"], LayoutPosition] | None = None):
    if not 1 <= len(title.strip()) <= 80:
        raise ValueError("图名应为 1 至 80 字")
    layout = {"title": title.strip(), "format": format, "legend": legend, "scale": scale, "border": border,
              "paper": paper, "orientation": orientation}
    if positions:
        layout["positions"] = {name: box.model_dump(mode="json") for name, box in positions.items()}
    ctx.json("layout.json", layout, "layout")
    return {"presentation": layout}


Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class MapAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["zoom", "visibility", "symbol", "table", "background"]
    layer: Annotated[str, StringConstraints(min_length=1, max_length=255)] | None = None
    color: Color | None = None
    fill: Color | None = None
    fill_opacity: Annotated[int, Field(ge=0, le=100)] | None = None
    visible: bool | None = None
    field: str | None = None
    direction: Literal["ascending", "descending"] = "ascending"


def map_view(ctx, action: Literal["zoom", "visibility", "symbol", "table", "background"],
             layer: str | None = None, color: Color | None = None, fill: Color | None = None,
             fill_opacity: int | None = None, visible: bool | None = None, field: str | None = None,
             direction: Literal["ascending", "descending"] = "ascending"):
    if (action == "background" and not color or action in {"visibility", "symbol", "table"} and not layer or
            action == "visibility" and visible is None or
            action == "symbol" and color is None and fill is None and fill_opacity is None):
        raise ValueError("请提供地图操作所需的图层和参数")
    value = MapAction(action=action, layer=layer, color=color, fill=fill, fill_opacity=fill_opacity,
                      visible=visible, field=field, direction=direction)
    ctx.json("map-actions.json", [value.model_dump(mode="json", exclude_none=True)], "map_actions")
