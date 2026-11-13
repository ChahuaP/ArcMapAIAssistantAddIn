"""Register CRS definitions and verify explicit PROJ pipelines against controls."""
from hashlib import sha256
import json
from math import isclose, pi
import shutil
from uuid import uuid4

import numpy as np
from pyproj import CRS, Transformer, network
from pyproj.enums import TransformDirection
from pyproj.exceptions import CRSError
import shapely

from guizhi.domain.crs import TransformPlan
from guizhi.infrastructure.crs_catalog import insert, store
from guizhi.infrastructure.vector import digest, source_revision

UNITS = {"degree": pi / 180, "m": 1, "ft": 0.3048, "us-ft": 1200 / 3937}
STEPS = {"pipeline", "unitconvert", "cart", "helmert", "tmerc", "utm", "hgridshift",
         "vgridshift", "axisswap", "affine", "stere", "lcc", "push", "pop", "noop", "longlat", "merc",
         "webmerc", "aea", "laea", "eqc", "omerc", "somerc", "krovak", "etmerc", "geoc", "molodensky"}


def describe_definition(raw):
    crs = CRS.from_wkt(raw)
    return {"original": raw, "wkt": crs.to_wkt(), "projjson": crs.to_json_dict(),
            "name": crs.name, "axes": [{"name": axis.name, "direction": axis.direction,
                                        "unit": axis.unit_name} for axis in crs.axis_info]}


def require_units(crs, unit, dimension=2):
    if len(crs.axis_info) != dimension or (unit == "degree") != crs.is_geographic or any(
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
    require_units(source, plan.source_unit, plan.dimension)
    require_units(target, plan.target_unit, plan.dimension)
    west, south, east, north = plan.area
    if not west < east or not south < north:
        raise ValueError("转换适用范围无效")
    geographic = None
    if plan.area_kind == "geographic":
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90) or plan.dimension != 2:
            raise ValueError("经纬度适用范围无效")
        geographic = Transformer.from_crs(source, source.geodetic_crs, always_xy=True, allow_ballpark=False)
    if plan.dimension == 3 and (plan.z_range is None or plan.z_range[0] >= plan.z_range[1]):
        raise ValueError("三维转换需要明确的高程或 Z 坐标范围")
    pipeline = resolved_pipeline(plan, root)
    direction = TransformDirection.FORWARD if plan.direction == "forward" else TransformDirection.INVERSE
    if direction == TransformDirection.INVERSE and not pipeline.has_inverse:
        raise ValueError("Pipeline does not support the declared inverse direction")

    def transform(x, y, z=None):
        u, v = geographic.transform(x, y, errcheck=True) if geographic else (x, y)
        if not np.all((np.asarray(u) >= west) & (np.asarray(u) <= east)
                      & (np.asarray(v) >= south) & (np.asarray(v) <= north)):
            raise ValueError("坐标超出已验证的转换适用范围（Coordinates outside the plan area）")
        if plan.dimension == 3:
            low, high = plan.z_range
            if z is None or not np.all((np.asarray(z) >= low) & (np.asarray(z) <= high)):
                raise ValueError("Z 坐标超出已验证的范围")
        return pipeline.transform(x, y, z, direction=direction, errcheck=True)

    return source, target, transform


def import_grids(grids, root, folder):
    for grid in grids:
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


def shp_definition(path):
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
    return raw, {"projection_file": str(projection.resolve()), "projection_sha256": sha256(data).hexdigest()}


def import_definition(workspace, path, layer=None):
    path = path.resolve(strict=True)
    suffix = path.suffix.lower()
    if suffix == ".shp" and path.is_file():
        raw, evidence = shp_definition(path)
    elif suffix in {".tif", ".tiff", ".img"}:
        from guizhi.infrastructure.operations.raster import source_files
        import rasterio

        files = source_files(path)
        with rasterio.open(path) as src:
            raw = src.crs.to_wkt()
        evidence = {"source_sha256": source_revision(files), "source_files": [str(item) for item in files]}
    elif suffix in {".gpkg", ".geojson"}:
        import pyogrio

        layers = [name for name, geometry in pyogrio.list_layers(path) if geometry]
        layer = layer or (layers[0] if len(layers) == 1 else None)
        if layer not in layers:
            raise ValueError("请指定要读取坐标定义的矢量图层")
        name = pyogrio.read_info(path, layer=layer)["crs"]
        if not name:
            raise ValueError("所选图层没有坐标定义")
        raw = CRS.from_user_input(name).to_wkt()
        evidence = {"layer": layer, "source_sha256": digest(path)}
    else:
        raise ValueError("请选择含坐标定义的 SHP、TIFF、IMG、GeoJSON 或 GeoPackage")
    try:
        definition = describe_definition(raw)
    except CRSError as exc:
        raise ValueError(f"{path.name} 缺少有效的坐标系定义") from exc
    crs = CRS.from_wkt(definition["wkt"])
    identifier = sha256(definition["wkt"].encode()).hexdigest()
    definition["sha256"] = identifier
    with workspace.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        latest = db.execute("""SELECT body FROM crs_catalog WHERE kind='definition' AND (id,revision) IN
                            (SELECT id,max(revision) FROM crs_catalog WHERE kind='definition' GROUP BY id)""")
        for row in latest:
            entry = json.loads(row[0])
            if not entry.get("removed") and crs.equals(CRS.from_wkt(entry["definition"]["wkt"]), ignore_axis_order=True):
                return entry
        return insert(db, "definition", identifier, definition["name"],
                      {"definition": definition, "source_file": str(path), **evidence})


def residuals(target, unit, predicted, known):
    if unit == "degree":
        return np.abs(target.get_geod().inv(predicted[:, 0], predicted[:, 1], known[:, 0], known[:, 1])[2])
    return np.linalg.norm(predicted - known, axis=1) * UNITS[unit]


def import_plan(workspace, path):
    plan = TransformPlan.model_validate_json(path.read_text(encoding="utf-8-sig"))
    if plan.tolerance_m > plan.accuracy_m:
        raise ValueError("Control point tolerance exceeds the declared accuracy")
    import_grids(plan.grids, workspace.root, path.parent)
    _, target, transform = converter(plan, workspace.root)
    known, predicted = [], []
    for point in plan.controls:
        predicted.append(transform(*point.source))
        known.append(point.target)
    errors = residuals(target, plan.target_unit, np.asarray(predicted), np.asarray(known)).tolist()
    if max(errors) > plan.tolerance_m:
        raise ValueError(f"Control point residual {max(errors):.4f} m exceeds tolerance")
    return store(workspace, "plan", plan.id, plan.title, {
        "plan": plan.model_dump(mode="json"), "source": describe_definition(plan.source_wkt),
        "target": describe_definition(plan.target_wkt), "residuals_m": errors, "axis_order": "xy",
    })


def apply_plan(frame, entry, root):
    plan = TransformPlan.model_validate(entry["plan"])
    source, target, transform = converter(plan, root)
    if not frame.crs.equals(source, ignore_axis_order=True):
        raise ValueError("Layer CRS does not match the selected transformation source")
    has_z = shapely.has_z(frame.geometry).any()
    if plan.dimension == 2 and has_z:
        raise ValueError("请选择三维转换方案处理带 Z 坐标的材料")
    if plan.dimension == 3 and not np.isfinite(shapely.get_coordinates(frame.geometry.array, include_z=True)).all():
        raise ValueError("三维转换要求每个顶点都含有效 Z 坐标")
    result = frame.copy()
    result.geometry = shapely.transform(frame.geometry.to_numpy(), transform, include_z=plan.dimension == 3, interleaved=False)
    return result.set_crs(target, allow_override=True)
