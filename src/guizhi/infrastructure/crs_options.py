"""Shared EPSG choices and layer coordinate metadata."""
from functools import lru_cache

from pyproj import CRS
from pyproj.database import query_crs_info

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
