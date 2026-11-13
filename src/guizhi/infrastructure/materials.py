"""Inspect file inputs and unpack material bundles into the task workspace."""
from itertools import islice
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
from uuid import uuid4
from zipfile import ZipFile

import pyogrio
from pyproj import CRS

from guizhi.domain.capabilities import MATERIAL_FORMATS, material_kind
from guizhi.infrastructure.vector import shapefile_projection, source_files

MAX_FILES = 1000
MAX_BYTES = 2 << 30
LEGACY = {".dwg", ".doc", ".xls"}


def redirected(path):
    return path.is_symlink() or getattr(path.lstat(), "st_reparse_tag", 0) == stat.IO_REPARSE_TAG_MOUNT_POINT


def describe(file):
    file = Path(file).resolve(strict=True)
    if not file.is_file():
        raise ValueError("请选择材料文件")
    kind = material_kind(file)
    layers = []
    metadata = {}
    if kind == "vector":
        parts = source_files(file)
        for name, geometry in pyogrio.list_layers(file):
            if not geometry:
                continue
            if file.suffix.lower() == ".shp":
                crs = shapefile_projection(parts)
            else:
                info = pyogrio.read_info(file, layer=name, force_feature_count=False)
                crs = CRS(info["crs"]) if info["crs"] else None
            layers.append({"name": name, "geometry": geometry, "crs": crs.to_string() if crs else None,
                           "projected_meters": bool(crs and crs.is_projected and
                                                    all(axis.unit_conversion_factor == 1 for axis in crs.axis_info[:2]))})
        if not layers:
            raise ValueError("此文件没有可用的矢量图层")
        metadata["companions"] = [part.name for part in parts if part != file]
    elif kind == "raster":
        from guizhi.infrastructure.operations import raster

        metadata = raster.metadata(file)
        crs = CRS(metadata["crs"])
        metadata.update(crs=crs.to_string(), companions=[part.name for part in raster.source_files(file)[1:]],
                        projected_meters=crs.is_projected and all(axis.unit_conversion_factor == 1 for axis in crs.axis_info[:2]))
    return {"file": str(file), "name": file.name, "kind": kind, "layers": [item["name"] for item in layers],
            "layer": layers[0]["name"] if len(layers) == 1 else "" if layers else None,
            "layerInfo": layers, "metadata": metadata}


def bound_layer_metadata(materials):
    """Describe selected layers by role, without opening feature rows or returning paths."""
    result = {}
    for role, group in materials.items():
        for item in group:
            kind = material_kind(item["file"])
            description = {"kind": kind, "name": Path(item["file"]).name, "layer": item["layer"]}
            if kind == "vector":
                file = Path(item["file"])
                if file.suffix.lower() == ".shp":
                    parts = source_files(file)
                    crs = shapefile_projection(parts)
                    # GDAL can read DBF fields independently of the SHP projection.
                    dbf = next(part for part in parts if part.suffix.lower() == ".dbf")
                    info = pyogrio.read_info(dbf, force_feature_count=False)
                    geometry = next((geom for name, geom in pyogrio.list_layers(file) if name == item["layer"]), None)
                    description.update(fields=info["fields"].tolist(), geometry=geometry,
                                       crs=crs.to_string() if crs else None)
                else:
                    info = pyogrio.read_info(file, layer=item["layer"], force_feature_count=False)
                    description.update(fields=info["fields"].tolist(), geometry=info["geometry_type"], crs=info["crs"])
            elif kind == "raster":
                from guizhi.infrastructure.operations.raster import metadata

                info = metadata(item["file"])
                description.update({key: info[key] for key in ("crs", "bands", "width", "height", "resolution")})
            result.setdefault(role, []).append(description)
    return result


def directory_files(folder):
    count = 0
    for root, folders, files in os.walk(folder, followlinks=False):
        folders[:] = sorted(name for name in folders if not name.startswith(".") and
                            not redirected(Path(root) / name))
        for name in sorted(files):
            file = Path(root) / name
            if file.is_symlink():
                continue
            count += 1
            if count > MAX_FILES:
                raise ValueError(f"一个材料目录最多可检查 {MAX_FILES} 个文件")
            yield file


def unpack(file, target):
    with ZipFile(file) as archive:
        members = archive.infolist()
        if len(members) > MAX_FILES or sum(entry.file_size for entry in members) > MAX_BYTES:
            raise ValueError("ZIP 超过 1000 项或 2 GB 解压限额")
        seen = set()
        for entry in members:
            name = entry.filename.rstrip("/")
            path = PurePosixPath(name)
            if (not name or path.is_absolute() or "\\" in name or
                    any(part in {"", ".", ".."} or part.endswith((".", " ")) or
                        ":" in part or PureWindowsPath(part).is_reserved() for part in name.split("/")) or
                    stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1 or name.casefold() in seen):
                raise ValueError("ZIP 内含不安全、重复或加密的路径")
            seen.add(name.casefold())
            if entry.is_dir() or any(part.startswith(".") or part == "__MACOSX" for part in path.parts):
                continue
            output = target.joinpath(*path.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as src, output.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1 << 20)


def intake(workspace, paths, kind="any"):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 32 or not all(isinstance(item, str) for item in paths):
        raise ValueError("一次请选择 1 至 32 个文件或目录")
    if kind not in {"any", "vector", "raster", "cad", "document"}:
        raise ValueError("材料类型无效")
    items, issues, seen = [], [], set()
    for item in paths:
        path = Path(item).resolve()
        extracted = None
        accepted = len(items)
        try:
            if path.suffix.lower() == ".zip" and path.is_file():
                parent = workspace.root / "material-bundles"
                parent.mkdir(exist_ok=True)
                if redirected(parent):
                    raise ValueError("材料归档目录不能指向其他位置")
                extracted = parent / uuid4().hex
                extracted.mkdir()
                unpack(path, extracted)
            grouped = extracted is not None or path.is_dir()
            files = list(directory_files(extracted or path)) if grouped else [path]
            prior_issues = len(issues)
            for file in files:
                suffix = file.suffix.lower()
                if grouped and (suffix not in MATERIAL_FORMATS and suffix not in LEGACY or suffix == ".onnx"):
                    continue
                if file in seen or grouped and kind != "any" and MATERIAL_FORMATS.get(suffix) not in {kind, None}:
                    continue
                seen.add(file)
                if len(items) >= 32:
                    issues.append({"name": path.name, "message": "一次最多添加 32 份材料，请分批选择"})
                    break
                try:
                    material = describe(file)
                    if material["kind"] == "model":
                        raise ValueError("请在设置中的影像模型资源添加识别模型")
                    if kind != "any" and material["kind"] != kind:
                        raise ValueError("材料格式与所选业务角色不符")
                    if grouped:
                        material["bundle"] = path.name
                    items.append(material)
                except Exception as error:
                    issues.append({"name": file.name, "message": str(error)})
            if grouped and len(items) == accepted and len(issues) == prior_issues:
                issues.append({"name": path.name, "message": "没有找到可添加的材料"})
        except Exception as error:
            issues.append({"name": path.name, "message": str(error)})
        finally:
            if extracted and extracted.exists() and len(items) == accepted:
                shutil.rmtree(extracted)
    return {"items": items, "issues": list(islice(issues, 32))}


def serve(requests, output):
    from guizhi.infrastructure.workspace import Workspace

    for line in requests:
        request = json.loads(line)
        try:
            workspace = Workspace(Path(request["workspace"]), create=True)
            if "map" in request:
                from guizhi.infrastructure.map_workspace import request as map_request

                result = {"result": map_request(workspace, request["map"])}
            else:
                result = {"result": intake(workspace, request["paths"], request["kind"])}
        except Exception as error:
            result = {"error": str(error)}
        print(json.dumps({"id": request["id"], **result}), file=output, flush=True)
