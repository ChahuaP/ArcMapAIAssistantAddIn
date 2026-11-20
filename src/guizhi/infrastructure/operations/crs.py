"""Apply an explicit saved definition or a verified transformation."""
from pyproj import CRS

from guizhi.infrastructure.coordinates import apply_plan
from guizhi.infrastructure.crs_catalog import get, get_plan
from guizhi.infrastructure.crs_options import describe_crs, projection, projection_summary, resolve_definition


def assign(ctx, definition: str):
    """更新坐标系定义并保留原坐标值，输出带新定义的图层。"""
    target = resolve_definition(ctx.workspace, definition)
    entry = get(ctx.workspace, "definition", definition) if "@" in definition else {
        "definition": {"wkt": target.to_wkt()}, "authority": ":".join(target.to_authority() or ()) or None,
    }
    return assign_definition(ctx, entry)


def assign_from(ctx):
    if ctx.frames[0].crs is not None:
        raise ValueError("目标图层已有坐标系。覆盖定义请使用定义坐标系")
    reference = ctx.frames[1]
    if reference.crs is None:
        raise ValueError("参照图层没有坐标系")
    return assign_definition(ctx, {"definition": {"wkt": reference.crs.to_wkt()}, "reference": ctx.request.inputs[1].layer})


def assign_definition(ctx, entry):
    frame = ctx.frames[0]
    ctx.json("definition.json", entry, "coordinate_definition")
    target = CRS.from_wkt(entry["definition"]["wkt"])
    ctx.publish(frame.set_crs(target, allow_override=True), geometry_type=ctx.info[0]["geometry_type"], preserve_fids=True)
    return {"changeset": {"target_revision": ctx.selection_hashes[0], "coordinates_changed": False},
            "statistics": {"coordinate_system": describe_crs(target)}}


def transform(ctx, plan: str):
    entry = get_plan(ctx.workspace, plan)
    converted = apply_plan(ctx.frames[0], entry, ctx.workspace.root)
    ctx.json("transformation.json", entry, "transform")
    ctx.publish(converted, geometry_type=ctx.info[0]["geometry_type"])


def reproject(ctx, definition: str | None = None, plan: str | None = None):
    """在源坐标系和目标坐标系间变换几何坐标，输出新图层及前后坐标系。"""
    import shapely

    if bool(definition) == bool(plan):
        raise ValueError("请选择目标坐标系或已验证的投影方案")
    frame = ctx.frames[0]
    if frame.crs is None:
        raise ValueError("请先为源图层定义实际坐标系")
    pipeline = None
    if plan:
        entry = get_plan(ctx.workspace, plan)
        converted = apply_plan(frame, entry, ctx.workspace.root)
        target = converted.crs
        ctx.json("transformation.json", entry, "transform")
    else:
        target = resolve_definition(ctx.workspace, definition)
        if frame.crs.equals(target, ignore_axis_order=True):
            raise ValueError("目标与源图层的坐标系相同，请选择不同的目标坐标系")
        present = frame.geometry.notna() & ~frame.geometry.is_empty
        tx = projection(frame.crs, target, frame.total_bounds if present.any() else None)
        pipeline = tx.definition
        converted = frame.set_geometry(shapely.transform(frame.geometry.array,
            lambda x, y, z=None: tx.transform(x, y, z, errcheck=True), interleaved=False, include_z=None))
    ctx.publish(converted.set_crs(target, allow_override=True), geometry_type=ctx.info[0]["geometry_type"], preserve_fids=True)
    return {"statistics": {"projection": projection_summary(frame.crs, target, pipeline),
                           "feature_count": len(converted)}}
