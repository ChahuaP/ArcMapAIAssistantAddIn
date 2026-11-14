"""Polygon control overlay with union-based area accounting."""
from collections import Counter
from hashlib import sha256
from dataclasses import asdict
from math import isclose
from pathlib import Path
import json
from uuid import uuid4

import geopandas
import pyogrio
import shapely
from shapely.geometry import mapping

from guizhi.domain.contracts import ClaimRevision, EvidenceRef, ObjectRevision
from guizhi.infrastructure.cache import fingerprint, implementation, read_entry, write_entry
from guizhi.infrastructure.vector import digest, import_layer
from guizhi.infrastructure.workspace import Workspace


def classify(project, boundary):
    hit = project.intersection(boundary)
    if hit.is_empty:
        return "disjoint", hit
    if hit.area > 0:
        return ("inside" if boundary.covers(project) else "overlap"), hit
    return ("touch_line" if shapely.get_dimensions(hit) == 1 else "touch_point"), hit


def polygons(frame, label):
    geom = frame.geometry
    if (frame.empty or not geom.geom_type.isin(["Polygon", "MultiPolygon"]).all()
            or not geom.is_valid.all() or geom.is_empty.any()
            or shapely.has_z(geom).any()):
        raise ValueError(f"{label} requires non-empty, valid 2D polygons")
    return geom


def parcel_intersections(workspace, parcels, boundaries, crs, check):
    shapes = list(boundaries)
    index = shapely.STRtree(shapes)
    control_keys = [sha256(shapely.to_wkb(shapely.normalize(shape))).hexdigest() for shape in shapes]
    revision = implementation("control_check")
    for parcel in parcels:
        check()
        neighbors = index.query(parcel, predicate="intersects")
        key = fingerprint([revision, crs.to_wkt(), shapely.to_wkb(shapely.normalize(parcel), hex=True),
                           sorted(control_keys[i] for i in neighbors)])
        saved = read_entry(workspace, "overlay-parcel", key)
        if saved:
            yield saved["relation"], shapely.from_wkb(saved["geometry"]), True
        else:
            relation, hit = classify(parcel, shapely.union_all([shapes[i] for i in neighbors]))
            write_entry(workspace, "overlay-parcel", key, {"relation": relation, "geometry": shapely.to_wkb(hit, hex=True)})
            yield relation, hit, False


def map_payload(project, coverage, crs):
    features = [{"role": "project", "geometry": mapping(project)}]
    if not coverage.is_empty:
        features.append({"role": "coverage", "geometry": mapping(coverage)})
    return {"crs": crs.to_string(), "bounds": project.bounds, "features": features}


def write_report(path, stats, rows, crs):
    names = {"inside": "完全覆盖", "overlap": "部分重叠", "touch_line": "线接触",
             "touch_point": "点接触", "disjoint": "不相交"}
    path.write_text(json.dumps({
        "title": "管控线核查", "note": f"分析坐标系：{crs.name}。面积按合并后的项目范围和管控范围计算；点、线接触单独计数。",
        "tables": [
            {"title": "核查摘要", "columns": ["核查指标", "核查结果"], "rows": [
                ["项目面积", f"{stats['project_area_m2']:,.2f} 平方米"],
                ["重叠面积", f"{stats['covered_area_m2']:,.2f} 平方米"],
                ["覆盖率", f"{stats['coverage_ratio']:.2%}"],
                *[[label, stats["relations"].get(key, 0)] for key, label in names.items()]]},
            {"title": "地块明细", "columns": ["源要素编号", "空间关系", "重叠面积（平方米）"], "rows": [
                [row["source_fid"], names[row["relation"]], f"{row['overlap_m2']:,.2f}"] for row in rows]}
        ]}, ensure_ascii=False), encoding="utf-8")


def check_control(task: dict, job: dict, directory: Path, progress, ensure_supervised, workspace_root: Path) -> dict:
    progress("import_started")
    project, _, project_rev, project_ref, project_sources = import_layer(
        task["source"], task["layer"], directory, "input.gpkg", job["id"], ensure_supervised,
    )
    control, _, control_rev, control_ref, control_sources = import_layer(
        task["control_source"], task["control_layer"], directory, "control.gpkg", job["id"], ensure_supervised,
    )
    progress("imported", datasets=[asdict(project_rev), asdict(control_rev)])
    evidence = [project_ref, control_ref, *project_sources, *control_sources]

    def transform_role(frame, role, revision):
        plan = task.get(role + "_plan")
        if plan is None:
            return frame
        from guizhi.infrastructure.coordinates import apply_plan

        converted = apply_plan(frame, plan, workspace_root)
        file = directory / "artifacts" / (role + "-crs.json")
        file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        evidence.append(EvidenceRef(uuid4().hex, revision.id, job["id"], "transform",
                                    file.relative_to(directory).as_posix(), digest(file)))
        progress("transformed", role=role, plan_id=plan["id"], revision=plan["revision"])
        return converted

    progress("coordinates")
    project = transform_role(project, "project", project_rev)
    control = transform_role(control, "control", control_rev)
    crs = project.crs
    if crs != control.crs:
        raise ValueError("Project and control CRS differ; select a verified transformation")
    if not crs.is_projected or len(crs.axis_info) != 2 or any(
            not isclose(axis.unit_conversion_factor, 1.0, abs_tol=1e-12) for axis in crs.axis_info):
        raise ValueError("Area analysis requires a projected CRS with metre axes")
    progress("overlay_started")
    project_geom, control_geom = polygons(project, "Project"), polygons(control, "Control")
    project_union = shapely.union_all(project_geom)
    control_union = shapely.union_all(control_geom)
    rows, intersections = [], []
    workspace = Workspace(workspace_root)
    reused = 0
    for feature, (relation, hit, from_cache) in zip(
            project.index, parcel_intersections(workspace, project_geom, control_geom, crs, ensure_supervised), strict=True):
        reused += from_cache
        rows.append({"source_fid": int(feature), "relation": relation, "overlap_m2": hit.area})
        intersections.append(hit)
    coverage = project_union.intersection(control_union)
    area, project_area = coverage.area, project_union.area
    if not isclose(area, shapely.union_all(intersections).area, abs_tol=0.0001, rel_tol=1e-9):
        raise ValueError("Independent intersection aggregation disagrees with total coverage")
    if not project_area > 0 or area > project_area + 0.0001:
        raise ValueError("Coverage exceeds the project area")
    stats = {"project_area_m2": project_area, "covered_area_m2": area,
             "coverage_ratio": min(1.0, area / project_area),
             "relations": dict(Counter(row["relation"] for row in rows))}
    progress("overlaid", **stats)
    progress("export_started")
    artifacts = directory / "artifacts"
    outputs = []
    checks = {"coverage_union_verified": True}
    if task.get("output_layer"):
        result = artifacts / "result.gpkg"
        frame = geopandas.GeoDataFrame(rows, geometry=project_geom.to_list(), crs=crs)
        pyogrio.write_dataframe(frame, result, layer="control_check", driver="GPKG")
        reread = pyogrio.read_dataframe(result, layer="control_check")
        if (reread.crs != crs or reread.drop(columns="geometry").to_dict("records") != rows
                or shapely.to_wkb(reread.geometry).tolist() != shapely.to_wkb(frame.geometry).tolist()):
            raise ValueError("Control export does not match the verified overlay")
        outputs.append(("export", result))
        checks["export_verified"] = True
    report = artifacts / "report.json"
    write_report(report, stats, rows, crs)
    map_file = artifacts / "map.json"
    map_file.write_text(json.dumps(map_payload(project_union, coverage, crs)), encoding="utf-8")
    for step, path in (*outputs, ("report", report), ("map", map_file)):
        evidence.append(EvidenceRef(uuid4().hex, project_rev.id, job["id"], step,
                                    path.relative_to(directory).as_posix(), digest(path)))
    obj = ObjectRevision(uuid4().hex, task["object_id"], project_rev.id)
    claim = ClaimRevision(uuid4().hex, obj.id, job["id"], "covered_area", area,
                          "m²", tuple(ref.id for ref in evidence))
    progress("export_verified", **checks)
    return {"dataset_revision": asdict(project_rev), "control_revision": asdict(control_rev),
            "object_revision": asdict(obj), "claim_revision": asdict(claim),
            "evidence": [asdict(ref) for ref in evidence], "statistics": stats,
            "validation": checks, **({"export_layer": "control_check"} if outputs else {}),
            "analysis_crs_wkt": crs.to_wkt(),
            "execution": {"reused_parcels": reused, "computed_parcels": len(rows) - reused, "aggregate_recomputed": True},
            "runtime": {"gdal": pyogrio.__gdal_version_string__, "geos": shapely.geos_version_string}}
