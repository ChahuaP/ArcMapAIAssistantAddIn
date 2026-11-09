"""Exchange saved MXD layers through a short-lived, local ArcGIS 10.2 bridge."""
from pathlib import Path
import json
import subprocess
from typing import Annotated, Literal
from uuid import uuid4
import warnings

import pyogrio
import shapely
from pandas.testing import assert_frame_equal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from guizhi.domain.capabilities import Hash
from guizhi.infrastructure.operations.evidence import read_artifact
from guizhi.infrastructure.vector import digest
from guizhi.infrastructure.workspace import Workspace


class Handoff(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["describe", "import", "publish"]
    map: Path
    map_revision: Hash | None = None
    frame: Annotated[int, Field(ge=0)] = 0
    layer: Annotated[int, Field(ge=0)] | None = None
    selected_only: bool = False
    selected_ids: Annotated[list[Annotated[int, Field(ge=0)]], Field(max_length=100000)] | None = None
    expected_layer_name: str | None = None
    result_run: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")] | None = None
    style: Annotated[int, Field(ge=0)] | None = None
    name: Annotated[str, StringConstraints(min_length=1, max_length=100)] = "规智成果"


def json_file(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def bridge(bridge_exe, directory, name, request):
    file = directory / (name + ".json")
    json_file(file, request)
    try:
        process = subprocess.run([str(bridge_exe), str(file)], cwd=directory, stdin=subprocess.DEVNULL,
                                 capture_output=True, timeout=23, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        stage = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
        (directory / (name + ".log")).write_text(stage, encoding="utf-8")
        raise ValueError("ArcMap handoff timed out at " + (stage or "startup")) from None
    stage = process.stderr.decode("utf-8", errors="replace").strip()
    (directory / (name + ".log")).write_text(stage, encoding="utf-8")
    if process.returncode:
        raise ValueError("ArcMap handoff failed: " + stage.splitlines()[-1] if stage else "ArcMap handoff failed")
    return json.loads(process.stdout.decode("utf-8-sig"))


def local_data(path, dataset):
    path = Path(path).resolve(strict=True)
    if not path.drive or str(path).startswith("\\\\"):
        raise ValueError("ArcMap handoff supports local feature data")
    if not dataset or Path(dataset).name != dataset:
        raise ValueError("Unsupported ArcMap dataset identifier")
    if path.suffix.lower() == ".gdb" and path.is_dir():
        return path, dataset
    if path.is_dir():
        shape = path / (dataset if dataset.lower().endswith(".shp") else dataset + ".shp")
        if shape.is_file():
            return shape, None
    raise ValueError("The MXD layer must use a local Shapefile or File Geodatabase")


def file_stamps(source):
    files = (source.iterdir() if source.is_dir() else
             (file for file in source.parent.iterdir() if file.stem == source.stem))
    return sorted((file.name, stat.st_size, stat.st_mtime_ns)
                  for file in files if file.is_file() and file.suffix.lower() != ".lock" for stat in [file.stat()])


def export_frame(frame, output, name, driver, tolerance=0):
    if frame.crs is None:
        raise ValueError("The data has no defined coordinate system")
    if driver == "OpenFileGDB":
        if shapely.has_z(frame.geometry.array).any() or shapely.has_m(frame.geometry.array).any():
            raise ValueError("ArcMap 10.2 publication currently accepts 2D vector results")
        # ArcMap 10.2 stores wide integers as double; reject values that would lose precision.
        for series in (frame[col] for col in frame.select_dtypes(include="integer").columns):
            if any(abs(int(value)) > 2 ** 53 for value in series.dropna()):
                raise ValueError("An integer attribute cannot be represented exactly in ArcMap 10.2")
    with warnings.catch_warnings():
        if driver == "OpenFileGDB":
            warnings.filterwarnings("ignore", message="Field .* of type Integer64 will be written as a Float64", category=RuntimeWarning)
        pyogrio.write_dataframe(frame, output, layer=name, driver=driver, promote_to_multi=False)
    check = pyogrio.read_dataframe(output, layer=name)
    left, right = frame.geometry, check.geometry
    if len(frame) != len(check) or frame.crs != check.crs:
        raise ValueError("ArcMap exchange changed feature count or coordinate system")
    if not all((a is None and b is None) or
               (a is not None and b is not None and
                (a.equals_exact(b, tolerance, normalize=True) or a.equals(b)))
               for a, b in zip(left, right, strict=True)):
        raise ValueError("ArcMap exchange geometry failed round-trip verification")
    assert_frame_equal(frame.drop(columns=left.name).reset_index(drop=True), check.drop(columns=right.name),
                       check_dtype=False, check_exact=True)


def exchange(root, bridge_path, payload):
    request = Handoff.model_validate(payload)
    root = Workspace(root, create=True)
    bridge_exe = bridge_path.resolve(strict=True)
    if bridge_exe.name.casefold() != "arcmapbridge.exe" or not bridge_exe.is_file():
        raise ValueError("Select the ArcMapBridge.exe built for this version")
    identity = bridge_exe.with_name("ArcMapBridge.build")
    facts = dict(line.split("=", 1) for line in identity.read_text(encoding="utf-8-sig").splitlines() if "=" in line)
    if facts.get("binary_sha256") != digest(bridge_exe):
        raise ValueError("ArcMap Bridge integrity verification failed")
    if not request.map.is_absolute() or str(request.map).startswith("\\\\"):
        raise ValueError("Select an absolute local MXD path")
    map_path = request.map.resolve(strict=True)
    if not map_path.is_file() or map_path.suffix.lower() != ".mxd":
        raise ValueError("Select a saved ArcMap MXD")
    revision = digest(map_path)
    if request.action != "describe" and request.map_revision != revision:
        raise ValueError("The MXD changed. Read the saved document again before the handoff.")
    parent = root.root / "host-exchanges"
    if parent.resolve() != parent:
        raise ValueError("Host exchange location must not redirect")
    folder = parent / uuid4().hex
    folder.mkdir(parents=True)
    result = bridge(bridge_exe, folder, "describe", {"action": "describe", "map": str(map_path)})
    result.update(map=str(map_path), map_revision=revision, action=request.action, status="succeeded")
    if request.action != "describe":
        frames = result["frames"]
        if request.frame >= len(frames):
            raise ValueError("The selected ArcMap data frame is unavailable")
        layers = frames[request.frame]["layers"]
        if request.action == "import":
            if request.layer is None or request.layer >= len(layers):
                raise ValueError("Select a feature layer to import")
            layer = layers[request.layer]
            if request.expected_layer_name is not None and request.expected_layer_name != layer["name"]:
                raise ValueError("The saved MXD no longer contains the selected handoff layer")
            if not layer["valid"] or not layer["source"] or not layer["dataset"]:
                raise ValueError("The selected ArcMap layer has no readable feature source")
            source, table = local_data(layer["source"], layer["dataset"])
            before = file_stamps(source)
            if request.selected_ids is not None and not request.selected_only:
                raise ValueError("Explicit feature IDs require selection-only import")
            selection = (request.selected_ids if request.selected_ids is not None else layer["selected"]) \
                if request.selected_only else None
            if selection == []:
                raise ValueError("The saved ArcMap layer contains no selected features")
            # Apply the layer's saved definition query before intersecting its feature selection.
            frame = pyogrio.read_dataframe(source, layer=table, where=layer["filter"] or None, fid_as_index=True)
            if selection is not None:
                frame = frame.loc[frame.index.isin(selection)]
                if set(frame.index) != set(selection):
                    raise ValueError("One or more selected features changed or are hidden by the saved definition query")
            if not len(frame):
                raise ValueError("The saved ArcMap layer contains no usable features for this handoff")
            output = folder / "import.gpkg"
            export_frame(frame, output, "result", "GPKG")
            if before != file_stamps(source):
                raise ValueError("The ArcMap source was edited during import. Save the edits and retry.")
            result.update(file=str(output), layer="result", feature_count=len(frame), artifact_sha256=digest(output))
        else:
            if request.result_run is None or (request.style is not None and request.style >= len(layers)):
                raise ValueError("Select a verified vector result and an available style layer")
            data = root.read(request.result_run)["result"]
            file = read_artifact(root, request.result_run, "export")
            if file.suffix.lower() != ".gpkg" or not data.get("export_layer"):
                raise ValueError("ArcMap publication accepts an independent vector result")
            frame = pyogrio.read_dataframe(file, layer=data["export_layer"])
            gdb = folder / "result.gdb"
            tolerance = 1e-8 if frame.crs and frame.crs.is_geographic else 0.0002
            export_frame(frame, gdb, "guizhi_result", "OpenFileGDB", tolerance)
            output = folder / "published.mxd"
            bridge(bridge_exe, folder, "publish", {"action": "publish", "map": str(map_path), "output": str(output),
                   "frame": request.frame, "database": str(gdb), "featureClass": "guizhi_result",
                   "name": request.name, "style": request.style})
            reopened = bridge(bridge_exe, folder, "verify", {"action": "describe", "map": str(output)})
            published = [item for item in reopened["frames"][request.frame]["layers"]
                         if item["valid"] and item["dataset"] == "guizhi_result" and item["source"]
                         and Path(item["source"]).resolve() == gdb]
            if len(published) != 1 or published[0]["name"] != request.name:
                raise ValueError("Published MXD does not reference its verified result layer")
            result.update(output=str(output), output_sha256=digest(output), published=published[0],
                          feature_count=len(frame), coordinate_tolerance=tolerance, source_run=request.result_run)
    if digest(map_path) != revision:
        raise ValueError("The source MXD changed during the handoff. Read it again.")
    manifest = folder / "handoff.json"
    result["manifest"] = str(manifest)
    json_file(manifest, result)
    return result
