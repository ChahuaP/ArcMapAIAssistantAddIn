"""Measure land-class areas and transitions on a common, non-overlapping survey grid."""
from collections import defaultdict
from math import fsum

import geopandas as gpd
import shapely


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
        raise ValueError(f"Unmapped land classes: {', '.join(missing[:20])}")
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
    return {"statistics": {"row_count": len(rows), "year_count": len(years)}}


def compare(ctx, from_year: int, to_year: int, field: str, classifications: dict[int, dict[str, str]]):
    if not 1 <= from_year < to_year <= 9999 or set(classifications) != {from_year, to_year}:
        raise ValueError("Specify both years and their mappings in chronological order")
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
    return {"statistics": {"row_count": len(rows), "common_coverage_m2": common,
                            "changed_area_m2": fsum(row["area_m2"] for row in rows
                                                    if row["from_class"] != row["to_class"])}}
