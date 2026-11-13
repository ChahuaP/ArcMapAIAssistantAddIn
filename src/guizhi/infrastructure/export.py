"""Export a committed vector result as a verified Shapefile package."""
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import warnings
from zipfile import ZIP_DEFLATED, ZipFile

from pandas.testing import assert_frame_equal
import pyogrio
import shapely

from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.workspace import external_root


def dbf_fields(columns, geometry_name):
    names = {}
    used = {geometry_name.casefold()}
    for name in columns:
        base = name if 0 < len(name.encode("utf-8")) <= 10 else (re.sub(r"[^A-Za-z0-9_]", "", name)[:10] or "field")
        value, counter = base, 1
        while value.casefold() in used:
            suffix = str(counter)
            prefix = base.encode("utf-8")[:10 - len(suffix)].decode("utf-8", errors="ignore")
            value, counter = prefix + suffix, counter + 1
        used.add(value.casefold())
        names[name] = value
    return names


def geometries(frame):
    return [shapely.to_wkb(shapely.normalize(shape.geoms[0] if shape.geom_type.startswith("Multi") and
                                            len(shape.geoms) == 1 else shape)) if shape is not None else None
            for shape in frame.geometry]


def shapefile(workspace, run, kind, output):
    if kind not in {"export", "boundaries"}:
        raise ValueError("请选择矢量成果")
    source = read_artifact(workspace, run, kind)
    return package_layer(workspace, source, output)


def package_layer(workspace, source, output, layer=None):
    output = external_root(Path(output))
    if source.suffix.lower() != ".gpkg" or output.suffix.lower() != ".zip" or output.is_relative_to(workspace.runs):
        raise ValueError("请选择独立位置保存 SHP 成果包（ZIP）")
    layers = [name for name, geometry in pyogrio.list_layers(source) if geometry]
    if layer is None and len(layers) != 1:
        raise ValueError("SHP 成果包需要一个可导出的矢量图层")
    frame = pyogrio.read_dataframe(source, layer=layer or layers[0])
    fields = dbf_fields(frame.drop(columns=frame.geometry.name).columns, frame.geometry.name)
    with TemporaryDirectory(prefix="guizhi-export-", dir=output.parent) as folder:
        root = Path(folder)
        data = root / "result.shp"
        renamed = frame.rename(columns=fields)
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            pyogrio.write_dataframe(renamed, data, driver="ESRI Shapefile", encoding="UTF-8")
        reopened = pyogrio.read_dataframe(data)
        if frame.crs != reopened.crs or geometries(frame) != geometries(reopened):
            raise ValueError("SHP 成果未通过坐标或几何校验，请使用原始成果格式")
        try:
            assert_frame_equal(renamed.drop(columns=renamed.geometry.name), reopened.drop(columns=reopened.geometry.name),
                               check_dtype=False, check_exact=True)
        except AssertionError as error:
            raise ValueError("SHP 字段值超出 DBF 精度或长度，请使用原始成果格式") from error
        (root / "fields.json").write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")
        pending = root / "package.zip"
        with ZipFile(pending, "w", compression=ZIP_DEFLATED) as archive:
            for file in sorted(root.iterdir()):
                if file != pending:
                    archive.write(file, file.name)
        pending.replace(output)
    return {"output": str(output), "features": len(reopened), "fields": fields}
