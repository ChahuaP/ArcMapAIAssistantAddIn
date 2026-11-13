"""Apply an explicit saved definition or a verified transformation."""
from pyproj import CRS

from guizhi.infrastructure.coordinates import apply_plan
from guizhi.infrastructure.crs_catalog import get, get_plan


def assign(ctx, definition: str):
    if definition.startswith("EPSG:") and definition[5:].isdigit():
        crs = CRS.from_epsg(int(definition[5:]))
        entry = {"definition": {"wkt": crs.to_wkt()}, "authority": definition}
    else:
        entry = get(ctx.workspace, "definition", definition)
    frame = ctx.frames[0]
    if frame.crs is not None:
        raise ValueError("Use transformation for a dataset with an existing CRS")
    ctx.json("definition.json", entry, "coordinate_definition")
    ctx.publish(frame.set_crs(CRS.from_wkt(entry["definition"]["wkt"])),
                geometry_type=ctx.info[0]["geometry_type"])
    return {"changeset": {"target_revision": ctx.selection_hashes[0], "coordinates_changed": False}}


def transform(ctx, plan: str):
    entry = get_plan(ctx.workspace, plan)
    converted = apply_plan(ctx.frames[0], entry, ctx.workspace.root)
    ctx.json("transformation.json", entry, "transform")
    ctx.publish(converted, geometry_type=ctx.info[0]["geometry_type"])
