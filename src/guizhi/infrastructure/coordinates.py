"""Register CRS definitions and verify explicit PROJ pipelines against controls."""
from hashlib import sha256
from math import hypot, isclose, pi
import shutil
from uuid import uuid4

import numpy as np
from pyproj import CRS, Transformer, network
from pyproj.enums import TransformDirection
from pyproj.exceptions import CRSError
import shapely

from guizhi.domain.crs import TransformPlan
from guizhi.infrastructure.crs_catalog import store
from guizhi.infrastructure.vector import digest

UNITS = {"degree": pi / 180, "m": 1, "ft": 0.3048, "us-ft": 1200 / 3937}
STEPS = {"pipeline", "unitconvert", "cart", "helmert", "tmerc", "utm", "hgridshift",
         "vgridshift", "axisswap", "affine", "stere", "lcc", "push", "pop", "noop"}


def describe_definition(raw):
    crs = CRS.from_wkt(raw)
    return {"original": raw, "wkt": crs.to_wkt(), "projjson": crs.to_json_dict(),
            "name": crs.name, "axes": [{"name": axis.name, "direction": axis.direction,
                                        "unit": axis.unit_name} for axis in crs.axis_info]}


def require_units(crs, unit):
    if len(crs.axis_info) != 2 or (unit == "degree") != crs.is_geographic or any(
        not isclose(axis.unit_conversion_factor, UNITS[unit], rel_tol=1e-9)
        for axis in crs.axis_info
    ):
        raise ValueError(f"Declared {unit} unit does not match CRS axes")


def grid_path(root, grid):
    path = root / "crs-grids" / grid.sha256 / grid.name
    if path.resolve() != path:
        raise ValueError(f"Grid path must not redirect: {grid.name}")
    return path


def resolved_pipeline(plan, root):
    network.set_network_enabled(False)
    grids = {grid.name: grid for grid in plan.grids}
    if len(grids) != len(plan.grids):
        raise ValueError("Grid names must be unique")
    used, tokens = set(), []
    for token in plan.pipeline.split():
        if not token.startswith("+") or any(char in token for char in '"\''):
            raise ValueError("Pipeline requires explicit PROJ tokens")
        key, _, value = token.partition("=")
        if key == "+proj" and value not in STEPS:
            raise ValueError(f"Unsupported pipeline step: {value}")
        if key in {"+init", "+catalog", "+file", "+nadgrids", "+geoidgrids"}:
            raise ValueError(f"Use declared grids, not {key}")
        if key == "+grids":
            if value not in grids:
                raise ValueError(f"Grid has no declared file: {value}")
            item = grids[value]
            path = grid_path(root, item)
            if not path.is_file() or digest(path) != item.sha256:
                raise ValueError(f"Grid missing or checksum mismatch: {value}")
            used.add(value)
            token = f'+grids="{path.as_posix()}"'
        tokens.append(token)
    if used != grids.keys() or not tokens or tokens[0] != "+proj=pipeline":
        raise ValueError("Use an explicit pipeline and declare exactly its grids")
    return Transformer.from_pipeline(" ".join(tokens))


def converter(plan, root):
    source = CRS.from_wkt(plan.source_wkt)
    target = CRS.from_wkt(plan.target_wkt)
    require_units(source, plan.source_unit)
    require_units(target, plan.target_unit)
    if not target.is_projected:
        raise ValueError("Target must be projected")
    west, south, east, north = plan.area
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Area must be a west/south/east/north box in source geographic coordinates")
    geographic = Transformer.from_crs(source, source.geodetic_crs, always_xy=True, allow_ballpark=False)
    pipeline = resolved_pipeline(plan, root)
    direction = TransformDirection.FORWARD if plan.direction == "forward" else TransformDirection.INVERSE
    if direction == TransformDirection.INVERSE and not pipeline.has_inverse:
        raise ValueError("Pipeline does not support the declared inverse direction")

    def transform(x, y):
        longitude, latitude = geographic.transform(x, y, errcheck=True)
        if not np.all((np.asarray(longitude) >= west) & (np.asarray(longitude) <= east)
                      & (np.asarray(latitude) >= south) & (np.asarray(latitude) <= north)):
            raise ValueError("Coordinates outside the plan area")
        return pipeline.transform(x, y, direction=direction, errcheck=True)

    return source, target, transform


def import_grids(plan, root, folder):
    for grid in plan.grids:
        origin = folder / grid.name
        if not origin.is_file() or digest(origin) != grid.sha256:
            raise ValueError(f"Grid missing or checksum mismatch: {grid.name}")
        target = grid_path(root, grid)
        if target.exists():
            if digest(target) != grid.sha256:
                raise ValueError(f"Stored grid checksum mismatch: {grid.name}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(uuid4().hex + ".pending")
        try:
            shutil.copyfile(origin, partial)
            if digest(partial) != grid.sha256:
                raise ValueError(f"Grid changed during import: {grid.name}")
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)


def import_definition(workspace, path):
    if path.suffix.lower() != ".shp" or not path.is_file():
        raise ValueError("请选择有效的 .shp 文件")
    projection_name = path.with_suffix(".prj").name
    projections = [file for file in path.parent.iterdir()
                   if file.name.casefold() == projection_name.casefold() and file.is_file()]
    if not projections:
        raise ValueError(f"未找到同名坐标文件 {projection_name}，请将它与 .shp 放在同一文件夹")
    if len(projections) != 1:
        raise ValueError(f"找到多个同名坐标文件 {projection_name}，请保留正确的一个")
    projection = projections[0]
    data = projection.read_bytes()
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            raw = data.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ValueError(f"{projection.name} 的文本编码无法识别") from exc
    try:
        definition = describe_definition(raw)
    except CRSError as exc:
        raise ValueError(f"{projection.name} 缺少有效的坐标系定义") from exc
    source = path.resolve()
    identifier = sha256(str(source).casefold().encode()).hexdigest()[:20]
    return store(workspace, "definition", identifier, definition["name"], {
        "definition": definition,
        "source_file": str(source),
        "projection_file": str(projection.resolve()),
        "projection_sha256": sha256(data).hexdigest(),
    })


def import_plan(workspace, path):
    plan = TransformPlan.model_validate_json(path.read_text(encoding="utf-8-sig"))
    if plan.tolerance_m > plan.accuracy_m:
        raise ValueError("Control point tolerance exceeds the declared accuracy")
    import_grids(plan, workspace.root, path.parent)
    _, _, transform = converter(plan, workspace.root)
    residuals = []
    for point in plan.controls:
        x, y = transform(*point.source)
        residuals.append(hypot(x - point.target[0], y - point.target[1]))
    if max(residuals) > plan.tolerance_m:
        raise ValueError(f"Control point residual {max(residuals):.4f} m exceeds tolerance")
    return store(workspace, "plan", plan.id, plan.title, {
        "plan": plan.model_dump(mode="json"), "source": describe_definition(plan.source_wkt),
        "target": describe_definition(plan.target_wkt), "residuals_m": residuals, "axis_order": "xy",
    })


def apply_plan(frame, entry, root):
    plan = TransformPlan.model_validate(entry["plan"])
    source, target, transform = converter(plan, root)
    if not frame.crs.equals(source, ignore_axis_order=True):
        raise ValueError("Layer CRS does not match the selected transformation source")
    if shapely.has_z(frame.geometry).any():
        raise ValueError("Coordinate plans currently accept 2D geometry")
    result = frame.copy()
    result.geometry = shapely.transform(frame.geometry.to_numpy(), transform, interleaved=False)
    return result.set_crs(target, allow_override=True)
