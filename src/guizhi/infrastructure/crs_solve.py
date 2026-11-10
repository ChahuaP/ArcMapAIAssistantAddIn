"""Build a reproducible CRS plan from matched, independently checked control points."""
from hashlib import sha256
from itertools import combinations, islice
import json
import os
from pathlib import Path
from uuid import uuid4
import warnings

import numpy as np
import pyogrio
from pyproj import CRS, Transformer, datadir, network
from pyproj.aoi import AreaOfInterest
from pyproj.transformer import TransformerGroup

from guizhi.domain.crs import ControlPoint, Grid, TransformPlan
from guizhi.infrastructure.coordinates import (UNITS, converter, describe_definition, import_grids,
                                               require_units, residuals)
from guizhi.infrastructure.crs_catalog import get, insert
from guizhi.infrastructure.vector import digest, source_files, source_revision

PREFERRED_KEYS = ("point_id", "点号", "编号", "id", "name", "名称")
CHECK_MARKS = {"check", "fit", "检查", "校核", "拟合", "1", "0", "true", "false"}
METHODS = {"official", "similarity-2d", "affine-2d", "similarity-3d"}
MAX_POINTS = 2000


def unit_for(crs, dimension):
    for unit in UNITS:
        try:
            require_units(crs, unit, dimension)
            return unit
        except ValueError:
            continue
    raise ValueError("坐标定义的维度或单位不适合所选转换方法")


def points(file, layer):
    path = Path(file).resolve(strict=True)
    if path.suffix.lower() not in {".shp", ".gpkg", ".geojson"}:
        raise ValueError("控制点材料需要 SHP、GeoJSON 或 GeoPackage")
    inputs = source_files(path)
    revision = source_revision(inputs)
    info = pyogrio.read_info(path, layer=layer)
    if info["features"] > MAX_POINTS:
        raise ValueError("单份控制点材料最多 2,000 个点")
    frame = pyogrio.read_dataframe(path, layer=layer, max_features=MAX_POINTS + 1)
    if (len(frame) > MAX_POINTS or frame.empty or frame.crs is None or
            not (frame.geom_type == "Point").all() or frame.geometry.is_empty.any()):
        raise ValueError("请选择带坐标定义的单点图层，每份最多 2,000 个点")
    if source_revision(source_files(path)) != revision:
        raise ValueError("读取期间控制点文件已变化")
    return frame, {"file": str(path), "layer": layer, "sha256": revision}


def id_column(frame, name):
    if name not in frame or name == frame.geometry.name:
        return None
    values = frame[name]
    if values.isna().any():
        return None
    ids = [str(int(value)) if isinstance(value, (float, np.floating)) and value.is_integer() else str(value).strip()
           for value in values]
    return ids if all(ids) and len(set(ids)) == len(ids) else None


def pair_options(left, right):
    source_fields = [field for field in left.columns if id_column(left, field) is not None]
    target_fields = [field for field in right.columns if id_column(right, field) is not None]
    pairs = []
    for a in source_fields:
        for b in target_fields:
            if a.casefold() == b.casefold():
                matches = set(id_column(left, a)) & set(id_column(right, b))
                if len(matches) >= 3:
                    pairs.append({"source": a, "target": b, "matched": len(matches)})
    pairs.sort(key=lambda item: (PREFERRED_KEYS.index(item["source"].casefold())
                                 if item["source"].casefold() in PREFERRED_KEYS else len(PREFERRED_KEYS),
                                 -item["matched"], item["source"], item["target"]))
    check_fields = [field for field in left.columns if field != left.geometry.name and not left[field].isna().any()
                    and {str(value).strip().casefold() for value in left[field]} <= CHECK_MARKS]
    return {"source_fields": source_fields, "target_fields": target_fields, "check_fields": check_fields, "matches": pairs}


def assemble(left, right, source_key, target_key, dimension, check_field):
    a, b = id_column(left, source_key), id_column(right, target_key)
    if a is None or b is None:
        raise ValueError("编号字段需完整且在各自图层中唯一")
    destination = dict(zip(b, right.geometry, strict=True))
    ids, source, target, check = [], [], [], []
    markers = left[check_field] if check_field and check_field in left else None
    if check_field and markers is None:
        raise ValueError("所选独立检查点标识字段不存在")
    for i, (key, geometry) in enumerate(zip(a, left.geometry, strict=True)):
        if key not in destination:
            continue
        ids.append(key)
        source.append(geometry.coords[0][:dimension])
        target.append(destination[key].coords[0][:dimension])
        if markers is not None:
            marker = str(markers.iloc[i]).strip().casefold()
            if marker not in CHECK_MARKS:
                raise ValueError("独立检查点字段请填写 check/fit、检查/拟合或 1/0")
            if marker in {"check", "检查", "校核", "1", "true"}:
                check.append(len(ids) - 1)
    if len(ids) < 3:
        raise ValueError("两份图层至少需要 3 个同编号控制点，无法从任意两份数据推算转换参数")
    if any(len(x) != dimension for x in source + target):
        raise ValueError("三维转换要求两份控制点均含 Z 坐标")
    source, target = np.asarray(source), np.asarray(target)
    if not np.isfinite(source).all() or not np.isfinite(target).all() or len(np.unique(source, axis=0)) != len(ids):
        raise ValueError("控制点坐标必须有效，且源位置不能重复")
    return ids, source, target, check


def fit_matrix(source, target, method):
    x, y = source - source.mean(axis=0), target - target.mean(axis=0)
    dimension = x.shape[1]
    if method == "affine-2d":
        matrix, _, rank, singular = np.linalg.lstsq(x, y, rcond=None)
        if rank != 2 or singular[-1] <= singular[0] * 1e-8:
            raise ValueError("控制点共线或分布太集中")
        matrix = matrix.T
    else:
        u, singular, vh = np.linalg.svd(y.T @ x)
        required = dimension - 1
        if singular[0] <= 0 or singular[required - 1] <= singular[0] * 1e-8:
            raise ValueError("控制点重合、共线或分布太集中")
        correction = np.ones(dimension)
        correction[-1] = np.sign(np.linalg.det(u @ vh))
        scale = np.dot(singular, correction) / np.sum(x * x)
        matrix = scale * (u * correction) @ vh
    if not np.isfinite(matrix).all() or np.linalg.cond(matrix) > 1e8:
        raise ValueError("控制点无法确定稳定的转换")
    return matrix, target.mean(axis=0) - matrix @ source.mean(axis=0)


def robust_fit(source, target, method, tolerance):
    minimum = 2 if method == "similarity-2d" else 3
    random = np.random.default_rng(17)
    seeds = list(islice(combinations(range(len(source)), minimum), 16))
    seeds.extend(tuple(random.choice(len(source), minimum, replace=False)) for _ in range(128))
    best, inliers = None, None
    for seed in seeds:
        try:
            matrix, offset = fit_matrix(source[list(seed)], target[list(seed)], method)
        except (ValueError, np.linalg.LinAlgError):
            continue
        distance = np.linalg.norm(source @ matrix.T + offset - target, axis=1)
        mask = distance <= tolerance
        rank = (int(mask.sum()), -float(np.sum(distance[mask] ** 2)))
        if best is None or rank > best:
            best, inliers = rank, mask
    if best is None or best[0] < max(minimum + 1, (3 * len(source) + 4) // 5):
        raise ValueError("一致控制点不足，请核对编号、单位和坐标定义")
    matrix, offset = fit_matrix(source[inliers], target[inliers], method)
    return matrix, offset, inliers


def extent(values):
    low, high = values.min(axis=0), values.max(axis=0)
    delta = np.maximum((high - low) * 0.02, 1e-8)
    return low - delta, high + delta


def geographic_area(source, coords):
    transform = Transformer.from_crs(source, source.geodetic_crs, always_xy=True, allow_ballpark=False)
    xy = np.column_stack(transform.transform(coords[:, 0], coords[:, 1], errcheck=True))
    low, high = extent(xy)
    if high[0] - low[0] >= 180:
        raise ValueError("控制点横跨日期变更线，请分区建立转换")
    return [max(-180, low[0]), max(-90, low[1]), min(180, high[0]), min(90, high[1])]


def options(source, target, area):
    network.set_network_enabled(False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        group = TransformerGroup(source, target, always_xy=True, allow_ballpark=False,
                                 area_of_interest=AreaOfInterest(*area))
    found = []
    for operation in group.transformers[:30]:
        raw = operation.definition
        if not raw or "unavailable" in raw or operation.accuracy < 0:
            continue
        tokens = [token if token.startswith("+") else "+" + token for token in raw.split()]
        if tokens[0] != "+proj=pipeline":
            tokens = ["+proj=pipeline", "+step", *tokens]
        pipeline = " ".join(tokens)
        found.append({"id": sha256(pipeline.encode()).hexdigest()[:24], "name": operation.description,
                      "accuracy_m": operation.accuracy, "pipeline": pipeline,
                      "area": list(operation.area_of_use.bounds) if operation.area_of_use else None})
    unavailable = sorted({grid.short_name for operation in group.unavailable_operations for grid in operation.grids
                          if not grid.available and grid.short_name})
    return found, unavailable


def grids_for(pipeline, workspace):
    grids = []
    folders = [*map(Path, datadir.get_data_dir().split(os.pathsep)), Path(datadir.get_user_data_dir())]
    for name in dict.fromkeys(token.split("=", 1)[1] for token in pipeline.split() if token.startswith("+grids=")):
        file = next((folder / name for folder in folders if Path(name).name == name and (folder / name).is_file()), None)
        if file is None:
            raise ValueError(f"转换所需格网尚未安装：{name}")
        grid = Grid(name=name, sha256=digest(file))
        import_grids([grid], workspace.root, file.parent)
        grids.append(grid)
    return grids


def solve(workspace, payload):
    action = payload.get("action")
    if action not in {"inspect", "preview", "save"}:
        raise ValueError("请选择坐标转换操作")
    materials = [payload[key] for key in ("source_points", "target_points")]
    loaded = [points(item["file"], item.get("layer")) for item in materials]
    (left, left_ref), (right, right_ref) = loaded
    choices = pair_options(left, right)
    if action == "inspect":
        return choices
    source_entry, target_entry = [get(workspace, "definition", payload[key]) for key in ("source", "target")]
    source, target = [CRS.from_wkt(item["definition"]["wkt"]) for item in (source_entry, target_entry)]
    if not left.crs.equals(source, ignore_axis_order=True) or not right.crs.equals(target, ignore_axis_order=True):
        raise ValueError("控制点图层与所选源或目标坐标定义不一致")
    method = payload.get("method")
    if method not in METHODS:
        raise ValueError("请选择已有转换、二维相似、二维仿射或三维相似")
    dimension = 3 if method == "similarity-3d" else 2
    units = [unit_for(item, dimension) for item in (source, target)]
    tolerance = float(payload["tolerance"])
    if not np.isfinite(tolerance) or not 0 < tolerance <= 1000:
        raise ValueError("检查容差须大于 0 且不超过 1000 米")
    ids, a, b, checks = assemble(left, right, payload["source_key"], payload["target_key"], dimension,
                                payload.get("check_field"))
    low, high = extent(a)
    z_range = (low[2], high[2]) if dimension == 3 else None
    area = [low[0], low[1], high[0], high[1]]
    area_kind, removed, fit_count, available, missing, grids = "source", [], 0, [], [], []
    if method == "official":
        area_kind, area = "geographic", geographic_area(source, a)
        available, missing = options(source, target, area)
        if not available:
            return {"status": "unavailable", "missing_grids": missing, "operations": []}
        choice = payload.get("operation")
        if not choice and len(available) > 1:
            return {"status": "choose", "operations": [{key: value for key, value in item.items() if key != "pipeline"}
                                                       for item in available], "missing_grids": missing}
        selected = next((item for item in available if item["id"] == choice), available[0] if not choice else None)
        if not selected:
            raise ValueError("所选已有转换已变化，请重新查询")
        if selected["area"] and (area[0] < selected["area"][0] or area[1] < selected["area"][1] or
                                 area[2] > selected["area"][2] or area[3] > selected["area"][3]):
            raise ValueError("检查点范围超出所选转换的适用区域")
        pipeline, accuracy, label = selected["pipeline"], selected["accuracy_m"], selected["name"]
        grids = grids_for(pipeline, workspace)
        checks = checks or list(range(len(ids)))
    else:
        if "degree" in units or not (source.is_projected or source.is_engineering or source.is_geocentric) or \
                not (target.is_projected or target.is_engineering or target.is_geocentric):
            raise ValueError("地方参数求解需要同维度线性坐标，不能直接拟合经纬度")
        if dimension == 3 and not (source.is_geocentric and target.is_geocentric or
                                   source.is_engineering and target.is_engineering):
            raise ValueError("三维相似求解需要一对三维地心或三维工程坐标定义")
        if not checks and not payload.get("check_field"):
            order = sorted(range(len(ids)), key=lambda i: sha256(ids[i].encode()).digest())
            checks = order[:max(3, len(ids) // 4)]
        fit = [i for i in range(len(ids)) if i not in checks]
        if len(checks) < 3 or len(fit) < (4 if method == "similarity-2d" else 5):
            raise ValueError("地方参数至少需要 3 个独立检查点，以及 4 个二维相似或 5 个仿射、三维拟合点")
        matrix, offset, inliers = robust_fit(a[fit], b[fit], method, tolerance / UNITS[units[1]])
        low, high = extent(a[[*np.asarray(fit)[inliers], *checks]])
        area = [low[0], low[1], high[0], high[1]]
        z_range = (low[2], high[2]) if dimension == 3 else None
        fit_count = int(inliers.sum())
        removed = [ids[i] for i, inlier in zip(fit, inliers, strict=True) if not inlier]
        labels = {"similarity-2d": "二维相似", "affine-2d": "二维仿射", "similarity-3d": "三维相似"}
        accuracy, label = tolerance, labels[method]
        axes = "xyz"[:dimension]
        tokens = [f"+{axis}off={offset[i]:.17g}" for i, axis in enumerate(axes)]
        tokens += [f"+s{i+1}{j+1}={matrix[i,j]:.17g}" for i in range(dimension) for j in range(dimension)]
        pipeline = "+proj=pipeline +step +proj=affine " + " ".join(tokens)
    if len(checks) < 3:
        raise ValueError("已有转换也需要至少 3 个独立检查点")
    title = str(payload.get("title") or label).strip()
    previous = get(workspace, "plan", payload["replace"]) if payload.get("replace") else None
    identifier = previous["id"] if previous else uuid4().hex
    plan = TransformPlan(id=identifier, title=title, source_wkt=source_entry["definition"]["wkt"],
                         target_wkt=target_entry["definition"]["wkt"], source_unit=units[0], target_unit=units[1],
                         direction="forward", pipeline=pipeline, area=area, accuracy_m=accuracy, tolerance_m=tolerance,
                         controls=[ControlPoint(source=a[i], target=b[i]) for i in checks[:100]],
                         reference=label, dimension=dimension, area_kind=area_kind, z_range=z_range, grids=grids)
    _, _, transform = converter(plan, workspace.root)
    prediction = np.column_stack(transform(*a[checks].T))
    errors = residuals(target, units[1], prediction, b[checks])
    if not np.isfinite(errors).all():
        raise ValueError("检查点坐标超出目标坐标系的有效范围")
    metrics = {"fit_count": fit_count, "check_count": len(checks), "max_m": float(errors.max()),
               "rmse_m": float(np.sqrt(np.mean(errors ** 2))), "rejected": removed,
               "check_ids": [ids[i] for i in checks], "available_operations": len(available)}
    body = {"plan": plan.model_dump(mode="json"), "source": describe_definition(plan.source_wkt),
            "target": describe_definition(plan.target_wkt), "source_reference": payload["source"],
            "target_reference": payload["target"], "inputs": [left_ref, right_ref], "method": method,
            "metrics": metrics, "residuals_m": errors.tolist(), "axis_order": "xyz" if dimension == 3 else "xy"}
    token = sha256(json.dumps([left_ref, right_ref, payload["source"], payload["target"],
        method, payload["source_key"], payload["target_key"], payload.get("check_field"), pipeline, tolerance, title,
        payload.get("replace")], sort_keys=True).encode()).hexdigest()
    status = "verified" if metrics["max_m"] <= tolerance else "failed"
    result = {"status": status, "token": token, "metrics": metrics, "area": area, "area_kind": area_kind,
              "method": label, "accuracy_m": accuracy, "tolerance_m": tolerance, "unit": units[1],
              "grids": [grid.name for grid in grids], "missing_grids": missing, "z_range": z_range}
    if action == "save":
        if status != "verified" or payload.get("token") != token:
            raise ValueError("请使用当前材料完成独立检查，且误差须在容差内")
        with workspace.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if previous:
                revision = db.execute("SELECT max(revision) FROM crs_catalog WHERE kind='plan' AND id=?",
                                      (identifier,)).fetchone()[0]
                if revision != previous["revision"]:
                    raise ValueError("转换方案已有新版本，请重新选择")
            return insert(db, "plan", identifier, title, body)
    return result
