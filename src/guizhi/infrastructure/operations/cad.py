"""Use the bundled GDAL DXF driver to read local drawing geometry."""
from collections import Counter
import re

from pyproj import CRS
import pyogrio
import shapely

from guizhi.infrastructure.coordinates import apply_plan
from guizhi.infrastructure.crs_catalog import get_plan
from guizhi.infrastructure.operations.report import publish_report

VERSIONS = {"AC1009", "AC1015", "AC1018", "AC1021", "AC1024", "AC1027", "AC1032"}


def drawing(ctx):
    file = ctx.drawings[0]
    with file.open("rb") as stream:
        header = stream.read(65536)
    version = re.search(rb"\$ACADVER[ \t]*\r?\n[ \t]*1[ \t]*\r?\n[ \t]*(AC\d{4})", header)
    if not version or version[1].decode() not in VERSIONS:
        raise ValueError("Unsupported DXF header or version; provide a supported ASCII DXF")
    info = pyogrio.read_info(file, layer="entities")
    if info["driver"] != "DXF":
        raise ValueError("Drawing is not an exchange DXF")
    ctx.check()
    frame = pyogrio.read_dataframe(file, layer="entities", on_invalid="raise")
    if "Layer" not in frame:
        raise ValueError("DXF decoder did not return named CAD layers")
    return version[1].decode(), frame


def inspect(ctx):
    version, frame = drawing(ctx)
    counts = Counter((str(layer), str(geometry)) for layer, geometry in zip(frame["Layer"], frame.geom_type, strict=True))
    entity_types = Counter(str(subclasses).split(":")[-1] for subclasses in frame["SubClasses"].fillna("Unknown"))
    info = {"version": version, "coordinates": "drawing_units",
            "entity_types": dict(sorted(entity_types.items())),
            "layers": [{"layer": layer, "geometry": geometry, "count": count}
                       for (layer, geometry), count in sorted(counts.items())]}
    ctx.json("drawing.json", info, "drawing")
    return {"drawing": info, "statistics": {"feature_count": len(frame), "layer_count": frame["Layer"].nunique()}}


def convert(ctx, source_crs: str, layer: str | None = None, plan: str | None = None, force_2d: bool = False):
    version, frame = drawing(ctx)
    if layer is not None:
        frame = frame[frame["Layer"] == layer].copy()
    if frame.empty or frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise ValueError("CAD selection has no usable geometry or contains an unsupported entity")
    frame = frame.set_crs(CRS.from_user_input(source_crs))
    if force_2d:
        frame.geometry = shapely.force_2d(frame.geometry.array)
    if plan:
        definition = get_plan(ctx.workspace, plan)
        frame = apply_plan(frame, definition, ctx.workspace.root)
        ctx.json("transformation.json", definition, "transform")
    ctx.json("drawing.json", {"version": version, "selected_layer": layer,
                              "source_crs": source_crs, "output_crs": frame.crs.to_wkt(), "force_2d": force_2d}, "drawing")
    ctx.publish(frame)
    counts = frame.groupby("Layer").size()
    publish_report(ctx, "CAD 坐标整理", ("图层", "实体数"), [(str(name), int(count)) for name, count in counts.items()],
                   measure=2, note=f"DXF {version}。坐标依据 {plan or source_crs}。")
