"""Immutable built-in map and layout revisions."""
import csv
from html import escape
import json
from typing import Annotated, Literal
from uuid import uuid4

import pandas as pd
from pyproj import CRS
from pydantic import StringConstraints
from shapely.geometry import GeometryCollection, mapping, shape

from guizhi.infrastructure.operations.common import Selection, field
from guizhi.infrastructure.operations.evidence import read_artifact

Label = Annotated[str, StringConstraints(min_length=1, max_length=200)]
PALETTE = ["#1b8873", "#3e70a5", "#ad713a", "#896a98", "#6f803e"]


def view(ctx, view_run: str | None):
    if view_run is None:
        return {"layers": [], "extent": None, "crs": None, "crs_wkt": None, "mode": "map",
                "text": {"title": "规智地图", "subtitle": "", "notes": ""}}
    return json.loads(read_artifact(ctx.workspace, view_run, "view").read_text(encoding="utf-8"))


def layer(state, layer_id):
    for item in state["layers"]:
        if item["id"] == layer_id:
            return item
    raise ValueError("Unknown map layer")


def extent(items):
    values = [item["bounds"] for item in items if item["bounds"] is not None]
    return ([min(b[0] for b in values), min(b[1] for b in values),
             max(b[2] for b in values), max(b[3] for b in values)] if values else None)


def summary(state):
    visible = [item for item in state["layers"] if item["visible"]]
    return {"layers": [{key: item[key] for key in ("id", "title", "visible", "bounds", "dataset_revision", "layer", "color")}
                       for item in state["layers"]], "extent": state["extent"] or extent(visible),
            "crs": state["crs"], "mode": state["mode"], "text": state["text"]}


def render_layout(state, data):
    canvas = ""
    bounds = data["bounds"]
    if bounds:
        x1, y1, x2, y2 = bounds
        scale = max(x2 - x1, y2 - y1, 1e-8)
        margin = scale * 0.07
        box = f"{x1-margin} {-y2-margin} {x2-x1+2*margin} {y2-y1+2*margin}"
        shapes = "".join(GeometryCollection([shape(item["geometry"])]).svg(
            scale_factor=scale / 180, color=item["color"],
        ) for item in data["features"])
        canvas = f'<svg viewBox="{box}" role="img" aria-label="地图"><g transform="scale(1,-1)">{shapes}</g></svg>'
    labels = "".join(f'<li>{escape(item["title"])}</li>' for item in state["layers"] if item["visible"])
    text = state["text"]
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>规智版面</title>"
            "<style>body{font:16px system-ui;max-width:900px;margin:40px auto;color:#173a37}"
            "svg{width:100%;height:520px;background:#eef6f1;border-radius:12px}"
            "footer{white-space:pre-wrap}</style>"
            f"<h1>{escape(text['title'])}</h1><p>{escape(text['subtitle'])}</p>{canvas}"
            f"<ul>{labels}</ul><footer>{escape(text['notes'])}</footer></html>")


def publish(ctx, state):
    meta = summary(state)
    ctx.json("view.json", state, "view")
    data = {"crs": state["crs"], "bounds": meta["extent"], "mode": state["mode"],
            "features": [{**feature, "role": "layer", "layer": item["id"], "color": item["color"]}
                         for item in reversed(state["layers"]) if item["visible"]
                         for feature in item["features"]]}
    if data["bounds"]:
        ctx.json("map.json", data, "map")
    if state["mode"] == "layout":
        file = ctx.path("layout.html")
        file.write_text(render_layout(state, data), encoding="utf-8")
        ctx.record("report", file)
    return {"presentation": meta, "statistics": {"layer_count": len(state["layers"])}}


def add_layer(ctx, view_run: str | None = None, title: Label | None = None):
    state = view(ctx, view_run)
    frame = ctx.frames[0]
    if state["crs_wkt"] is not None and frame.crs != CRS.from_wkt(state["crs_wkt"]):
        raise ValueError("Layer CRS differs from the view CRS")
    if len(state["layers"]) >= 32:
        raise ValueError("A view accepts at most 32 layers")
    geometries = [(int(fid), geom) for fid, geom in frame.geometry.items()
                  if geom is not None and not geom.is_empty]
    bounds = extent([{"bounds": geom.bounds} for _, geom in geometries])
    title = title or ctx.request.inputs[0].layer
    state["layers"].insert(0, {"id": uuid4().hex, "title": title, "visible": True, "bounds": bounds,
        "dataset_revision": ctx.selection_hashes[0], "layer": ctx.request.inputs[0].layer,
        "color": PALETTE[len(state["layers"]) % len(PALETTE)],
        "features": [{"fid": fid, "geometry": mapping(geom)} for fid, geom in geometries]})
    state["crs"], state["crs_wkt"] = frame.crs.to_string(), frame.crs.to_wkt()
    state["extent"] = None
    return publish(ctx, state)


def set_visibility(ctx, view_run: str, layer_id: str, visible: bool):
    state = view(ctx, view_run)
    layer(state, layer_id)["visible"] = visible
    return publish(ctx, state)


def remove_layer(ctx, view_run: str, layer_id: str):
    state = view(ctx, view_run)
    state["layers"].remove(layer(state, layer_id))
    if not state["layers"]:
        state.update(crs=None, crs_wkt=None, extent=None)
    return publish(ctx, state)


def clear_layers(ctx, view_run: str):
    state = view(ctx, view_run)
    state.update(layers=[], crs=None, crs_wkt=None, extent=None)
    return publish(ctx, state)


def reorder_layers(ctx, view_run: str, layer_id: str, position: Literal["top", "bottom", "before", "after"],
                   reference: str | None = None):
    state = view(ctx, view_run)
    item = layer(state, layer_id)
    if (position in {"before", "after"}) != bool(reference) or reference == layer_id:
        raise ValueError("Before/after requires a different reference layer")
    other = layer(state, reference) if reference else None
    state["layers"].remove(item)
    index = state["layers"].index(other) + (position == "after") if other else (
        0 if position == "top" else len(state["layers"])
    )
    state["layers"].insert(index, item)
    return publish(ctx, state)


def zoom_to_dataset(ctx, view_run: str, layer_id: str):
    state = view(ctx, view_run)
    bounds = layer(state, layer_id)["bounds"]
    if bounds is None:
        raise ValueError("Layer has no geometry to fit")
    state["extent"] = bounds
    return publish(ctx, state)


def zoom_to_selection(ctx, view_run: str, layer_id: str, selection: Selection):
    state = view(ctx, view_run)
    item = layer(state, layer_id)
    if item["layer"] != selection.layer or item["dataset_revision"] != selection.revision:
        raise ValueError("Selection does not belong to this layer revision")
    if not selection.fids or len(set(selection.fids)) != len(selection.fids):
        raise ValueError("Selection requires distinct feature IDs")
    bounds = {feature["fid"]: shape(feature["geometry"]).bounds for feature in item["features"]}
    if not set(selection.fids) <= bounds.keys():
        raise ValueError("Selected features have no visible geometry")
    state["extent"] = extent([{"bounds": bounds[fid]} for fid in selection.fids])
    return publish(ctx, state)


def list_layers(ctx, view_run: str):
    return {"layers": summary(view(ctx, view_run))["layers"]}


def get_extent(ctx, view_run: str):
    return {"extent": summary(view(ctx, view_run))["extent"]}


def get_view_crs(ctx, view_run: str):
    state = view(ctx, view_run)
    return {"view_crs": state["crs"], "view_crs_wkt": state["crs_wkt"]}


def list_layout_elements(ctx, view_run: str, element_type: Literal["text", "map", "legend"] | None = None):
    state = view(ctx, view_run)
    items = [{"id": name, "type": "text", "text": value} for name, value in state["text"].items()]
    items += [{"id": "map", "type": "map"}, {"id": "legend", "type": "legend"}]
    return {"elements": [item for item in items if element_type is None or item["type"] == element_type]}


def set_layout_text(ctx, view_run: str, element: Literal["title", "subtitle", "notes"], text: str):
    if len(text) > 10000:
        raise ValueError("Layout text exceeds 10000 characters")
    state = view(ctx, view_run)
    state["text"][element] = text
    return publish(ctx, state)


def set_view_mode(ctx, view_run: str, mode: Literal["map", "layout"]):
    state = view(ctx, view_run)
    state["mode"] = mode
    return publish(ctx, state)


def snapshot(ctx, view_run: str):
    state = view(ctx, view_run)
    return {"presentation": summary(state)}


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
