"""Seal local ArcGIS data and invoke the native layer bridge."""
from pathlib import Path
import json
import subprocess
import warnings

import pyogrio
import shapely
from pandas.testing import assert_frame_equal

from guizhi.infrastructure.vector import digest


def json_file(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def bridge(bridge_exe, directory, name, request):
    bridge_exe = bridge_exe.resolve(strict=True)
    if bridge_exe.name.casefold() != "arcmapbridge.exe" or not bridge_exe.is_file():
        raise ValueError("请选择规智提供的 ArcGIS 连接组件")
    identity = bridge_exe.with_name("ArcMapBridge.build")
    facts = dict(line.split("=", 1) for line in identity.read_text(encoding="utf-8-sig").splitlines() if "=" in line)
    if facts.get("binary_sha256") != digest(bridge_exe):
        raise ValueError("ArcGIS Bridge 完整性检查失败")
    file = directory / (name + ".json")
    json_file(file, request)
    try:
        process = subprocess.run([str(bridge_exe), str(file)], cwd=directory, stdin=subprocess.DEVNULL,
                                 capture_output=True, timeout=23, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        stage = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        (directory / (name + ".log")).write_text(stage, encoding="utf-8")
        raise ValueError("ArcGIS 图层生成超时：" + (stage or "startup")) from None
    stage = process.stderr.decode("utf-8", errors="replace").strip()
    (directory / (name + ".log")).write_text(stage, encoding="utf-8")
    if process.returncode:
        raise ValueError("ArcGIS 图层生成失败：" + stage.splitlines()[-1] if stage else "ArcGIS 图层生成失败")
    return json.loads(process.stdout.decode("utf-8-sig"))


def local_data(path, dataset):
    path = Path(path).resolve(strict=True)
    if not path.drive or str(path).startswith("\\\\"):
        raise ValueError("请选择本机要素数据")
    if not dataset or Path(dataset).name != dataset:
        raise ValueError("ArcGIS 数据集标识无效")
    if path.suffix.lower() == ".gdb" and path.is_dir():
        return path, dataset
    if path.is_dir():
        shape = path / (dataset if dataset.lower().endswith(".shp") else dataset + ".shp")
        if shape.is_file():
            return shape, None
    raise ValueError("请选择本机 Shapefile 或文件地理数据库")


def file_stamps(source):
    files = (source.iterdir() if source.is_dir() else
             (file for file in source.parent.iterdir() if file.stem == source.stem))
    return sorted((file.name, stat.st_size, stat.st_mtime_ns)
                  for file in files if file.is_file() and file.suffix.lower() != ".lock" for stat in [file.stat()])


def export_frame(frame, output, name, driver, tolerance=0):
    if frame.crs is None:
        raise ValueError("The data has no defined coordinate system")
    if driver == "OpenFileGDB":
        if shapely.has_z(frame.geometry.array).any() or shapely.has_m(frame.geometry.array).any():
            raise ValueError("ArcGIS Desktop 10.2 publication currently accepts 2D vector results")
        # ArcMap 10.2 stores wide integers as double; reject values that would lose precision.
        for series in (frame[col] for col in frame.select_dtypes(include="integer").columns):
            if any(abs(int(value)) > 2 ** 53 for value in series.dropna()):
                raise ValueError("An integer attribute cannot be represented exactly in ArcGIS Desktop 10.2")
    with warnings.catch_warnings():
        if driver == "OpenFileGDB":
            warnings.filterwarnings("ignore", message="Field .* of type Integer64 will be written as a Float64", category=RuntimeWarning)
        pyogrio.write_dataframe(frame, output, layer=name, driver=driver, promote_to_multi=False)
    check = pyogrio.read_dataframe(output, layer=name)
    left, right = frame.geometry, check.geometry
    if len(frame) != len(check) or frame.crs != check.crs:
        raise ValueError("ArcGIS exchange changed feature count or coordinate system")
    if not all((a is None and b is None) or
               (a is not None and b is not None and
                (a.equals_exact(b, tolerance, normalize=True) or a.equals(b)))
               for a, b in zip(left, right, strict=True)):
        raise ValueError("ArcGIS exchange geometry failed round-trip verification")
    assert_frame_equal(frame.drop(columns=left.name).reset_index(drop=True), check.drop(columns=right.name),
                       check_dtype=False, check_exact=True)
