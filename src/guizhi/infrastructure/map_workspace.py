"""Read and version the layers used by the desktop map."""
from base64 import b64encode
import json
import math
from pathlib import Path
import re
import sqlite3
from contextlib import closing
from functools import lru_cache
from hashlib import sha256
from shutil import copyfile
from uuid import uuid4

import numpy as np
import pyogrio
import shapely
from pyproj import CRS
from pyproj.enums import TransformDirection

from guizhi.application.diagnostics import contained, failure_summary, save
from guizhi.application.skills import execute as execute_skill
from guizhi.application.tasks import run_operation
from guizhi.infrastructure.cache import reuse_job
from guizhi.infrastructure.crs_catalog import get_plan
from guizhi.infrastructure.crs_options import projection
from guizhi.infrastructure.map_overview import coverage, display_copy, many_in_view
from guizhi.infrastructure.export import package_layer
from guizhi.infrastructure.operations.evidence import artifact_path, artifact_reference, read_artifact
from guizhi.infrastructure.operations.runner import data_revision
from guizhi.infrastructure.vector import digest, layer_geometry, shapefile_projection, source_files as vector_files
from guizhi.infrastructure.workspace import Workspace

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
RASTER_ARTIFACTS = {"raster_source", "raster_preview"}


def raster_signature(file):
    from guizhi.infrastructure.operations.raster import source_files

    return local_signature(source_files(file))


def local_signature(parts):
    result = []
    for part in parts:
        stat = part.stat()
        result.append([str(part), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
    return result


def link_shape(workspace, item):
    file = Path(item["file"]).resolve(strict=True)
    name = item["layer"]
    signature = local_signature(vector_files(file))
    token = sha256(json.dumps([signature, name]).encode()).hexdigest()[:32]
    manifest = contained(workspace.root, "map-shapes", token + ".json")
    if not manifest.is_file():
        save(manifest, {"path": str(file), "layer": name, "signature": signature})
    return describe(workspace, {"run": token, "artifact": "shape_source"}, file, name)


def shape_record(workspace, ref):
    if not isinstance(ref.get("run"), str) or not re.fullmatch(r"[0-9a-f]{32}", ref["run"]):
        raise ValueError("地图图层引用无效")
    manifest = contained(workspace.root, "map-shapes", ref["run"] + ".json")
    entry = json.loads(manifest.read_bytes())
    file = Path(entry["path"])
    if not file.is_file() or local_signature(vector_files(file)) != entry["signature"]:
        raise ValueError("原始 SHP 或配套文件已移动或修改，请重新添加图层")
    return entry, file


def shape_snapshot(workspace, ref):
    entry, file = shape_record(workspace, ref)
    saved = contained(workspace.root, "map-shapes", ref["run"] + ".snapshot.json")
    if saved.is_file():
        return source(workspace, json.loads(saved.read_bytes()))
    record = successful(run_operation(workspace.root, {"operation": "data.describe",
        "inputs": [{"file": str(file), "layer": entry["layer"]}],
        "idempotency_key": "map-shape-" + ref["run"]}))
    snapshot = {"run": record["id"], "artifact": "import"}
    save(saved, snapshot)
    return source(workspace, snapshot)


def shape_display_file(workspace, ref, file, name):
    from xml.sax.saxutils import escape, quoteattr

    prj = next((part for part in vector_files(file) if part.suffix.lower() == ".prj"), None)
    if not prj or prj.read_bytes().isascii():
        return file
    # Read coordinates through GDAL without decoding the GBK WKT in Pyogrio.
    # The map reads the actual CRS separately from the original PRJ.
    view = contained(workspace.root, "map-shapes", ref["run"] + ".vrt")
    if not view.is_file():
        view.write_text(f'<OGRVRTDataSource><OGRVRTLayer name={quoteattr(name)}>'
                        f'<SrcDataSource relativeToVRT="0">{escape(str(file))}</SrcDataSource>'
                        f'<SrcLayer>{escape(name)}</SrcLayer><LayerSRS>NULL</LayerSRS>'
                        '</OGRVRTLayer></OGRVRTDataSource>', encoding="utf-8")
    return view


def map_revision(ref, material):
    return ref["run"] if ref["artifact"] == "shape_source" else material["revision"]


def link_raster(workspace, file, name):
    file = Path(file).resolve(strict=True)
    signature = raster_signature(file)
    identifier = sha256(json.dumps(signature).encode()).hexdigest()[:32]
    entry = contained(workspace.root, "map-rasters", identifier + ".json")
    if not entry.is_file():
        save(entry, signature)
    return raster_layer(workspace, {"run": identifier, "artifact": "raster_source"}, name)


def raster_file(workspace, ref):
    if ref["artifact"] == "raster_preview":
        file, checksum = artifact_reference(workspace, ref["run"], "raster-1")
        stat = file.stat()
        if raster_digest(file, stat.st_size, stat.st_mtime_ns) != checksum:
            raise ValueError("影像成果已移动或修改")
        return file
    if ref["artifact"] != "raster_source" or not re.fullmatch(r"[0-9a-f]{32}", ref["run"]):
        raise ValueError("影像引用无效")
    entry = contained(workspace.root, "map-rasters", ref["run"] + ".json")
    signature = json.loads(entry.read_bytes())
    file = Path(signature[0][0])
    if not file.is_file() or raster_signature(file) != signature:
        raise ValueError("原始影像或配套文件已移动或修改，请重新添加图层")
    return file


@lru_cache(maxsize=64)
def raster_digest(file, size, modified):
    return digest(file)


def raster_layer(workspace, ref, name):
    if ref["artifact"] == "raster_source":
        from guizhi.infrastructure.operations.map_raster import image_preview

        file = raster_file(workspace, ref)
        _, info = image_preview(file, metadata_only=True)
        return {**info, "kind": "raster", "ref": ref, "name": name, "sourceName": str(file)}
    info = workspace.read(ref["run"])["result"]["raster_preview"]
    source_path = workspace.read(ref["run"])["task"]["request"]["inputs"][0]["file"]
    return {**info, "kind": "raster", "ref": ref, "name": name, "sourceName": source_path}


def map_material(workspace, ref):
    if not isinstance(ref, dict) or set(ref) != {"run", "artifact"}:
        raise ValueError("请选择已保存的地图图层")
    if ref["artifact"] in RASTER_ARTIFACTS:
        if ref["artifact"] == "raster_source":
            from guizhi.infrastructure.vector import source_revision

            file = raster_file(workspace, ref)
            parts = [Path(part[0]) for part in raster_signature(file)]
            stat = file.stat()
            revision = source_revision(parts) if len(parts) > 1 else raster_digest(file, stat.st_size, stat.st_mtime_ns)
            return {"file": str(file), "layer": None, "revision": revision}
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
            result.append(link_raster(workspace, file, title + " · 影像"))
    return result


def selection_material(workspace, ref, revision, fids):
    file, name = source(workspace, ref)
    item = material(file, name)
    if map_revision(ref, item) != revision:
        raise ValueError("图层版本已变化，请重新选择要素")
    if not isinstance(fids, list) or not fids or any(type(fid) is not int for fid in fids):
        raise ValueError("请选择有效的地图要素")
    selection = {"layer": name, "revision": item["revision"], "fids": sorted(set(fids))}
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
            edited = artifact == "export" and "target_revision" in change
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
        revision = material(file, name)["revision"]
        previous = parameters.get("previous")
        if previous is not None and (not isinstance(previous, dict) or previous.get("revision") != revision):
            raise ValueError("图层版本已变化，请重新选择要素")
        fids = filter_ids(file, name, parameters["where"], mode=parameters.get("mode", "new_selection"),
                          previous=previous["fids"] if previous is not None else None)
        return {"layers": [], "selection": {"layer": name, "revision": revision,
                                             "fids": fids}, "statistics": {"selected_count": len(fids)}}
    items = [map_material(workspace, ref) for ref in refs]
    if operation in {"data.export_selection", "geometry.multipart", "geometry.explode"}:
        fids = parameters.pop("fids", None)
        if len(items) != 1 or not isinstance(fids, list) or not fids or any(type(fid) is not int for fid in fids):
            raise ValueError("请选择有效的地图要素")
        parameters["selection"] = {"layer": items[0]["layer"], "revision": items[0]["revision"], "fids": fids}
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
    if kind == "shape_source":
        return shape_snapshot(workspace, ref)
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
    linked = ref["artifact"] == "shape_source"
    if file is None:
        if linked:
            entry, file = shape_record(workspace, ref)
            name = entry["layer"]
        else:
            file, name = source(workspace, ref)
    projection = shapefile_projection(vector_files(file)) if linked else None
    display_file = shape_display_file(workspace, ref, file, name) if linked else file
    info = pyogrio.read_info(display_file, layer=name, force_total_bounds=True)
    name = info["layer_name"]
    crs = CRS(info["crs"]) if info["crs"] else None
    if crs and crs.to_epsg() in {0, 404000}:
        crs = None
    original = str(file)
    source_crs_name = None
    source_parts = []
    if linked:
        crs = projection
    if ref["artifact"] == "import":
        details = workspace.read(ref["run"])["result"]
        original = (details.get("datasets") or [{}])[0].get("source") or original
        crs = CRS(details["crs"]) if details["crs"] else None
        source_crs_name = details.get("crs_name")
        source_parts = [item["sha256"] for item in details["evidence"] if "-source-" in item["step"]]
    stat = file.stat()
    revision = ref["run"] if linked else interactive_revision(file, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)[1]
    fid_field = info["fid_column"]
    if linked:
        fid_field = "fid"
        names = {field.casefold() for field in info["fields"]}
        while fid_field in names:
            fid_field += "_"
    return {"ref": ref, "name": name, "sourceName": original, "revision": revision, "sourceRevision": ":".join(source_parts) or revision, "geometry": layer_geometry(info, file),
            "singlePoint": info["geometry_type"].split(" ")[0] == "Point",
            "count": info["features"], "fidField": fid_field, "bounds": numbers(info["total_bounds"]),
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


@lru_cache(maxsize=64)
def feature_metadata(root, run, artifact, file, name, revision):
    return describe(Workspace(root), {"run": run, "artifact": artifact}, file, name)


@lru_cache(maxsize=32)
def display_transform(current, target, extent):
    return projection(current, target, extent)


def overview_window(extent):
    """Group neighboring viewports into reusable, padded overview windows."""
    west, south, east, north = extent
    span = max(east - west, north - south)
    if span <= 0:
        return extent
    grid = math.ldexp(1.0, math.frexp(span)[1] - 3)
    return (math.floor(west / grid) * grid, math.floor(south / grid) * grid,
            math.ceil(east / grid) * grid, math.ceil(north / grid) * grid)


def features(workspace, payload):
    ref = payload["ref"]
    linked = ref["artifact"] == "shape_source"
    if linked and not payload.get("plan"):
        entry, file = shape_record(workspace, ref)
        name, revision = entry["layer"], ref["run"]
    else:
        file, name = source(workspace, ref)
        stat = file.stat()
        revision = interactive_revision(file, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)[1]
        if linked:
            ref = {"run": json.loads(contained(workspace.root, "map-shapes", ref["run"] + ".snapshot.json").read_bytes())["run"],
                   "artifact": "import"}
    info = feature_metadata(workspace.root, ref["run"], ref["artifact"], file, name, revision)
    if linked and ref["artifact"] == "shape_source":
        file = shape_display_file(workspace, ref, file, name)
    interactive = payload.get("interactive") is True
    outlines = payload.get("outlines") is True
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
    tx = display_transform(current, target, tuple(info["bounds"]) if info["bounds"] else None) if convert else None
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
                if interactive:
                    window = overview_window(window)
        where = None
        if highlight:
            from guizhi.infrastructure.operations.table import identifier
            where = ("FID" if linked and not plan else identifier(info["fidField"])) + " IN (" + ",".join(map(str, highlight)) + ")"
        revision = file.stem if plan else info["revision"]
        response = {"features": [], "limited": True, "bounds": display_bounds}
        if interactive:
            overview_key = sha256(json.dumps([name, revision, full, window, payload.get("target"), where, outlines]).encode()).hexdigest()
            response["overviewKey"] = overview_key
            if payload.get("knownOverview") == overview_key:
                return response
        shapes = coverage(file, name, revision, full, window, workspace.root / "cache", where, outlines)
        if tx and shapes:
            shapes = shapely.transform(np.asarray(shapes, dtype=object), lambda x, y: tx.transform(x, y, errcheck=True),
                                       interleaved=False)
        return {**response, "overview": [shapely.geometry.mapping(shape) for shape in shapes]}

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
    result = {"limited": False, "bounds": display_bounds, "clipped": clipped}
    if interactive and fids is None:
        # Use GEOS' native writer and a compact payload for the interactive renderer.
        geometries = shapely.to_wkb(frame.geometry.array, flavor="iso")
        items = [{"id": int(fid), "wkb": b64encode(data).decode("ascii"), "properties": attrs}
                 for fid, data, attrs, empty in zip(frame.index, geometries, attributes, frame.geometry.is_empty, strict=True)
                 if data is not None and not empty]
    else:
        items = [{"id": int(fid), "geometry": shapely.geometry.mapping(shape), "properties": attrs}
                 for (fid, shape), attrs in zip(frame.geometry.items(), attributes, strict=True)
                 if shape is not None and not shape.is_empty]
    return {**result, "features": items}


def request(workspace, payload):
    action = payload["action"]
    if action in {"mxd-read", "mxd-import", "mxd-write"}:
        from guizhi.infrastructure import map_document

        return {"mxd-read": map_document.read_document, "mxd-import": map_document.import_frame,
                "mxd-write": map_document.write_document}[action](workspace, payload)
    if action == "xy-fields":
        from guizhi.infrastructure.operations.xy import table_headers

        return table_headers(payload["file"])
    if action == "xy-import":
        file = Path(payload["file"])
        record = successful(run_operation(workspace.root, {"operation": "xy.points",
            "inputs": [{"file": str(file), "layer": None, "revision": None}],
            "parameters": payload["parameters"]}))
        return output_layers(workspace, record, file.stem)
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
        if map_revision(payload["ref"], origin) != payload["revision"] or \
                map_revision(payload["destination"], destination) != payload["destinationRevision"]:
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
            if Path(item["file"]).suffix.lower() == ".shp":
                result.append(link_shape(workspace, item))
                continue
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
            result.append(link_raster(workspace, item["file"], item["name"]))
        return result
    if action == "raster-display":
        from guizhi.infrastructure.operations.map_raster import image_preview

        file = raster_file(workspace, payload["ref"])
        image, info = image_preview(file, payload.get("target"), payload.get("bands"), payload.get("bbox"))
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
        layer = raster_layer(workspace, ref, None) if ref.get("artifact") in RASTER_ARTIFACTS else describe(workspace, ref)
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
        if map_revision(payload["ref"], material(file, name)) != payload.get("revision"):
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
    if ref["artifact"] in RASTER_ARTIFACTS:
        from guizhi.infrastructure.operations import raster_backend as raster

        file = raster_file(workspace, ref)
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
