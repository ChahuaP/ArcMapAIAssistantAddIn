"""GeoPackage snapshot, inventory and verified export."""
from contextlib import closing
from dataclasses import asdict
from functools import lru_cache
from hashlib import file_digest, sha256
from math import isclose
from pathlib import Path
import sqlite3
import shutil
from time import monotonic
from uuid import uuid4
import warnings

import geopandas
from pandas.testing import assert_frame_equal
import pyogrio
from pyproj import CRS
from pyproj.exceptions import CRSError
import shapely

from guizhi.domain.contracts import ClaimRevision, DatasetRevision, EvidenceRef, ObjectRevision
from guizhi.domain.diagnostics import Fault

SOURCE_LOCK_WAIT = 5


@lru_cache(maxsize=128)
def mixed_layer_family(file, layer, column, stamp):
    def quoted(name):
        return '"' + name.replace('"', '""') + '"'
    geom = quoted(column)
    rows = pyogrio.read_dataframe(file, sql=f"SELECT DISTINCT ST_GeometryType({geom}) AS kind "
        f"FROM {quoted(layer)} WHERE {geom} IS NOT NULL LIMIT 3", sql_dialect="SQLITE", read_geometry=False)
    kinds = {str(value).upper().removeprefix("ST_").removeprefix("MULTI") for value in rows["kind"]}
    for family in ("Point", "LineString", "Polygon"):
        if kinds == {family.upper()}:
            return family
    return "Unknown"


def layer_geometry(info, file):
    declared = info["geometry_type"]
    if declared == "Unknown":
        family = (info.get("layer_metadata") or {}).get("GEOMETRY_FAMILY")
        if family in {"Point", "LineString", "Polygon"}:
            return family
        if Path(file).suffix.lower() == ".gpkg" and info.get("geometry_name"):
            stat = Path(file).stat()
            try:
                wal = Path(str(file) + "-wal").stat()
                wal_stamp = (wal.st_size, wal.st_mtime_ns, wal.st_ctime_ns)
            except FileNotFoundError:
                wal_stamp = ()
            return mixed_layer_family(str(file), info["layer_name"], info["geometry_name"],
                                      (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, wal_stamp))
    return declared


def storage_geometry(frame, declared=None):
    """GeoPackage GEOMETRY permits single and multipart values in the same feature table."""
    types = set(frame.geom_type.dropna().unique())
    for family in ("Point", "LineString", "Polygon"):
        if types == {family, "Multi" + family}:
            return {"geometry_type": "Unknown", "layer_metadata": {"GEOMETRY_FAMILY": family}}
    if declared in {"Point", "Polygon", "LineString"} and types == {"Multi" + declared}:
        return {"geometry_type": "Multi" + declared}
    return {"geometry_type": declared}


def snapshot_gpkg(source: Path, target: Path, layer: str, ensure_supervised, *, allow_undefined=False):
    # SQLite backup captures a coherent database, including committed WAL data.
    blocked_at = None

    def progress(status, _remaining, _total):
        nonlocal blocked_at
        ensure_supervised()
        if status & 0xff not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            blocked_at = None
            return
        now = monotonic()
        if blocked_at is None:
            blocked_at = now
        elif now - blocked_at >= SOURCE_LOCK_WAIT:
            raise Fault("environment", "source_locked", "GeoPackage 正被其他进程占用，请解除占用后重试。",
                        wait_seconds=SOURCE_LOCK_WAIT)

    incoming = sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True, timeout=0.25)
    try:
        outgoing = sqlite3.connect(target)
        try:
            incoming.backup(outgoing, pages=256, progress=progress, sleep=0.1)
        finally:
            outgoing.close()
    finally:
        incoming.close()
    with closing(sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA application_id").fetchone()[0] != 0x47504B47:
            raise ValueError("Input is not a GeoPackage")
        row = db.execute(
            """SELECT g.srs_id, g.m, s.organization, s.definition
               FROM gpkg_geometry_columns g JOIN gpkg_spatial_ref_sys s ON s.srs_id = g.srs_id
               WHERE g.table_name = ?""", (layer,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Vector layer not found: {layer}")
        undefined = row[0] in {-1, 0} or (
            row[2] == "GDAL" and row[3].startswith('LOCAL_CS["Undefined SRS",')
        )
        if undefined and not allow_undefined:
            raise ValueError(f"Layer has no defined CRS: {layer}")
        if row[1]:
            raise ValueError("Measured geometries (M) are outside this export contract")
        return not undefined


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def source_files(path):
    if path.suffix.lower() != ".shp":
        return [path]
    allowed = {".shp", ".shx", ".dbf", ".prj", ".cpg"}
    parts = {}
    for item in path.parent.iterdir():
        suffix = item.suffix.lower()
        if item.stem.casefold() == path.stem.casefold() and suffix in allowed and item.is_file():
            if suffix in parts or item.is_symlink():
                raise ValueError(f"SHP 配套文件重复或指向其他位置：{item.name}")
            parts[suffix] = item
    missing = {".shp", ".shx", ".dbf"} - parts.keys()
    if missing:
        raise ValueError(f"{path.name} 缺少同名配套文件：{'、'.join(sorted(missing))}")
    return [parts[suffix] for suffix in sorted(parts)]


def projection_text(path, data):
    try:
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return data.decode("gb18030")
    except UnicodeDecodeError as error:
        raise ValueError(f"{path.name} 的坐标系定义无法读取，请检查配套 .prj 文件") from error


def shapefile_projection(files):
    path = next((item for item in files if item.suffix.lower() == ".prj"), None)
    if path is None:
        return None
    try:
        return CRS.from_wkt(projection_text(path, path.read_bytes()))
    except CRSError as error:
        raise ValueError(f"{path.name} 的坐标系定义无法读取，请检查配套 .prj 文件") from error


def source_revision(files):
    if len(files) == 1:
        return digest(files[0])
    return sha256("\n".join(f"{file.name.casefold()} {digest(file)}" for file in files).encode()).hexdigest()


def snapshot_external(source, snapshot, layer, ensure_supervised, allow_undefined, expected, metadata_only):
    files = source_files(source)
    initial = source_revision(files)
    if expected and expected != initial:
        raise Fault("association", "input_revision_mismatch", "矢量材料或配套文件已变化，请重新添加")
    original = snapshot.parent / (snapshot.stem + "-original")
    original.mkdir()
    copies = []
    # Use a common basename so GDAL finds companions after copying, including on case-sensitive hosts.
    for file in files:
        ensure_supervised()
        target = original / (source.stem + file.suffix.lower())
        shutil.copyfile(file, target)
        copies.append(target)
    if source_revision(copies) != initial or source_revision(source_files(source)) != initial:
        raise ValueError("复制期间矢量材料或配套文件已变化")
    copied = original / (source.stem + source.suffix.lower())
    if source.suffix.lower() == ".shp" and shapefile_projection(copies) is not None:
        # Preserve original .prj bytes as evidence. GDAL expects UTF-8 when returning the CRS name.
        staging = original.parent / (original.name + "-gdal")
        staging.mkdir()
        try:
            for file in copies:
                target = staging / file.name
                if file.suffix == ".prj":
                    target.write_text(projection_text(file, file.read_bytes()), encoding="utf-8")
                else:
                    target.hardlink_to(file)
            defined = convert_external(staging / copied.name, snapshot, layer, allow_undefined, ensure_supervised, metadata_only)
        finally:
            shutil.rmtree(staging)
    else:
        defined = convert_external(copied, snapshot, layer, allow_undefined, ensure_supervised, metadata_only)
    return defined, copies


def convert_external(copied, snapshot, layer, allow_undefined, check, metadata_only):
    info = pyogrio.read_info(copied, layer=layer)
    if metadata_only and copied.suffix.lower() == ".shp":
        from guizhi.infrastructure.gdal_translate import vector_translate

        names = {name.casefold() for name in info["fields"]}
        fid, geometry = "fid", "geom"
        while fid in names:
            fid += "_"
        while geometry in names:
            geometry += "_"
        crs = CRS(info["crs"]) if info["crs"] else shapefile_projection(source_files(copied))
        projection = ("-a_srs", crs.to_wkt()) if crs else ()
        # SHP line and polygon records can contain either one or several parts.
        family = info["geometry_type"] if info["geometry_type"] in {"LineString", "Polygon"} else None
        storage = ("-nlt", "GEOMETRY") if family else ()
        vector_translate(copied, snapshot, "-f", "GPKG", "-preserve_fid", *projection, *storage,
                         "-lco", "FID=" + fid, "-lco", "GEOMETRY_NAME=" + geometry,
                         "-lco", "SPATIAL_INDEX=YES", layer, geometry_family=family)
        saved = pyogrio.read_info(snapshot, layer=layer)
        before, after = info["total_bounds"], saved["total_bounds"]
        same_bounds = not info["features"] or before is None and after is None or before is not None and after is not None and all(
            isclose(a, b, rel_tol=1e-9, abs_tol=1e-8) for a, b in zip(before, after, strict=True))
        if (saved["features"] != info["features"] or saved["fields"].tolist() != info["fields"].tolist() or not same_bounds
                or (crs is not None and not crs.equals(CRS(saved["crs"]), ignore_axis_order=True))):
            raise ValueError("矢量快照的要素数、字段、范围或坐标系校验失败")
        check()
        return crs is not None
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        frame = pyogrio.read_dataframe(copied, layer=layer, fid_as_index=True)
        if not isinstance(frame, geopandas.GeoDataFrame):
            raise ValueError("请选择含空间要素的矢量图层")
        if frame.crs is None and not allow_undefined:
            raise ValueError(f"{copied.name} 缺少坐标定义，请补齐同名 .prj 或先定义已知坐标系")
        names = {name.casefold() for name in frame.columns}
        fid, geometry = "fid", "geom"
        while fid in names:
            fid += "_"
        while geometry in names:
            geometry += "_"
        # GDAL uses the FID-valued column when creating the SQLite feature table.
        data = frame.assign(**{fid: frame.index.astype("int64")})
        pyogrio.write_dataframe(data.reset_index(drop=True), snapshot, layer=layer, driver="GPKG",
                                **storage_geometry(frame, info["geometry_type"]), promote_to_multi=False,
                                layer_options={"FID": fid, "GEOMETRY_NAME": geometry})
    defined = frame.crs is not None
    reopened = pyogrio.read_dataframe(snapshot, layer=layer, fid_as_index=True)
    if ((defined and reopened.crs != frame.crs) or not reopened.index.equals(frame.index) or
            shapely.to_wkb(reopened.geometry).tolist() != shapely.to_wkb(frame.geometry).tolist()):
        raise ValueError("矢量快照未通过坐标系、要素编号或几何校验")
    assert_frame_equal(frame.drop(columns=frame.geometry.name), reopened.drop(columns=reopened.geometry.name),
                       check_dtype=False, check_exact=True)
    check()
    return defined


def import_layer(source_path, layer, directory, filename, job_id, ensure_supervised, *, allow_undefined=False,
                 expected_source_revision=None, metadata_only=False):
    source = Path(source_path)
    snapshot = directory / "artifacts" / filename
    originals = []
    if source.suffix.lower() == ".gpkg":
        defined = snapshot_gpkg(source, snapshot, layer, ensure_supervised, allow_undefined=allow_undefined)
    else:
        defined, originals = snapshot_external(source, snapshot, layer, ensure_supervised, allow_undefined,
                                               expected_source_revision, metadata_only)
    info = pyogrio.read_info(snapshot, layer=layer)
    info["geometry_type"] = layer_geometry(info, snapshot)
    crs = CRS(info["crs"]) if defined else None
    frame = None
    if not metadata_only or info["geometry_type"] in {"Unknown", "GeometryCollection"}:
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            frame = pyogrio.read_dataframe(snapshot, layer=layer, fid_as_index=True)
        if not isinstance(frame, geopandas.GeoDataFrame) or (defined and frame.crs is None):
            raise ValueError(f"Layer must contain spatial features with a defined CRS: {layer}")
        if not defined and frame.crs is not None:
            frame = frame.set_crs(None, allow_override=True)
    original_crs = shapefile_projection(originals) if defined and source.suffix.lower() == ".shp" else None
    if original_crs:
        info["source_crs_name"] = original_crs.name

    revision_id = uuid4().hex
    input_ref = EvidenceRef(uuid4().hex, revision_id, job_id, "import",
                            snapshot.relative_to(directory).as_posix(), digest(snapshot))
    dataset = DatasetRevision(revision_id, str(source), layer, input_ref.id,
                              crs.to_wkt() if crs else None)
    raw_refs = [EvidenceRef(uuid4().hex, revision_id, job_id, f"{snapshot.stem}-source-{i}",
                           file.relative_to(directory).as_posix(), digest(file)) for i, file in enumerate(originals, 1)]
    return frame, info, dataset, input_ref, raw_refs


def inventory(task: dict, job: dict, directory: Path, progress, ensure_supervised) -> dict:
    export = directory / "artifacts" / "result.gpkg"
    progress("import_started")
    frame, info, dataset, input_ref, originals = import_layer(
        task["source"], task["layer"], directory, "input.gpkg", job["id"], ensure_supervised,
    )
    frame = frame.reset_index(drop=True)
    progress("diagnostic_snapshot", ref=asdict(input_ref), layer=task["layer"], revision=input_ref.sha256, role=None)
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
            frame, export, layer="inventory", driver="GPKG", promote_to_multi=False,
            **storage_geometry(frame, info["geometry_type"]),
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
        "evidence": [asdict(ref) for ref in (input_ref, *originals, export_ref)],
        "statistics": stats,
        "validation": checks,
        "export_layer": "inventory",
        "runtime": {"gdal": pyogrio.__gdal_version_string__, "pyogrio": pyogrio.__version__},
    }
