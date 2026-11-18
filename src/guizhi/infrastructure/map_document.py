"""Exchange local map documents with ArcGIS Desktop through ArcObjects."""
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from uuid import uuid4

import pyogrio
from pyproj import CRS

from guizhi.application.tasks import run_operation
from guizhi.infrastructure.arcmap import bridge, export_frame
from guizhi.infrastructure.map_workspace import bounds, describe, map_material, raster_layer, source, successful


def frame_layers(layers, parent=(), visible=True):
    for item in layers:
        enabled = visible and item["visible"]
        if "layers" in item:
            yield from frame_layers(item["layers"], (*parent, item["name"]), enabled)
        else:
            yield {**item, "group": " / ".join(parent), "visible": enabled}


def local_source(item, folder):
    kind = item["kind"]
    if kind == "raster":
        data = item.get("source")
        if data:
            file = Path(data)
            return (file if file.is_absolute() else folder / file), None
    if kind == "vector":
        name, place = item.get("dataset"), item.get("workspace")
        if place and name:
            path = Path(place)
            path = path if path.is_absolute() else folder / path
            if path.suffix.lower() == ".gdb":
                return path, name
            if Path(name).name == name:
                filename = name if name.lower().endswith(".shp") else name + ".shp"
                return path / filename, Path(filename).stem
    return None, None


def layer_file(item, document, search):
    source_file, name = local_source(item, document.parent)
    if source_file is None:
        raise ValueError("本系统无法打开此图层的数据源类型")
    if not source_file.exists():
        candidates = [document.parent / source_file.name, document.parent / (document.stem + "_data") / source_file.name]
        if search is not None:
            candidates.insert(0, search / source_file.name)
        source_file = next((item for item in candidates if item.exists()), None)
        if source_file is None:
            raise FileNotFoundError("找不到同名数据源")
    if not source_file.drive or str(source_file).startswith("\\"):
        raise ValueError("请选择本机数据源")
    if source_file.suffix.lower() not in {".shp", ".gdb", ".tif", ".tiff", ".img"}:
        raise ValueError("支持本机 SHP、文件地理数据库及 TIFF/IMG")
    return source_file.resolve(strict=True), name


def read_document(workspace, payload):
    document = Path(payload["source"]).resolve(strict=True)
    with TemporaryDirectory(prefix="mxd-", dir=workspace.root) as temp:
        result = bridge(Path(payload["bridge"]), Path(temp), "read", {"action": "read-mxd", "source": str(document)}, timeout=90)
    if result.get("schema") != "guizhi.mxd/1":
        raise ValueError("请更新 ArcGIS 组件后再打开 MXD")
    for frame in result["frames"]:
        frame["layers"] = list(frame_layers(frame["layers"]))
    return result


def import_frame(workspace, payload):
    document = Path(payload["source"]).resolve(strict=True)
    search = Path(payload["search"]).resolve(strict=True) if payload.get("search") else None
    result, issues = [], []
    layers = payload["frame"]["layers"]
    if len(layers) > 256:
        raise ValueError("一次最多打开含 256 个图层的数据框")
    for item in layers:
        try:
            file, name = layer_file(item, document, search)
            if item["kind"] == "raster":
                record = successful(run_operation(workspace.root, {"operation": "map_raster.preview", "inputs": [
                    {"file": str(file), "layer": None}]}))
                layer = raster_layer(workspace, {"run": record["id"], "artifact": "raster_preview"}, item["name"])
            else:
                if file.suffix.lower() == ".gdb":
                    # ArcGIS stores qualified dataset names in some documents.
                    names = {value[0] for value in pyogrio.list_layers(file) if value[1]}
                    if name not in names and name.split(".")[-1] in names:
                        name = name.split(".")[-1]
                    if name not in names:
                        raise ValueError("找不到指定要素类")
                record = successful(run_operation(workspace.root, {"operation": "data.describe", "inputs": [
                    {"file": str(file), "layer": name}]}))
                layer = describe(workspace, {"run": record["id"], "artifact": "import"})
            result.append({**layer, "name": item["name"], "style": item.get("style"), "group": item["group"],
                           "visible": item["visible"], "opacity": item.get("opacity", 100), "sourceName": str(file)})
        except (OSError, ValueError, RuntimeError) as error:
            issues.append({"name": item["name"], "message": str(error), "missing": isinstance(error, FileNotFoundError)})
    frame = {key: payload["frame"].get(key) for key in ("name", "crs", "bounds", "background")}
    if frame["crs"]:
        crs = CRS(frame["crs"])
        frame["crsName"] = crs.name
        frame["units"] = "degrees" if crs.is_geographic else "m" if crs.is_projected else None
        frame["metresPerUnit"] = crs.axis_info[0].unit_conversion_factor if crs.is_projected else None
    return {"layers": result, "issues": issues, "frame": frame}


def verify_mxd_symbols(expected, actual):
    if expected is None:
        return True
    if actual is None:
        return False
    def color(value):
        return value.casefold() if value else None

    if any(expected.get(key, default) != actual.get(key, default)
           for key, default in (("opacity", 100), ("noOutline", False))):
        return False
    if expected.get("classification"):
        left = expected["classification"]
        right = actual.get("classification")
        if not right or left["field"] != right["field"]:
            return False
        if [(item["value"], item.get("label") or ("空值" if item["value"] is None else item["value"]), color(item["color"]))
            for item in left["classes"]] != [(item["value"], item.get("label"), color(item["color"])) for item in right["classes"]]:
            return False
        if color(left["other"]) != color(right["other"]):
            return False
        if expected.get("fill") is not None and actual.get("fill") is not None:
            if color(left.get("otherFill", left["other"])) != color(right.get("otherFill")) or any(
                    color(a.get("fillColor", a["color"])) != color(b.get("fillColor"))
                    for a, b in zip(left["classes"], right["classes"], strict=True)):
                return False
    elif color(expected["color"]) != color(actual.get("color")):
        return False
    if actual.get("fill") is not None:
        if expected.get("fill") is None or expected.get("fillOpacity", 100) != actual["fillOpacity"]:
            return False
        if not expected.get("classification") and color(expected["fill"]) != color(actual["fill"]):
            return False
    if "shape" in actual:
        return expected["shape"] == actual["shape"] and abs(expected["size"] - actual["size"]) < 1e-7
    return (expected.get("noOutline", False) or expected["pattern"] == actual["pattern"]) and abs(expected["width"] - actual["width"]) < 1e-7


def write_document(workspace, payload):
    output = Path(payload["output"])
    if output.suffix.lower() != ".mxd" or output.exists() or not output.parent.is_dir():
        raise ValueError("请选择尚未存在的 MXD 文件")
    items = payload["layers"]
    if not isinstance(items, list) or not 1 <= len(items) <= 256:
        raise ValueError("请先添加 1 至 256 个图层")
    data = output.with_name(output.stem + "_data")
    if data.exists():
        raise ValueError("同名地图数据目录已存在，请使用其他文件名")
    temporary = output.with_name(f".guizhi-{uuid4().hex}.mxd")
    data.mkdir()
    try:
        gdb = data / "layers.gdb"
        layers = []
        for index, item in enumerate(items, 1):
            ref = item["ref"]
            dataset = f"layer_{index}"
            geometry = None
            if ref["artifact"] == "raster_preview":
                from guizhi.infrastructure.operations.raster import normalize

                file = Path(map_material(workspace, ref)["file"])
                target = data / (dataset + ".tif")
                normalize(file, target, lambda: None)
                layer = {"kind": "raster", "source": str(target)}
            else:
                file, name = source(workspace, ref)
                frame = pyogrio.read_dataframe(file, layer=name)
                geometry = pyogrio.read_info(file, layer=name)["geometry_type"]
                try:
                    export_frame(frame, gdb, dataset, "OpenFileGDB", tolerance=2e-8, allow_z=True)
                except (ValueError, AssertionError, pyogrio.errors.DataLayerError) as error:
                    raise ValueError(f"图层「{item['name']}」无法保存到 ArcGIS 文件地理数据库：{error}") from error
                layer = {"kind": "vector", "source": str(gdb), "dataset": dataset}
            layers.append({**layer, "name": item["name"], "visible": item["visible"], "opacity": item.get("opacity", 100), "style": item.get("style"), "geometry": geometry})
        target_crs = CRS(payload["crs"]).to_wkt(version="WKT1_ESRI") if payload.get("crs") else None
        view = bounds(payload["bounds"]) if payload.get("bounds") else None
        with TemporaryDirectory(prefix="mxd-", dir=workspace.root) as temp:
            bridge(Path(payload["bridge"]), Path(temp), "write",
                   {"action": "write-mxd", "output": str(temporary), "layers": layers, "crs": target_crs,
                    "bounds": view, "background": payload.get("background"), "name": payload.get("name")}, timeout=90)
            check = bridge(Path(payload["bridge"]), Path(temp), "verify",
                           {"action": "read-mxd", "source": str(temporary)}, timeout=90)
            actual = list(frame_layers(check["frames"][0]["layers"]))
            if [item["name"] for item in actual] != [item["name"] for item in reversed(layers)] or not all(item["valid"] for item in actual):
                raise ValueError("MXD 保存后未能重新打开全部图层")
            if payload.get("background") and (check["frames"][0].get("background") or "").casefold() != payload["background"].casefold():
                raise ValueError("MXD 底色未能保留")
            for before, after in zip(reversed(layers), actual, strict=True):
                valid = verify_mxd_symbols(before["style"], after.get("style"))
                if before["style"] and "Polygon" in (before["geometry"] or ""):
                    valid = valid and (before["style"]["fill"] is None) == (after.get("style", {}).get("fill") is None)
                if not valid or before["visible"] != after["visible"] or (
                        before["kind"] == "raster" and before["opacity"] != after.get("opacity", 100)):
                    raise ValueError(f"图层「{before['name']}」的符号或可见性未能保留")
        temporary.rename(output)
    except (OSError, ValueError, RuntimeError, InterruptedError):
        shutil.rmtree(data)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return {"output": str(output), "layers": len(layers)}
