"""Read and version the layers used by the desktop map."""
from base64 import b64encode
import json
import math
import re
from uuid import uuid4

import pyogrio
import shapely
from pyproj import CRS, network
from pyproj.aoi import AreaOfInterest
from pyproj.transformer import TransformerGroup

from guizhi.application.tasks import run_operation
from guizhi.infrastructure.crs_catalog import get_plan
from guizhi.infrastructure.coordinates import apply_plan
from guizhi.infrastructure.export import package_layer
from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.operations.runner import data_revision

MAX_VISIBLE = 8000


def source(workspace, ref):
    if not isinstance(ref, dict) or set(ref) != {"run", "artifact"}:
        raise ValueError("请选择已保存的地图图层")
    run, kind = ref["run"], ref["artifact"]
    if not isinstance(run, str) or not re.fullmatch(r"[a-f0-9]{32}", run) or kind not in {"import", "export", "boundaries"}:
        raise ValueError("地图图层引用无效")
    file = read_artifact(workspace, run, kind)
    if file.suffix.lower() != ".gpkg":
        raise ValueError("该成果不是矢量图层")
    layers = [(name, geom) for name, geom in pyogrio.list_layers(file) if geom]
    if kind == "import":
        name = workspace.read(run)["result"]["layer"]
    elif len(layers) == 1:
        name = layers[0][0]
    else:
        raise ValueError("请选择一个矢量图层")
    return file, name


def successful(record):
    if record["status"] != "succeeded":
        raise ValueError(record["error"] or "地图操作未完成")
    return record


def material(file, name):
    return {"file": str(file), "layer": name, "revision": data_revision(file)}


def numbers(values):
    return [float(value) for value in values] if values is not None and all(math.isfinite(x) for x in values) else None


def describe(workspace, ref, file=None, name=None):
    if file is None:
        file, name = source(workspace, ref)
    info = pyogrio.read_info(file, layer=name, force_total_bounds=True)
    crs = CRS(info["crs"]) if info["crs"] else None
    if crs and crs.to_epsg() in {0, 404000}:
        crs = None
    if ref["artifact"] == "import" and not workspace.read(ref["run"])["result"]["crs"]:
        crs = None
    return {"ref": ref, "name": name, "revision": data_revision(file), "geometry": info["geometry_type"],
            "count": info["features"], "bounds": numbers(info["total_bounds"]),
            "crs": crs.to_string() if crs else None,
            "crsName": crs.name if crs else "未定义坐标系",
            "units": "degrees" if crs and crs.is_geographic else "m" if crs and crs.is_projected and
                crs.axis_info[0].unit_conversion_factor == 1 else None,
            "fields": [{"name": field, "type": dtype} for field, dtype in zip(info["fields"], info["dtypes"], strict=True)]}


def bounds(value):
    if (not isinstance(value, list) or len(value) != 4 or
            not all(type(x) in {int, float} and math.isfinite(x) for x in value)
            or value[0] > value[2] or value[1] > value[3]):
        raise ValueError("地图范围无效")
    return tuple(value)


def projection(source_crs, target_crs, extent):
    network.set_network_enabled(False)
    group = TransformerGroup(source_crs, target_crs, always_xy=True, allow_ballpark=False)
    if not group.best_available or not group.transformers:
        raise ValueError("当前图层缺少可靠的坐标转换或所需格网，请在设置中选择已验证的转换方案")
    approximate = group.transformers[0]
    if extent:
        world = approximate.transform_bounds(*extent, errcheck=True)
        if target_crs.to_epsg() != 4326:
            to_geo = TransformerGroup(source_crs, 4326, always_xy=True, allow_ballpark=False)
            if not to_geo.best_available or not to_geo.transformers:
                raise ValueError("无法验证坐标转换的适用范围")
            world = to_geo.transformers[0].transform_bounds(*extent, errcheck=True)
        if world[0] <= world[2] and world[1] <= world[3] and -90 <= world[1] <= world[3] <= 90:
            area = AreaOfInterest(*world)
            group = TransformerGroup(source_crs, target_crs, always_xy=True, allow_ballpark=False, area_of_interest=area)
    if not group.best_available or not group.transformers:
        raise ValueError("当前范围缺少可用的坐标转换或格网")
    return group.transformers[0]


def features(workspace, payload):
    file, name = source(workspace, payload["ref"])
    info = describe(workspace, payload["ref"], file, name)
    extent = bounds(payload["bbox"]) if payload.get("bbox") is not None else None
    target = CRS(payload["target"]) if payload.get("target") else None
    current = CRS(info["crs"]) if info["crs"] else None
    if target and current is None:
        raise ValueError("请先为该图层指定已知坐标系")
    convert = target and current and not current.equals(target, ignore_axis_order=True)
    plan = get_plan(workspace, payload["plan"]) if convert and payload.get("plan") else None
    read_extent = extent
    if convert:
        # A saved operation may have a limited domain or no validated inverse.
        read_extent = None if plan or extent is None else projection(target, current, extent).transform_bounds(*extent, errcheck=True)
    fids = payload.get("fids")
    if fids is not None:
        if not isinstance(fids, list) or not fids or len(fids) > 500 or any(type(fid) is not int for fid in fids):
            raise ValueError("请选择有效的地图要素")
        options = {"fids": fids}
    else:
        options = {"bbox": read_extent, "max_features": MAX_VISIBLE + 1}
    frame = pyogrio.read_dataframe(file, layer=name, fid_as_index=True, columns=None if fids else [], **options)
    if len(frame) > MAX_VISIBLE:
        return {"features": [], "limited": True}
    if convert and len(frame):
        if plan:
            frame = apply_plan(frame, plan, workspace.root)
            if not frame.crs.equals(target, ignore_axis_order=True):
                raise ValueError("转换方案的目标坐标系与地图不一致")
        else:
            tx = projection(current, target, info["bounds"])
            frame.geometry = shapely.transform(frame.geometry.array,
                lambda x, y, z=None: tx.transform(x, y, z, errcheck=True),
                interleaved=False, include_z=None)
    records = frame.drop(columns=frame.geometry.name).to_json(orient="records", date_format="iso", force_ascii=False)
    attributes = json.loads(records) if fids and len(frame.columns) > 1 else [{} for _ in frame.index]
    items = [{"id": int(fid), "geometry": shapely.geometry.mapping(shape), "properties": attrs}
             for (fid, shape), attrs in zip(frame.geometry.items(), attributes, strict=True)
             if shape is not None and not shape.is_empty]
    return {"features": items, "limited": False}


def request(workspace, payload):
    action = payload["action"]
    if action == "import":
        items = payload["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= 32:
            raise ValueError("一次请选择 1 至 32 个图层")
        result = []
        for item in items:
            record = successful(run_operation(workspace.root, {"operation": "data.describe", "inputs": [item]}))
            result.append(describe(workspace, {"run": record["id"], "artifact": "import"}))
        return result
    if action == "task-inputs":
        record = successful(workspace.read(payload["run"]))
        groups = record["result"]["workflow"]["material_runs"]
        refs = dict.fromkeys(item for group in groups.values() for item in group if isinstance(item, str))
        return [describe(workspace, {"run": run, "artifact": "import"}) for run in refs]
    if action == "raster-import":
        if not isinstance(payload["items"], list) or not 1 <= len(payload["items"]) <= 32:
            raise ValueError("一次请选择 1 至 32 个影像")
        result = []
        for item in payload["items"]:
            record = successful(run_operation(workspace.root, {"operation": "map_raster.preview", "inputs": [
                {name: item[name] for name in ("file", "layer")}]}))
            ref = {"run": record["id"], "artifact": "raster_preview"}
            result.append({**record["result"]["raster_preview"], "kind": "raster", "ref": ref, "name": item["name"]})
        return result
    if action == "raster-preview":
        ref = payload["ref"]
        image = read_artifact(workspace, ref["run"], "raster_preview")
        return b64encode(image.read_bytes()).decode("ascii")
    if action == "describe":
        return describe(workspace, payload["ref"])
    if action == "features":
        return features(workspace, payload)
    if action == "table":
        file, name = source(workspace, payload["ref"])
        page = payload["page"]
        if type(page) is not int or not 0 <= page <= 1_000_000:
            raise ValueError("属性表页码无效")
        frame = pyogrio.read_dataframe(file, layer=name, fid_as_index=True, read_geometry=False,
                                       skip_features=page * 50, max_features=50)
        return {"rows": [{"id": int(fid), "values": attrs} for fid, attrs in zip(frame.index,
                 json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False)), strict=True)]}
    if action == "crs-search":
        from pyproj.database import query_crs_info
        query = str(payload.get("query", "")).strip().casefold()
        defaults = {"4490", "4326", "4548", "4550", "3857"}
        return [{"value": "EPSG:" + item.code, "label": item.name + " · EPSG:" + item.code,
                 "units": "degrees" if item.type.name.startswith("GEOGRAPHIC") else "m"}
                for item in query_crs_info(auth_name="EPSG", allow_deprecated=False)
                if (query and (query in item.name.casefold() or query.removeprefix("epsg:") == item.code)
                    or not query and item.code in defaults)][:30]
    file, name = source(workspace, payload["ref"])
    if action == "material":
        return {**material(file, name), "layers": [name], "kind": "vector", "name": payload.get("name", name)}
    if action == "export":
        return package_layer(workspace, file, payload["output"], name)
    if action == "assign":
        code = payload["crs"]
        if describe(workspace, payload["ref"])["crs"]:
            raise ValueError("图层已有坐标系，请使用坐标转换")
        return produce(workspace, "crs.assign", file, name, {"definition": code})
    if action == "transform":
        return produce(workspace, "crs.transform", file, name, {"plan": payload["plan"]})
    if action in {"edit", "selection"}:
        item = material(file, name)
        if item["revision"] != payload.get("revision"):
            raise ValueError("图层版本已变化，请重新打开图层")
        if action == "edit":
            return produce(workspace, "edit.changes", file, name, {"changes": payload["changes"]})
        fids = payload["fids"]
        if not isinstance(fids, list) or not 1 <= len(fids) <= 500 or any(type(fid) is not int for fid in fids):
            raise ValueError("请选择 1 至 500 个地图要素")
        record = successful(run_operation(workspace.root, {"operation": "data.export_selection", "inputs": [item],
            "parameters": {"selection": {"layer": name, "revision": item["revision"], "fids": fids}}}))
        out = read_artifact(workspace, record["id"], "export")
        result = material(out, record["result"]["export_layer"])
        return {**result, "layers": [result["layer"]], "kind": "vector", "name": payload.get("name", name),
                "mapSelection": {"source": payload["ref"], "revision": item["revision"], "fids": fids}}
    raise ValueError("未知地图操作")


def produce(workspace, operation, file, name, parameters):
    record = successful(run_operation(workspace.root, {"operation": operation,
        "inputs": [material(file, name)], "parameters": parameters, "idempotency_key": uuid4().hex}))
    return describe(workspace, {"run": record["id"], "artifact": "export"})
