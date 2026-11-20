"""Shared EPSG choices and layer coordinate metadata."""
from functools import lru_cache

from pyproj import CRS, network
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_crs_info
from pyproj.exceptions import ProjError
from pyproj.transformer import TransformerGroup

from guizhi.domain.crs import TransformPlan
from guizhi.infrastructure.crs_catalog import catalog, get

COMMON = {
    4490: "CGCS2000（国家大地坐标系 2000）", 4326: "WGS 84",
    4214: "北京 1954", 4610: "西安 1980", 3857: "WGS 84 / Web 墨卡托",
    **{code: f"CGCS2000 / 3° 高斯-克吕格 · 中央经线 {meridian}°E（不带带号）"
       for code, meridian in zip(range(4546, 4551), range(111, 124, 3), strict=True)},
    **{code: f"CGCS2000 / 3° 高斯-克吕格 · 第 {zone} 带（带号）"
       for code, zone in zip(range(4526, 4530), range(38, 42), strict=True)},
    **{code: f"WGS 84 / UTM {zone}N"
       for code, zone in zip(range(32649, 32652), range(49, 52), strict=True)},
}


def describe_crs(crs):
    if crs is None:
        return {"kind": "未定义"}
    crs = CRS.from_user_input(crs)
    code = crs.to_epsg()
    kind = "投影坐标系" if crs.is_projected else "地理坐标系" if crs.is_geographic else "其他坐标系"
    axis = crs.axis_info[0] if crs.axis_info else None
    factor = axis.unit_conversion_factor if axis and crs.is_projected else None
    return {"code": crs.to_string() if code else None, "name": COMMON.get(code, crs.name), "kind": kind,
            "unit": "度" if crs.is_geographic and axis and axis.unit_name == "degree"
            else "米" if factor == 1 else axis.unit_name if axis else None, "metresPerUnit": factor}


@lru_cache(maxsize=1)
def epsg_list():
    return query_crs_info(auth_name="EPSG", allow_deprecated=False)


def resolve_definition(workspace, reference):
    if not reference:
        raise ValueError("请选择坐标系")
    value = get(workspace, "definition", reference)["definition"]["wkt"] if "@" in reference else reference
    crs = CRS.from_user_input(value)
    if not (crs.is_projected or crs.is_geographic) or len(crs.axis_info) != 2:
        raise ValueError("请选择二维地理或投影坐标系")
    return crs


def area_of_interest(source, extent):
    network.set_network_enabled(False)
    if extent is None or source.geodetic_crs is None:
        return None
    # Undo the projection in the source datum before testing the transformation's area.
    local = TransformerGroup(source, source.geodetic_crs, always_xy=True, allow_ballpark=False)
    if not local.best_available or not local.transformers:
        raise ValueError("无法从图层坐标系确定转换范围，请选择已验证的转换方案")
    west, south, east, north = local.transformers[0].transform_bounds(*extent, errcheck=True)
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= north <= 90):
        raise ValueError("图层坐标超出坐标系适用范围，请检查图层坐标系")
    return AreaOfInterest(west, south, east, north)


def standard_transformers(source, target, area=None, *, always_xy=True):
    network.set_network_enabled(False)
    return TransformerGroup(source, target, always_xy=always_xy, allow_ballpark=False, area_of_interest=area)


def automatic_projection(source, target, area, *, always_xy=True):
    group = standard_transformers(source, target, area, always_xy=always_xy)
    if not group.best_available or not group.transformers:
        raise ValueError("缺少可靠的坐标转换或所需格网，请选择已验证的转换方案")
    return group.transformers[0]


def projection(source, target, extent, *, always_xy=True):
    return automatic_projection(source, target, area_of_interest(source, extent), always_xy=always_xy)


def within_area(bounds, outer):
    west, south, east, north = bounds
    left, bottom, right, top = outer
    if not bottom <= south <= north <= top:
        return False
    if left <= right:
        return left <= west <= east <= right or west > east and left == -180 and right == 180
    if west > east:
        return west >= left and east <= right
    return west >= left or east <= right


def geographic_bounds(area):
    return area.west_lon_degree, area.south_lat_degree, area.east_lon_degree, area.north_lat_degree


def projection_options(workspace, layer, query):
    if not layer["crs"]:
        return []
    source = CRS(layer["crs"])
    extent = layer["bounds"]
    area_error = None
    try:
        area = area_of_interest(source, extent)
    except (ProjError, ValueError) as error:
        area = None
        area_error = error
    geographic = geographic_bounds(area) if area is not None else None
    rows = []
    entries = catalog(workspace)
    token = query.casefold().strip()

    candidates = [(item, CRS(item["value"])) for item in coordinate_options(query)]
    for entry in entries:
        if entry["kind"] != "definition" or token not in entry["title"].casefold():
            continue
        target = CRS(entry["definition"]["wkt"])
        if len(target.axis_info) == 2 and (target.is_geographic or target.is_projected):
            candidates.append(({"value": entry["id"] + "@" + str(entry["revision"]), "label": entry["title"],
                                "kind": describe_crs(target)["kind"]}, target))

    def find(target):
        return next((item for item, crs in rows if target.equals(crs, ignore_axis_order=True)), None)

    requested_code = "".join(token.split()).removeprefix("epsg:").removeprefix("epsg：")
    for item, target in candidates:
        if area_error or target.equals(source, ignore_axis_order=True) or find(target):
            continue
        outside = bool(geographic and target.area_of_use and not within_area(geographic, target.area_of_use.bounds))
        if outside and item["value"] != "EPSG:" + requested_code:
            continue
        try:
            operation = automatic_projection(source, target, area)
            if geographic and operation.area_of_use and not within_area(geographic, operation.area_of_use.bounds):
                continue
            if extent:
                operation.transform_bounds(*extent, errcheck=True)
        except (ProjError, ValueError):
            continue
        rows.append(({**item, "outsideArea": outside,
                      "plans": [{"value": "automatic", "label": "自动匹配（PROJ）"}]}, target))

    for entry in entries:
        if entry["kind"] != "plan":
            continue
        plan = TransformPlan.model_validate(entry["plan"])
        has_z = (layer.get("geometry") or "").endswith((" Z", " ZM", "25D"))
        if plan.dimension != (3 if has_z else 2) or area_error and plan.area_kind == "geographic":
            continue
        if not source.equals(CRS(plan.source_wkt), ignore_axis_order=True):
            continue
        bounds = extent if plan.area_kind == "source" else geographic
        if bounds is not None and not within_area(bounds, plan.area):
            continue
        target = CRS(plan.target_wkt)
        row = find(target)
        ref = entry["id"] + "@" + str(entry["revision"])
        info = describe_crs(target)
        code = info["code"]
        label = info["name"] + (" · " + code if code else "")
        if row is None:
            if token and not any(token in value.casefold() for value in (label, entry["title"], code or "")):
                continue
            row = {"value": "plan:" + ref, "label": label, "kind": info["kind"], "plans": []}
            rows.append((row, target))
        row["plans"].append({"value": ref, "label": entry["title"]})
    if area_error and not rows:
        raise area_error
    return [row for row, _ in rows]


def coordinate_options(text):
    query = "".join(text.casefold().split())
    token = query.removeprefix("epsg:").removeprefix("epsg：")
    if not token:
        codes = list(COMMON)
    elif token.isdecimal():
        exact = [item.code for item in epsg_list() if item.code == token]
        codes = exact or [code for code, name in COMMON.items() if token in name]
    else:
        codes = [code for code, label in COMMON.items() if token in "".join(label.casefold().split())]
        if len(codes) < 30:
            codes.extend(item.code for item in epsg_list()
                         if token in "".join(item.name.casefold().split()) and int(item.code) not in codes)
    result = []
    for code in dict.fromkeys(map(int, codes)):
        crs = CRS.from_epsg(code)
        if not (crs.is_geographic or crs.is_projected) or len(crs.axis_info) != 2:
            continue
        row = describe_crs(crs)
        value = f"EPSG:{code}"
        result.append({"value": value, "label": row["name"] + " · " + value, "kind": row["kind"],
                       "units": "degrees" if row["unit"] == "度" else "m" if row["unit"] == "米" else None,
                       "metresPerUnit": row["metresPerUnit"]})
        if len(result) == 30:
            break
    return result



def library_definitions(query):
    return [{**row, **describe_crs(row["value"])} for row in coordinate_options(query)]


def library_transforms(workspace, source_ref, target_ref):
    source, target = (resolve_definition(workspace, reference) for reference in (source_ref, target_ref))
    if source.equals(target, ignore_axis_order=True):
        return {"same": True, "available": [], "requiresGrids": []}
    group = standard_transformers(source, target)

    def details(operation):
        area = operation.area_of_use
        return {"name": getattr(operation, "description", None) or operation.name,
                "accuracy": operation.accuracy if operation.accuracy >= 0 else None,
                "area": list(area.bounds) if area else None,
                "areaLabel": area.name if area else None}

    available = [{**details(operation), "pipeline": operation.definition} for operation in group.transformers]
    unavailable = [{**details(operation),
                    "grids": [grid.short_name for grid in operation.grids if not grid.available]}
                   for operation in group.unavailable_operations]
    return {"same": False, "available": available, "requiresGrids": unavailable, "bestAvailable": group.best_available}
