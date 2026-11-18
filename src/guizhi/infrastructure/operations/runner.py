"""Execute a registered operation against sealed input revisions."""
from dataclasses import asdict
from functools import lru_cache
from hashlib import file_digest, sha256
from importlib import import_module
from inspect import Parameter, signature
import json
from pathlib import Path
import shutil
from typing import get_type_hints
from uuid import uuid4
import warnings

from pandas.testing import assert_frame_equal
import pyogrio
import shapely
from pydantic import BaseModel, ConfigDict, ValidationError, create_model, model_validator

from guizhi.domain.capabilities import CAPABILITIES, OperationRequest
from guizhi.domain.contracts import EvidenceRef
from guizhi.domain.diagnostics import Fault
from guizhi.infrastructure.vector import digest, import_layer, source_revision


def handler(operation):
    if operation not in CAPABILITIES:
        raise ValueError(f"Unknown operation: {operation}")
    domain, name = operation.split(".")
    return getattr(import_module(f"guizhi.infrastructure.operations.{domain}"), name)


@lru_cache(maxsize=128)
def parameter_model(function):
    fields = {}
    hints = get_type_hints(function, include_extras=True)
    for name, param in list(signature(function).parameters.items())[1:]:
        fields[name] = (hints[name], ... if param.default is Parameter.empty else param.default)
    validators = {}
    from guizhi.domain.operation_catalog import EXCLUSIVE_PARAMETERS

    operation = function.__module__.rsplit(".", 1)[-1] + "." + function.__name__
    exclusive = EXCLUSIVE_PARAMETERS.get(operation)
    if exclusive:
        @model_validator(mode="before")
        @classmethod
        def one_filter(cls, values):
            if sum(values.get(key) is not None for key in exclusive) != 1:
                raise ValueError("须提供 " + " 或 ".join(exclusive) + " 其中一项")
            return values
        validators["one_filter"] = one_filter
    return create_model(function.__name__, __config__=ConfigDict(extra="forbid"), __validators__=validators, **fields)


class NoParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ControlParameters(NoParameters):
    project_plan: str | None = None
    control_plan: str | None = None
    output_layer: bool = False


def operation_parameters(operation):
    if operation == "control_check":
        return ControlParameters
    if operation == "vector_inventory":
        return NoParameters
    return parameter_model(handler(operation))


def capability(operation):
    from guizhi.application.skills import OPERATIONS
    from guizhi.domain.operation_catalog import describe_contract
    from guizhi.domain.operation_parameters import parameter_help

    spec = OPERATIONS[operation]
    schema = operation_parameters(operation).model_json_schema()
    for name, definition in schema["properties"].items():
        definition.update(parameter_help(operation, name))
    return {**asdict(spec), **describe_contract(operation, spec), "parameters": schema}


def data_revision(path):
    """Hash a sealed SQLite image independently of backup-maintained header counters."""
    with path.open("rb") as stream:
        header = bytearray(stream.read(100))
        if len(header) != 100 or header[:16] != b"SQLite format 3\0":
            raise ValueError("Invalid SQLite database header")
        # Change counter, schema cookie, version-valid-for and SQLite writer version.
        for offset in (24, 40, 92, 96):
            header[offset:offset + 4] = b"\0" * 4
        checksum = sha256(header)
        return file_digest(stream, lambda: checksum).hexdigest()


class Execution:
    def __init__(self, request, job, directory, workspace, progress, check):
        self.request = request
        self.job = job
        self.directory = directory
        self.workspace = workspace
        self.progress = progress
        self.check = check
        self.frames, self.datasets, self.evidence = [], [], []
        self.documents = []
        self.rasters, self.drawings = [], []
        self.models = []
        self.info = []
        self.selection_hashes = []
        self.result = {}
        for i, material in enumerate(request.inputs):
            if material.kind == "document":
                file, ref = self.seal_document(material.file, material.revision, i + 1)
                self.documents.append((file, ref))
                self.snapshot(ref, None, ref["sha256"])
                continue
            if material.kind in {"raster", "cad", "model"}:
                file, ref = self.seal_file(material.kind, material.file, material.revision, i + 1)
                {"raster": self.rasters, "cad": self.drawings, "model": self.models}[material.kind].append(file)
                self.snapshot(ref, None, ref["sha256"])
                continue
            source = Path(material.file)
            frame, info, revision, evidence, originals = import_layer(
                material.file, material.layer, directory, f"input-{i + 1}.gpkg", job["id"], check,
                allow_undefined=request.operation in {"data.describe", "data.list_fields", "data.select_by_location",
                                                      "table.lookup", "crs.assign", "crs.assign_from"},
                metadata_only=request.operation in {"data.describe", "data.list_fields"},
                expected_source_revision=material.revision,
            )
            stable = data_revision(directory / evidence.path)
            if source.suffix.lower() == ".gpkg" and material.revision is not None and material.revision != stable:
                raise Fault("association", "input_revision_mismatch", f"Input {i + 1} differs from its expected snapshot revision",
                            input_number=i + 1, expected=material.revision, actual=stable)
            self.frames.append(frame)
            self.info.append(info)
            self.datasets.append(asdict(revision))
            self.evidence.extend(asdict(ref) for ref in (evidence, *originals))
            self.selection_hashes.append(stable)
            self.snapshot(asdict(evidence), material.layer, stable)
        progress("inputs_sealed", count=len(request.inputs))

    def snapshot(self, ref, layer, revision, *, role=None):
        self.progress("diagnostic_snapshot", ref=ref, layer=layer, revision=revision, role=role)

    def seal_document(self, source, expected, number):
        if Path(source).stat().st_size > 50 << 20:
            raise ValueError("审批文档大小不得超过 50 MB")
        return self.seal_file("document", source, expected, number)

    def seal_file(self, kind, source, expected, number):
        self.check()
        source = Path(source)
        if kind == "model" and not 0 < source.stat().st_size <= 50 << 20:
            raise ValueError("Expected a self-contained ONNX model up to 50 MB")
        step = f"{kind}-{number}"
        if kind == "raster":
            from guizhi.infrastructure.operations.raster import normalize, source_files

            files = source_files(source)
            if len(files) > 1:
                before = source_revision(files)
                if expected and expected != before:
                    raise Fault("association", "input_revision_mismatch", "影像或配套文件已变化，请重新添加")
                original = self.path(step + "-original")
                original.mkdir()
                copies = []
                for i, file in enumerate(files, 1):
                    self.check()
                    target = original / file.name
                    shutil.copyfile(file, target)
                    self.record(f"{step}-source-{i}", target)
                    copies.append(target)
                if source_revision(copies) != before or source_revision(source_files(source)) != before:
                    raise ValueError("复制期间影像或配套文件已变化")
                output = self.path(step + ".tif")
                normalize(copies[0], output, self.check)
                return output, self.record(step, output)
        before = digest(source)
        if expected is not None and before != expected:
            raise Fault("association", "input_revision_mismatch", "Material differs from its expected revision",
                        input_number=number, expected=expected, actual=before)
        copy = self.path(f"{step}{source.suffix.lower()}")
        shutil.copyfile(source, copy)
        ref = self.record(step, copy)
        if ref["sha256"] != before or digest(source) != before:
            raise ValueError("Material changed while importing")
        return copy, ref

    def path(self, filename):
        if Path(filename).name != filename:
            raise ValueError("Artifact requires a local filename")
        return self.directory / "artifacts" / filename

    def record(self, step, path):
        entry = EvidenceRef(uuid4().hex, self.datasets[0]["id"] if self.datasets else None,
                            self.job["id"], step, path.relative_to(self.directory).as_posix(), digest(path))
        self.evidence.append(asdict(entry))
        return asdict(entry)

    def json(self, name, value, step):
        path = self.path(name)
        path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        return self.record(step, path)

    def publish(self, frame, *, geometry_type=None, artifact="export", filename="result.gpkg", preserve_fids=False):
        self.check()
        out = self.path(filename)
        if not preserve_fids:
            frame = frame.reset_index(drop=True)
        if frame.crs is None:
            raise ValueError("Result requires a defined CRS")
        # GeoPackage reserves its FID and geometry columns; choose names outside attribute names.
        names = {str(column).casefold() for column in frame.drop(columns=frame.geometry.name).columns}
        def available(base):
            while base.casefold() in names:
                base += "_"
            names.add(base.casefold())
            return base
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            fid, geometry = available("fid"), available("geom")
            data = frame.assign(**{fid: frame.index.astype("int64")}) if preserve_fids else frame
            pyogrio.write_dataframe(data, out, layer="result", driver="GPKG", geometry_type=geometry_type,
                                    promote_to_multi=False,
                                    layer_options={"FID": fid, "GEOMETRY_NAME": geometry})
            check = pyogrio.read_dataframe(out, layer="result", fid_as_index=preserve_fids)
        if check.crs != frame.crs or shapely.to_wkb(frame.geometry).tolist() != shapely.to_wkb(check.geometry).tolist():
            raise Fault("tool", "export_geometry_mismatch", "Export failed geometry or CRS verification",
                        same_crs=check.crs == frame.crs, expected_count=len(frame), actual_count=len(check))
        assert_frame_equal(frame.drop(columns=frame.geometry.name), check.drop(columns=check.geometry.name),
                           check_dtype=False, check_exact=True)
        self.record(artifact, out)
        if artifact == "export":
            self.result.update(export_layer="result", data_revision=data_revision(out),
                               statistics={"feature_count": len(frame)})

    def finish(self, payload):
        result = {**self.result, **(payload or {}), "datasets": self.datasets,
                  "evidence": self.evidence, "validation": {"artifacts_verified": True}}
        self.progress("operation_verified", artifacts=len(self.evidence))
        return result


def execute(request, job, directory, workspace, progress, check):
    request = OperationRequest.model_validate(request)
    function = handler(request.operation)
    model = parameter_model(function)
    try:
        params = model.model_validate(request.parameters)
    except ValidationError as exc:
        fields = [{"field": list(item["loc"]), "rule": item["type"]}
                  for item in exc.errors(include_input=False, include_url=False)]
        raise Fault("parameter", "parameter_schema", str(exc), fields=fields) from exc
    ctx = Execution(request, job, directory, workspace, progress, check)
    return ctx.finish(function(ctx, **dict(params)))
