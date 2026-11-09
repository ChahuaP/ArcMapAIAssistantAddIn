
"""Derive a failure case from recorded checks and replay its smallest available node."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
from time import monotonic
from uuid import UUID, uuid4

from guizhi.application.tasks import inspect, run_operation, run_vector
from guizhi.infrastructure.cache import RUN_ARGUMENTS, fingerprint, record_dependencies
from guizhi.infrastructure.operations.evidence import artifact_path
from guizhi.infrastructure.runtime_identity import runtime_identity
from guizhi.infrastructure.vector import digest
from guizhi.infrastructure.workspace import Workspace

MAX_PROBES = 1
IMPACT_LIMIT = 256
SNAPSHOT_LIMIT = 64 << 20


def contained(root, *parts):
    path = root.joinpath(*parts)
    if path.resolve() != path:
        raise ValueError("Diagnostic storage must not redirect")
    return path


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".pending")
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def failure_node(workspace, root):
    record = root
    for _ in range(3):
        children = [item["details"]["child_run_id"] for item in record["events"]
                    if item["step"] in {"material_resolved", "workflow_step_finished"}
                    and item["details"].get("status") in {"failed", "interrupted"}]
        if not children:
            break
        record = workspace.read(children[-1])
    return record


def impact(workspace, run_ids):
    with workspace.connect() as db:
        rows = db.execute("SELECT id, task, result FROM runs WHERE status='succeeded' ORDER BY created_at DESC LIMIT ?",
                          (IMPACT_LIMIT + 1,)).fetchall()
    edges = {row["id"]: record_dependencies({"task": json.loads(row["task"]), "result": json.loads(row["result"])})
             for row in rows[:IMPACT_LIMIT]}
    affected = set(run_ids)
    for _ in edges:
        children = {run for run, parents in edges.items() if parents & affected}
        if children <= affected:
            break
        affected.update(children)
    return {"runs": sorted(affected - set(run_ids)), "complete": len(rows) <= IMPACT_LIMIT}


def assertion_fact(record, assertion):
    if (not isinstance(assertion, dict) or set(assertion) != {"statistic", "expected", "oracle"} or
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", assertion.get("statistic", "")) or
            not isinstance(assertion["oracle"], str) or not 1 <= len(assertion["oracle"]) <= 160 or
            type(assertion["expected"]) not in {str, bool, int, float}):
        raise ValueError("Provide a scalar expected statistic and identify its independent source")
    actual = (record["result"] or {}).get("statistics", {}).get(assertion["statistic"])
    if actual == assertion["expected"]:
        raise ValueError("Recorded result agrees with the independent assertion")
    return {"category": "tool", "code": "independent_statistic_disagreement",
            "evidence": {**assertion, "actual": actual}}


def diagnose_run(workspace, run_id, assertion=None):
    original = inspect(workspace, run_id)
    record = failure_node(workspace, original)
    task = record["task"]
    if assertion is not None:
        if original["status"] != "succeeded":
            raise ValueError("Use an independent result assertion on a completed run")
        finding = assertion_fact(record, assertion)
    elif record["status"] == "interrupted":
        finding = {"category": "environment", "code": "receipt_unknown", "evidence": {"status": "interrupted"}}
    elif record["status"] == "failed":
        finding = next((item["details"] for item in reversed(record["events"]) if item["step"] == "diagnostic_failure"),
                       {"category": "undetermined", "code": "no_typed_failure", "evidence": {"status": "failed"}})
    else:
        raise ValueError("Diagnose a failed, interrupted, or independently contradicted run")
    runtime = next((item["details"].get("runtime") for item in record["events"] if item["step"] == "worker_started"), None)
    snapshots = [item["details"] for item in record["events"] if item["step"] == "diagnostic_snapshot"]
    operation = task["operation"]
    case = {
        "schema_version": "1.0.0", "run_id": original["id"], "probe_run_id": record["id"],
        "operation": operation, "operation_version": task["operation_version"], "runtime": runtime,
        "fingerprint": fingerprint([operation, task["operation_version"], finding["category"], finding["code"]]),
        "finding": finding, "error": record["error"], "snapshots": snapshots,
        "excluded": {"workflow_as_origin": original["id"] != record["id"],
                     "worker_crash": assertion is not None or finding["category"] in {"data", "parameter", "association", "workflow"}},
        "uncertainty": "independent assertion is conditional on its stated oracle" if assertion is not None
        else "origin cannot be determined from recorded evidence" if finding["category"] == "undetermined" else None,
        "impact": impact(workspace, [original["id"], record["id"]]), "probe_limit": MAX_PROBES,
    }
    case["id"] = fingerprint([case["run_id"], case["probe_run_id"], finding])
    save(contained(workspace.root, "diagnostics", "cases", case["id"] + ".json"), case)
    return case


def diagnose_dispatch(workspace, dispatch_id):
    if str(UUID(dispatch_id)) != dispatch_id:
        raise ValueError("Invalid dispatch ID")
    folder = contained(workspace.root, "dispatch", dispatch_id)
    if not folder.is_dir():
        raise ValueError("Unknown dispatch")
    issue = contained(folder, "input-issue.json")
    result = contained(folder, "result.json")
    if result.is_file():
        receipt = json.loads(result.read_text(encoding="utf-8"))
        if (receipt.get("operation") or {}).get("run_id"):
            return diagnose_run(workspace, receipt["operation"]["run_id"])
        # A later valid operation takes precedence over an earlier rejected tool call.
        if receipt.get("status") in {"succeeded", "answered"}:
            raise ValueError("The dispatch completed")
    raw = json.loads(issue.read_text(encoding="utf-8")) if issue.is_file() else {}
    finding = raw.get("diagnostic")
    if finding is None:
        if not result.is_file() and contained(folder, "operation.claim").exists():
            finding = {"category": "environment", "code": "receipt_unknown", "evidence": {"claim_exists": True}}
        else:
            finding = {"category": "undetermined", "code": "no_committed_operation", "evidence": {"status": raw.get("status")}}
    case = {"schema_version": "1.0.0", "dispatch_id": dispatch_id, "finding": finding, "probe_limit": 0,
            "fingerprint": fingerprint(["dispatch", finding["category"], finding["code"]]),
            "uncertainty": "no replayable local operation was committed"}
    case["id"] = fingerprint([dispatch_id, finding])
    save(contained(workspace.root, "diagnostics", "cases", case["id"] + ".json"), case)
    return case


def copy_snapshot(workspace, probe_run, snapshot, destination):
    ref = snapshot["ref"]
    src = artifact_path(workspace, snapshot.get("source_run", probe_run), ref["path"])
    if not src.is_file() or digest(src) != ref["sha256"]:
        raise ValueError("Frozen replay input is missing or has changed")
    destination.mkdir()
    file = destination / src.name
    shutil.copyfile(src, file)
    if digest(file) != ref["sha256"]:
        raise ValueError("Replay snapshot copy failed verification")
    return {"file": str(file), "layer": snapshot["layer"], "revision": snapshot["revision"]}


def supported_request(task, snapshots):
    request = task["request"]
    if request is None:
        if task["operation"] != "vector_inventory" or len(snapshots) != 1:
            raise ValueError("This operation has no self-contained replay")
        return None
    request = deepcopy(request)
    request["idempotency_key"] = "diagnostic"
    if task["operation"] == "workflow.execute":
        spec = request["parameters"]["specification"]
        groups = spec["materials"]
        if Counter(x["role"] for x in snapshots) != Counter({role: len(items) for role, items in groups.items() if items}):
            raise ValueError("The workflow did not freeze every material")
        if spec.get("coordinate_plans") or any(
            name in RUN_ARGUMENTS and value and not (isinstance(value, str) and value.startswith("$run."))
            for step in spec["steps"] for name, value in step.get("parameters", {}).items()
        ):
            raise ValueError("Workflow needs an external run or coordinate catalog")
    elif (len(snapshots) != len(request["inputs"]) or
          any(name in RUN_ARGUMENTS and value for name, value in request["parameters"].items()) or
          task["operation"].startswith(("presentation.", "evidence.", "crs.", "knowledge."))):
        raise ValueError("This operation has no self-contained replay")
    return request


def replay(workspace, case, *, seconds=20):
    if not 1 <= seconds <= 60:
        raise ValueError("Replay deadline must be 1–60 seconds")
    if case["finding"]["code"] == "receipt_unknown" or not case.get("probe_run_id"):
        raise ValueError("Reconcile the original receipt before replay")
    if case["runtime"] != runtime_identity():
        raise ValueError("Recorded source and runtime must match the replay runtime")
    record = workspace.read(case["probe_run_id"])
    task = record["task"]
    snapshots = case["snapshots"]
    request = supported_request(task, snapshots)
    parameters = request["parameters"] if request else {"layer": task["layer"]}
    if task["operation"] == "workflow.execute":
        parameters["specification"]["materials"] = {role: [] for role in parameters["specification"]["materials"]}
    key = fingerprint([case["fingerprint"], parameters, [(item["role"], item["revision"]) for item in snapshots],
                       case["finding"]["evidence"].get("oracle"), case["finding"]["evidence"].get("expected"),
                       case["runtime"]])
    directory = contained(workspace.root, "diagnostics", "replays", key)
    receipt = contained(directory, "result.json")
    directory.parent.mkdir(parents=True, exist_ok=True)
    try:
        directory.mkdir()
    except FileExistsError:
        if not receipt.is_file():
            raise ValueError("Replay receipt is unknown; reconcile before another attempt") from None
        return {**json.loads(receipt.read_text(encoding="utf-8")), "reused": True}
    deadline = monotonic() + seconds
    try:
        size = sum(artifact_path(workspace, item.get("source_run", record["id"]), item["ref"]["path"]).stat().st_size
                   for item in snapshots)
        if size > SNAPSHOT_LIMIT:
            raise ValueError("Frozen inputs exceed the 64 MiB diagnostic replay budget")
        inputs = [copy_snapshot(workspace, record["id"], item, directory / str(number))
                  for number, item in enumerate(snapshots, 1)]
        if monotonic() >= deadline:
            result = {"status": "budget_exhausted", "source_run_id": record["id"], "reused": False}
            save(receipt, result)
            return result
        target = directory / "workspace"
        if request is None:
            run = run_vector(target, Path(inputs[0]["file"]), inputs[0]["layer"], task["object_id"],
                             lambda: monotonic() >= deadline)
        else:
            if task["operation"] == "workflow.execute":
                materials = parameters["specification"]["materials"]
                for item, material in zip(snapshots, inputs, strict=True):
                    materials[item["role"]].append(material)
            else:
                request["inputs"] = inputs
            run = run_operation(target, request, lambda: monotonic() >= deadline)
        prior = case["finding"]
        if run["status"] == "succeeded" and prior["code"] == "independent_statistic_disagreement":
            actual = run["result"].get("statistics", {}).get(prior["evidence"]["statistic"])
            matched = actual == prior["evidence"]["actual"]
        else:
            node = failure_node(Workspace(target), run)
            matched = any(event["step"] == "diagnostic_failure" and
                          event["details"]["code"] == prior["code"] and event["details"]["category"] == prior["category"]
                          for event in node["events"])
        result = {"status": "reproduced" if matched else "budget_exhausted" if monotonic() >= deadline else "changed_outcome",
                  "run_id": run["id"], "operation": task["operation"], "source_run_id": record["id"], "reused": False}
    except (OSError, ValueError) as exc:
        result = {"status": "blocked", "error": str(exc), "source_run_id": record["id"], "reused": False}
    save(receipt, result)
    return result
