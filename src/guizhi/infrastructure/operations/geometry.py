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
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, PositiveInt, StrictInt
import shapely
from shapely.geometry import LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon, box

from guizhi.infrastructure.operations.common import Selection, Unit, distance_in_crs, selection_fids
from guizhi.infrastructure.operations.runner import data_revision

XY = tuple[FiniteFloat, FiniteFloat]
Line = Annotated[list[XY], Field(min_length=2, max_length=100000)]
Ring = Annotated[list[XY], Field(min_length=3, max_length=100000)]
Sides = Annotated[int, Field(ge=3, le=360)]
Radius = Annotated[FiniteFloat, Field(gt=0)]
GeometryType = Literal["point", "polyline", "polygon"]
GEOMETRY_TYPES = {"point": "Point", "polyline": "LineString", "polygon": "Polygon"}
MULTIPART = {"Point": MultiPoint, "LineString": MultiLineString, "Polygon": MultiPolygon}


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


def create_dataset(ctx, geometry_type: GeometryType, epsg: PositiveInt | None = None,
                   name: str | None = None):
    crs = crs_for(ctx, epsg)
    kind = GEOMETRY_TYPES[geometry_type]
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
    out = ctx.path("result.gpkg")
    snapshot = ctx.directory / ctx.evidence[0]["path"]
    layer = ctx.request.inputs[0].layer
    if target_type.startswith("Multi"):
        from guizhi.infrastructure.gdal_translate import vector_translate

        vector_translate(snapshot, out, "-f", "GPKG", "-preserve_fid", "-nlt", "GEOMETRY",
                         "-lco", "FID=" + info["fid_column"], "-lco", "GEOMETRY_NAME=" + info["geometry_name"],
                         layer, geometry_family=family)
    else:
        shutil.copyfile(snapshot, out)
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


def part_family(frame, info):
    family = info["geometry_type"].removeprefix("Multi")
    if family not in MULTIPART or not frame.geom_type.isin({family, "Multi" + family}).all():
        raise ValueError("请选择同类型的二维点、线或面要素")
    geometry = frame.geometry
    if geometry.has_z.any() or geometry.has_m.any():
        raise ValueError("多部件编辑适用于二维图层")
    if geometry.isna().any() or geometry.is_empty.any() or not geometry.is_valid.all():
        raise ValueError("图层包含空几何或无效几何，请先修复")
    return family


PartsDestination = Literal["current", "new"]


def publish_parts(ctx, result, selected, destination, family):
    source = ctx.frames[0]
    if destination == "current":
        result = pd.concat([source.drop(index=selected.index), result])
    kinds = result.geom_type.dropna().unique()
    ctx.publish(result, preserve_fids=True, geometry_type=kinds[0] if len(kinds) == 1 else family)
    receipt = {"statistics": {"feature_count": len(result), "source_feature_count": len(selected)}}
    if destination == "current":
        receipt["changeset"] = {"target_revision": ctx.selection_hashes[0]}
    return receipt


def multipart(ctx, selection: Selection, destination: PartsDestination = "current",
              attribute_fid: StrictInt | None = None):
    ids = selection_fids(ctx, selection)
    if not 2 <= len(ids) <= 500:
        raise ValueError("请选择 2 至 500 个待合并要素")
    selected = ctx.frames[0].loc[selection.fids]
    family = part_family(selected, ctx.info[0])
    if attribute_fid is None:
        attribute_fid = selection.fids[0]
    if attribute_fid not in ids:
        raise ValueError("请选择一个参与合并的要素作为属性来源")
    shape = MULTIPART[family](list(shapely.get_parts(selected.geometry.array)))
    if not shape.is_valid:
        raise ValueError("所选图形无法保留为独立部件。重叠或共边的面可先使用「合并要素」。")
    result = selected.loc[[attribute_fid]].set_geometry([shape])
    receipt = publish_parts(ctx, result, selected, destination, family)
    receipt["statistics"]["part_count"] = len(shape.geoms)
    return receipt


def explode(ctx, selection: Selection, destination: PartsDestination = "current"):
    if not selection_fids(ctx, selection):
        raise ValueError("请先选择要拆分的要素")
    source = ctx.frames[0]
    selected = source.loc[selection.fids]
    family = part_family(selected, ctx.info[0])
    if not selected.geom_type.eq("Multi" + family).any():
        raise ValueError("所选要素均为单部件，无需拆分")
    result = selected.explode(index_parts=False)
    # Keep the first part's FID and allocate new IDs above all existing features.
    additional = result.index.duplicated()
    ids = result.index.to_numpy(copy=True)
    start = int(source.index.max()) + 1
    ids[additional] = range(start, start + int(additional.sum()))
    result.index = ids
    return publish_parts(ctx, result, selected, destination, family)


def check_topology(ctx):
    from contextlib import closing
    import sqlite3

    frame = ctx.frames[0]
    geometry = frame.geometry
    empty = geometry.isna() | geometry.is_empty
    invalid = ~empty & ~geometry.is_valid
    polygons = geometry[~empty & ~invalid & geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
    report = ctx.path("topology.sqlite")
    with closing(sqlite3.connect(report)) as db, db:
        db.execute("CREATE TABLE issues (kind TEXT, fid INTEGER, peer INTEGER, reason TEXT)")
        db.executemany("INSERT INTO issues VALUES ('empty', ?, NULL, NULL)", ((int(fid),) for fid in geometry[empty].index))
        db.executemany("INSERT INTO issues VALUES ('invalid', ?, NULL, ?)",
                       ((int(fid), shapely.is_valid_reason(shape)) for fid, shape in geometry[invalid].items()))
        shapes = polygons.to_numpy()
        fids = polygons.index.to_numpy()
        index = shapely.STRtree(shapes)
        overlap_pairs = 0
        for i, shape in enumerate(shapes):
            ctx.check()
            if i % 1024 == 0:
                ctx.progress("tool_progress", phase="topology", completed=i, total=len(shapes))
            candidates = index.query(shape, predicate="intersects")
            candidates = candidates[candidates > i]
            hits = candidates[shapely.relate_pattern(shape, shapes[candidates], "2********")]
            db.executemany("INSERT INTO issues VALUES ('overlap', ?, ?, NULL)",
                           ((int(fids[i]), int(fids[j])) for j in hits))
            overlap_pairs += len(hits)
        db.execute("CREATE INDEX issues_kind ON issues(kind)")
    ctx.record("topology", report)
    if len(shapes):
        ctx.progress("tool_progress", phase="topology", completed=len(shapes), total=len(shapes))
    return {"statistics": {"feature_count": len(frame), "empty_geometry_count": int(empty.sum()),
                           "invalid_geometry_count": int(invalid.sum()), "overlap_pair_count": overlap_pairs},
            "topology": {"issue_count": int(empty.sum() + invalid.sum()) + overlap_pairs}}


def repair(ctx, delete_null: bool = False, fids: list[StrictInt] | None = None, preserve_overlap_fid: StrictInt | None = None):
    frame = ctx.frames[0]
    selected = set(fids) if fids is not None else set(frame.index)
    if fids is not None and (not 1 <= len(fids) <= 500 or not selected.issubset(frame.index)):
        raise ValueError("请选择当前图层中有效的待修复要素")
    if preserve_overlap_fid is not None:
        if fids is None or len(selected) != 1 or preserve_overlap_fid in selected or preserve_overlap_fid not in frame.index:
            raise ValueError("请选择一个修复要素和一个保留要素")
        fid, = selected
        shape, peer = frame.geometry.loc[fid], frame.geometry.loc[preserve_overlap_fid]
        if any(item is None or not item.is_valid or item.geom_type not in {"Polygon", "MultiPolygon"} or item.has_z
               for item in (shape, peer)):
            raise ValueError("仅支持裁除两个有效二维面要素之间的重叠")
        fixed = shape.difference(peer)
        if fixed.is_empty or not fixed.is_valid or fixed.geom_type not in {"Polygon", "MultiPolygon"} or fixed.area >= shape.area:
            raise ValueError("裁除后要素将消失或没有可裁除的重叠，请手动编辑")
        result = frame.copy()
        result.at[fid, frame.geometry.name] = fixed
        ctx.publish(result, preserve_fids=True)
        return {"statistics": {"feature_count": len(result), "updated": 1},
                "changeset": {"target_revision": ctx.selection_hashes[0]}}
    repaired, changes, kept = [], [], []
    for index, (fid, shape) in enumerate(frame.geometry.items()):
        ctx.check()
        if index % 1024 == 0:
            ctx.progress("tool_progress", phase="geometry", completed=index, total=len(frame))
        if fid in selected and (shape is None or shape.is_empty) and delete_null:
            changes.append({"fid": int(fid), "reason": "null", "removed": True})
            continue
        if fid in selected and shape is not None and not shape.is_valid:
            if shape.has_z:
                raise ValueError("Invalid 3D geometry requires a dedicated repair method")
            fixed = shapely.make_valid(shape)
            family = shape.geom_type.removeprefix("Multi")
            if fixed.geom_type == "GeometryCollection" and family in MULTIPART:
                parts = [part for component in fixed.geoms for part in shapely.get_parts(component)
                         if part.geom_type == family]
                if parts:
                    fixed = parts[0] if len(parts) == 1 else MULTIPART[family](parts)
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
    if not result.loc[result.index.isin(selected)].geometry.dropna().is_valid.all():
        raise ValueError("Repair output remains invalid")
    ctx.publish(result, geometry_type=ctx.info[0]["geometry_type"] if result.empty else None, preserve_fids=True)
    change = {"target_revision": ctx.selection_hashes[0], "features": changes}
    ctx.json("changeset.json", change, "changeset")
    return {"changeset": change}
