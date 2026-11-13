"""Prepare an offline display image of a sealed raster."""
import numpy as np

from guizhi.infrastructure.operations import raster
from rasterio.enums import ColorInterp, Resampling
from rasterio.io import MemoryFile
from rasterio.vrt import WarpedVRT


def preview(ctx):
    file = ctx.rasters[0]
    with raster.rio.open(file) as source, WarpedVRT(source, resampling=Resampling.nearest) as src:
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
            out = ctx.path("preview.png")
            out.write_bytes(memory.read())
        crs = raster.CRS(src.crs)
        ctx.record("raster_preview", out)
        return {"raster_preview": {"bounds": list(src.bounds), "crs": crs.to_string(), "crsName": crs.name,
                 "units": "degrees" if crs.is_geographic else "m" if crs.axis_info[0].unit_conversion_factor == 1 else None,
                 "width": src.width, "height": src.height, "bands": src.count}}
