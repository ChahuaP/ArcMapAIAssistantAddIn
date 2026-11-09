"""Read committed results and verify artifact bytes."""
import json
from typing import Annotated
from pydantic import Field

from guizhi.infrastructure.vector import digest


def read_artifact(workspace, run_id, step):
    record = workspace.read(run_id)
    if record["status"] != "succeeded":
        raise ValueError("The referenced run has no committed result")
    refs = [item for item in record["result"]["evidence"] if item["step"] == step]
    if len(refs) != 1:
        raise ValueError(f"Run must have exactly one {step} artifact")
    file = artifact_path(workspace, run_id, refs[0]["path"])
    if not file.is_file() or digest(file) != refs[0]["sha256"]:
        raise ValueError("Artifact is missing or has changed")
    return file


def artifact_path(workspace, run_id, relative):
    root = workspace.directory(run_id)
    path = (root / relative).resolve()
    if not path.is_relative_to(root / "artifacts"):
        raise ValueError("Invalid artifact path")
    return path


def verify(ctx, run_id: str):
    record = ctx.workspace.read(run_id)
    if record["status"] != "succeeded":
        return {"verification": {"run_id": run_id, "passed": False, "status": record["status"], "artifacts": []}}
    checks = []
    for ref in record["result"]["evidence"]:
        path = artifact_path(ctx.workspace, run_id, ref["path"])
        passed = path.is_file() and digest(path) == ref["sha256"]
        checks.append({"id": ref["id"], "step": ref["step"], "passed": passed})
    return {"verification": {"run_id": run_id, "passed": all(item["passed"] for item in checks), "artifacts": checks}}


def history(ctx, limit: Annotated[int, Field(ge=1, le=100)] = 20):
    with ctx.workspace.connect() as db:
        rows = db.execute("SELECT id, job, status, created_at FROM runs WHERE id != ? ORDER BY created_at DESC LIMIT ?",
                          (ctx.job["run_id"], limit)).fetchall()
    return {"history": [{"id": row["id"], "operation": json.loads(row["job"])["operation"],
                         "status": row["status"], "created_at": row["created_at"]} for row in rows]}
