"""Process GeoTIFF and ERDAS IMAGINE rasters without preserving invalid samples."""
from contextlib import ExitStack
from datetime import date
from math import fsum, isclose
from pathlib import Path
from typing import Literal
from uuid import uuid4

import numpy as np
from pyproj import CRS

from guizhi.infrastructure.optional_components import require

require("raster")

import rasterio as rio
from rasterio.shutil import copy as copy_dataset
from rasterio.enums import Resampling
from rasterio.features import geometry_mask, geometry_window
from rasterio.merge import merge
from rasterio.warp import calculate_default_transform, reproject as warp
from rasterio.windows import Window, transform as window_transform


def source_files(file):
    file = Path(file).absolute()
    with rio.open(file) as src:
        driver = "HFA" if file.suffix.lower() == ".img" else "GTiff"
        if src.driver != driver or not src.count or src.subdatasets:
            raise ValueError("请选择波段可读的 GeoTIFF 或 ERDAS IMG")
        if any(np.dtype(dtype).kind not in "iuf" or
               (np.dtype(dtype).kind in "iu" and np.dtype(dtype).itemsize > 4) for dtype in src.dtypes):
            raise ValueError("Raster samples must be real numbers representable exactly as float64")
        if any(not np.isfinite(value) for value in src.transform):
            raise ValueError("Invalid raster transform")
        names = {file.name.casefold() + ext for ext in (".aux.xml", ".msk", ".msk.aux.xml", ".ovr")}
        if driver == "HFA":
            names.update(file.stem.casefold() + ext for ext in (".ige", ".rrd", ".aux"))
        parts = sorted({Path(name).absolute() for name in src.files if Path(name).absolute() != file},
                       key=lambda part: part.name.casefold())
        if any(part.parent != file.parent or part.name.casefold() not in names or
               not part.is_file() or part.is_symlink() for part in parts):
            raise ValueError("栅格配套文件缺失或位于其他目录，请将影像及其配套文件放在同一目录")
        return [file, *parts]


def check_source(file):
    return source_files(file)


def normalize(source, target, check):
    # Copying the GDAL dataset includes georeferencing, masks, descriptions and metadata.
    with rio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        copy_dataset(source, target, driver="GTiff", strict=True, compress="deflate", BIGTIFF="IF_SAFER")
    with rio.open(source) as before:
        scope = before.tags(ns="GUIZHI")
    if scope:
        with rio.open(target, "r+") as output:
            output.update_tags(ns="GUIZHI", **scope)
    before_info, after_info = metadata(source), metadata(target)
    del before_info["crs"], after_info["crs"]
    if source_files(target) != [target] or before_info != after_info:
        raise ValueError("栅格快照未通过空间参考、波段和缺测值校验")
    with rio.open(source) as before, rio.open(target) as after:
        if before.crs != after.crs or before.descriptions != after.descriptions or \
                any(before.tags(i) != after.tags(i) for i in before.indexes):
            raise ValueError("栅格快照未保留波段描述或波段属性")
        for _, window in after.block_windows(1):
            check()
            if not np.array_equal(before.read(window=window), after.read(window=window), equal_nan=True) or \
                    not np.array_equal(before.read_masks(window=window), after.read_masks(window=window)):
                raise ValueError("栅格快照像元或掩膜校验失败")


def metadata(file):
    check_source(file)
    with rio.open(file) as src:
        return {"crs": src.crs.to_wkt() if src.crs else None, "width": src.width, "height": src.height, "bands": src.count,
                "resolution": list(src.res), "transform": list(src.transform)[:6], "bounds": list(src.bounds),
                "dtypes": list(src.dtypes), "nodata": [
                    val if val is None or np.isfinite(val) else "NaN" for val in src.nodatavals]}


def samples(src, window):
    data = src.read(window=window, masked=True, out_dtype="float64").filled(np.nan)
    data[~np.isfinite(data)] = np.nan
    return data


def statistics(ctx, observed_on: date | None = None):
    file = ctx.rasters[0]
    info = metadata(file)
    bands = []
    with rio.open(file) as src:
        for index in src.indexes:
            count, minimum, maximum, sums = 0, None, None, []
            for _, window in src.block_windows(index):
                ctx.check()
                block = src.read(index, window=window, masked=True).compressed().astype("float64")
                block = block[np.isfinite(block)]
                if not block.size:
                    continue
                count += int(block.size)
                low, high = float(block.min()), float(block.max())
                minimum = low if minimum is None else min(low, minimum)
                maximum = high if maximum is None else max(high, maximum)
                # Divide before summation to avoid overflow of otherwise representable means.
                sums.append(float(np.sum(block / (src.width * src.height))))
            bands.append({"band": index, "valid_count": count, "nodata_count": src.width * src.height - count,
                          "min": minimum, "max": maximum,
                          "mean": fsum(sums) / (count / (src.width * src.height)) if count else None})
    info.update(observed_on=observed_on.isoformat() if observed_on else None, band_statistics=bands)
    ctx.json("raster.json", info, "raster")
    return {"raster": info, "value": info["bands"], "statistics": {"band_count": info["bands"],
            "valid_samples": sum(item["valid_count"] for item in bands),
            "nodata_samples": sum(item["nodata_count"] for item in bands)}}


def options(src, width, height, transform, crs):
    return dict(driver="GTiff", width=width, height=height, count=src.count, transform=transform, crs=crs,
                dtype="float64", nodata=np.nan, compress="deflate", predictor=3,
                tiled=True, blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER")


def write_windows(ctx, path, src, extent, shapes=None, all_touched=False):
    profile = options(src, int(extent.width), int(extent.height), window_transform(extent, src.transform), src.crs)
    with rio.open(path, "w", **profile) as dst:
        for _, window in dst.block_windows(1):
            ctx.check()
            source_window = Window(window.col_off + extent.col_off, window.row_off + extent.row_off,
                                   window.width, window.height)
            block = samples(src, source_window)
            if shapes is not None:
                valid = geometry_mask(shapes, out_shape=block.shape[1:], transform=dst.window_transform(window),
                                      all_touched=all_touched, invert=True)
                block[:, ~valid] = np.nan
            dst.write(block, window=window)
    with rio.open(path) as result:
        if result.transform != profile["transform"] or result.crs != src.crs or result.shape != (
                profile["height"], profile["width"]):
            raise ValueError("Written raster has the wrong grid")


def deliver(ctx, path, operation):
    ctx.check()
    info = metadata(path)
    with rio.open(path) as src:
        for _, window in src.block_windows(1):
            ctx.check()
            block = src.read(window=window)
            if np.isinf(block).any() or not np.array_equal(np.isnan(block), src.read_masks(window=window) == 0):
                raise ValueError("Raster export failed NoData verification")
    info["operation"] = operation
    ctx.json("raster.json", info, "raster")
    ref = ctx.record("export", path)
    return {"raster": info, "data_revision": ref["sha256"],
            "statistics": {"band_count": info["bands"], "width": info["width"], "height": info["height"]}}


def clip(ctx, all_touched: bool = False):
    if len(ctx.rasters) != 1 or len(ctx.frames) != 1:
        raise ValueError("Raster clipping requires one GeoTIFF and one polygon layer")
    frame = ctx.frames[0]
    if frame.empty or frame.crs is None or not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all() or (
            ~frame.is_valid | frame.is_empty).any():
        raise ValueError("Clipping requires valid, nonempty polygons with a defined CRS")
    out = ctx.path("result.tif")
    with rio.open(ctx.rasters[0]) as src:
        if frame.crs != CRS.from_user_input(src.crs):
            raise ValueError("Align the clipping polygon to the raster CRS first")
        shapes = list(frame.geometry)
        extent = geometry_window(src, shapes)
        write_windows(ctx, out, src, extent, shapes, all_touched)
    result = deliver(ctx, out, {"type": "clip", "pixel_inclusion": "all_touched" if all_touched else "center"})
    return {**result, "scope_revision": ctx.selection_hashes[0]}


def raster_plan_pipeline(workspace, reference, source, extent):
    from itertools import groupby

    from guizhi.domain.crs import TransformPlan
    from guizhi.infrastructure.coordinates import converter, pipeline_tokens
    from guizhi.infrastructure.crs_catalog import get_plan
    from guizhi.infrastructure.crs_options import area_of_interest, geographic_bounds, within_area

    plan = TransformPlan.model_validate(get_plan(workspace, reference)["plan"])
    if plan.dimension != 2:
        raise ValueError("栅格投影转换需要二维方案")
    expected, target, _ = converter(plan, workspace.root)
    if not source.equals(expected, ignore_axis_order=True):
        raise ValueError("源图层坐标系与转换方案不一致")
    area = extent if plan.area_kind == "source" else geographic_bounds(area_of_interest(source, extent))
    if not within_area(area, plan.area):
        raise ValueError("栅格范围超出转换方案的适用范围")

    tokens = pipeline_tokens(plan, workspace.root)
    groups = [list(group) for separator, group in groupby(tokens, lambda token: token == "+step") if not separator]
    header, *steps = groups
    if plan.direction == "inverse":
        steps.reverse()
        for step in steps:
            if "+inv" in step:
                step.remove("+inv")
            else:
                step.insert(0, "+inv")
            for index, token in enumerate(step):
                if token in {"+omit_fwd", "+omit_inv"}:
                    step[index] = "+omit_inv" if token == "+omit_fwd" else "+omit_fwd"
    # Saved plans use GIS x/y. GDAL's explicit operation receives native CRS axis order.
    xy = ["+proj=axisswap", "+order=2,1"]
    if source.axis_info[0].direction == "north":
        steps.insert(0, xy)
    if target.axis_info[0].direction == "north":
        steps.append(xy)
    return target, " ".join(header + [token for step in steps for token in ["+step", *step]])


def reproject_raster(ctx, target_crs, resampling, resolution, plan):
    from guizhi.infrastructure.crs_options import describe_crs, projection, resolve_definition

    out = ctx.path("result.tif")
    source = ctx.rasters[0]
    with rio.open(source) as src:
        if src.crs is None:
            raise ValueError("请先为源影像定义实际坐标系")
        source_crs = CRS.from_user_input(src.crs)
        if len(source_crs.axis_info) != 2 or not (source_crs.is_geographic or source_crs.is_projected):
            raise ValueError("源影像必须使用二维地理或投影坐标系")
        if plan:
            target, pipeline = raster_plan_pipeline(ctx.workspace, plan, source_crs, src.bounds)
        else:
            target = resolve_definition(ctx.workspace, target_crs)
            if source_crs.equals(target, ignore_axis_order=True):
                raise ValueError("目标与源影像的坐标系相同，请选择不同的目标坐标系")
            pipeline = projection(source_crs, target, src.bounds, always_xy=False).definition
        with rio.Env(PROJ_NETWORK="OFF"):
            grid, width, height = calculate_default_transform(
                src.crs, target, src.width, src.height, *src.bounds, resolution=resolution,
                COORDINATE_OPERATION=pipeline)
            normalized = ctx.directory / f"normalized-{uuid4().hex}.tif"
            try:
                write_windows(ctx, normalized, src, Window(0, 0, src.width, src.height))
                with rio.open(normalized) as clean, rio.open(out, "w", **options(src, width, height, grid, target)) as dst:
                    for index in src.indexes:
                        ctx.check()
                        warp(rio.band(clean, index), rio.band(dst, index), src_nodata=np.nan, dst_nodata=np.nan,
                             resampling=Resampling[resampling], warp_mem_limit=64, COORDINATE_OPERATION=pipeline)
            finally:
                normalized.unlink(missing_ok=True)
    result = deliver(ctx, out, {"type": "reproject", "source_crs": source_crs.to_wkt(),
                                "resampling": resampling, "coordinate_operation": pipeline, "plan": plan})
    return {**result, "statistics": {**result["statistics"],
            "projection": {"source": describe_crs(source_crs), "target": describe_crs(target)}}}


def reproject(ctx, target_crs: str | None = None, resampling: Literal["nearest", "bilinear", "cubic"] = "nearest",
              resolution: tuple[float, float] | None = None, plan: str | None = None):
    if bool(target_crs) == bool(plan):
        raise ValueError("请选择目标坐标系或已验证的转换方案")
    if resolution is not None and any(not np.isfinite(value) or value <= 0 for value in resolution):
        raise ValueError("Target resolution must contain two positive finite numbers")
    return reproject_raster(ctx, target_crs, resampling, resolution, plan)


def assign(ctx, target_crs: str):
    from guizhi.infrastructure.crs_options import resolve_definition

    source = ctx.rasters[0]
    target = resolve_definition(ctx.workspace, target_crs)
    out = ctx.path("result.tif")
    with rio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        copy_dataset(source, out, driver="GTiff", strict=True, compress="deflate", BIGTIFF="IF_SAFER")
        with rio.open(out, "r+") as dst:
            dst.crs = target.to_wkt()
    return {"raster": metadata(out), "data_revision": ctx.record("export", out)["sha256"]}


def mosaic(ctx, overlap: Literal["first", "last"] = "first"):
    out = ctx.path("result.tif")
    with ExitStack() as stack:
        sources = [stack.enter_context(rio.open(path)) for path in ctx.rasters]
        first = sources[0]
        grid = first.transform
        if grid.b or grid.d or grid.a <= 0 or grid.e >= 0:
            raise ValueError("Mosaic requires a north-up, unrotated grid")
        normalized = []
        for index, src in enumerate(sources):
            x, y = (~grid) * (src.transform.c, src.transform.f)
            if (src.crs != first.crs or src.count != first.count or src.res != first.res or
                src.transform.b or src.transform.d or src.transform.a <= 0 or src.transform.e >= 0 or
                not isclose(x, round(x), abs_tol=1e-7, rel_tol=0) or not isclose(y, round(y), abs_tol=1e-7, rel_tol=0)):
                raise ValueError("Mosaic sources must share the CRS, band count, resolution and pixel alignment")
            path = ctx.directory / f"mosaic-{index}-{uuid4().hex}.tif"
            stack.callback(path.unlink, missing_ok=True)
            write_windows(ctx, path, src, Window(0, 0, src.width, src.height))
            normalized.append(path)
        merge(normalized, method=overlap, dtype="float64", nodata=np.nan, mem_limit=64, dst_path=out,
              dst_kwds=dict(compress="deflate", predictor=3, tiled=True, blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER"))
    return deliver(ctx, out, {"type": "mosaic", "overlap": overlap, "source_count": len(ctx.rasters)})
