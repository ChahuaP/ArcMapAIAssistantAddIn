"""GeoPackage snapshot, inventory and verified export."""
from contextlib import closing
from dataclasses import asdict
from hashlib import file_digest
from pathlib import Path
import sqlite3
from uuid import uuid4
import warnings

import geopandas
from pandas.testing import assert_frame_equal
import pyogrio
import shapely

from guizhi.domain.contracts import ClaimRevision, DatasetRevision, EvidenceRef, ObjectRevision


def snapshot_gpkg(source: Path, target: Path, layer: str, ensure_supervised):
    # SQLite backup captures a coherent database, including committed WAL data.
    incoming = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=0.25)
    try:
        outgoing = sqlite3.connect(target)
        try:
            incoming.backup(outgoing, pages=256, progress=lambda *_: ensure_supervised(), sleep=0.1)
        finally:
            outgoing.close()
    finally:
        incoming.close()
    with closing(sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA application_id").fetchone()[0] != 0x47504B47:
            raise ValueError("Input is not a GeoPackage")
        row = db.execute(
            "SELECT srs_id, m FROM gpkg_geometry_columns WHERE table_name = ?", (layer,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Vector layer not found: {layer}")
        if row[0] in {-1, 0}:
            raise ValueError(f"Layer has no defined CRS: {layer}")
        if row[1]:
            raise ValueError("Measured geometries (M) are outside this export contract")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def inventory(task: dict, job: dict, directory: Path, progress, ensure_supervised) -> dict:
    source = Path(task["source"])
    snapshot = directory / "artifacts" / "input.gpkg"
    export = directory / "artifacts" / "result.gpkg"
    layer = task["layer"]
    progress("import_started")
    snapshot_gpkg(source, snapshot, layer, ensure_supervised)
    info = pyogrio.read_info(snapshot, layer=layer)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        frame = pyogrio.read_dataframe(snapshot, layer=layer)
    if not isinstance(frame, geopandas.GeoDataFrame) or frame.crs is None:
        raise ValueError(f"Layer must contain spatial features with a defined CRS: {layer}")

    revision_id = uuid4().hex
    input_ref = EvidenceRef(uuid4().hex, revision_id, job["id"], "import",
                            snapshot.relative_to(directory).as_posix(), digest(snapshot))
    dataset = DatasetRevision(revision_id, str(source), layer, input_ref.id, frame.crs.to_wkt())
    obj = ObjectRevision(uuid4().hex, task["object_id"], dataset.id)
    progress("imported", dataset_revision=asdict(dataset), evidence=asdict(input_ref))

    stats = {
        "feature_count": len(frame),
        "geometry_types": frame.geom_type.fillna("null").value_counts().to_dict(),
        "empty_geometries": int(frame.geometry.is_empty.sum()),
    }
    progress("counted", **stats)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        pyogrio.write_dataframe(
            frame, export, layer="inventory", driver="GPKG",
            layer_options={"FID": info["fid_column"], "GEOMETRY_NAME": info["geometry_name"]},
        )
        reopened = pyogrio.read_dataframe(export, layer="inventory")
    if reopened.crs != frame.crs:
        raise ValueError("Export CRS differs from the imported CRS")
    if shapely.to_wkb(reopened.geometry).tolist() != shapely.to_wkb(frame.geometry).tolist():
        raise ValueError("Export geometries differ from imported geometries")
    assert_frame_equal(
        frame.drop(columns=frame.geometry.name),
        reopened.drop(columns=reopened.geometry.name),
        check_dtype=False, check_exact=True,
    )
    export_ref = EvidenceRef(uuid4().hex, dataset.id, job["id"], "export",
                             export.relative_to(directory).as_posix(), digest(export))
    checks = {"feature_count": len(reopened), "crs_preserved": True,
              "geometries_preserved": True, "attributes_preserved": True}
    progress("export_verified", **checks)
    claim = ClaimRevision(uuid4().hex, obj.id, job["id"], "feature_count", len(frame),
                          "feature", (input_ref.id, export_ref.id))
    return {
        "dataset_revision": asdict(dataset),
        "object_revision": asdict(obj),
        "claim_revision": asdict(claim),
        "evidence": [asdict(input_ref), asdict(export_ref)],
        "statistics": stats,
        "validation": checks,
        "export_layer": "inventory",
        "runtime": {"gdal": pyogrio.__gdal_version_string__, "pyogrio": pyogrio.__version__},
    }
