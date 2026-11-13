"""Prepare an offline display image of a sealed raster."""
import numpy as np

from guizhi.infrastructure.operations import raster
from rasterio.enums import ColorInterp, Resampling
from rasterio.io import MemoryFile
from rasterio.vrt import WarpedVRT
from pyproj.transformer import TransformerGroup


def image_preview(file, target=None):
    with raster.rio.open(file) as source:
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
        with raster.rio.Env(PROJ_NETWORK="OFF"), WarpedVRT(source, resampling=Resampling.nearest, **options) as src:
            image = render(src)
            crs = raster.CRS(src.crs) if src.crs else None
            return image, {"bounds": list(src.bounds), "crs": crs.to_string() if crs else None, "crsName": crs.name if crs else "未定义坐标系",
                           "units": "degrees" if crs and crs.is_geographic else "m" if crs and crs.axis_info[0].unit_conversion_factor == 1 else None,
                           "metresPerUnit": crs.axis_info[0].unit_conversion_factor if crs and crs.is_projected else None,
                           "width": src.width, "height": src.height, "bands": src.count}


def render(src):
    scale = min(1, 960 / max(src.width, src.height))
    width, height = max(1, round(src.width * scale)), max(1, round(src.height * scale))
    bands = [1, 2, 3] if src.count >= 3 else [1]
    data = src.read(bands, out_shape=(len(bands), height, width), masked=True,
                    resampling=Resampling.bilinear, out_dtype="float64").filled(np.nan)
    valid = np.isfinite(data).all(axis=0)
    image = np.zeros((4, height, width), dtype=np.uint8)
    image[3] = np.where(valid, 255, 0)
    for idx in range(3):
        band = data[idx if len(bands) == 3 else 0]
        if not valid.any():
            continue
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
