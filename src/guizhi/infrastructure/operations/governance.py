"""Publish an explanation from verified case facts and applicable rule references."""
import shutil
from datetime import date
from math import isfinite

import geopandas as gpd
import pandas as pd
import shapely

from guizhi.domain.semantics import code, normalized
from guizhi.infrastructure.control import map_payload
from guizhi.infrastructure.operations.common import compatible, field, valid
from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.operations.knowledge import citation
from guizhi.infrastructure.operations.semantics import artifact_json, case_report, key
from guizhi.infrastructure.operations.report import publish_report


def measure(ctx):
    crs = compatible(ctx.frames)
    if not crs.is_projected or any(axis.unit_conversion_factor != 1 for axis in crs.axis_info[:2]):
        raise ValueError("Use a common metre-based analysis CRS")
    for frame in ctx.frames:
        valid(frame, polygon=True)
        if frame.empty or frame.geometry.is_empty.any():
            raise ValueError("Provide nonempty polygon coverage")
    return crs


def farmland_balance(ctx, quality_field: str, minimum_quality: float, replacement_ratio: float, basis: str):
    crs = measure(ctx)
    if not basis.strip() or not isfinite(minimum_quality) or not isfinite(replacement_ratio) or replacement_ratio <= 0:
        raise ValueError("Provide the rule source, finite quality threshold and positive replacement ratio")
    removed, added, quality = ctx.frames
    if not shapely.coverage_is_valid(quality.geometry.array):
        raise ValueError("Quality polygons overlap or have inconsistent shared edges")
    grades = pd.to_numeric(field(quality, quality_field), errors="raise")
    if not all(isfinite(value) for value in grades):
        raise ValueError("Quality grades must be finite numeric values")
    loss, replacement = (shapely.union_all(frame.geometry.array) for frame in (removed, added))
    if loss.intersection(replacement).area > 0:
        raise ValueError("Removed and replacement polygons overlap")
    covered = replacement.intersection(shapely.union_all(quality.geometry.array))
    qualified = replacement.intersection(shapely.union_all(quality[grades >= minimum_quality].geometry.array))
    required = loss.area * replacement_ratio
    stats = {"removed_area_m2": loss.area, "replacement_area_m2": replacement.area,
             "qualified_area_m2": qualified.area, "required_area_m2": required,
             "unrated_area_m2": max(0, replacement.area - covered.area),
             "connected_parts": int(shapely.get_num_geometries(replacement)),
             "meets_declared_ratio": qualified.area >= required}
    ctx.json("balance.json", {"basis": basis, "minimum_quality": minimum_quality,
                              "replacement_ratio": replacement_ratio, "measurements": stats}, "balance")
    ctx.json("map.json", map_payload(replacement, qualified, crs), "map")
    out = gpd.GeoDataFrame({"part": ["调出", "补划"] + ([] if qualified.is_empty else ["符合质量阈值"])},
                          geometry=[loss, replacement] + ([] if qualified.is_empty else [qualified]), crs=crs)
    ctx.publish(out)
    publish_report(ctx, "永农调整与占补平衡", ("指标", "面积（m²）"),
                   [("调出", loss.area), ("补划", replacement.area), ("达到质量阈值", qualified.area),
                    ("缺少质量资料", stats["unrated_area_m2"]), ("配置要求", required)],
                   measure=2, note=f"计算依据：{basis}。结论限定于本次配置的比例、质量阈值和输入资料。")
    return {"statistics": stats}


def permit_history(ctx, as_of: date, intended_use: str, approved_status: str, approval_source: str,
                   history_run: str):
    crs = measure(ctx)
    if not all(value.strip() for value in (intended_use, approved_status, approval_source)):
        raise ValueError("Provide the target use, accepted status and permit source")
    history = ctx.workspace.read(history_run)
    if history["task"]["operation"] != "annual.compare":
        raise ValueError("Provide a verified annual land transition result")
    matrix = artifact_json(ctx.workspace, history_run, "table")
    transition_file = read_artifact(ctx.workspace, history_run, "export")
    transition = gpd.read_file(transition_file, layer="result", engine="pyogrio")
    project, permits = ctx.frames
    focus = shapely.union_all(project.geometry.array)
    if transition.crs != crs or any(geometry.difference(focus).area > 1e-7 for geometry in transition.geometry):
        raise ValueError("Annual changes must first be clipped to the project scope")
    required = ("permit_id", "status", "allowed_use", "valid_from", "valid_to")
    for name in required:
        values = field(permits, name)
        if values.isna().any() or not values.astype(str).str.strip().all():
            raise ValueError(f"Permit field requires values: {name}")
    if not permits["permit_id"].is_unique:
        raise ValueError("Permit identifiers must be unique")
    rows = []
    eligible = []
    for permit in permits.itertuples():
        ctx.check()
        start, end = date.fromisoformat(str(permit.valid_from)), date.fromisoformat(str(permit.valid_to))
        if start > end:
            raise ValueError("Permit validity start must precede end")
        area = permit.geometry.intersection(focus)
        applies = start <= as_of <= end and permit.status == approved_status and permit.allowed_use == intended_use
        if applies:
            eligible.append(area)
        rows.append((str(permit.permit_id), str(permit.status), "适用" if applies else "不适用", area.area))
    coverage = shapely.union_all(eligible)
    uncovered = focus.difference(coverage)
    stats = {"covered_area_m2": coverage.area, "uncovered_area_m2": uncovered.area,
             "project_area_m2": focus.area, "history_common_coverage_m2": matrix["common_coverage_m2"],
             "coverage_ratio": coverage.area / focus.area}
    ctx.json("permit-history.json", {"source": approval_source, "as_of": as_of.isoformat(),
                                     "use": intended_use, "history_run": history_run, "history": matrix,
                                     "measurements": stats}, "permit_history")
    ctx.json("map.json", map_payload(focus, coverage, crs), "map")
    publish_report(ctx, "用地手续与地类历史", ("手续编号", "原状态", "适用状态", "与项目交叠（m²）"),
                   rows, measure=4, note=f"按 {as_of} 的 {intended_use} 用途核查。"
                   f"已覆盖 {coverage.area:.2f} m²，现有材料未覆盖 {uncovered.area:.2f} m²。依据：{approval_source}。")
    return {"statistics": stats}


def explain_findings(ctx, case_run: str, rules_run: str, title: str = "审批核查与适用依据"):
    case = artifact_json(ctx.workspace, case_run, "case")
    result = artifact_json(ctx.workspace, rules_run, "rule_resolution")
    project = case["project"]
    if normalized(project["region"]) != normalized(result["region"]):
        raise ValueError("Case and rule resolution belong to different regions")
    if result["project_code"] and code(result["project_code"]) != code(project["code"]):
        raise ValueError("Case and rules belong to different project identifiers")
    if case["as_of"] and case["as_of"] != result["as_of"]:
        raise ValueError("Case and rule resolution use different dates")
    for claim in case["claims"]:
        for ref in claim["evidence"]:
            read_artifact(ctx.workspace, ref["run"], ref["artifact"])
    for item in result["findings"]:
        rule = item["rule"]
        evidence = citation(ctx, rule["basis"])
        case["claims"].append({"id": key(project["revision"], rule["edition"], result["catalog_revision"]),
            "subject": project["id"], "kind": "explanation", "field": result["matter"], "value": rule["title"],
            "status": "supported" if item["status"] == "applicable" else "conflict", "quote": evidence["quote"],
            "edition": rule["edition"], "as_of": result["as_of"], "qualifier": rule["transition"],
            "evidence": [evidence]})
    if not result["findings"]:
        case["claims"].append({"id": key(project["id"], rules_run, "missing-basis"), "subject": project["id"],
            "kind": "recommendation", "field": "适用依据", "status": "insufficient",
            "value": "补充本地区和日期有效的规则依据", "evidence": []})
    if result["status"] != "applicable" and case["status"] != "conflict":
        case["status"] = "conflict" if result["status"] == "conflict" else "insufficient"
    case["sources"]["rules_run"] = rules_run
    case["rule_resolution"] = result
    ctx.json("case.json", case, "case")
    source = read_artifact(ctx.workspace, case_run, "map")
    output = ctx.path("map.json")
    shutil.copyfile(source, output)
    ctx.record("map", output)
    case_report(ctx, case, title)
    return {"statistics": {"claim_count": len(case["claims"]), "rule_count": len(result["findings"]),
                           "changed_parcels": len(case["parcel_changes"])},
            "case_status": case["status"]}
