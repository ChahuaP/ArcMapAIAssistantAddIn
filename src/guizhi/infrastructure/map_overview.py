"""Generalize a complete vector layer to its occupied cells at screen scale.

GDAL burns every geometry touched into a byte mask. Polygonizing the occupied
pixels retains disjoint areas and holes, without sending individual feature IDs.
"""
from contextlib import ExitStack
from ctypes import POINTER, byref, c_char_p, c_int, c_uint, c_void_p
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import sqlite3
from contextlib import closing

import pyogrio
import shapely

from guizhi.infrastructure.gdal_translate import gdal_library


@lru_cache(maxsize=1)
def overview_gdal():
    return gdal_library({
        "GDALOpenEx": (c_void_p, c_char_p, c_uint, c_void_p, c_void_p, c_void_p),
        "GDALClose": (c_int, c_void_p),
        "GDALRasterizeOptionsNew": (c_void_p, POINTER(c_char_p), c_void_p),
        "GDALRasterizeOptionsFree": (None, c_void_p),
        "GDALRasterize": (c_void_p, c_char_p, c_void_p, c_void_p, c_void_p, POINTER(c_int)),
        "GDALGetRasterBand": (c_void_p, c_void_p, c_int),
        "GDALGetDriverByName": (c_void_p, c_char_p),
        "GDALCreate": (c_void_p, c_void_p, c_char_p, c_int, c_int, c_int, c_int, c_void_p),
        "GDALDatasetCreateLayer": (c_void_p, c_void_p, c_char_p, c_void_p, c_int, c_void_p),
        "GDALPolygonize": (c_int, c_void_p, c_void_p, c_void_p, c_int, c_void_p, c_void_p, c_void_p),
        "CPLGetLastErrorMsg": (c_char_p,),
    })


def footprint(file, layer, extent, where=None):
    gd = overview_gdal()
    xmin, ymin, xmax, ymax = extent
    cell = max(xmax - xmin, ymax - ymin) / 480 or 1.0
    # A half-cell apron includes features lying exactly on the outer grid edge.
    bounds = (xmin - cell/2, ymin - cell/2, xmax + cell/2, ymax + cell/2)
    args = ["-of", "MEM", "-ot", "Byte", "-at", "-burn", "1", "-te", *map(str, bounds),
            "-tr", str(cell), str(cell), "-l", layer]
    if where:
        args.extend(["-where", where])
    argv = (c_char_p * (len(args) + 1))(*(value.encode("utf8") for value in args))

    def check(handle):
        if not handle:
            raise ValueError("图层覆盖范围生成失败：" + gd.CPLGetLastErrorMsg().decode("utf8", "replace"))
        return handle

    with TemporaryDirectory() as temp:
        output = Path(temp) / "coverage.geojson"
        with ExitStack() as resources:
            src = check(gd.GDALOpenEx(str(file).encode("utf8"), 4, None, None, None))
            resources.callback(gd.GDALClose, src)
            opts = check(gd.GDALRasterizeOptionsNew(argv, None))
            resources.callback(gd.GDALRasterizeOptionsFree, opts)
            mask = check(gd.GDALRasterize(b"", None, src, opts, byref(c_int())))
            resources.callback(gd.GDALClose, mask)
            band = check(gd.GDALGetRasterBand(mask, 1))
            driver = check(gd.GDALGetDriverByName(b"GeoJSON"))
            polygons = check(gd.GDALCreate(driver, str(output).encode("utf8"), 0, 0, 0, 0, None))
            resources.callback(gd.GDALClose, polygons)
            out_layer = check(gd.GDALDatasetCreateLayer(polygons, b"coverage", None, 3, None))
            if gd.GDALPolygonize(band, band, out_layer, -1, None, None, None):
                raise ValueError("无法提取图层覆盖边界：" + gd.CPLGetLastErrorMsg().decode("utf8", "replace"))
        return tuple(shapely.from_geojson(json.dumps(f["geometry"])) for f in json.loads(
            output.read_text(encoding="utf8"))["features"])


@lru_cache(maxsize=24)
def coverage(file, name, revision, full_bounds, view, cache, where=None):
    """Persist whole-layer footprints and retain recent viewport footprints in memory."""
    if view is not None or where:
        return footprint(file, name, view or full_bounds, where)
    token = sha256(json.dumps([name, revision, full_bounds]).encode()).hexdigest()
    directory = cache / "coverage-v1"
    directory.mkdir(exist_ok=True, parents=True)
    path = directory / (token + ".wkb")
    if path.is_file():
        return tuple(shapely.from_wkb(path.read_bytes()).geoms)
    shapes = footprint(file, name, full_bounds)
    staging = directory / (uuid4().hex + ".pending")
    try:
        staging.write_bytes(shapely.to_wkb(shapely.GeometryCollection(shapes)))
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)
    return shapes


def many_in_view(file, layer, count, extent, limit):
    if count <= limit:
        return False
    if extent is None:
        return True
    from guizhi.infrastructure.operations.table import identifier
    with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as db:
        geom = db.execute("SELECT column_name FROM gpkg_geometry_columns WHERE table_name=?", (layer,)).fetchone()[0]
        rtree = f"rtree_{layer}_{geom}"
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (rtree,)).fetchone():
            # The ordinary bounded GDAL read below still detects dense, non-indexed layers.
            return False
        xmin, ymin, xmax, ymax = extent
        return db.execute(f"SELECT 1 FROM {identifier(rtree)} WHERE maxx>=? AND minx<=? AND maxy>=? AND miny<=? "
                          "LIMIT 1 OFFSET ?", (xmin, xmax, ymin, ymax, limit)).fetchone() is not None


def display_copy(workspace, file, layer, info, entry, target):
    """Index custom coordinate-plan output once, retaining original feature IDs."""
    from guizhi.infrastructure.coordinates import apply_plan
    token = sha256(json.dumps([info["revision"], layer, entry], sort_keys=True).encode()).hexdigest()
    directory = workspace.root / "cache" / "display-plans-v1"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (token + ".gpkg")
    if path.is_file():
        return path
    with TemporaryDirectory(dir=directory) as temp:
        staged = Path(temp) / "display.gpkg"
        batch = 8000
        for offset in range(0, max(1, info["count"]), batch):
            frame = pyogrio.read_dataframe(file, layer=layer, skip_features=offset, max_features=batch, fid_as_index=True)
            frame = apply_plan(frame, entry, workspace.root)
            if not frame.crs.equals(target, ignore_axis_order=True):
                raise ValueError("转换方案的目标坐标系与地图不一致")
            frame.index.name = info["fidField"]
            pyogrio.write_dataframe(frame.reset_index(), staged, layer=layer, append=offset > 0,
                                    geometry_type=info["geometry"], layer_options={"FID": info["fidField"]})
        staged.replace(path)
    return path
