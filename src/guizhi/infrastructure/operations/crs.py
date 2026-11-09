"""Apply an explicit saved definition or a verified transformation."""
import json

from pyproj import CRS

from guizhi.infrastructure.coordinates import apply_plan
from guizhi.infrastructure.crs_catalog import get_plan


def assign(ctx, definition: str):
    identifier, _, revision = definition.partition("@")
    if not revision.isdecimal():
        raise ValueError("Specify the coordinate definition as id@revision")
    with ctx.workspace.connect() as db:
        row = db.execute(
            "SELECT body FROM crs_catalog WHERE kind='definition' AND id=? AND revision=?",
            (identifier, int(revision)),
        ).fetchone()
    if row is None:
        raise ValueError("Coordinate definition not found")
    frame = ctx.frames[0]
    if frame.crs is not None:
        raise ValueError("Use transformation for a dataset with an existing CRS")
    entry = json.loads(row[0])
    ctx.json("definition.json", entry, "coordinate_definition")
    ctx.publish(frame.set_crs(CRS.from_wkt(entry["definition"]["wkt"])),
                geometry_type=ctx.info[0]["geometry_type"])
    return {"changeset": {"target_revision": ctx.selection_hashes[0], "coordinates_changed": False}}


def transform(ctx, plan: str):
    entry = get_plan(ctx.workspace, plan)
    converted = apply_plan(ctx.frames[0], entry, ctx.workspace.root)
    ctx.json("transformation.json", entry, "transform")
    ctx.publish(converted, geometry_type=ctx.info[0]["geometry_type"])
