"""Measure land-class areas and transitions on a common, non-overlapping survey grid."""
from collections import defaultdict
import json
from math import fsum

import geopandas as gpd
import shapely
from guizhi.domain.diagnostics import Fault

from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.operations.report import publish_report


def classified(frame, field, codes):
    if (frame.crs is None or not frame.crs.is_projected or
            any(axis.unit_conversion_factor != 1 for axis in frame.crs.axis_info[:2])):
        raise ValueError("Annual area measurements require a projected metre-based CRS")
    if frame.empty or not frame.geom_type.isin(["Polygon", "MultiPolygon"]).all() or (
            ~frame.is_valid | frame.is_empty).any():
        raise ValueError("Annual input requires valid, nonempty polygon coverage")
    if not shapely.coverage_is_valid(frame.geometry.array):
        raise ValueError("Annual polygon coverage has overlaps or inconsistent shared edges")
    if field not in frame or frame[field].isna().any() or not codes or any(not code.strip() for code in codes.values()):
        raise ValueError("A complete classification field and mapping are required")
    series = frame[field].astype(str)
    missing = sorted(set(series) - codes.keys())
    if missing:
        raise Fault("parameter", "unmapped_land_classes", f"Unmapped land classes: {', '.join(missing[:20])}",
                    unmapped_count=len(missing))
    return gpd.GeoDataFrame({"land_class": series.map(codes)}, geometry=frame.geometry, crs=frame.crs)


def summarize(ctx, years: list[int], field: str, classifications: dict[int, dict[str, str]]):
    if (len(years) != len(ctx.frames) or len(set(years)) != len(years) or set(years) != classifications.keys()
            or any(not 1 <= year <= 9999 for year in years)):
        raise ValueError("Supply one input and classification mapping per explicit, distinct year")
    rows = []
    for year, source in sorted(zip(years, ctx.frames, strict=True), key=lambda item: item[0]):
        ctx.check()
        frame = classified(source, field, classifications[year])
        for name, group in frame.groupby("land_class", sort=True):
            rows.append({"year": year, "land_class": name, "area_m2": fsum(group.area)})
    ctx.json("land-classes.json", {"unit": "m²", "rows": rows}, "table")
    return {"source_revisions": ctx.selection_hashes,
            "statistics": {"row_count": len(rows), "year_count": len(years)}}


def compare(ctx, from_year: int, to_year: int, field: str, classifications: dict[int, dict[str, str]]):
    if not 1 <= from_year < to_year <= 9999 or set(classifications) != {from_year, to_year}:
        raise Fault("parameter", "annual_chronology", "Specify both years and their mappings in chronological order",
                    from_year=from_year, to_year=to_year)
    left, right = (classified(frame, field, classifications[year]) for frame, year in
                   zip(ctx.frames, (from_year, to_year), strict=True))
    if left.crs != right.crs:
        raise ValueError("Annual coverage must use the same analysis CRS")
    ctx.check()
    transitions = gpd.overlay(left.rename(columns={"land_class": "from_class"}),
                              right.rename(columns={"land_class": "to_class"}),
                              how="intersection", keep_geom_type=True, make_valid=False)
    if transitions.empty:
        raise ValueError("The two years have no common polygon coverage")
    matrix = defaultdict(list)
    for before, after, area in zip(transitions.from_class, transitions.to_class, transitions.area, strict=True):
        ctx.check()
        matrix[before, after].append(area)
    rows = [{"from_class": before, "to_class": after, "area_m2": fsum(areas)}
            for (before, after), areas in sorted(matrix.items())]
    common = fsum(row["area_m2"] for row in rows)
    ctx.json("transitions.json", {"from_year": from_year, "to_year": to_year, "unit": "m²",
                                  "common_coverage_m2": common, "only_from_m2": max(0, fsum(left.area) - common),
                                  "only_to_m2": max(0, fsum(right.area) - common), "rows": rows}, "table")
    ctx.publish(transitions)
    return {"source_revisions": ctx.selection_hashes,
            "statistics": {"row_count": len(rows), "common_coverage_m2": common,
                            "changed_area_m2": fsum(row["area_m2"] for row in rows
                                                    if row["from_class"] != row["to_class"])}}


def report(ctx, summary_run: str, changes_run: str):
    records = [ctx.workspace.read(run) for run in (summary_run, changes_run)]
    if [item["task"]["operation"] for item in records] != ["annual.summarize", "annual.compare"]:
        raise ValueError("Supply verified annual summaries and transitions")
    left, right = (record["task"]["request"]["parameters"] for record in records)
    if (left["years"] != [right["from_year"], right["to_year"]] or
            any(left[key] != right[key] for key in ("field", "classifications")) or
            records[0]["result"]["source_revisions"] != records[1]["result"]["source_revisions"]):
        raise ValueError("Annual summary and transitions must share input revisions and classification rules")
    summary = json.loads(read_artifact(ctx.workspace, summary_run, "table").read_text(encoding="utf-8"))
    changes = json.loads(read_artifact(ctx.workspace, changes_run, "table").read_text(encoding="utf-8"))
    years = {item["year"] for item in summary["rows"]}
    if years != {changes["from_year"], changes["to_year"]}:
        raise ValueError("Annual summary and change periods differ")
    rows = [(str(item["year"]) + " " + item["land_class"], "年度面积", item["area_m2"]) for item in summary["rows"]]
    rows += [(item["from_class"] + " → " + item["to_class"], "共同覆盖内变化", item["area_m2"])
             for item in changes["rows"]]
    publish_report(ctx, "年度地类面积与变化", ("类别", "统计口径", "面积（m²）"), rows, measure=3,
                   note=f"共同覆盖 {changes['common_coverage_m2']:.2f} m²。单年覆盖以原有资料为准。")
    return {"statistics": {"row_count": len(rows), "year_count": len(years)}}
