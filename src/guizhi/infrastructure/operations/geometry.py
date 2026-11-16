"""Construct, append, split and repair explicit simple geometries."""
from hashlib import sha256
from math import cos, radians, sin
import shutil
from typing import Annotated, Literal

import geopandas
import pandas as pd
from pandas.testing import assert_frame_equal
import pyogrio
from pyproj import CRS
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, PositiveInt
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon, box

from guizhi.infrastructure.operations.common import Unit, distance_in_crs
from guizhi.infrastructure.operations.runner import data_revision

XY = tuple[FiniteFloat, FiniteFloat]
Line = Annotated[list[XY], Field(min_length=2, max_length=100000)]
Ring = Annotated[list[XY], Field(min_length=3, max_length=100000)]
Sides = Annotated[int, Field(ge=3, le=360)]
Radius = Annotated[FiniteFloat, Field(gt=0)]


class Named(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=255)


class PointFeature(Named):
    coordinates: XY


class LineFeature(Named):
    coordinates: Line


class PolygonFeature(Named):
    coordinates: Ring
    holes: list[Ring] = Field(default_factory=list)


class Radial(Named):
    center: XY
    start_angle_degrees: FiniteFloat = 0


class Regular(Radial):
    radius: Radius
    sides: Sides


class Star(Radial):
    outer_radius: Radius
    inner_radius: Radius
    point_count: Sides


def crs_for(ctx, epsg):
    if bool(ctx.frames) == (epsg is not None):
        raise ValueError("Specify either an EPSG code or one CRS reference layer")
    crs = ctx.frames[0].crs if ctx.frames else CRS.from_epsg(epsg)
    if len(crs.axis_info) != 2 or not (crs.is_projected or crs.is_geographic):
        raise ValueError("Creation requires a 2D projected or geographic CRS")
    return crs


def generated(features, builder, crs):
    if not 1 <= len(features) <= 10000:
        raise ValueError("Supply 1–10000 features")
    shapes = [builder(feature) for feature in features]
    for shape in shapes:
        if shape.is_empty or not shape.is_valid or (shape.geom_type == "LineString" and shape.length <= 0):
            raise ValueError(f"Invalid or degenerate {shape.geom_type}")
        if crs.is_geographic:
            x1, y1, x2, y2 = shape.bounds
            if not (-180 <= x1 <= x2 <= 180 and -90 <= y1 <= y2 <= 90):
                raise ValueError("Geographic coordinates exceed longitude/latitude bounds")
    return geopandas.GeoDataFrame({"name": [feature.name for feature in features]},
                                  geometry=shapes, crs=crs)


def polygon(feature):
    return shapely.orient_polygons(Polygon(feature.coordinates, holes=feature.holes))


def create_dataset(ctx, geometry_type: Literal["point", "polyline", "polygon"], epsg: PositiveInt | None = None,
                   name: str | None = None):
    crs = crs_for(ctx, epsg)
    kind = {"point": "Point", "polyline": "LineString", "polygon": "Polygon"}[geometry_type]
    ctx.publish(geopandas.GeoDataFrame({"name": pd.Series(dtype="str")}, geometry=[], crs=crs),
                geometry_type=kind)
    if name is not None:
        if not 1 <= len(name.strip()) <= 120:
            raise ValueError("请填写 1 至 120 字的图层名称")
        return {"layer_name": name.strip()}


def create_points(ctx, features: list[PointFeature], epsg: PositiveInt | None = None):
    ctx.publish(generated(features, lambda f: Point(f.coordinates), crs_for(ctx, epsg)))


def create_lines(ctx, features: list[LineFeature], epsg: PositiveInt | None = None):
    ctx.publish(generated(features, lambda f: LineString(f.coordinates), crs_for(ctx, epsg)))


def create_polygons(ctx, features: list[PolygonFeature], epsg: PositiveInt | None = None):
    ctx.publish(generated(features, polygon, crs_for(ctx, epsg)))


def create_rectangle(ctx, left: FiniteFloat, top: FiniteFloat, right: FiniteFloat, bottom: FiniteFloat,
                     epsg: PositiveInt | None = None, name: str | None = None):
    if not left < right or not bottom < top:
        raise ValueError("Rectangle requires left < right and bottom < top")
    crs = crs_for(ctx, epsg)
    ctx.publish(generated([Named(name=name)], lambda _: box(left, bottom, right, top), crs))


def radial(feature, crs, unit):
    if isinstance(feature, Regular):
        radii = [distance_in_crs(crs, feature.radius, unit)] * feature.sides
    else:
        if feature.inner_radius >= feature.outer_radius:
            raise ValueError("Inner radius must be smaller than outer radius")
        radii = [distance_in_crs(crs, radius, unit)
                 for radius in (feature.outer_radius, feature.inner_radius)] * feature.point_count
    x, y = feature.center
    vertices = []
    for index, radius in enumerate(radii):
        angle = radians(feature.start_angle_degrees + index * 360 / len(radii))
        vertices.append((x + radius * cos(angle), y + radius * sin(angle)))
    return Polygon(vertices)


def create_regular_polygons(ctx, features: list[Regular], unit: Unit, epsg: PositiveInt | None = None):
    crs = crs_for(ctx, epsg)
    ctx.publish(generated(features, lambda feature: radial(feature, crs, unit), crs))


def create_stars(ctx, features: list[Star], unit: Unit, epsg: PositiveInt | None = None):
    crs = crs_for(ctx, epsg)
    ctx.publish(generated(features, lambda feature: radial(feature, crs, unit), crs))


def append(ctx, features, builder, family):
    base, info = ctx.frames[0], ctx.info[0]
    target_type = info["geometry_type"]
    if target_type not in {family, "Multi" + family}:
        raise ValueError("Target geometry type or dimensions do not match the new features")
    new = generated(features, builder, base.crs)
    if "name" not in base:
        if new["name"].notna().any():
            raise ValueError("Target has no name field")
        new = new.drop(columns="name")
    if target_type.startswith("Multi"):
        wrap = {"Point": MultiPoint, "LineString": MultiLineString, "Polygon": MultiPolygon}[family]
        new.geometry = new.geometry.map(lambda shape: wrap([shape]))
    out = ctx.path("result.gpkg")
    shutil.copyfile(ctx.directory / ctx.evidence[0]["path"], out)
    layer = ctx.request.inputs[0].layer
    pyogrio.write_dataframe(new, out, layer=layer, driver="GPKG", append=True)
    final = pyogrio.read_dataframe(out, layer=layer, fid_as_index=True)
    if len(final) != len(base) + len(new) or final.crs != base.crs:
        raise ValueError("Append did not preserve the target layer")
    if shapely.to_wkb(final.loc[base.index].geometry).tolist() != shapely.to_wkb(base.geometry).tolist():
        raise ValueError("Append changed existing geometries")
    assert_frame_equal(base.drop(columns=base.geometry.name),
                       final.loc[base.index].drop(columns=final.geometry.name),
                       check_dtype=False, check_exact=True)
    appended = final.loc[~final.index.isin(base.index)]
    if shapely.to_wkb(appended.geometry).tolist() != shapely.to_wkb(new.geometry).tolist():
        raise ValueError("Append did not preserve added geometries")
    if "name" in new and new["name"].notna().any():
        if appended.loc[new["name"].notna().to_numpy(), "name"].tolist() != new["name"].dropna().tolist():
            raise ValueError("Append did not preserve added names")
    ctx.record("export", out)
    return {"export_layer": layer, "data_revision": data_revision(out), "statistics": {"feature_count": len(final)},
            "changeset": {"target_revision": ctx.selection_hashes[0], "appended": len(appended),
                          "new_fids": list(map(int, appended.index))}}


def append_points(ctx, features: list[PointFeature]):
    return append(ctx, features, lambda f: Point(f.coordinates), "Point")


def append_lines(ctx, features: list[LineFeature]):
    return append(ctx, features, lambda f: LineString(f.coordinates), "LineString")


def append_polygons(ctx, features: list[PolygonFeature]):
    return append(ctx, features, polygon, "Polygon")


def append_regular_polygons(ctx, features: list[Regular], unit: Unit):
    return append(ctx, features, lambda f: radial(f, ctx.frames[0].crs, unit), "Polygon")


def append_stars(ctx, features: list[Star], unit: Unit):
    return append(ctx, features, lambda f: radial(f, ctx.frames[0].crs, unit), "Polygon")


def explode(ctx):
    frame = ctx.frames[0]
    if "source_fid" in frame:
        raise ValueError("Explode requires an unused source_fid field")
    if frame.geometry.isna().any() or not frame.geometry.is_valid.all():
        raise ValueError("Repair invalid or null geometries before splitting")
    result = frame.assign(source_fid=frame.index).explode(index_parts=False)
    ctx.publish(result, geometry_type=ctx.info[0]["geometry_type"].replace("Multi", "", 1) if result.empty else None)
    return {"changeset": {"target_revision": ctx.selection_hashes[0], "parts": len(result)}}


def repair(ctx, delete_null: bool = False):
    frame = ctx.frames[0]
    repaired, changes, kept = [], [], []
    for fid, shape in frame.geometry.items():
        ctx.check()
        if shape is None and delete_null:
            changes.append({"fid": int(fid), "reason": "null", "removed": True})
            continue
        if shape is not None and not shape.is_valid:
            if shape.has_z:
                raise ValueError("Invalid 3D geometry requires a dedicated repair method")
            fixed = shapely.make_valid(shape)
            if fixed.is_empty or fixed.geom_type.replace("Multi", "", 1) != shape.geom_type.replace("Multi", "", 1):
                raise ValueError("Repair would change geometry family or remove geometry")
            changes.append({"fid": int(fid), "reason": shapely.is_valid_reason(shape),
                            "before_sha256": sha256(shape.wkb).hexdigest(),
                            "after_sha256": sha256(fixed.wkb).hexdigest(),
                            "area_delta_map_units2": fixed.area - shape.area})
            shape = fixed
        kept.append(fid)
        repaired.append(shape)
    result = frame.loc[kept].copy()
    result.geometry = repaired
    if not result.geometry.dropna().is_valid.all():
        raise ValueError("Repair output remains invalid")
    ctx.publish(result, geometry_type=ctx.info[0]["geometry_type"] if result.empty else None)
    change = {"target_revision": ctx.selection_hashes[0], "features": changes}
    ctx.json("changeset.json", change, "changeset")
    return {"changeset": change}
