"""Use the GDAL runtime already bundled by Pyogrio."""
from ctypes import CDLL, c_char_p, c_int, c_uint, c_void_p, POINTER
from pathlib import Path

import pyogrio


def gdal_library(signatures):
    library = Path(pyogrio.__file__).parent.parent / "pyogrio.libs"
    binaries = list(library.glob("*gdal*.dll")) or list(library.glob("libgdal*.so*"))
    if len(binaries) != 1:
        raise RuntimeError("无法定位 GDAL 矢量转换运行库")
    gdal = CDLL(str(binaries[0]))
    for name, (returns, *types) in signatures.items():
        function = getattr(gdal, name)
        function.restype, function.argtypes = returns, types
    return gdal


def vector_translate(source, target, *arguments, geometry_family=None):
    gdal = gdal_library({
        "GDALOpenEx": (c_void_p, c_char_p, c_uint, c_void_p, c_void_p, c_void_p),
        "GDALClose": (c_int, c_void_p),
        "GDALVectorTranslateOptionsNew": (c_void_p, POINTER(c_char_p), c_void_p),
        "GDALVectorTranslateOptionsFree": (None, c_void_p),
        "GDALVectorTranslate": (c_void_p, c_char_p, c_void_p, c_int, POINTER(c_void_p), c_void_p, POINTER(c_int)),
        "GDALDatasetGetLayer": (c_void_p, c_void_p, c_int),
        "GDALSetMetadataItem": (c_int, c_void_p, c_char_p, c_char_p, c_char_p),
        "CPLGetLastErrorMsg": (c_char_p,),
    })
    args = (c_char_p * (len(arguments) + 1))(*(value.encode("utf-8") for value in arguments))
    options = gdal.GDALVectorTranslateOptionsNew(args, None)
    if not options:
        raise ValueError(gdal.CPLGetLastErrorMsg().decode("utf-8", "replace"))
    incoming = None
    try:
        incoming = gdal.GDALOpenEx(str(source).encode("utf-8"), 4, None, None, None)
        if not incoming:
            raise ValueError(gdal.CPLGetLastErrorMsg().decode("utf-8", "replace"))
        outgoing = gdal.GDALVectorTranslate(str(target).encode("utf-8"), None, 1,
                                           (c_void_p * 1)(incoming), options, None)
        if not outgoing:
            raise ValueError(gdal.CPLGetLastErrorMsg().decode("utf-8", "replace"))
        try:
            if geometry_family:
                layer = gdal.GDALDatasetGetLayer(outgoing, 0)
                if not layer or gdal.GDALSetMetadataItem(layer, b"GEOMETRY_FAMILY", geometry_family.encode("ascii"), None):
                    raise ValueError("无法记录图层几何类型")
        finally:
            closed = gdal.GDALClose(outgoing)
        if closed:
            raise ValueError(gdal.CPLGetLastErrorMsg().decode("utf-8", "replace"))
    finally:
        if incoming:
            gdal.GDALClose(incoming)
        gdal.GDALVectorTranslateOptionsFree(options)
