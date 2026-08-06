from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import geopandas as gpd
import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union


SEED = 20260730
CRS = "EPSG:32650"
VERSION = "synthetic-city-v1"
CITY_BOUNDS = (670000.0, 3538000.0, 690000.0, 3558000.0)
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "experiments" / "data" / VERSION


def frame(records: Sequence[Mapping[str, Any]], geometries: Sequence[Any]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(list(records), geometry=list(geometries), crs=CRS)


def intersects_closed_metric_buffer(
    targets: gpd.GeoDataFrame,
    sources: gpd.GeoDataFrame,
    distance: float,
) -> Any:
    """Match ArcGIS INTERSECT against a metric buffer without polygon tessellation error."""
    if distance < 0:
        raise ValueError("Buffer distance cannot be negative")
    source_geometry = unary_union(sources.geometry)
    if source_geometry.is_empty:
        return np.zeros(len(targets), dtype=bool)
    return targets.geometry.distance(source_geometry) <= distance


def district_id(point: Point) -> str:
    x_mid = (CITY_BOUNDS[0] + CITY_BOUNDS[2]) / 2
    y_mid = (CITY_BOUNDS[1] + CITY_BOUNDS[3]) / 2
    if point.x < x_mid and point.y >= y_mid:
        return "D01"
    if point.x >= x_mid and point.y >= y_mid:
        return "D02"
    if point.x < x_mid and point.y < y_mid:
        return "D03"
    return "D04"


def build_districts() -> gpd.GeoDataFrame:
    xmin, ymin, xmax, ymax = CITY_BOUNDS
    xm, ym = (xmin + xmax) / 2, (ymin + ymax) / 2
    geometries = [
        box(xmin, ym, xm, ymax),
        box(xm, ym, xmax, ymax),
        box(xmin, ymin, xm, ym),
        box(xm, ymin, xmax, ym),
    ]
    names = ["Northwest", "Northeast", "Southwest", "Southeast"]
    populations = [82000, 91000, 74000, 88000]
    return frame(
        [
            {"DIST_ID": f"D{index:02d}", "DIST_NM": name, "POP": population}
            for index, (name, population) in enumerate(zip(names, populations), start=1)
        ],
        geometries,
    )


def build_parcels() -> gpd.GeoDataFrame:
    xmin, ymin, _, _ = CITY_BOUNDS
    records: List[Dict[str, Any]] = []
    geometries = []
    plan_cycle = ("RES", "PUBLIC", "COMM", "GREEN", "IND")
    for row in range(10):
        for col in range(10):
            index = row * 10 + col + 1
            geometry = box(xmin + col * 2000, ymin + row * 2000, xmin + (col + 1) * 2000, ymin + (row + 1) * 2000)
            plan_use = plan_cycle[(row + 2 * col) % len(plan_cycle)]
            mismatch = index % 7 == 0 or index in {23, 44, 67, 82}
            actual_use = plan_cycle[(plan_cycle.index(plan_use) + 1) % len(plan_cycle)] if mismatch else plan_use
            records.append(
                {
                    "PARCEL_ID": f"P{index:03d}",
                    "PLAN_USE": plan_use,
                    "ACT_USE": actual_use,
                    "USE_MATCH": "NO" if mismatch else "YES",
                    "PERMIT": "NO" if index % 11 == 0 else "YES",
                    "AREA_HA": 400.0,
                    "DIST_ID": district_id(geometry.centroid),
                }
            )
            geometries.append(geometry)
    return frame(records, geometries)


def build_roads() -> gpd.GeoDataFrame:
    xmin, ymin, xmax, ymax = CITY_BOUNDS
    records: List[Dict[str, Any]] = []
    geometries: List[LineString] = []
    road_index = 1
    classes = ("ARTERIAL", "SECONDARY", "LOCAL")
    for offset in (2000, 5000, 8000, 11000, 14000, 17000, 19000):
        geometries.append(LineString([(xmin + offset, ymin), (xmin + offset, ymax)]))
        records.append(
            {
                "ROAD_ID": f"R{road_index:02d}",
                "ROAD_NM": f"NorthRoad{road_index:02d}",
                "ROAD_CLS": classes[(road_index - 1) % 3],
                "SPEED_KMH": 40 + 10 * ((road_index - 1) % 4),
                "TRAFFIC": 900 + road_index * 170,
                "PASSABLE": "YES",
            }
        )
        road_index += 1
    for offset in (2000, 5000, 8000, 11000, 14000, 17000, 19000):
        geometries.append(LineString([(xmin, ymin + offset), (xmax, ymin + offset)]))
        records.append(
            {
                "ROAD_ID": f"R{road_index:02d}",
                "ROAD_NM": f"EastRoad{road_index:02d}",
                "ROAD_CLS": classes[(road_index - 1) % 3],
                "SPEED_KMH": 40 + 10 * ((road_index - 1) % 4),
                "TRAFFIC": 900 + road_index * 170,
                "PASSABLE": "NO" if road_index in {9, 13} else "YES",
            }
        )
        road_index += 1
    for name, coordinates in (
        ("CentralDiagonal", [(xmin, ymin + 1500), (xmax, ymax - 1500)]),
        ("RingConnector", [(xmin + 1000, ymax - 3000), (xmin + 7000, ymin + 9000), (xmax - 1000, ymin + 3000)]),
    ):
        geometries.append(LineString(coordinates))
        records.append(
            {
                "ROAD_ID": f"R{road_index:02d}",
                "ROAD_NM": name,
                "ROAD_CLS": "ARTERIAL",
                "SPEED_KMH": 70,
                "TRAFFIC": 4200 + road_index * 80,
                "PASSABLE": "YES",
            }
        )
        road_index += 1
    return frame(records, geometries)


def build_accidents(
    roads: gpd.GeoDataFrame,
    rng: np.random.Generator,
    *,
    count: int = 108,
) -> gpd.GeoDataFrame:
    weights = np.array([2, 2, 8, 2, 7, 2, 2, 2, 9, 2, 2, 7, 2, 2, 8, 6], dtype=float)
    weights /= weights.sum()
    choices = rng.choice(len(roads), size=count, p=weights)
    records: List[Dict[str, Any]] = []
    geometries = []
    for index, road_position in enumerate(choices, start=1):
        road = roads.iloc[int(road_position)]
        location = road.geometry.interpolate(float(rng.uniform(0.05, 0.95)), normalized=True)
        records.append(
            {
                "ACC_ID": f"A{index:03d}",
                "ROAD_ID": road["ROAD_ID"],
                "SEVERITY": int(rng.choice([1, 2, 3, 4, 5], p=[0.24, 0.28, 0.24, 0.16, 0.08])),
                "CASUALTY": int(rng.integers(0, 5)),
                "NIGHT": "YES" if rng.random() < 0.42 else "NO",
                "YEAR": 2025,
            }
        )
        geometries.append(location)
    return frame(records, geometries)


def build_communities(rng: np.random.Generator) -> gpd.GeoDataFrame:
    xmin, ymin, _, _ = CITY_BOUNDS
    flood_priority_profiles = {
        "C12": (980, "HIGH"),
        "C17": (1561, "HIGH"),
        "C21": (1220, "HIGH"),
        "C26": (1318, "HIGH"),
        "C30": (2010, "HIGH"),
    }
    records: List[Dict[str, Any]] = []
    geometries = []
    index = 1
    for row in range(6):
        for col in range(6):
            x = xmin + 1800 + col * 3200 + float(rng.uniform(-380, 380))
            y = ymin + 1800 + row * 3200 + float(rng.uniform(-380, 380))
            point = Point(x, y)
            vulnerability = "HIGH" if index % 4 in {0, 1} else ("MEDIUM" if index % 3 else "LOW")
            community_id = f"C{index:02d}"
            population = int(420 + (index * 173) % 1800)
            if community_id in flood_priority_profiles:
                population, vulnerability = flood_priority_profiles[community_id]
            records.append(
                {
                    "COMM_ID": community_id,
                    "COMM_NM": f"Community{index:02d}",
                    "POP": population,
                    "ELDER_RT": round(0.08 + (index % 9) * 0.025, 3),
                    "VULN_LVL": vulnerability,
                    "DIST_ID": district_id(point),
                }
            )
            geometries.append(point)
            index += 1
    return frame(records, geometries)


def ellipse(x: float, y: float, x_radius: float, y_radius: float):
    return affinity.scale(Point(x, y).buffer(1.0, quad_segs=48), xfact=x_radius, yfact=y_radius)


def build_flood_zones() -> gpd.GeoDataFrame:
    definitions = [
        (674200, 3551800, 2600, 1800, 5, 1.8),
        (680200, 3549400, 3000, 1900, 4, 1.2),
        (686000, 3544400, 2400, 2800, 4, 0.9),
        (676000, 3541800, 2200, 1500, 3, 0.6),
        (683200, 3540200, 1800, 2200, 2, 0.4),
        (687500, 3553000, 1400, 1100, 5, 2.1),
        (672500, 3546500, 1100, 1800, 1, 0.2),
        (681500, 3555200, 1600, 900, 3, 0.5),
    ]
    geometries = [ellipse(*item[:4]) for item in definitions]
    records = [
        {
            "FLOOD_ID": f"F{index:02d}",
            "RISK_LVL": risk,
            "DEPTH_M": depth,
            "EVENT_ID": "RAIN_2026_01",
        }
        for index, (*_, risk, depth) in enumerate(definitions, start=1)
    ]
    return frame(records, geometries)


def build_shelters() -> gpd.GeoDataFrame:
    coordinates = [
        (673500, 3551500), (676500, 3550000), (679500, 3551800), (683000, 3549000),
        (686500, 3546500), (688000, 3551500), (672800, 3544500), (676800, 3546200),
        (680500, 3544200), (684000, 3542500), (687200, 3541500), (673800, 3539800),
        (678200, 3539200), (681800, 3539500), (685500, 3539000), (688500, 3539800),
    ]
    records = []
    for index, (x, y) in enumerate(coordinates, start=1):
        point = Point(x, y)
        records.append(
            {
                "SHLT_ID": f"S{index:02d}",
                "SHLT_NM": f"Shelter{index:02d}",
                "CAPACITY": 450 + (index * 275) % 1850,
                "STATUS": "CLOSED" if index in {4, 11, 15} else "OPEN",
                "DIST_ID": district_id(point),
            }
        )
    return frame(records, [Point(*coordinate) for coordinate in coordinates])


def build_hospitals() -> gpd.GeoDataFrame:
    coordinates = [(674500, 3549000), (679500, 3550500), (684500, 3546500), (687000, 3540500), (677000, 3541500)]
    return frame(
        [
            {"HOSP_ID": f"H{index:02d}", "HOSP_NM": f"Hospital{index:02d}", "LEVEL": 3 if index < 3 else 2, "BEDS": 300 + index * 140}
            for index in range(1, len(coordinates) + 1)
        ],
        [Point(*coordinate) for coordinate in coordinates],
    )


def build_schools() -> gpd.GeoDataFrame:
    coordinates = [
        (675200, 3542900), (678100, 3545100), (681200, 3548100), (684000, 3551100),
        (687100, 3546100), (673100, 3550200), (686200, 3540100), (680100, 3539900),
        (688000, 3553400), (676000, 3547600),
    ]
    records = []
    for index, coordinate in enumerate(coordinates, start=1):
        point = Point(*coordinate)
        records.append(
            {
                "SCH_ID": f"SC{index:02d}",
                "SCH_NM": f"School{index:02d}",
                "STUDENTS": 450 + index * 115,
                "DIST_ID": district_id(point),
            }
        )
    return frame(records, [Point(*coordinate) for coordinate in coordinates])


def build_rivers() -> gpd.GeoDataFrame:
    xmin, ymin, xmax, ymax = CITY_BOUNDS
    lines = [
        LineString([(xmin, ymin + 2500), (xmin + 5000, ymin + 6000), (xmin + 10500, ymin + 9200), (xmax, ymin + 12500)]),
        LineString([(xmin + 2500, ymax), (xmin + 7000, ymin + 14500), (xmin + 12000, ymin + 11000), (xmax, ymin + 8000)]),
        LineString([(xmin + 14000, ymin), (xmin + 13500, ymin + 7000), (xmin + 15000, ymin + 14000), (xmin + 17000, ymax)]),
    ]
    return frame(
        [
            {"RIVER_ID": f"RV{index:02d}", "RIVER_NM": name, "GRADE": index}
            for index, name in enumerate(("WestRiver", "CentralRiver", "EastRiver"), start=1)
        ],
        lines,
    )


def build_industry() -> gpd.GeoDataFrame:
    definitions = [
        (673000, 3540500, 5), (679000, 3546500, 4), (685500, 3543500, 5),
        (687000, 3553000, 3), (675500, 3554000, 4), (682500, 3539500, 2),
    ]
    geometries = [box(x - 450, y - 450, x + 450, y + 450) for x, y, _ in definitions]
    return frame(
        [
            {"IND_ID": f"I{index:02d}", "IND_NM": f"Industry{index:02d}", "RISK_LVL": risk, "STATUS": "ACTIVE"}
            for index, (*_, risk) in enumerate(definitions, start=1)
        ],
        geometries,
    )


def build_protected() -> gpd.GeoDataFrame:
    geometries = [
        ellipse(675000, 3552500, 2200, 1400),
        ellipse(686000, 3543000, 1800, 2400),
        box(679000, 3539000, 682000, 3541800),
    ]
    return frame(
        [
            {"PROT_ID": "PT01", "PROT_TYP": "WETLAND", "LEVEL": 1},
            {"PROT_ID": "PT02", "PROT_TYP": "FOREST", "LEVEL": 1},
            {"PROT_ID": "PT03", "PROT_TYP": "WATER", "LEVEL": 2},
        ],
        geometries,
    )


def build_candidate_sites(rng: np.random.Generator) -> gpd.GeoDataFrame:
    xmin, ymin, _, _ = CITY_BOUNDS
    records: List[Dict[str, Any]] = []
    geometries = []
    land_uses = ("PUBLIC", "MIXED", "COMM", "IND")
    for index in range(1, 29):
        col = (index - 1) % 7
        row = (index - 1) // 7
        width = 900 + 120 * (index % 4)
        height = 750 + 150 * ((index + 1) % 4)
        x = xmin + 1100 + col * 2700 + float(rng.uniform(-180, 180))
        y = ymin + 1500 + row * 4400 + float(rng.uniform(-220, 220))
        geometry = box(x, y, x + width, y + height)
        records.append(
            {
                "SITE_ID": f"CS{index:02d}",
                "AREA_HA": round(geometry.area / 10000.0, 2),
                "LAND_USE": land_uses[index % len(land_uses)],
                "COST_IDX": 25 + (index * 13) % 76,
                "OWNER": "PUBLIC" if index % 3 else "PRIVATE",
                "STATUS": "AVAILABLE" if index % 5 else "RESERVED",
            }
        )
        geometries.append(geometry)
    return frame(records, geometries)


def build_construction(parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    parcel_indexes = [2, 6, 13, 19, 22, 29, 34, 41, 43, 50, 57, 61, 66, 72, 78, 81, 87, 91, 94, 98]
    records: List[Dict[str, Any]] = []
    geometries = []
    for index, parcel_position in enumerate(parcel_indexes, start=1):
        parcel = parcels.iloc[parcel_position]
        minx, miny, maxx, maxy = parcel.geometry.bounds
        inset = 280 + (index % 4) * 90
        geometry = box(minx + inset, miny + inset, maxx - inset, maxy - inset)
        permit = "NO" if index % 3 == 0 or index in {2, 11, 17} else "YES"
        status = "STOP" if index in {5, 9, 14, 18} else "ACTIVE"
        records.append(
            {
                "PROJ_ID": f"PJ{index:02d}",
                "PARCEL_ID": parcel["PARCEL_ID"],
                "PERMIT": permit,
                "STATUS": status,
                "FLOOR_A": 6500 + index * 1375,
                "PROJ_TYP": "HOUSING" if index % 2 else "COMMERCIAL",
            }
        )
        geometries.append(geometry)
    return frame(records, geometries)


def build_truth(layers: Mapping[str, gpd.GeoDataFrame]) -> tuple[Dict[str, gpd.GeoDataFrame], Dict[str, List[str]]]:
    flood_high = layers["flood_zones"].loc[layers["flood_zones"]["RISK_LVL"] >= 4].copy()
    high_union = unary_union(flood_high.geometry)
    communities = layers["communities"]
    flood_affected = communities.loc[
        communities.geometry.within(high_union)
        & (communities["POP"] >= 800)
        & (communities["VULN_LVL"] == "HIGH")
    ].copy()
    shelters = layers["shelters"]
    flood_available = shelters.loc[
        intersects_closed_metric_buffer(shelters, flood_affected, 2000)
        & (shelters["STATUS"] == "OPEN")
    ].copy()
    flood_priority = flood_available.loc[flood_available["CAPACITY"] >= 1000].copy()

    sites = layers["candidate_sites"]
    site_attr = sites.loc[
        (sites["AREA_HA"] >= 8)
        & sites["LAND_USE"].isin(["PUBLIC", "MIXED"])
        & (sites["COST_IDX"] <= 60)
        & (sites["STATUS"] == "AVAILABLE")
    ].copy()
    exclusions = unary_union(
        list(layers["schools"].geometry.buffer(500))
        + list(layers["industry"].geometry.buffer(1000))
        + list(layers["rivers"].geometry.buffer(300))
        + list(layers["flood_zones"].geometry)
        + list(layers["protected"].geometry)
    )
    site_safe = site_attr.copy()
    site_safe.geometry = site_safe.geometry.difference(exclusions)
    site_safe = site_safe.loc[~site_safe.geometry.is_empty & (site_safe.geometry.area >= 30000)].copy()
    dense_communities = communities.loc[communities["POP"] >= 1000]
    site_final = site_safe.loc[
        intersects_closed_metric_buffer(site_safe, dense_communities, 1500)
    ].copy()

    construction = layers["construction"]
    land_suspect = construction.loc[(construction["PERMIT"] == "NO") | (construction["STATUS"] == "STOP")].copy()
    protected_union = unary_union(layers["protected"].geometry)
    protected_ids = set(land_suspect.loc[land_suspect.geometry.intersects(protected_union), "PROJ_ID"])
    mismatch_parcels = layers["parcels"].loc[layers["parcels"]["USE_MATCH"] == "NO"].copy()
    mismatch_union = unary_union(mismatch_parcels.geometry)
    priority_ids = protected_ids | set(land_suspect.loc[land_suspect.geometry.intersects(mismatch_union), "PROJ_ID"])
    land_priority = land_suspect.loc[land_suspect["PROJ_ID"].isin(priority_ids)].copy()
    protected_conflict = land_suspect.loc[land_suspect["PROJ_ID"].isin(protected_ids)].copy()
    protected_conflict.geometry = protected_conflict.geometry.intersection(protected_union)

    accident_counts = layers["accidents"].groupby("ROAD_ID").size()
    road_ids = set(accident_counts.loc[accident_counts >= 8].index)
    road_hotspots = layers["roads"].loc[layers["roads"]["ROAD_ID"].isin(road_ids)].copy()
    road_hotspots["ACC_COUNT"] = road_hotspots["ROAD_ID"].map(accident_counts).astype(int)
    road_risk_schools = layers["schools"].loc[
        intersects_closed_metric_buffer(layers["schools"], road_hotspots, 300)
    ].copy()
    road_priority = road_hotspots.loc[
        intersects_closed_metric_buffer(road_hotspots, road_risk_schools, 500)
    ].copy()

    truth_layers = {
        "flood_high": flood_high,
        "flood_affected_comm": flood_affected,
        "flood_available_shelters": flood_available,
        "flood_priority_shelters": flood_priority,
        "site_attr_ok": site_attr,
        "site_safe": site_safe,
        "site_final": site_final,
        "land_suspect_projects": land_suspect,
        "land_protected_conflicts": protected_conflict,
        "land_mismatch_parcels": mismatch_parcels,
        "land_priority_projects": land_priority,
        "road_hotspots": road_hotspots,
        "road_risk_schools": road_risk_schools,
        "road_priority_roads": road_priority,
    }
    id_fields = {
        "flood_high": "FLOOD_ID",
        "flood_affected_comm": "COMM_ID",
        "flood_available_shelters": "SHLT_ID",
        "flood_priority_shelters": "SHLT_ID",
        "site_attr_ok": "SITE_ID",
        "site_safe": "SITE_ID",
        "site_final": "SITE_ID",
        "land_suspect_projects": "PROJ_ID",
        "land_protected_conflicts": "PROJ_ID",
        "land_mismatch_parcels": "PARCEL_ID",
        "land_priority_projects": "PROJ_ID",
        "road_hotspots": "ROAD_ID",
        "road_risk_schools": "SCH_ID",
        "road_priority_roads": "ROAD_ID",
    }
    expected_ids = {
        name: sorted(gdf[field_name].astype(str).unique().tolist())
        for name, gdf in truth_layers.items()
        for field_name in [id_fields[name]]
    }
    return truth_layers, expected_ids


def task_cases(expected_ids: Mapping[str, Sequence[str]]) -> Dict[str, Any]:
    common = {
        "crs": CRS,
        "output_folder_placeholder": "{OUTPUT_FOLDER}",
        "source_policy": "read_only",
        "reset_policy": "clear generated layers and reload all source layers before each mode",
    }
    cases = [
        {
            "case_id": "FLOOD_RESPONSE",
            "name_zh": "强降雨防汛应急调度",
            "rounds": [
                {
                    "round": 1,
                    "prompt": "进入防汛研判第一轮：从当前淹没区中提取风险等级不低于4级的区域，再找出这些区域内人口不少于800且脆弱性为HIGH的社区，分别生成 flood_high 和 affected_comm；不要修改源数据。",
                    "expected_outputs": ["flood_high", "affected_comm"],
                    "expected_id_keys": ["flood_high", "flood_affected_comm"],
                },
                {
                    "round": 2,
                    "prompt": "继续上一轮结果：以 affected_comm 为中心建立2公里应急服务区，在服务区内筛选状态为OPEN的避难场所，生成 available_shelters；保留上一轮结果供下一轮使用。",
                    "expected_outputs": ["affected_service_2km", "available_shelters"],
                    "expected_id_keys": ["flood_available_shelters"],
                },
                {
                    "round": 3,
                    "prompt": "完成应急调度成果：从 available_shelters 中筛选容量不少于1000人的场所，生成 priority_shelters，并导出其属性表和当前应急分布图。",
                    "expected_outputs": ["priority_shelters", "priority_shelters.csv", "flood_response_map.png"],
                    "expected_id_keys": ["flood_priority_shelters"],
                },
            ],
        },
        {
            "case_id": "FACILITY_SITING",
            "name_zh": "公共服务设施综合选址",
            "rounds": [
                {
                    "round": 1,
                    "prompt": "开展公共服务设施选址：先从候选地块中筛选面积不少于8公顷、用地类型为PUBLIC或MIXED、成本指数不高于60且状态为AVAILABLE的地块，生成 site_attr_ok。",
                    "expected_outputs": ["site_attr_ok"],
                    "expected_id_keys": ["site_attr_ok"],
                },
                {
                    "round": 2,
                    "prompt": "继续选址约束分析：分别建立学校500米、工业区1000米和河流300米缓冲区，将这些范围与淹没区、保护区合并为排除区，从 site_attr_ok 中剔除排除区，生成 site_safe。",
                    "expected_outputs": ["school_buf", "industry_buf", "river_buf", "site_exclusion", "site_safe"],
                    "expected_id_keys": ["site_safe"],
                },
                {
                    "round": 3,
                    "prompt": "完成选址成果：在 site_safe 中保留距离人口不少于1000人的社区1500米以内的地块，生成 final_sites，并导出候选地块表和选址成果图。",
                    "expected_outputs": ["final_sites", "final_sites.csv", "facility_siting_map.png"],
                    "expected_id_keys": ["site_final"],
                },
            ],
        },
        {
            "case_id": "LAND_COMPLIANCE",
            "name_zh": "建设项目用地合规核查",
            "rounds": [
                {
                    "round": 1,
                    "prompt": "开始建设项目合规核查：筛选未取得许可或状态为STOP的建设项目，生成 suspect_projects，源项目数据不得修改。",
                    "expected_outputs": ["suspect_projects"],
                    "expected_id_keys": ["land_suspect_projects"],
                },
                {
                    "round": 2,
                    "prompt": "继续核查：计算 suspect_projects 与保护区的相交部分生成 protected_conflicts，同时筛选规划用途与实际用途不一致的地块生成 mismatch_parcels。",
                    "expected_outputs": ["protected_conflicts", "mismatch_parcels"],
                    "expected_id_keys": ["land_protected_conflicts", "land_mismatch_parcels"],
                },
                {
                    "round": 3,
                    "prompt": "形成重点违规清单：从 suspect_projects 中找出位于 protected_conflicts 或 mismatch_parcels 范围内的项目，生成 priority_violations，并导出属性表和合规核查图。",
                    "expected_outputs": ["priority_violations", "priority_violations.csv", "land_compliance_map.png"],
                    "expected_id_keys": ["land_priority_projects"],
                },
            ],
        },
        {
            "case_id": "ROAD_SAFETY",
            "name_zh": "道路事故热点与校园安全治理",
            "rounds": [
                {
                    "round": 1,
                    "prompt": "开展道路安全研判：将事故点空间连接到道路，统计每条道路的事故数量，生成 road_accident_join。",
                    "expected_outputs": ["road_accident_join"],
                    "acceptance": {"minimum_accident_count_field": "Join_Count"},
                },
                {
                    "round": 2,
                    "prompt": "继续研判：从 road_accident_join 中筛选事故数不少于8的道路作为 hotspot_roads，并建立300米缓冲区 hotspot_buffer。",
                    "expected_outputs": ["hotspot_roads", "hotspot_buffer"],
                    "expected_id_keys": ["road_hotspots"],
                },
                {
                    "round": 3,
                    "prompt": "完成校园周边治理成果：识别 hotspot_buffer 内的学校，并找出距学校500米内的热点道路，分别生成 risk_schools 和 priority_roads，最后导出道路安全图。",
                    "expected_outputs": ["risk_schools", "priority_roads", "road_safety_map.png"],
                    "expected_id_keys": ["road_risk_schools", "road_priority_roads"],
                },
            ],
        },
    ]
    return {"dataset": common, "cases": cases, "expected_ids": dict(expected_ids)}


def write_shapefile(gdf: gpd.GeoDataFrame, path: Path) -> None:
    if gdf.empty:
        raise ValueError(f"Refusing to write empty truth layer: {path.name}")
    if not bool(gdf.geometry.is_valid.all()):
        raise ValueError(f"Invalid geometry in {path.name}")
    gdf.to_file(path, driver="ESRI Shapefile", encoding="UTF-8", index=False)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_data_dictionary(layers: Mapping[str, gpd.GeoDataFrame], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["layer", "field", "dtype", "description"])
        for layer_name, gdf in layers.items():
            for field_name, dtype in gdf.drop(columns="geometry").dtypes.items():
                writer.writerow([layer_name, field_name, str(dtype), "synthetic experiment attribute"])


def validate_written_layers(
    layer_dir: Path,
    layers: Mapping[str, gpd.GeoDataFrame],
    city_bounds: Sequence[float],
) -> Dict[str, Any]:
    validation: Dict[str, Any] = {"ok": True, "layers": {}}
    for name, original in layers.items():
        path = layer_dir / f"{name}.shp"
        loaded = gpd.read_file(path)
        checks = {
            "feature_count": len(loaded),
            "expected_feature_count": len(original),
            "crs": loaded.crs.to_string() if loaded.crs else None,
            "valid_geometries": bool(loaded.geometry.is_valid.all()),
            "null_geometries": int(loaded.geometry.isna().sum()),
            "fields": [field for field in loaded.columns if field != "geometry"],
        }
        xmin, ymin, xmax, ymax = loaded.total_bounds
        expected_xmin, expected_ymin, expected_xmax, expected_ymax = city_bounds
        tolerance = 1e-6
        checks["within_city_bounds"] = bool(
            xmin >= expected_xmin - tolerance
            and ymin >= expected_ymin - tolerance
            and xmax <= expected_xmax + tolerance
            and ymax <= expected_ymax + tolerance
        )
        checks["ok"] = (
            checks["feature_count"] == checks["expected_feature_count"]
            and checks["crs"] == CRS
            and checks["valid_geometries"]
            and checks["null_geometries"] == 0
            and checks["within_city_bounds"]
        )
        validation["layers"][name] = checks
        validation["ok"] = validation["ok"] and checks["ok"]
    return validation


def validate_generation_inputs(
    scale: float,
    city_bounds: Sequence[float],
) -> tuple[float, tuple[float, float, float, float]]:
    scale = float(scale)
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("scale must be a positive finite number")
    if len(city_bounds) != 4:
        raise ValueError("city_bounds must contain xmin, ymin, xmax, ymax")
    bounds = tuple(float(value) for value in city_bounds)
    if not all(np.isfinite(value) for value in bounds):
        raise ValueError("city_bounds values must be finite")
    xmin, ymin, xmax, ymax = bounds
    if xmax <= xmin or ymax <= ymin:
        raise ValueError("city_bounds must have positive width and height")
    return scale, bounds


def transform_layers(
    layers: Mapping[str, gpd.GeoDataFrame],
    target_bounds: Sequence[float],
) -> Dict[str, gpd.GeoDataFrame]:
    if tuple(target_bounds) == CITY_BOUNDS:
        return {name: layer.copy() for name, layer in layers.items()}
    source_xmin, source_ymin, source_xmax, source_ymax = CITY_BOUNDS
    target_xmin, target_ymin, target_xmax, target_ymax = target_bounds
    x_scale = (target_xmax - target_xmin) / (source_xmax - source_xmin)
    y_scale = (target_ymax - target_ymin) / (source_ymax - source_ymin)

    def transform_geometry(geometry):
        scaled = affinity.scale(
            geometry,
            xfact=x_scale,
            yfact=y_scale,
            origin=(source_xmin, source_ymin),
        )
        return affinity.translate(
            scaled,
            xoff=target_xmin - source_xmin,
            yoff=target_ymin - source_ymin,
        )

    transformed: Dict[str, gpd.GeoDataFrame] = {}
    for name, layer in layers.items():
        result = layer.copy()
        result.geometry = result.geometry.apply(transform_geometry)
        transformed[name] = result
    return transformed


def _generate_into(
    staging_dir: Path,
    final_output_dir: Path,
    *,
    seed: int,
    scale: float,
    city_bounds: tuple[float, float, float, float],
) -> None:
    source_dir = staging_dir / "source"
    truth_dir = staging_dir / "truth"
    source_dir.mkdir(parents=True)
    truth_dir.mkdir(parents=True)

    rng = np.random.default_rng(seed)
    districts = build_districts()
    parcels = build_parcels()
    roads = build_roads()
    layers: Dict[str, gpd.GeoDataFrame] = {
        "districts": districts,
        "parcels": parcels,
        "roads": roads,
        "accidents": build_accidents(
            roads,
            rng,
            count=max(1, int(round(108 * scale))),
        ),
        "communities": build_communities(rng),
        "flood_zones": build_flood_zones(),
        "shelters": build_shelters(),
        "hospitals": build_hospitals(),
        "schools": build_schools(),
        "rivers": build_rivers(),
        "industry": build_industry(),
        "protected": build_protected(),
        "candidate_sites": build_candidate_sites(rng),
        "construction": build_construction(parcels),
    }
    layers = transform_layers(layers, city_bounds)
    truth_layers, expected_ids = build_truth(layers)
    if any(not values for values in expected_ids.values()):
        empty = [name for name, values in expected_ids.items() if not values]
        raise ValueError(f"Ground-truth groups must be non-empty: {empty}")

    for name, gdf in layers.items():
        write_shapefile(gdf, source_dir / f"{name}.shp")
    for name, gdf in truth_layers.items():
        write_shapefile(gdf, truth_dir / f"{name}.shp")

    source_validation = validate_written_layers(source_dir, layers, city_bounds)
    truth_validation = validate_written_layers(truth_dir, truth_layers, city_bounds)
    validation = {
        "ok": source_validation["ok"] and truth_validation["ok"],
        "source": source_validation,
        "truth": truth_validation,
    }
    if not validation["ok"]:
        raise ValueError("Written layer validation failed")
    (staging_dir / "validation.json").write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    (staging_dir / "task_cases.json").write_text(json.dumps(task_cases(expected_ids), ensure_ascii=False, indent=2), encoding="utf-8")
    (truth_dir / "expected_ids.json").write_text(json.dumps(expected_ids, ensure_ascii=False, indent=2), encoding="utf-8")
    write_data_dictionary(layers, staging_dir / "data_dictionary.csv")

    load_order = [str((final_output_dir / "source" / f"{name}.shp").resolve()) for name in layers]
    (staging_dir / "load_order.json").write_text(json.dumps(load_order, ensure_ascii=False, indent=2), encoding="utf-8")
    file_records = [
        {"path": str(path.relative_to(staging_dir)).replace("\\", "/"), "bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in sorted(staging_dir.rglob("*"))
        if path.is_file()
    ]
    manifest = {
        "version": VERSION,
        "seed": seed,
        "crs": CRS,
        "data_scale": scale,
        "city_bounds": list(city_bounds),
        "source_layers": {name: len(gdf) for name, gdf in layers.items()},
        "truth_layers": {name: len(gdf) for name, gdf in truth_layers.items()},
        "source_feature_total": sum(len(gdf) for gdf in layers.values()),
        "case_count": 4,
        "rounds_per_case": 3,
        "files": file_records,
    }
    (staging_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def generate(
    output_dir: Path,
    *,
    seed: int = SEED,
    scale: float = 1.0,
    city_bounds: Sequence[float] = CITY_BOUNDS,
) -> None:
    scale, bounds = validate_generation_inputs(scale, city_bounds)
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"Output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.tmp-",
        dir=output_dir.parent,
    ) as temporary:
        staging_dir = Path(temporary) / "dataset"
        staging_dir.mkdir()
        _generate_into(
            staging_dir,
            output_dir,
            seed=seed,
            scale=scale,
            city_bounds=bounds,
        )
        os.replace(staging_dir, output_dir)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the deterministic GeoPilot synthetic-city experiment dataset.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument(
        "--city-bounds",
        type=float,
        nargs=4,
        metavar=("XMIN", "YMIN", "XMAX", "YMAX"),
        default=CITY_BOUNDS,
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    generate(
        arguments.output,
        seed=arguments.seed,
        scale=arguments.scale,
        city_bounds=arguments.city_bounds,
    )
