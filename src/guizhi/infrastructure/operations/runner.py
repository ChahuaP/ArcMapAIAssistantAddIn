"""Execute a registered operation against sealed input revisions."""
from dataclasses import asdict
from hashlib import file_digest, sha256
from importlib import import_module
from inspect import Parameter, signature
import json
from pathlib import Path
from typing import get_type_hints
from uuid import uuid4
import warnings

from pandas.testing import assert_frame_equal
import pyogrio
import shapely
from pydantic import ConfigDict, create_model

from guizhi.domain.capabilities import CAPABILITIES, OperationRequest
from guizhi.domain.contracts import EvidenceRef
from guizhi.infrastructure.vector import digest, import_layer


def handler(operation):
    if operation not in CAPABILITIES:
        raise ValueError(f"Unknown operation: {operation}")
    domain, name = operation.split(".")
    return getattr(import_module(f"guizhi.infrastructure.operations.{domain}"), name)


def parameter_model(function):
    fields = {}
    hints = get_type_hints(function, include_extras=True)
    for name, param in list(signature(function).parameters.items())[1:]:
        fields[name] = (hints[name], ... if param.default is Parameter.empty else param.default)
    return create_model(function.__name__, __config__=ConfigDict(extra="forbid"), **fields)


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
        self.info = []
        self.selection_hashes = []
        self.result = {}
        for i, material in enumerate(request.inputs):
            frame, info, revision, evidence = import_layer(
                material.file, material.layer, directory, f"input-{i + 1}.gpkg", job["id"], check,
                allow_undefined=request.operation in {"data.describe", "data.list_fields", "crs.assign"},
            )
            stable = data_revision(directory / evidence.path)
            if material.revision is not None and material.revision != stable:
                raise ValueError(f"Input {i + 1} differs from its expected snapshot revision")
            self.frames.append(frame)
            self.info.append(info)
            self.datasets.append(asdict(revision))
            self.evidence.append(asdict(evidence))
            self.selection_hashes.append(stable)
        progress("inputs_sealed", count=len(self.frames))

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

    def publish(self, frame, *, geometry_type=None):
        self.check()
        out = self.path("result.gpkg")
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
            pyogrio.write_dataframe(frame, out, layer="result", driver="GPKG", geometry_type=geometry_type,
                                    promote_to_multi=False,
                                    layer_options={"FID": available("fid"), "GEOMETRY_NAME": available("geom")})
            check = pyogrio.read_dataframe(out, layer="result")
        if check.crs != frame.crs or shapely.to_wkb(frame.geometry).tolist() != shapely.to_wkb(check.geometry).tolist():
            raise ValueError("Export failed geometry or CRS verification")
        assert_frame_equal(frame.drop(columns=frame.geometry.name), check.drop(columns=check.geometry.name),
                           check_dtype=False, check_exact=True)
        self.record("export", out)
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
    params = model.model_validate(request.parameters)
    ctx = Execution(request, job, directory, workspace, progress, check)
    return ctx.finish(function(ctx, **dict(params)))
