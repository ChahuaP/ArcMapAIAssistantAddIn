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
from pyproj import CRS
from pyproj.enums import TransformDirection

from guizhi.application.diagnostics import failure_summary
from guizhi.application.skills import execute as execute_skill
from guizhi.application.tasks import run_operation
from guizhi.infrastructure.cache import reuse_job
from guizhi.infrastructure.crs_catalog import get_plan
from guizhi.infrastructure.crs_options import projection
from guizhi.infrastructure.map_overview import coverage, display_copy, many_in_view
from guizhi.infrastructure.export import package_layer
from guizhi.infrastructure.operations.evidence import artifact_path, read_artifact
from guizhi.infrastructure.operations.runner import data_revision
from guizhi.infrastructure.vector import digest

MAX_VISIBLE = 8000
MAX_MAP_POINTS = 180_000
VECTOR_ARTIFACTS = {"import", "export", "boundaries", "transfer-source"}
MANUAL_OPERATIONS = {
    "data.describe", "vector.area", "vector.buffer", "vector.clip", "vector.intersect",
    "vector.erase", "vector.dissolve", "vector.union", "vector.identity", "vector.symmetric_difference", "vector.update_overlay",
    "vector.spatial_join", "data.copy", "data.merge", "data.select_by_attribute",
    "data.select_by_location", "data.export_selection", "geometry.create_dataset", "geometry.repair",
    "geometry.multipart", "geometry.explode",
    "crs.assign", "crs.reproject",
    "table.add_field", "table.delete_field", "flow.unique_values", "flow.calculate",
    "raster.statistics", "raster.clip", "raster.reproject", "raster.assign", "raster.mosaic",
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
        if kind not in {"export", "boundaries", "transfer-source"}:
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


def selection_material(workspace, ref, revision, fids):
    file, name = source(workspace, ref)
    item = material(file, name)
    if item["revision"] != revision:
        raise ValueError("图层版本已变化，请重新选择要素")
    if not isinstance(fids, list) or not fids or any(type(fid) is not int for fid in fids):
        raise ValueError("请选择有效的地图要素")
    selection = {"layer": name, "revision": revision, "fids": sorted(set(fids))}
    parameters = {"selection": selection}
    record, _ = reuse_job(workspace, "data.export_selection", [item], parameters, lambda: run_operation(
        workspace.root, {"operation": "data.export_selection", "inputs": [item], "parameters": parameters}))
    record = successful(record)
    result = material(read_artifact(workspace, record["id"], "export"), record["result"]["export_layer"])
    return {**result, "layers": [result["layer"]], "kind": "vector",
            "mapSelection": {"source": ref, "revision": revision, "fids": fids}}


def workflow_layers(workspace, record):
    """Project committed results into map rows, following edits of the same layer."""
    steps = record["result"].get("workflow", {}).get("steps", [])
    if not steps:
        return output_layers(workspace, record, "办理成果")

    rows = {}
    targets, last_result = {}, {}
    for step in steps:
        child = successful(workspace.read(step["run_id"]))
        operation = step["operation"]
        if operation == "vector_inventory":
            continue
        outputs = output_layers(workspace, child, child["result"].get("layer_name") or step_title(operation))
        change = child["result"].get("changeset", {})
        for layer in outputs:
            artifact = layer["ref"]["artifact"]
            key = "step." + step["id"] + ("" if artifact == "export" else "." + artifact)
            edited = artifact == "export" and "target_revision" in change and operation != "geometry.explode"
            moved_source = artifact == "transfer-source" and operation == "edit.transfer"
            if edited or moved_source:
                target = step["inputs"][int(operation == "edit.transfer" and not moved_source)]
                if target.startswith("step.") and target not in targets:
                    raise ValueError("编辑引用的上一步缺少地图图层")
                root = targets.get(target, target)
                if root in rows:
                    previous = rows[root]
                    # A second edit must consume the first edit's result.
                    if targets.get(target) != root or previous["ref"] != last_result[target]:
                        raise ValueError("同一图层的后续编辑须引用上一步成果")
                    layer["name"] = previous["name"]
                if root.startswith("material."):
                    layer["replacesRole"] = root.removeprefix("material.")
                targets[key] = root
                rows[root] = layer
            else:
                targets[key] = key
                rows[key] = layer
            last_result[key] = layer["ref"]
    for row in rows.values():
        row["stepReferences"] = [name for name, root in targets.items() if rows[root] is row]
    return list(rows.values())


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
    result = {"run": record["id"], "layers": layers, "statistics": statistics,
              "selection": data.get("selection"), "map": map_data}
    if "value" in data:
        result["value"] = data["value"]
    if band_statistics := data.get("raster", {}).get("band_statistics"):
        statistics["band_statistics"] = band_statistics
    return result


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
    parameters = dict(payload["parameters"])
    if operation == "data.select_by_attribute":
        from guizhi.infrastructure.map_attributes import filter_ids

        if len(refs) != 1:
            raise ValueError("请选择一个查询图层")
        file, name = source(workspace, refs[0])
        fids = filter_ids(file, name, parameters["where"])
        return {"layers": [], "selection": {"layer": name, "revision": material(file, name)["revision"],
                                             "fids": fids}, "statistics": {"selected_count": len(fids)}}
    items = [map_material(workspace, ref) for ref in refs]
    if operation == "data.export_selection":
        fids = parameters.get("fids")
        if len(items) != 1 or not isinstance(fids, list) or not fids or any(type(fid) is not int for fid in fids):
            raise ValueError("请选择有效的地图要素")
        parameters = {"selection": {"layer": items[0]["layer"], "revision": items[0]["revision"], "fids": fids}}
    title = {"data.export_selection": "所选要素"}.get(operation) or step_title(operation)
    if operation == "geometry.create_dataset" or payload.get("name") is not None:
        name = payload.get("name")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError("请填写 1 至 120 字的图层名称")
        title = name.strip()
    record = run_operation(workspace.root, {"operation": operation, "inputs": items,
                           "parameters": parameters, "idempotency_key": uuid4().hex})
    if record["status"] != "succeeded":
        return {"status": record["status"], "run": record["id"], "message": record["error"],
                "failure": failure_summary(workspace, record)}
    try:
        return map_result(workspace, record, title)
    except (ValueError, OSError, RuntimeError) as error:
        return {"status": "failed", "run": record["id"], "message": str(error),
                "failure": {"step": "read_tool_output", "operation": operation, "error": str(error),
                            "errorCode": type(error).__name__}}


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
    if not isinstance(run, str) or not re.fullmatch(r"[a-f0-9]{32}", run) or kind not in VECTOR_ARTIFACTS:
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
    if kind == "import":
        return file, record["result"]["layer"]
    layers = [name for name, geometry in pyogrio.list_layers(file) if geometry]
    if len(layers) != 1:
        raise ValueError("请选择一个矢量图层")
    name = layers[0]
    return file, name


def successful(record):
    if record["status"] != "succeeded":
        raise ValueError(record["error"] or "地图操作未完成")
    return record


def material(file, name):
    stat = file.stat()
    _, revision = interactive_revision(file, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return {"file": str(file), "layer": name, "revision": revision}


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
    if plan:
        file = display_copy(workspace, file, name, info, plan, target)
        info = {**info, "bounds": numbers(pyogrio.read_info(file, layer=name, force_total_bounds=True)["total_bounds"])}
        convert = False
    highlight = payload.get("selectionFids")
    if highlight is not None and (not isinstance(highlight, list) or not highlight or
                                  any(type(fid) is not int for fid in highlight)):
        raise ValueError("请选择有效的地图要素")
    fids = highlight if highlight is not None and len(highlight) <= 500 else payload.get("fids")
    tx = projection(current, target, info["bounds"]) if convert else None
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
    def overview():
        full = tuple(info["bounds"])
        window = None
        if read_extent is not None:
            xmin, ymin = max(full[0], read_extent[0]), max(full[1], read_extent[1])
            xmax, ymax = min(full[2], read_extent[2]), min(full[3], read_extent[3])
            if xmin > xmax or ymin > ymax:
                return {"features": [], "limited": False, "bounds": display_bounds}
            # Reuse the whole-layer footprint at regional scales. Refine in a dense local view.
            if (xmax - xmin) * (ymax - ymin) < (full[2]-full[0]) * (full[3]-full[1]) / 16:
                window = (xmin, ymin, xmax, ymax)
        where = None
        if highlight:
            from guizhi.infrastructure.operations.table import identifier
            where = identifier(info["fidField"]) + " IN (" + ",".join(map(str, highlight)) + ")"
        revision = file.stem if plan else info["revision"]
        shapes = coverage(file, name, revision, full, window, workspace.root / "cache", where)
        if tx and shapes:
            shapes = shapely.transform(np.asarray(shapes, dtype=object), lambda x, y: tx.transform(x, y, errcheck=True),
                                       interleaved=False)
        return {"features": [], "overview": [shapely.geometry.mapping(shape) for shape in shapes],
                "limited": True, "bounds": display_bounds}

    if fids is not None:
        if not isinstance(fids, list) or not fids or len(fids) > 500 or any(type(fid) is not int for fid in fids):
            raise ValueError("请选择有效的地图要素")
        options = {"fids": fids}
    else:
        if highlight or many_in_view(file, name, info["count"], read_extent, MAX_VISIBLE):
            return overview()
        options = {"bbox": tuple(read_extent) if read_extent is not None else None, "max_features": MAX_VISIBLE + 1}
    frame = pyogrio.read_dataframe(file, layer=name, fid_as_index=True, columns=None if fids and not highlight else payload.get("fields", []), **options)
    if len(frame) > MAX_VISIBLE:
        return overview()
    if tx and len(frame):
        frame.geometry = shapely.transform(frame.geometry.array,
            lambda x, y, z=None: tx.transform(x, y, z, errcheck=True), interleaved=False, include_z=None)
    clipped = False
    if fids is None:
        if convert and extent and read_extent is None and len(frame):
            frame = frame.cx[extent[0]:extent[2], extent[1]:extent[3]]
        if int(shapely.get_num_coordinates(frame.geometry.array).sum()) > MAX_MAP_POINTS:
            if not extent or extent[2] <= extent[0] or extent[3] <= extent[1]:
                return overview()
            # Keep magnified portions of very long or complex features addressable by original FID.
            # Editing reloads full geometries by FID before any change can be made.
            frame = frame.set_geometry(shapely.clip_by_rect(frame.geometry.array, *extent))
            if int(shapely.get_num_coordinates(frame.geometry.array).sum()) > MAX_MAP_POINTS:
                return overview()
            clipped = True
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
    return {"features": items, "limited": False, "bounds": display_bounds, "clipped": clipped}


def request(workspace, payload):
    action = payload["action"]
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
        selection = {"layer": origin["layer"], "revision": origin["revision"], "fids": payload["fids"]}
        record = successful(run_operation(workspace.root, {"operation": "edit.transfer", "inputs": [origin, destination],
            "parameters": {"selection": selection, "mode": payload["mode"]}, "idempotency_key": uuid4().hex}))
        return output_layers(workspace, record, "粘贴要素")
    if action == "workflow-layers":
        return workflow_layers(workspace, successful(workspace.read(payload["run"])))
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
    if action in {"crs-search", "crs-projection-options"}:
        from guizhi.infrastructure.crs_options import coordinate_options, projection_options

        query = str(payload.get("query", ""))
        if action == "crs-search":
            return coordinate_options(query)
        ref = payload["ref"]
        layer = raster_layer(workspace, ref, None) if ref.get("artifact") == "raster_preview" else describe(workspace, ref)
        return projection_options(workspace, layer, query)
    if action == "material":
        item = map_material(workspace, payload["ref"])
        name = item["layer"]
        return {**item, "layers": [name] if name else [], "kind": "vector" if name else "raster",
                "name": payload.get("name", name)}
    if action == "export":
        return export_layer(workspace, payload)
    file, name = source(workspace, payload["ref"])
    if action == "edit":
        if material(file, name)["revision"] != payload.get("revision"):
            raise ValueError("图层版本已变化，请重新打开图层")
        return produce(workspace, "edit.changes", file, name, {"changes": payload["changes"]})
    if action == "selection":
        return {**selection_material(workspace, payload["ref"], payload["revision"], payload["fids"]),
                "name": payload.get("name", name)}
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
        from guizhi.infrastructure.operations import raster_backend as raster

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
        if extension in {"zip", "shp"}:
            return package_layer(workspace, file, output, name)
        if extension == "csv":
            record = successful(run_operation(workspace.root, {"operation": "presentation.export_table",
                "inputs": [material(file, name)], "parameters": {"fields": payload.get("fields")}}))
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
