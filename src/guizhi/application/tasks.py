"""Submit, supervise and inspect vector tasks without a GIS host."""
from collections.abc import Callable
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

from guizhi.domain.contracts import Job, Run, TaskSpec
from guizhi.infrastructure.workspace import SOURCE_ROOT, Workspace, supervisor_lock


def product_version() -> str:
    version = (SOURCE_ROOT / "VERSION").read_text(encoding="ascii").strip()
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError("VERSION must contain architecture.feature.fix")
    return version


def worker(workspace: Workspace, run_id: str) -> dict:
    from guizhi.infrastructure.vector import inventory

    record = workspace.read(run_id)
    if record["status"] != "running":
        raise ValueError("Worker requires an active run")
    directory = workspace.directory(run_id)
    # Exactly one worker can claim the artifact directory for a run.
    (directory / "artifacts").mkdir()

    def ensure_supervised():
        with supervisor_lock(directory / "supervisor.lock") as acquired:
            if acquired:
                raise RuntimeError("Worker lost its supervisor")

    def progress(step, **details):
        ensure_supervised()
        workspace.event(run_id, step, **details)

    progress("worker_started", pid=os.getpid())
    return inventory(record["task"], record["job"], directory, progress, ensure_supervised)


def supervise(workspace: Workspace, run_id: str, cancel_requested: Callable[[], bool] | None):
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
                    process.kill()
                    process.communicate()
                    workspace.finish(run_id, "cancelled")
                    return
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
               cancel_requested: Callable[[], bool] | None = None) -> dict:
    source = source.resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != ".gpkg":
        raise ValueError("Input must be a GeoPackage file")
    if not layer.strip() or (object_id is not None and not object_id.strip()):
        raise ValueError("Layer and any supplied object ID must be non-empty")
    task = TaskSpec(uuid4().hex, 1, str(source), layer, object_id or uuid4().hex)
    run = Run(uuid4().hex, task.id, task.revision, product_version())
    job = Job(uuid4().hex, run.id, task.operation)
    workspace = Workspace(root, create=True)
    directory = workspace.directory(run.id)
    directory.mkdir()
    lock = directory / "supervisor.lock"
    lock.write_bytes(b"\0")
    with supervisor_lock(lock) as acquired:
        if not acquired:
            raise RuntimeError("Unable to acquire new run lock")
        workspace.start(task, run, job)
        try:
            supervise(workspace, run.id, cancel_requested)
        except KeyboardInterrupt:
            workspace.request_cancel(run.id)
            workspace.finish(run.id, "cancelled")
        except Exception as exc:
            workspace.finish(run.id, "failed", error=f"{type(exc).__name__}: {exc}")
    return workspace.read(run.id)


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
