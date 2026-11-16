"""Read and version the layers used by the desktop map."""
from base64 import b64encode
import json
import math
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from shutil import copyfile
from uuid import uuid4

import numpy as np
import pyogrio
import shapely
from pyproj import CRS, network
from pyproj.aoi import AreaOfInterest
from pyproj.enums import TransformDirection
from pyproj.transformer import TransformerGroup

from guizhi.application.skills import execute as execute_skill
from guizhi.application.tasks import run_operation
from guizhi.infrastructure.crs_catalog import get_plan
from guizhi.infrastructure.coordinates import apply_plan
from guizhi.infrastructure.export import package_layer
from guizhi.infrastructure.operations.evidence import artifact_path, read_artifact
from guizhi.infrastructure.operations.runner import data_revision
from guizhi.infrastructure.vector import digest

MAX_VISIBLE = 8000
MAX_MAP_POINTS = 180_000
MANUAL_OPERATIONS = {
    "data.describe", "vector.area", "vector.buffer", "vector.clip", "vector.intersect",
    "vector.erase", "vector.dissolve", "vector.union", "vector.identity", "vector.symmetric_difference", "vector.update_overlay",
    "vector.spatial_join", "data.copy", "data.merge", "data.append", "data.select_by_attribute",
    "data.select_by_location", "data.export_selection", "geometry.create_dataset", "geometry.repair",
    "geometry.explode", "table.add_field", "table.delete_field", "table.add_coordinates",
    "presentation.export_table", "raster.statistics", "raster.clip", "raster.reproject", "raster.mosaic",
    "table.calculate_field", "table.calculate_geometry", "table.edit_cell",
}


def raster_layer(workspace, ref, name):
    info = workspace.read(ref["run"])["result"]["raster_preview"]
    source_path = workspace.read(ref["run"])["task"]["request"]["inputs"][0]["file"]
    return {**info, "kind": "raster", "ref": ref, "name": name, "sourceName": source_path}


def map_material(workspace, ref):
    if not isinstance(ref, dict) or set(ref) != {"run", "artifact"}:
        raise ValueError("请选择已保存的地图图层")
    if ref["artifact"] == "raster_preview":
        file = read_artifact(workspace, ref["run"], "raster-1")
        return {"file": str(file), "layer": None, "revision": digest(file)}
    return material(*source(workspace, ref))


def output_layers(workspace, record, title):
    data = record["result"]
    result = []
    for entry in data["evidence"]:
        kind = entry["step"]
        if kind not in {"export", "boundaries"}:
            continue
        ref = {"run": record["id"], "artifact": kind}
        file = read_artifact(workspace, record["id"], kind)
        if file.suffix.lower() == ".gpkg":
            layer = describe(workspace, ref, file, data.get("export_layer") if kind == "export" else None)
            result.append({**layer, "name": title + (" · 边界" if kind == "boundaries" else "")})
        elif file.suffix.lower() in {".tif", ".tiff", ".img"}:
            preview = successful(run_operation(workspace.root, {"operation": "map_raster.preview", "inputs": [
                {"file": str(file), "layer": None, "revision": entry["sha256"]}]}))
            result.append(raster_layer(workspace, {"run": preview["id"], "artifact": "raster_preview"}, title + " · 影像"))
    return result


def map_result(workspace, record, title, *, include_steps=False):
    data = record["result"]
    steps = data.get("workflow", {}).get("steps", [])
    records = [(record, title)]
    if include_steps:
        records = [(successful(workspace.read(step["run_id"])), title if len(steps) == 1 else
                    title + " · " + step_title(step["operation"])) for step in steps]
    layers = [layer for child, label in records for layer in output_layers(workspace, child, label)]
    map_data = next((json.loads(read_artifact(workspace, child["id"], "map").read_text(encoding="utf-8"))
                     for child, _ in reversed(records) if any(ref["step"] == "map" for ref in child["result"]["evidence"])), None)
    statistics = {}
    for child, _ in records:
        statistics.update(child["result"].get("statistics", {}))
    statistics.update(data.get("statistics", {}))
    return {"run": record["id"], "layers": layers, "statistics": statistics,
            "selection": data.get("selection"), "map": map_data}


def step_title(operation):
    from guizhi.domain.capabilities import CAPABILITIES
    from guizhi.domain.operations import OPERATIONS

    return (OPERATIONS.get(operation) or CAPABILITIES[operation]).title


def manual_tool(workspace, payload):
    operation = payload["operation"]
    if operation not in MANUAL_OPERATIONS:
        raise ValueError("地图分析工具无效")
    refs = payload["refs"]
    if not isinstance(refs, list) or len(refs) > 32:
        raise ValueError("一次最多选择 32 个图层")
    items = [map_material(workspace, ref) for ref in refs]
    parameters = dict(payload["parameters"])
    if operation == "data.export_selection":
        fids = parameters.get("fids")
        if len(items) != 1 or not isinstance(fids, list) or not 1 <= len(fids) <= 500:
            raise ValueError("请选择 1 至 500 个地图要素")
        parameters = {"selection": {"layer": items[0]["layer"], "revision": items[0]["revision"], "fids": fids}}
    title = {"data.export_selection": "所选要素"}.get(operation) or step_title(operation)
    if operation == "geometry.create_dataset" or payload.get("name") is not None:
        name = payload.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError("请填写 1 至 120 字的图层名称")
        title = name.strip()
    record = run_operation(workspace.root, {"operation": operation, "inputs": items,
                           "parameters": parameters, "idempotency_key": uuid4().hex})
    return map_result(workspace, successful(record), title)


def manual_skill(workspace, payload):
    groups = {role: [map_material(workspace, item["ref"]) if "ref" in item else item for item in items]
              for role, items in payload["materials"].items()}
    record = execute_skill(workspace.root, groups, skill=payload["skill"], skill_parameters=payload["parameters"],
                           coordinate_plans=payload["coordinatePlans"])
    return map_result(workspace, successful(record), record["result"]["workflow"]["skill"]["title"], include_steps=True)


@lru_cache(maxsize=256)
def interactive_revision(file, size, modified, changed):
    return digest(file), data_revision(file)


def source(workspace, ref):
    if not isinstance(ref, dict) or set(ref) != {"run", "artifact"}:
        raise ValueError("请选择已保存的地图图层")
    run, kind = ref["run"], ref["artifact"]
    if not isinstance(run, str) or not re.fullmatch(r"[a-f0-9]{32}", run) or kind not in {"import", "export", "boundaries", "transfer-source"}:
        raise ValueError("地图图层引用无效")
    record = workspace.read(run)
    if record["status"] != "succeeded":
        raise ValueError("图层尚未发布")
    evidence = next((item for item in record["result"]["evidence"] if item["step"] == kind), None)
    if evidence is None:
        raise ValueError("图层成果不存在")
    file = artifact_path(workspace, run, evidence["path"])
    stat = file.stat()
    checksum, _ = interactive_revision(file, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if checksum != evidence["sha256"]:
        raise ValueError("图层成果已变化")
    if file.suffix.lower() != ".gpkg":
        raise ValueError("该成果不是矢量图层")
    layers = [(name, geom) for name, geom in pyogrio.list_layers(file) if geom]
    if kind == "import":
        name = record["result"]["layer"]
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
    name = info["layer_name"]
    crs = CRS(info["crs"]) if info["crs"] else None
    if crs and crs.to_epsg() in {0, 404000}:
        crs = None
    original = str(file)
    source_crs_name = None
    source_parts = []
    if ref["artifact"] == "import":
        details = workspace.read(ref["run"])["result"]
        original = (details.get("datasets") or [{}])[0].get("source") or original
        crs = CRS(details["crs"]) if details["crs"] else None
        source_crs_name = details.get("crs_name")
        source_parts = [item["sha256"] for item in details["evidence"] if "-source-" in item["step"]]
    stat = file.stat()
    _, revision = interactive_revision(file, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return {"ref": ref, "name": name, "sourceName": original, "revision": revision, "sourceRevision": ":".join(source_parts) or revision, "geometry": info["geometry_type"],
            "count": info["features"], "fidField": info["fid_column"], "bounds": numbers(info["total_bounds"]),
            "crs": crs.to_string() if crs else None,
            "crsName": (source_crs_name or crs.name) if crs else "未定义坐标系",
            "metresPerUnit": crs.axis_info[0].unit_conversion_factor if crs and crs.is_projected else None,
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
    area = None
    geodetic = source_crs.geodetic_crs
    if extent is not None and geodetic is not None:
        # An AOI needs longitude and latitude. Undo the source projection in its own datum.
        # A separate WGS 84 datum transformation can require grids unrelated to the map.
        local = TransformerGroup(source_crs, geodetic, always_xy=True, allow_ballpark=False)
        if not local.best_available or not local.transformers:
            raise ValueError("无法从图层坐标系确定转换范围，请选择已验证的转换方案")
        west, south, east, north = local.transformers[0].transform_bounds(*extent, errcheck=True)
        if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= north <= 90):
            raise ValueError("图层坐标超出坐标系适用范围，请检查图层坐标系")
        area = AreaOfInterest(west, south, east, north)
    group = TransformerGroup(source_crs, target_crs, always_xy=True, allow_ballpark=False, area_of_interest=area)
    if not group.best_available or not group.transformers:
        raise ValueError("缺少可靠的坐标转换或所需格网，请选择已验证的转换方案")
    return group.transformers[0]


def category_value(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e21:
        return str(int(value))
    return str(value)


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
    fids = payload.get("fids")
    tx = projection(current, target, info["bounds"]) if convert and not plan else None
    display_bounds = info["bounds"]
    if tx and display_bounds:
        display_bounds = numbers(tx.transform_bounds(*display_bounds, errcheck=True))
        if display_bounds and display_bounds[0] > display_bounds[2]:
            display_bounds = [-180.0, display_bounds[1], 180.0, display_bounds[3]]
    read_extent = extent
    if convert and fids is None:
        read_extent = None
        if tx and tx.has_inverse and extent and display_bounds:
            west, south = max(extent[0], display_bounds[0]), max(extent[1], display_bounds[1])
            east, north = min(extent[2], display_bounds[2]), min(extent[3], display_bounds[3])
            if west > east or south > north:
                return {"features": [], "limited": False, "bounds": display_bounds}
            read_extent = numbers(tx.transform_bounds(west, south, east, north,
                                                     direction=TransformDirection.INVERSE, errcheck=True))
            if read_extent and read_extent[0] > read_extent[2]:
                read_extent = (-180.0, read_extent[1], 180.0, read_extent[3])
    if fids is not None:
        if not isinstance(fids, list) or not fids or len(fids) > 500 or any(type(fid) is not int for fid in fids):
            raise ValueError("请选择有效的地图要素")
        options = {"fids": fids}
    else:
        options = {"bbox": tuple(read_extent) if read_extent is not None else None, "max_features": MAX_VISIBLE + 1}
    frame = pyogrio.read_dataframe(file, layer=name, fid_as_index=True, columns=None if fids else payload.get("fields", []), **options)
    limited = len(frame) > MAX_VISIBLE
    if limited:
        frame = frame.iloc[:MAX_VISIBLE]
    if convert and len(frame):
        if plan:
            frame = apply_plan(frame, plan, workspace.root)
            if not frame.crs.equals(target, ignore_axis_order=True):
                raise ValueError("转换方案的目标坐标系与地图不一致")
        else:
            frame.geometry = shapely.transform(frame.geometry.array,
                lambda x, y, z=None: tx.transform(x, y, z, errcheck=True),
                interleaved=False, include_z=None)
    if fids is None:
        if convert and extent and read_extent is None and len(frame):
            frame = frame.cx[extent[0]:extent[2], extent[1]:extent[3]]
        # Bound each map response by vertex count; original geometries remain in the source.
        if payload.get("tolerance") is not None and payload["tolerance"] > 0 and len(frame):
            tolerance = float(payload["tolerance"])
            if not math.isfinite(tolerance):
                raise ValueError("地图分辨率无效")
            frame = frame.set_geometry(shapely.simplify(frame.geometry.array, tolerance, preserve_topology=True))
        points = np.cumsum(shapely.get_num_coordinates(frame.geometry.array))
        limit = int(np.searchsorted(points, MAX_MAP_POINTS, side="right"))
        limited = limited or limit < len(frame)
        frame = frame.iloc[:limit]
    records = frame.drop(columns=frame.geometry.name).to_json(orient="records", date_format="iso", force_ascii=False)
    attributes = json.loads(records) if len(frame.columns) > 1 else [{} for _ in frame.index]
    if fids:
        for row in attributes:
            for key, value in row.items():
                if type(value) is int and abs(value) > 2**53 - 1:
                    row[key] = str(value)
    elif payload.get("fields"):
        for row in attributes:
            for key, value in row.items():
                row[key] = category_value(value)
    items = [{"id": int(fid), "geometry": shapely.geometry.mapping(shape), "properties": attrs}
             for (fid, shape), attrs in zip(frame.geometry.items(), attributes, strict=True)
             if shape is not None and not shape.is_empty]
    return {"features": items, "limited": limited, "bounds": display_bounds if not plan else numbers(frame.total_bounds)}


def request(workspace, payload):
    action = payload["action"]
    if action.startswith("flow-"):
        from guizhi.infrastructure import map_flow

        if action == "flow-tools":
            return map_flow.catalog()
        if action == "flow-run":
            return map_flow.run(workspace, payload)
        if action == "flow-progress":
            return map_flow.progress(workspace, payload)
        if action in {"flow-templates", "flow-save"}:
            return map_flow.template(workspace, payload)
        if action == "flow-export-table":
            return map_flow.export_table(workspace, payload)
    if action in {"mxd-read", "mxd-import", "mxd-write"}:
        from guizhi.infrastructure import map_document

        return {"mxd-read": map_document.read_document, "mxd-import": map_document.import_frame,
                "mxd-write": map_document.write_document}[action](workspace, payload)
    if action == "tool":
        return manual_tool(workspace, payload)
    if action == "skill":
        return manual_skill(workspace, payload)
    if action == "edit-sketch":
        from guizhi.infrastructure.map_editing import sketch

        return sketch(payload)
    if action == "edit-transfer":
        if payload["ref"] == payload["destination"]:
            raise ValueError("请选择另一个图层")
        origin = material(*source(workspace, payload["ref"]))
        destination = material(*source(workspace, payload["destination"]))
        if origin["revision"] != payload["revision"] or destination["revision"] != payload["destinationRevision"]:
            raise ValueError("图层已修改，请重新复制或剪切要素")
        mode = payload["mode"]
        record = successful(run_operation(workspace.root, {"operation": "edit.transfer", "inputs": [origin, destination],
            "parameters": {"selection": {"layer": origin["layer"], "revision": origin["revision"], "fids": payload["fids"]},
                           "mode": mode}, "idempotency_key": uuid4().hex}))
        result = {"destination": describe(workspace, {"run": record["id"], "artifact": "export"}),
                  "count": record["result"]["changeset"]["moved"]}
        if mode == "cut":
            result["source"] = describe(workspace, {"run": record["id"], "artifact": "transfer-source"})
        return result
    if action == "step-layers":
        record = successful(workspace.read(payload["run"]))
        return output_layers(workspace, record, payload.get("title") or step_title(record["task"]["operation"]))
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
        groups = record["result"].get("workflow", {}).get("material_runs", {})
        existing = payload.get("existing", {})
        refs = {}
        for role, group in groups.items():
            for index, run in enumerate(group):
                if not isinstance(run, str):
                    continue
                ref = {"run": run, "artifact": "import"}
                candidates = existing.get(role, [])
                original = candidates[index] if index < len(candidates) else None
                if original:
                    source_file, source_layer = source(workspace, original)
                    saved_file, saved_layer = source(workspace, ref)
                    if source_layer == saved_layer and data_revision(source_file) == data_revision(saved_file):
                        ref = original
                refs[(ref["run"], ref["artifact"])] = ref
        return [describe(workspace, ref) for ref in refs.values()]
    if action == "raster-import":
        if not isinstance(payload["items"], list) or not 1 <= len(payload["items"]) <= 32:
            raise ValueError("一次请选择 1 至 32 个影像")
        result = []
        for item in payload["items"]:
            record = successful(run_operation(workspace.root, {"operation": "map_raster.preview", "inputs": [
                {name: item[name] for name in ("file", "layer")}]}))
            ref = {"run": record["id"], "artifact": "raster_preview"}
            result.append(raster_layer(workspace, ref, item["name"]))
        return result
    if action == "raster-display":
        from guizhi.infrastructure.operations.map_raster import image_preview

        file = Path(map_material(workspace, payload["ref"])["file"])
        image, info = image_preview(file, payload.get("target"))
        return {"image": b64encode(image).decode("ascii"), "bounds": info["bounds"]}
    if action == "raster-preview":
        ref = payload["ref"]
        image = read_artifact(workspace, ref["run"], "raster_preview")
        return b64encode(image.read_bytes()).decode("ascii")
    if action == "describe":
        return describe(workspace, payload["ref"])
    if action == "features":
        return features(workspace, payload)
    if action == "categories":
        from collections import Counter

        file, name = source(workspace, payload["ref"])
        field = payload.get("field")
        info = pyogrio.read_info(file, layer=name)
        if field not in info["fields"]:
            raise ValueError("请选择图层中的有效分类字段")
        frame = pyogrio.read_dataframe(file, layer=name, columns=[field], read_geometry=False)
        values = json.loads(frame[field].to_json(orient="values", date_format="iso", force_ascii=False))
        counts = Counter(map(category_value, values))
        return {"classes": [{"value": value, "count": count} for value, count in counts.most_common(120)],
                "remaining": max(0, len(counts) - 120)}
    if action in {"table", "table-complement", "table-statistics", "table-unique-values", "table-feature"}:
        from guizhi.infrastructure import map_attributes

        file, name = source(workspace, payload["ref"])
        reader = {"table": map_attributes.table_page, "table-complement": map_attributes.complement,
                  "table-statistics": map_attributes.statistics, "table-unique-values": map_attributes.unique_values,
                  "table-feature": map_attributes.feature_attributes}[action]
        return reader(file, name, payload)
    if action == "crs-search":
        from pyproj.database import query_crs_info
        query = str(payload.get("query", "")).strip().casefold()
        defaults = {"4490", "4326", "4548", "4550", "3857"}
        entries = [item for item in query_crs_info(auth_name="EPSG", allow_deprecated=False)
                   if (query and (query in item.name.casefold() or query.removeprefix("epsg:") == item.code)
                       or not query and item.code in defaults)][:30]
        result = []
        for item in entries:
            crs = CRS.from_epsg(item.code)
            if not (crs.is_geographic or crs.is_projected) or len(crs.axis_info) != 2:
                continue
            units = "degrees" if crs.is_geographic else "m" if crs.axis_info[0].unit_conversion_factor == 1 else None
            result.append({"value": "EPSG:" + item.code, "label": item.name + " · EPSG:" + item.code, "units": units,
                           "metresPerUnit": crs.axis_info[0].unit_conversion_factor if crs.is_projected else None,
                           "kind": "投影坐标系" if crs.is_projected else "地理坐标系"})
        return result
    if action == "material":
        item = map_material(workspace, payload["ref"])
        name = item["layer"]
        return {**item, "layers": [name] if name else [], "kind": "vector" if name else "raster",
                "name": payload.get("name", name)}
    if action in {"assign", "reproject"} and payload["ref"]["artifact"] == "raster_preview":
        item = map_material(workspace, payload["ref"])
        code = payload["crs"]
        if action == "reproject" and not code.startswith("EPSG:"):
            from guizhi.infrastructure.crs_catalog import get

            code = get(workspace, "definition", code)["definition"]["wkt"]
        record = successful(run_operation(workspace.root, {"operation": "raster." + action, "inputs": [item],
            "parameters": {"target_crs": code, **({"resampling": payload.get("resampling", "nearest")} if action == "reproject" else {})}}))
        return output_layers(workspace, record, "影像")[0]
    if action == "export":
        return export_layer(workspace, payload)
    file, name = source(workspace, payload["ref"])
    if action == "assign":
        code = payload["crs"]
        if describe(workspace, payload["ref"])["crs"]:
            raise ValueError("图层已有坐标系，请使用坐标转换")
        return produce(workspace, "crs.assign", file, name, {"definition": code})
    if action == "reproject":
        return produce(workspace, "crs.reproject", file, name, {"definition": payload["crs"]})
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


def export_layer(workspace, payload):
    output = Path(payload["output"])
    extension = payload["format"]
    if output.suffix.lower() != "." + extension or output.exists():
        raise ValueError("请选择尚未使用的导出文件名")
    ref = payload["ref"]
    if ref["artifact"] == "raster_preview":
        from guizhi.infrastructure.operations import raster

        file = map_material(workspace, ref)["file"]
        if extension == "tif":
            copyfile(file, output)
        elif extension == "jpg":
            with raster.rio.open(file) as src:
                if src.count not in {1, 3} or set(src.dtypes) != {"uint8"}:
                    raise ValueError("JPEG 支持单波段或三波段 8 位影像，请将此图层导出为 GeoTIFF")
            raster.copy_dataset(file, output, driver="JPEG", strict=True, WORLDFILE="YES")
        else:
            raise ValueError("请选择 GeoTIFF 或 JPEG")
    else:
        file, name = source(workspace, ref)
        if extension == "zip":
            return package_layer(workspace, file, output, name)
        if extension == "csv":
            record = successful(run_operation(workspace.root, {"operation": "presentation.export_table",
                "inputs": [material(file, name)]}))
            copyfile(read_artifact(workspace, record["id"], "table"), output)
        elif extension == "kml":
            from guizhi.infrastructure.map_kml import export_kml

            if not describe(workspace, ref)["crs"]:
                raise ValueError("请先为图层定义正确的坐标系，再导出 KML")
            export_kml(file, name, output, payload)
        elif extension in {"gpkg", "geojson"}:
            from guizhi.infrastructure.gdal_translate import vector_translate

            driver = {"gpkg": "GPKG", "geojson": "GeoJSON"}[extension]
            vector_translate(file, output, "-f", driver, name)
        else:
            raise ValueError("请选择有效的矢量导出格式")
    return {"file": str(output)}
