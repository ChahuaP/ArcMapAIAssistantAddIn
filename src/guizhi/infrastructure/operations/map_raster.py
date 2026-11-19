"""Prepare an offline display image of a sealed raster."""
import numpy as np
from functools import lru_cache
from pathlib import Path

from guizhi.infrastructure.operations import raster
from pyproj.transformer import TransformerGroup


def image_preview(file, target=None, bands=None, viewport=None, *, metadata_only=False, appearance=None):
    from contextlib import nullcontext
    from math import isfinite
    from guizhi.infrastructure.operations.raster_backend import Resampling, WarpedVRT, rio
    from rasterio.transform import from_origin
    from rasterio.windows import from_bounds, bounds as window_bounds, Window, intersection

    with rio.Env(PROJ_NETWORK="OFF"), rio.open(file) as source:
        options = {}
        if target:
            if source.crs is None:
                raise ValueError("请先定义影像的坐标系")
            destination = raster.CRS.from_user_input(target)
            origin = raster.CRS.from_user_input(source.crs)
            if not origin.equals(destination, ignore_axis_order=True):
                group = TransformerGroup(origin, destination, always_xy=True, allow_ballpark=False)
                if not group.best_available or not group.transformers:
                    raise ValueError("影像缺少可靠的坐标转换或所需格网")
                options = {"crs": destination.to_wkt(),
                           "warp_extras": {"COORDINATE_OPERATION": group.transformers[0].definition}}
        warped = options or source.transform.b or source.transform.d or (source.crs and source.transform.e > 0)
        with (WarpedVRT(source, resampling=Resampling.nearest, **options) if warped else nullcontext(source)) as src:
            transform = src.transform if src.crs or src.transform.e < 0 else from_origin(0, 0, 1, 1)
            whole = Window(0, 0, src.width, src.height)
            window = whole
            if viewport is not None:
                if not isinstance(viewport, list) or len(viewport) != 4 or not all(isfinite(v) for v in viewport):
                    raise ValueError("影像显示范围无效")
                window = intersection(whole, from_bounds(*viewport, transform=transform))
                # Avoid subpixel windows when zooming past the source resolution.
                window = Window(max(0, int(window.col_off)), max(0, int(window.row_off)),
                                min(src.width - int(window.col_off), max(1, round(window.width))),
                                min(src.height - int(window.row_off), max(1, round(window.height))))
            image = None if metadata_only else render(src, window, bands, file, appearance, viewport is not None)
            crs = raster.CRS(src.crs) if src.crs else None
            return image, {"bounds": list(window_bounds(window, transform)), "crs": crs.to_string() if crs else None, "crsName": crs.name if crs else "未定义坐标系",
                           "units": "degrees" if crs and crs.is_geographic else "m" if crs and crs.axis_info[0].unit_conversion_factor == 1 else None,
                           "metresPerUnit": crs.axis_info[0].unit_conversion_factor if crs and crs.is_projected else None,
                           "width": src.width, "height": src.height, "bands": src.count,
                           "displayBands": default_bands(src), "bandNames": list(src.descriptions),
                           "byteImagery": all(dtype == "uint8" for dtype in src.dtypes)}


def default_bands(src):
    names = [item.name for item in src.colorinterp]
    if all(name in names for name in ("red", "green", "blue")):
        return [names.index(name) + 1 for name in ("red", "green", "blue")]
    return [1, 2, 3] if src.count == 3 else [1]


def natural_breaks(values):
    weights, edges = np.histogram(values, bins=64)
    present = weights > 0
    centers = ((edges[:-1] + edges[1:]) / 2)[present]
    weights = weights[present]
    n = len(weights)
    if n <= 1:
        return ()
    centers = (centers - centers[0]) / (centers[-1] - centers[0])
    sums = [np.r_[0, np.cumsum(weight)] for weight in (weights, weights * centers, weights * centers ** 2)]
    groups = min(5, n)
    score = np.full((groups + 1, n + 1), np.inf)
    cuts = np.zeros((groups + 1, n + 1), dtype=int)
    score[0, 0] = 0
    for group in range(1, groups + 1):
        for end in range(group, n + 1):
            start = np.arange(group - 1, end)
            count, total, square = [s[end] - s[start] for s in sums]
            candidates = score[group - 1, start] + square - total ** 2 / count
            best = int(np.argmin(candidates))
            cuts[group, end], score[group, end] = start[best], candidates[best]
    edges = edges[1:][present]
    result, end = [], n
    for group in range(groups, 1, -1):
        end = cuts[group, end]
        result.append(float(edges[end - 1]))
    return tuple(reversed(result))


@lru_cache(maxsize=32)
def stretch_limits(file, modified, bands, mode):
    from guizhi.infrastructure.operations.raster_backend import rio

    with rio.open(file) as src:
        scale = min(1, 480 / max(src.width, src.height))
        sample = src.read(list(bands), out_shape=(len(bands), max(1, round(src.height * scale)),
                                                  max(1, round(src.width * scale))), masked=True)
        result = []
        for band in sample:
            values = band.compressed()
            values = values[np.isfinite(values)]
            result.append(natural_breaks(values) if mode == "natural" and values.size else
                          tuple(np.percentile(values, [2, 98])) if values.size else ())
        return result


def render(src, window, bands, file, appearance, detailed):
    from guizhi.infrastructure.operations.raster_backend import ColorInterp, MemoryFile, Resampling

    scale = min(1, (1800 if detailed else 960) / max(window.width, window.height))
    width, height = max(1, round(window.width * scale)), max(1, round(window.height * scale))
    if bands is None:
        bands = default_bands(src)
    if not isinstance(bands, list) or len(bands) not in (1, 3) or any(type(b) is not int or not 1 <= b <= src.count for b in bands):
        raise ValueError("请选择有效的显示波段")
    appearance = appearance or {}
    mode = appearance.get("stretch", "percent")
    gamma = appearance.get("gamma", 1)
    if mode not in {"percent", "raw", "natural"} or type(gamma) not in {float, int} or not .1 <= gamma <= 5:
        raise ValueError("请选择有效的拉伸方式和伽马值")
    if mode == "natural" and len(bands) != 1:
        raise ValueError("自然断点分级适用于单波段")
    limits = stretch_limits(str(file), Path(file).stat().st_mtime_ns, tuple(bands), mode) if mode != "raw" else None
    data = src.read(bands, window=window, out_shape=(len(bands), height, width), masked=True,
                    resampling=Resampling.nearest)
    valid = (~np.ma.getmaskarray(data) & np.isfinite(data.data)).all(axis=0)
    image = np.zeros((4, height, width), dtype=np.uint8)
    image[3] = np.where(valid, 255, 0)
    for idx in range(3):
        band = data.data[idx if len(bands) == 3 else 0]
        if not valid.any():
            continue
        values = band[valid].astype(np.float64)
        reference = limits[idx if len(bands) == 3 else 0] if limits is not None else ()
        if mode == "natural":
            normalized = np.searchsorted(reference, values, side="right") / len(reference) if reference else .55
        elif mode == "raw":
            normalized = np.clip(values / 255, 0, 1)
        else:
            low, high = reference if reference else (0, 0)
            normalized = np.clip((values - low) / (high - low), 0, 1) if high > low else np.clip(values / 255, 0, 1)
        image[idx, valid] = np.rint(np.power(normalized, 1 / gamma) * 255)
    with MemoryFile(ext=".png") as memory:
        with memory.open(driver="PNG", width=width, height=height, count=4, dtype="uint8") as output:
            output.colorinterp = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
            output.write(image)
        return memory.read()


def preview(ctx):
    image, info = image_preview(ctx.rasters[0])
    out = ctx.path("preview.png")
    out.write_bytes(image)
    ctx.record("raster_preview", out)
    return {"raster_preview": info}
