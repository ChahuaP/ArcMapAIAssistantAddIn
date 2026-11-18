"""Load the optional raster runtime on first use."""
from guizhi.infrastructure.optional_components import require

require("raster")

import rasterio as rio
from rasterio.enums import ColorInterp, Resampling
from rasterio.features import geometry_mask, geometry_window, shapes
from rasterio.io import MemoryFile
from rasterio.merge import merge
from rasterio.shutil import copy as copy_dataset
from rasterio.vrt import WarpedVRT
from rasterio.warp import calculate_default_transform, reproject as warp
from rasterio.windows import Window, transform as window_transform
