"""Submit, supervise and inspect vector tasks without a GIS host."""
from collections.abc import Callable
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from time import monotonic
from uuid import uuid4

from guizhi.domain.contracts import Job, Run, TaskSpec
from guizhi.domain.diagnostics import Fault
from guizhi.domain.operations import OPERATIONS
from guizhi.infrastructure.workspace import SOURCE_ROOT, Workspace, supervisor_lock


def product_version() -> str:
    version = (SOURCE_ROOT / "VERSION").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError("VERSION must contain architecture.feature.fix")
    return version


def worker(workspace: Workspace, run_id: str) -> dict:
    from guizhi.infrastructure.control import check_control
    from guizhi.infrastructure.runtime_identity import runtime_identity
    from guizhi.infrastructure.vector import inventory

    record = workspace.read(run_id)
    if record["status"] != "running":
        raise ValueError("Worker requires an active run")
    directory = workspace.directory(run_id)
    workflow = record["task"]["operation"] == "workflow.execute"
    # Exactly one worker can claim the artifact directory for a run.
    (directory / "artifacts").mkdir()

    def ensure_supervised():
        with supervisor_lock(directory / "supervisor.lock") as acquired:
            if acquired:
                raise RuntimeError("Worker lost its supervisor")
        if workflow and workspace.status(run_id) == "cancelling":
            raise InterruptedError("Workflow cancelled")

    def progress(step, **details):
        ensure_supervised()
        workspace.event(run_id, step, **details)

    progress("worker_started", pid=os.getpid(), runtime=runtime_identity())
    arguments = (record["task"], record["job"], directory, progress, ensure_supervised)
    try:
        if record["task"].get("request") is not None:
            from guizhi.infrastructure.operations.runner import execute

            return execute(record["task"]["request"], record["job"], directory, workspace,
                           progress, ensure_supervised)
        if record["task"]["operation"] == "control_check":
            return check_control(*arguments, workspace.root)
        return inventory(*arguments)
    except Exception as exc:
        if workspace.status(run_id) == "running":
            if isinstance(exc, Fault):
                failure = exc.fact()
            elif isinstance(exc, PermissionError):
                failure = {"category": "environment", "code": "permission_denied",
                           "evidence": {"errno": exc.errno, "winerror": getattr(exc, "winerror", None)}}
            else:
                failure = {"category": "undetermined", "code": "unclassified_exception",
                           "evidence": {"exception": type(exc).__name__}}
            progress("diagnostic_failure", **failure)
        raise


def supervise(workspace: Workspace, run_id: str, cancel_requested: Callable[[], bool] | None):
    workflow = workspace.read(run_id)["task"]["operation"] == "workflow.execute"
    cancel_deadline = None
    command = [
        sys.executable, "-I", "-B", str(Path(__file__).resolve().parents[1] / "__main__.py"),
        "--workspace", str(workspace.root), "_worker", run_id,
    ]
    with subprocess.Popen(
        command, cwd=workspace.directory(run_id),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
    ) as process:
        try:
            while True:
                if cancel_requested is not None and cancel_requested():
                    workspace.request_cancel(run_id)
                if workspace.status(run_id) == "cancelling":
                    if not workflow or (cancel_deadline is not None and monotonic() >= cancel_deadline):
                        process.kill()
                        process.communicate()
                        workspace.finish(run_id, "cancelled")
                        return
                    if cancel_deadline is None:
                        cancel_deadline = monotonic() + 5
                try:
                    output, error = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if cancel_requested is not None and cancel_requested():
                workspace.request_cancel(run_id)
            if process.returncode:
                workspace.finish(run_id, "failed", error=error.strip() or f"Worker exited {process.returncode}")
            else:
                workspace.finish(run_id, "succeeded", result=json.loads(output))
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.communicate()
            raise


def run_vector(root: Path, source: Path, layer: str, object_id: str | None,
               cancel_requested: Callable[[], bool] | None = None, *,
               operation: str = "vector_inventory", control: tuple[Path, str] | None = None,
               project_plan: str | None = None, control_plan: str | None = None) -> dict:
    definition = OPERATIONS[operation]
    source = source.resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".gpkg":
        raise ValueError("Input must be a GeoPackage file")
    if not layer.strip() or (object_id is not None and not object_id.strip()):
        raise ValueError("Layer and any supplied object ID must be non-empty")
    control_source = control_layer = None
    if operation == "control_check":
        if control is None or not control[1].strip():
            raise ValueError("Control GeoPackage and layer are required")
        target = control[0].resolve(strict=True)
        if not target.is_file() or target.suffix.lower() != ".gpkg":
            raise ValueError("Control input must be a GeoPackage file")
        control_source, control_layer = str(target), control[1]
    workspace = Workspace(root, create=True)
    if project_plan or control_plan:
        from guizhi.infrastructure.crs_catalog import get_plan

        if operation != "control_check":
            raise ValueError("Transformation plans apply to control checks")
        project_plan = get_plan(workspace, project_plan) if project_plan else None
        control_plan = get_plan(workspace, control_plan) if control_plan else None
    task = TaskSpec(uuid4().hex, 1, str(source), layer, object_id or uuid4().hex,
                    operation, definition.version, definition.rule, control_source, control_layer,
                    project_plan, control_plan)
    return submit(workspace, task, cancel_requested)


def run_operation(root, payload, cancel_requested=None):
    from guizhi.domain.capabilities import CAPABILITIES, OperationRequest

    request = OperationRequest.model_validate(payload)
    spec = CAPABILITIES.get(request.operation)
    if spec is None:
        raise ValueError(f"Unknown operation: {request.operation}")
    if not spec.minimum <= len(request.inputs) <= spec.maximum:
        raise ValueError(f"{request.operation} requires {spec.minimum}–{spec.maximum} inputs")
    if spec.edit and (not request.idempotency_key or (request.inputs and request.inputs[0].revision is None)):
        raise ValueError("An edit requires an idempotency key and the target snapshot revision")
    for item in request.inputs:
        path = Path(item.file).resolve(strict=True)
        if not path.is_file() or item.kind not in spec.kinds:
            raise ValueError(f"{request.operation} accepts material types: {', '.join(spec.kinds)}")
        item.file = str(path)
    payload = request.model_dump(mode="json")
    fingerprint = sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
    task = TaskSpec(uuid4().hex, 1, None, None, uuid4().hex, request.operation, spec.version,
                    request=payload)
    return submit(Workspace(root, create=True), task, cancel_requested,
                  key=request.idempotency_key, fingerprint=fingerprint)


def submit(workspace, task, cancel_requested, *, key=None, fingerprint=None):
    run = Run(uuid4().hex, task.id, task.revision, product_version())
    job = Job(uuid4().hex, run.id, task.operation)
    directory = workspace.directory(run.id)
    directory.mkdir()
    lock = directory / "supervisor.lock"
    lock.write_bytes(b"\0")
    published = None
    try:
        with supervisor_lock(lock) as acquired:
            if not acquired:
                raise RuntimeError("Unable to acquire new run lock")
            published = workspace.start(task, run, job, key=key, fingerprint=fingerprint)
            if published == run.id:
                try:
                    supervise(workspace, run.id, cancel_requested)
                except KeyboardInterrupt:
                    workspace.request_cancel(run.id)
                    workspace.finish(run.id, "cancelled")
                except Exception as exc:
                    workspace.finish(run.id, "failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        if published != run.id:
            lock.unlink()
            directory.rmdir()
    return inspect(workspace, published)


def inspect(workspace: Workspace, run_id: str, *, cancel: bool = False) -> dict:
    record = workspace.read(run_id)
    if record["status"] in {"running", "cancelling"}:
        with supervisor_lock(workspace.directory(run_id) / "supervisor.lock") as acquired:
            if acquired:
                workspace.finish(run_id, "interrupted", error="Supervisor exited before publishing a result")
            elif cancel:
                workspace.request_cancel(run_id)
        record = workspace.read(run_id)
    return record
