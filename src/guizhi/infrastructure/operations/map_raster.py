"""Prepare an offline display image of a sealed raster."""
import numpy as np

from guizhi.infrastructure.operations import raster
from pyproj.transformer import TransformerGroup


def image_preview(file, target=None, bands=None, viewport=None, *, metadata_only=False):
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
            image = None if metadata_only else render(src, window, bands)
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


def render(src, window, bands):
    from guizhi.infrastructure.operations.raster_backend import ColorInterp, MemoryFile, Resampling

    scale = min(1, 960 / max(window.width, window.height))
    width, height = max(1, round(window.width * scale)), max(1, round(window.height * scale))
    if bands is None:
        bands = default_bands(src)
    if not isinstance(bands, list) or len(bands) not in (1, 3) or any(type(b) is not int or not 1 <= b <= src.count for b in bands):
        raise ValueError("请选择有效的显示波段")
    data = src.read(bands, window=window, out_shape=(len(bands), height, width), masked=True,
                    resampling=Resampling.nearest)
    valid = (~np.ma.getmaskarray(data) & np.isfinite(data.data)).all(axis=0)
    image = np.zeros((4, height, width), dtype=np.uint8)
    image[3] = np.where(valid, 255, 0)
    for idx in range(3):
        band = data.data[idx if len(bands) == 3 else 0]
        if not valid.any():
            continue
        if band.dtype == np.uint8:
            image[idx, valid] = band[valid]
        else:
            low, high = np.percentile(band[valid], [2, 98])
            image[idx, valid] = np.clip((band[valid] - low) * 255 / (high - low), 0, 255) if high > low else 140
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
