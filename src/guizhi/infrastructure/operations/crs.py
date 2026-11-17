"""Apply an explicit saved definition or a verified transformation."""
from pyproj import CRS

from guizhi.infrastructure.coordinates import apply_plan
from guizhi.infrastructure.crs_catalog import get, get_plan
from guizhi.infrastructure.crs_options import describe_crs


def assign(ctx, definition: str):
    if definition.startswith("EPSG:") and definition[5:].isdigit():
        crs = CRS.from_epsg(int(definition[5:]))
        entry = {"definition": {"wkt": crs.to_wkt()}, "authority": definition}
    else:
        entry = get(ctx.workspace, "definition", definition)
    return assign_definition(ctx, entry)


def assign_from(ctx):
    reference = ctx.frames[1]
    if reference.crs is None:
        raise ValueError("参照图层没有坐标系")
    return assign_definition(ctx, {"definition": {"wkt": reference.crs.to_wkt()}, "reference": ctx.request.inputs[1].layer})


def assign_definition(ctx, entry):
    frame = ctx.frames[0]
    if frame.crs is not None:
        raise ValueError("目标图层已有坐标系。若要转换坐标，请使用投影转换")
    ctx.json("definition.json", entry, "coordinate_definition")
    target = CRS.from_wkt(entry["definition"]["wkt"])
    ctx.publish(frame.set_crs(target), geometry_type=ctx.info[0]["geometry_type"], preserve_fids=True)
    return {"changeset": {"target_revision": ctx.selection_hashes[0], "coordinates_changed": False},
            "statistics": {"coordinate_system": describe_crs(target)}}


def transform(ctx, plan: str):
    entry = get_plan(ctx.workspace, plan)
    converted = apply_plan(ctx.frames[0], entry, ctx.workspace.root)
    ctx.json("transformation.json", entry, "transform")
    ctx.publish(converted, geometry_type=ctx.info[0]["geometry_type"])


def reproject(ctx, definition: str):
    import shapely
    from guizhi.infrastructure.map_workspace import projection

    target = (CRS.from_user_input(definition) if definition.startswith("EPSG:")
              else CRS(get(ctx.workspace, "definition", definition)["definition"]["wkt"]))
    if not (target.is_projected or target.is_geographic) or len(target.axis_info) != 2:
        raise ValueError("请选择二维地理或投影坐标系")
    frame = ctx.frames[0]
    if frame.crs is None:
        raise ValueError("请先为源图层定义实际坐标系")
    if frame.crs.equals(target, ignore_axis_order=True):
        raise ValueError("目标与源图层的坐标系相同，请选择不同的目标坐标系")
    present = frame.geometry.notna() & ~frame.geometry.is_empty
    tx = projection(frame.crs, target, frame.total_bounds if present.any() else None)
    converted = frame.set_geometry(shapely.transform(frame.geometry.array,
        lambda x, y, z=None: tx.transform(x, y, z, errcheck=True), interleaved=False, include_z=None))
    ctx.publish(converted.set_crs(target, allow_override=True), geometry_type=ctx.info[0]["geometry_type"], preserve_fids=True)
    return {"statistics": {"projection": {"source": describe_crs(frame.crs), "target": describe_crs(target)},
                           "feature_count": len(converted)}}
