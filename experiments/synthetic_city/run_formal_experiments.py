#!/usr/bin/env python3
"""Run and score reproducible GeoPilot/ArcMap ablation experiments.

Every task is executed through the GeoPilot Gateway.  The runner never uses
ArcPy or UI automation: before each mode/case/repetition it asks GeoPilot to
clear ArcMap and reload the immutable source layers, then verifies the fresh
ArcMap context returned by the Bridge.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import geopandas as gpd

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from pair_workspace import (
    PairWorkspaceError,
    relocate_pair_workspace,
    reset_pair_workspace,
)
from source_provenance import repository_state


MODES = ("g0_direct", "g1_context", "g2_constrained", "g3_audited")
TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "context_failed",
    "cancelled",
    "clarify",
    "reject",
}
INFRASTRUCTURE_STOP_STATUSES = {"indeterminate"}
MODEL_QUOTA_MARKERS = (
    "quota exhausted",
    "quota exceeded",
    "insufficient quota",
    "insufficient balance",
    "out of quota",
    "余额不足",
    "额度不足",
    "额度已用完",
    "额度耗尽",
    "配额不足",
    "配额已用完",
)
DEFAULT_GATEWAY = "http://127.0.0.1:8765"
OUTPUT_TRUTH_KEYS = {
    "flood_high": "flood_high", "affected_comm": "flood_affected_comm",
    "available_shelters": "flood_available_shelters", "priority_shelters": "flood_priority_shelters",
    "site_attr_ok": "site_attr_ok", "site_safe": "site_safe", "final_sites": "site_final",
    "suspect_projects": "land_suspect_projects", "protected_conflicts": "land_protected_conflicts",
    "mismatch_parcels": "land_mismatch_parcels", "priority_violations": "land_priority_projects",
    "hotspot_roads": "road_hotspots", "risk_schools": "road_risk_schools", "priority_roads": "road_priority_roads",
}
INTERMEDIATE_VECTOR_OUTPUTS = {
    "affected_service_2km", "school_buf", "industry_buf", "river_buf", "site_exclusion", "hotspot_buffer",
}


class ExperimentError(RuntimeError):
    pass


class InfrastructureStop(ExperimentError):
    def __init__(self, run_id: str, status: str):
        self.run_id = run_id
        self.status = status
        super().__init__(
            "ArcMap authoritative execution is unresolved (%s): %s" % (status, run_id)
        )


class ModelQuotaStop(ExperimentError):
    def __init__(self, run_id: str, marker: str):
        self.run_id = run_id
        self.marker = marker
        super().__init__("Model quota is exhausted (%s): %s" % (marker, run_id))


class RunDeadlineExceeded(ExperimentError):
    """The experiment deadline expired while the Gateway still owned the run."""

    def __init__(self, run_id: str, row: dict[str, Any]):
        self.run_id = run_id
        self.row = deepcopy(row)
        super().__init__("Method run exceeded the experiment deadline: %s" % run_id)


class GatewayClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def get(self, path: str) -> dict[str, Any]:
        return self._request(path, None)

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _request(self, path: str, payload: bytes | None) -> dict[str, Any]:
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", "replace")
            raise ExperimentError("Gateway HTTP %s: %s" % (exc.code, message)) from exc
        except urllib.error.URLError as exc:
            raise ExperimentError("Gateway unavailable: %s" % exc.reason) from exc
        if not isinstance(body, dict):
            raise ExperimentError("Gateway returned a non-object response.")
        return body

    def submit(
        self,
        mode: str,
        command: str,
        plan_artifact: dict[str, Any] | None = None,
        provider: str = "",
        model: str = "",
    ) -> str:
        payload = {"mode": mode, "command": command, "execute": True}
        if plan_artifact is not None:
            payload["plan_artifact"] = plan_artifact
        if provider:
            payload["provider"] = provider
        if model:
            payload["model"] = model
        response = self.post("/runs", payload)
        return self._run_id(response)

    def submit_reset(self, source_paths: list[str]) -> str:
        response = self.post("/experiments/reset", {"source_paths": source_paths})
        return self._run_id(response)

    @staticmethod
    def _run_id(response: dict[str, Any]) -> str:
        run = response.get("run") if isinstance(response.get("run"), dict) else {}
        run_id = run.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise ExperimentError("Gateway did not return a run id.")
        return run_id

    def wait(self, run_id: str, timeout_seconds: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            row = self.get("/runs/" + run_id).get("run")
            if not isinstance(row, dict):
                raise ExperimentError("Gateway returned an invalid run record: %s" % run_id)
            if row.get("status") in INFRASTRUCTURE_STOP_STATUSES:
                raise InfrastructureStop(run_id, row["status"])
            if row.get("status") in TERMINAL_STATUSES:
                return row
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RunDeadlineExceeded(run_id, row)
            time.sleep(min(0.5, remaining))


def source_layer_names(load_order: list[str]) -> set[str]:
    return {Path(item).stem for item in load_order}


def task_command(round_spec: dict[str, Any], output_dir: Path, mode: str, static_catalog: str = "") -> str:
    outputs = round_spec["expected_outputs"]
    artifact_rules = []
    for output in outputs:
        suffix = Path(output).suffix.lower()
        if suffix in (".csv", ".png"):
            artifact_rules.append(output)
        else:
            artifact_rules.append(output + ".shp")
    static_clause = ""
    if mode == "g0_direct":
        static_clause = (
            "这是 G0 直接单智能体实验。下列是唯一可用的静态源数据字典，必须使用其中的精确图层名和字段名："
            "%s。"
            "属性条件使用 field、op 和 value；只有 op=in 时必须使用非空 values 数组，不得把数组写入 value。"
            "不得调用 context.* 操作，也不得返回空步骤。"
        ) % static_catalog
    return (
        "%s\n%s\n"
        "这是连续业务任务的第 %s 轮。所有矢量成果必须以 Shapefile 写入 %s，"
        "表格和地图成果也必须写入该目录。必须生成：%s。"
        "保留前序轮次成果；源数据只读；必须执行实际 GIS 操作，不得用文字回答替代。"
        "矢量或栅格写出成果会自动加入地图：禁止对本轮已生成的地图成果再调用 layer.add_layer；"
        "后续步骤引用这类地图成果时必须使用 from_step:<步骤 id>。"
        "CSV、PNG属于文件成果，不是地图图层，不得使用 from_step 引用。"
        % (static_clause, round_spec["prompt"], round_spec["round"], output_dir, "、".join(artifact_rules))
    )


def run_reset(
    client: GatewayClient,
    load_order: list[str],
    timeout_seconds: int,
    expected_policy: dict[str, Any],
) -> dict[str, Any]:
    run_id = client.submit_reset(load_order)
    try:
        row = client.wait(run_id, timeout_seconds)
    except RunDeadlineExceeded as exc:
        raise InfrastructureStop(exc.run_id, "timeout") from exc
    require_run_policy(row, expected_policy)
    if row.get("status") != "succeeded":
        raise ExperimentError("ArcMap reset failed: %s" % row.get("result"))
    trace = run_trace(row)
    layers = trace.get("execution", {}).get("context_next_summary", {}).get("layers", [])
    names = {item.get("name") for item in layers if isinstance(item, dict)}
    expected = source_layer_names(load_order)
    if names != expected or len(layers) != len(expected):
        raise ExperimentError("ArcMap reset verification failed: %s" % sorted(str(item) for item in names))
    return row


def run_trace(row: dict[str, Any]) -> dict[str, Any]:
    items = row.get("agent_trace")
    if not isinstance(items, list) or not items:
        return {}
    first = items[0]
    if not isinstance(first, dict):
        return {}
    trace = first.get("run")
    return trace if isinstance(trace, dict) else {}


def method_run_status(row: dict[str, Any]) -> str:
    outcome = row.get("experiment_outcome")
    if isinstance(outcome, dict) and isinstance(outcome.get("status"), str):
        return outcome["status"]
    status = row.get("status")
    return status if isinstance(status, str) else ""


def _deadline_failure_stage(row: dict[str, Any]) -> str:
    trace = run_trace(row)
    stages = trace.get("stages")
    if isinstance(stages, list):
        for stage in reversed(stages):
            if not isinstance(stage, dict) or stage.get("status") != "running":
                continue
            return "execution" if stage.get("name") == "execution" else "planning"
    return "execution" if isinstance(trace.get("execution_owner"), dict) else "planning"


def wait_for_method_run(
    client: GatewayClient,
    run_id: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        row = client.wait(run_id, timeout_seconds)
    except RunDeadlineExceeded as exc:
        row = deepcopy(exc.row)
        row["experiment_outcome"] = {
            "status": "experiment_timeout",
            "failure_stage": _deadline_failure_stage(row),
            "timeout_seconds": timeout_seconds,
        }
    marker = model_quota_marker(row)
    if marker:
        raise ModelQuotaStop(run_id, marker)
    return row


def model_quota_marker(row: dict[str, Any]) -> str:
    trace = run_trace(row)
    evidence = {
        "result": row.get("result"),
        "failure": trace.get("failure"),
        "turn_errors": [
            turn.get("error")
            for turn in trace.get("turns", [])
            if isinstance(turn, dict) and turn.get("error")
        ],
    }
    text = json.dumps(evidence, ensure_ascii=False, sort_keys=True).lower()
    return next((marker for marker in MODEL_QUOTA_MARKERS if marker in text), "")


def submit_run(
    client: GatewayClient,
    args: argparse.Namespace,
    mode: str,
    command: str,
    plan_artifact: dict[str, Any] | None = None,
) -> str:
    provider = getattr(args, "provider", "")
    model = getattr(args, "model", "")
    if provider:
        return client.submit(mode, command, plan_artifact, provider=provider, model=model)
    return client.submit(mode, command, plan_artifact)


def require_run_policy(row: dict[str, Any], expected_policy: dict[str, Any]) -> None:
    trace = run_trace(row)
    actual = trace.get("planning_policy")
    if actual != expected_policy:
        failure = trace.get("failure")
        if actual is None and isinstance(failure, dict):
            raise ExperimentError(
                "Run failed before its planning policy could be verified: %s: %s"
                % (failure.get("stage") or "unknown", failure.get("message") or "unknown failure")
            )
        differing = _different_policy_paths(expected_policy, actual)
        raise ExperimentError(
            "Run planning policy does not match the frozen experiment policy: %s."
            % ", ".join(differing)
        )


def _different_policy_paths(expected: Any, actual: Any, path: str = "planning_policy") -> list[str]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        paths = []
        for key in sorted(set(expected) | set(actual)):
            paths.extend(_different_policy_paths(expected.get(key), actual.get(key), path + "." + key))
        return paths
    return [] if expected == actual else [path]


def require_gateway_identity(
    client: GatewayClient,
    expected_version: str,
    expected_policy: dict[str, Any],
) -> None:
    health = client.get("/health")
    if health.get("ok") is not True or health.get("app_version") != expected_version:
        raise ExperimentError("Gateway version changed during the experiment.")
    capabilities = client.get("/api/capabilities?detail=1")
    if capabilities.get("planning_policy") != expected_policy:
        raise ExperimentError("Gateway planning policy changed during the experiment.")


def run_failure_stage(row: dict[str, Any]) -> str:
    outcome = row.get("experiment_outcome")
    if isinstance(outcome, dict) and isinstance(outcome.get("failure_stage"), str):
        return outcome["failure_stage"]
    trace = run_trace(row)
    failure = trace.get("failure")
    if isinstance(failure, dict) and isinstance(failure.get("stage"), str):
        return failure["stage"]
    terminal = trace.get("terminal")
    if isinstance(terminal, dict) and isinstance(terminal.get("stage"), str):
        return terminal["stage"]
    return ""


def score_vector(path: Path, expected_ids: list[str]) -> dict[str, Any]:
    if not path.is_file():
        return {"kind": "vector", "ok": False, "reason": "missing", "path": str(path)}
    frame = gpd.read_file(path)
    expected = {str(item) for item in expected_ids}
    candidates: list[tuple[str, set[str]]] = []
    for field in frame.columns:
        if field == frame.geometry.name:
            continue
        values = {str(value) for value in frame[field].dropna().tolist()}
        if values:
            candidates.append((field, values))
    if not candidates:
        return {"kind": "vector", "ok": False, "reason": "no_attribute_values", "path": str(path)}
    field, actual = max(candidates, key=lambda item: (len(item[1] & expected), -len(item[1] - expected)))
    true_positive = len(actual & expected)
    precision = true_positive / len(actual) if actual else 0.0
    recall = true_positive / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "kind": "vector",
        "ok": actual == expected,
        "path": str(path),
        "id_field": field,
        "expected_ids": sorted(expected),
        "actual_ids": sorted(actual),
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def score_join(path: Path, minimum_field: str) -> dict[str, Any]:
    if not path.is_file():
        return {"kind": "join", "ok": False, "reason": "missing", "path": str(path)}
    frame = gpd.read_file(path)
    if minimum_field not in frame.columns:
        return {"kind": "join", "ok": False, "reason": "missing_field", "path": str(path)}
    values = frame[minimum_field].dropna()
    numeric = values.astype(float) if not values.empty else values
    return {
        "kind": "join",
        "ok": not values.empty and float(numeric.max()) >= 8,
        "path": str(path),
        "field": minimum_field,
        "max_value": float(numeric.max()) if not values.empty else None,
    }


def score_intermediate_vector(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"kind": "intermediate", "ok": False, "reason": "missing", "path": str(path)}
    frame = gpd.read_file(path)
    return {"kind": "intermediate", "ok": not frame.empty, "path": str(path), "feature_count": len(frame)}


def score_round(round_spec: dict[str, Any], output_dir: Path, truth: dict[str, list[str]]) -> dict[str, Any]:
    scores = []
    acceptance = round_spec.get("acceptance") if isinstance(round_spec.get("acceptance"), dict) else {}
    for output in round_spec["expected_outputs"]:
        suffix = Path(output).suffix.lower()
        if suffix in (".csv", ".png"):
            path = output_dir / output
            scores.append({"kind": "file", "ok": path.is_file() and path.stat().st_size > 0, "path": str(path)})
            continue
        path = output_dir / (output + ".shp")
        if output == "road_accident_join":
            scores.append(score_join(path, acceptance["minimum_accident_count_field"]))
            continue
        key = OUTPUT_TRUTH_KEYS.get(output)
        if output in INTERMEDIATE_VECTOR_OUTPUTS:
            scores.append(score_intermediate_vector(path))
            continue
        if key is None:
            raise ExperimentError("No truth mapping is defined for vector output: %s" % output)
        if key not in truth:
            raise ExperimentError("Truth key is absent: %s" % key)
        scores.append(score_vector(path, truth[key]))
    return {"round": round_spec["round"], "ok": all(item["ok"] for item in scores), "artifacts": scores}


def write_reports(output_root: Path, records: list[dict[str, Any]]) -> None:
    (output_root / "run_records.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_root / "run_traces.json").write_text(
        json.dumps(
            [
                {
                    "run_id": record.get("run_id", ""),
                    "mode": record["mode"],
                    "case_id": record["case_id"],
                    "repetition": record["repetition"],
                    "round": record["round"],
                    "agent_trace": record.get("agent_trace", []),
                    "result": record.get("result"),
                }
                for record in records
                if record.get("run_id")
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    tool_calls = []
    efficiency = []
    audit_scores = []
    for record in records:
        trace = _run_trace(record)
        workflow_versions = trace.get("workflow_versions") or []
        final_workflow = workflow_versions[-1].get("workflow", {}) if workflow_versions else {}
        final_steps = final_workflow.get("steps") or []
        for index, step in enumerate(final_steps, start=1):
            tool_calls.append(
                {
                    "run_id": record.get("run_id", ""),
                    "mode": record["mode"],
                    "case_id": record["case_id"],
                    "repetition": record["repetition"],
                    "round": record["round"],
                    "sequence": index,
                    "step_id": step.get("id", ""),
                    "operation": step.get("operation", ""),
                    "arguments": step.get("arguments", {}),
                }
            )
        turns = trace.get("turns") or []
        usage = [_turn_usage(turn) for turn in turns]
        efficiency.append(
            {
                "run_id": record.get("run_id", ""),
                "mode": record["mode"],
                "case_id": record["case_id"],
                "repetition": record["repetition"],
                "round": record["round"],
                "duration_seconds": record.get("duration_seconds", 0.0),
                "input_tokens": sum(item[0] for item in usage),
                "output_tokens": sum(item[1] for item in usage),
                "tool_calls": len(final_steps),
            }
        )
        if record["mode"] == "g3_audited":
            audits = trace.get("audits") or []
            counts = trace.get("counts") or {}
            auditor_turns = [turn for turn in turns if turn.get("role") == "auditor"]
            audit_usage = [_turn_usage(turn) for turn in auditor_turns]
            findings = [finding for audit in audits for finding in audit.get("findings", [])]
            audit_scores.append(
                {
                    "run_id": record.get("run_id", ""),
                    "case_id": record["case_id"],
                    "repetition": record["repetition"],
                    "round": record["round"],
                    "audit_intervened": bool(counts.get("audit_revisions", 0)),
                    "audit_revision_count": int(counts.get("audit_revisions", 0)),
                    "audit_decisions": ";".join(str(audit.get("decision", "")) for audit in audits),
                    "finding_count": len(findings),
                    "finding_categories": ";".join(
                        sorted({str(finding.get("category", "")) for finding in findings})
                    ),
                    "baseline_workflow_hash": (
                        workflow_versions[0].get("hash", "") if workflow_versions else ""
                    ),
                    "final_workflow_hash": (
                        workflow_versions[-1].get("hash", "") if workflow_versions else ""
                    ),
                    "final_round_ok": bool(
                        record.get("scores") and record["scores"][0].get("ok")
                    ),
                    "audit_input_tokens": sum(item[0] for item in audit_usage),
                    "audit_output_tokens": sum(item[1] for item in audit_usage),
                    "audit_duration_seconds": round(
                        sum(
                            max(0.0, float(turn.get("finished_at", 0)) - float(turn.get("started_at", 0)))
                            for turn in auditor_turns
                        ),
                        3,
                    ),
                }
            )
    (output_root / "tool_calls.json").write_text(
        json.dumps(tool_calls, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(
        output_root / "audit_scores.csv",
        audit_scores,
        [
            "run_id", "case_id", "repetition", "round", "audit_intervened",
            "audit_revision_count", "audit_decisions", "finding_count",
            "finding_categories", "baseline_workflow_hash", "final_workflow_hash",
            "final_round_ok", "audit_input_tokens", "audit_output_tokens",
            "audit_duration_seconds",
        ],
    )
    _write_csv(
        output_root / "efficiency.csv",
        efficiency,
        [
            "run_id", "mode", "case_id", "repetition", "round",
            "duration_seconds", "input_tokens", "output_tokens", "tool_calls",
        ],
    )
    rows = []
    for record in records:
        for score in record.get("scores", []):
            rows.append(
                {
                    "mode": record["mode"],
                    "case_id": record["case_id"],
                    "repetition": record["repetition"],
                    "round": score["round"],
                    "run_id": record["run_id"],
                    "run_status": record["run_status"],
                    "round_ok": score["ok"],
                    "duration_seconds": record["duration_seconds"],
                    "failure_stage": (
                        "" if record["run_status"] == "succeeded"
                        else record.get("failure_stage", "")
                    ),
                    "blocked_by_round": record.get("blocked_by_round", ""),
                    "blocked_by_run_id": record.get("blocked_by_run_id", ""),
                    "skip_reason": record.get("skip_reason", ""),
                    "expected_rounds": record.get("expected_rounds", ""),
                    "runner_error": record.get("runner_error", ""),
                    "pair_id": record.get("pair_id", ""),
                    "paired_artifact_hash": record.get("paired_artifact_hash", ""),
                    "baseline_workflow_hash": record.get("baseline_workflow_hash", ""),
                    "g3_baseline_artifact_hash": record.get("g3_baseline_artifact_hash", ""),
                    "pair_kind": record.get("pair_kind", ""),
                    "artifact_equal": record.get("artifact_equal", ""),
                    "context_equal": record.get("context_equal", ""),
                    "post_context_equal": record.get("post_context_equal", ""),
                    "post_result_equal": record.get("post_result_equal", ""),
                    "post_snapshot_equal": record.get("post_snapshot_equal", ""),
                    "pair_valid": record.get("pair_valid", ""),
                }
            )
    _write_csv(output_root / "round_scores.csv", rows, [
        "mode", "case_id", "repetition", "round", "run_id", "run_status", "round_ok",
        "duration_seconds", "failure_stage", "blocked_by_round", "blocked_by_run_id", "skip_reason",
        "expected_rounds", "runner_error", "pair_id", "paired_artifact_hash", "baseline_workflow_hash",
        "g3_baseline_artifact_hash", "pair_kind", "artifact_equal", "context_equal",
        "post_context_equal", "post_result_equal", "post_snapshot_equal", "pair_valid",
    ])
    rows = [row for row in rows if row.get("pair_valid", "") not in (False, "False")]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["mode"], row["case_id"]), []).append(row)
    flow_scores = []
    summary = []
    for (mode, case_id), values in sorted(groups.items()):
        attempted = [row for row in values if row["run_status"] != "skipped_dependency"]
        succeeded = [row for row in attempted if row["run_status"] == "succeeded"]
        flows: dict[int, list[dict[str, Any]]] = {}
        for row in values:
            flows.setdefault(row["repetition"], []).append(row)
        for repetition, flow in sorted(flows.items()):
            expected_rounds = int(flow[0].get("expected_rounds") or 0) if flow else 0
            completed_rounds = sum(bool(row["round_ok"]) for row in flow)
            flow_scores.append(
                {
                    "mode": mode,
                    "case_id": case_id,
                    "repetition": repetition,
                    "expected_rounds": expected_rounds,
                    "reported_rounds": len(flow),
                    "completed_rounds": completed_rounds,
                    "flow_ok": bool(
                        flow
                        and len(flow) == expected_rounds
                        and all(row["round_ok"] for row in flow)
                    ),
                    "first_failed_round": next(
                        (row["round"] for row in flow if not row["round_ok"]),
                        "",
                    ),
                }
            )
        summary.append(
            {
                "mode": mode,
                "case_id": case_id,
                "attempted_rounds": len(attempted),
                "planning_failures": sum(
                    row.get("failure_stage") == "planning" for row in attempted
                ),
                "execution_failures": sum(
                    row["run_status"] != "succeeded"
                    and row.get("failure_stage") != "planning"
                    for row in attempted
                ),
                "scoring_failures": sum(
                    row["run_status"] == "succeeded" and not row["round_ok"]
                    for row in attempted
                ),
                "dependency_skips": len(values) - len(attempted),
                "attempt_success_rate": (
                    sum(row["run_status"] == "succeeded" for row in attempted)
                    / len(attempted)
                    if attempted
                    else 0.0
                ),
                "exact_among_succeeded": (
                    sum(row["round_ok"] for row in succeeded) / len(succeeded)
                    if succeeded
                    else 0.0
                ),
                "business_flow_completion_rate": (
                    sum(
                        bool(flow)
                        and len(flow) == int(flow[0].get("expected_rounds") or 0)
                        and all(row["round_ok"] for row in flow)
                        for flow in flows.values()
                    )
                    / len(flows)
                    if flows
                    else 0.0
                ),
                "mean_duration_seconds": (
                    sum(float(row["duration_seconds"]) for row in attempted)
                    / len(attempted)
                    if attempted
                    else 0.0
                ),
            }
        )
    _write_csv(
        output_root / "flow_scores.csv",
        flow_scores,
        [
            "mode", "case_id", "repetition", "expected_rounds",
            "reported_rounds", "completed_rounds", "flow_ok", "first_failed_round",
        ],
    )
    _write_csv(output_root / "summary.csv", summary)


def _run_trace(record: dict[str, Any]) -> dict[str, Any]:
    agent_trace = record.get("agent_trace") or []
    if not agent_trace or not isinstance(agent_trace[0], dict):
        return {}
    trace = agent_trace[0].get("run")
    return trace if isinstance(trace, dict) else {}


def _turn_usage(turn: dict[str, Any]) -> tuple[int, int]:
    response = turn.get("provider_response") or {}
    usage = response.get("usage") or {}
    return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fields: list[str] | None = None,
) -> None:
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_infrastructure_stop(output_root: Path, exc: InfrastructureStop) -> None:
    (output_root / "infrastructure_stop.json").write_text(
        json.dumps(
            {
                "run_id": exc.run_id,
                "status": exc.status,
                "stopped_at": time.time(),
                "excluded_from_method_statistics": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def write_model_quota_stop(output_root: Path, exc: ModelQuotaStop) -> None:
    (output_root / "model_quota_stop.json").write_text(
        json.dumps(
            {
                "run_id": exc.run_id,
                "marker": exc.marker,
                "stopped_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_dataset_files(dataset: Path, manifest: dict[str, Any]) -> None:
    records = manifest.get("files")
    if not isinstance(records, list) or not records:
        raise ExperimentError("Dataset manifest has no frozen file hashes.")
    for record in records:
        relative = record.get("path")
        expected_bytes = record.get("bytes")
        expected_hash = record.get("sha256")
        if not isinstance(relative, str) or not relative:
            raise ExperimentError("Dataset manifest contains an invalid file path.")
        path = (dataset / relative).resolve()
        try:
            path.relative_to(dataset.resolve())
        except ValueError as exc:
            raise ExperimentError("Dataset manifest path escapes its root: %s" % relative) from exc
        if not path.is_file():
            raise ExperimentError("Dataset file is missing: %s" % relative)
        if path.stat().st_size != expected_bytes:
            raise ExperimentError("Dataset file size mismatch: %s" % relative)
        if sha256_file(path) != expected_hash:
            raise ExperimentError("Dataset file hash mismatch: %s" % relative)


def validate_dataset(dataset: Path) -> tuple[list[str], dict[str, Any], dict[str, list[str]]]:
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    verify_dataset_files(dataset, manifest)
    validation = json.loads((dataset / "validation.json").read_text(encoding="utf-8"))
    if validation.get("ok") is not True:
        raise ExperimentError("Synthetic dataset validation is not successful.")
    load_order = json.loads((dataset / "load_order.json").read_text(encoding="utf-8"))
    if not isinstance(load_order, list) or len(load_order) != 14:
        raise ExperimentError("load_order.json must contain exactly 14 source layers.")
    cases = json.loads((dataset / "task_cases.json").read_text(encoding="utf-8"))
    truth = json.loads((dataset / "truth" / "expected_ids.json").read_text(encoding="utf-8"))
    for case in cases.get("cases", []):
        for round_spec in case.get("rounds", []):
            for output in round_spec.get("expected_outputs", []):
                if (
                    Path(output).suffix.lower() in (".csv", ".png")
                    or output == "road_accident_join"
                    or output in INTERMEDIATE_VECTOR_OUTPUTS
                ):
                    continue
                key = OUTPUT_TRUTH_KEYS.get(output)
                if key not in truth:
                    raise ExperimentError("Dataset has no score contract for output: %s" % output)
    return load_order, cases, truth


def select_cases(task_spec: dict[str, Any], case_ids: list[str], rounds: list[int] | None = None) -> dict[str, Any]:
    """Select an explicit diagnostic subset without changing the frozen dataset."""
    rounds = list(rounds or [])
    if not case_ids and not rounds:
        return task_spec
    if len(set(case_ids)) != len(case_ids):
        raise ExperimentError("Diagnostic case ids must be unique.")
    by_id = {case.get("case_id"): case for case in task_spec.get("cases", [])}
    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise ExperimentError("Unknown diagnostic case ids: %s" % missing)
    result = dict(task_spec)
    selected = [by_id[case_id] for case_id in case_ids] if case_ids else list(task_spec.get("cases", []))
    if rounds:
        result["cases"] = []
        for case in selected:
            available = {item.get("round") for item in case.get("rounds", [])}
            missing_rounds = [value for value in rounds if value not in available]
            if missing_rounds:
                raise ExperimentError("Unknown diagnostic rounds for %s: %s" % (case.get("case_id"), ", ".join(map(str, missing_rounds))))
            case = deepcopy(case)
            case["rounds"] = [item for item in case["rounds"] if item.get("round") in rounds]
            result.setdefault("cases", []).append(case)
        return result
    result["cases"] = selected
    return result


def _replay_source_artifact(path: Path, case_id: str, round_number: int) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(document, dict) and document.get("schema") == "geopilot-plan-artifact":
        return document
    if not isinstance(document, list):
        raise ExperimentError("Replay baseline must be a PlanArtifact or run_records array.")
    for record in document:
        if record.get("mode") == "g2_constrained" and record.get("case_id") == case_id and record.get("round") == round_number:
            return _artifact_from_run(record)
    raise ExperimentError("Replay baseline record was not found for %s round %d." % (case_id, round_number))


def rebind_replay_task_contract(
    source: dict[str, Any], command: str, pair_work: Path,
) -> dict[str, Any]:
    """Atomically move request evidence and explicit destinations to a new run path."""
    previous_request = source.get("request")
    task_contract = deepcopy(source.get("task_contract"))
    if not isinstance(previous_request, str) or not previous_request or not isinstance(task_contract, dict):
        raise ExperimentError("Historical G2 replay artifact has no closed request contract.")
    for collection in ("input_entities", "requirements", "clarifications"):
        items = task_contract.get(collection)
        if not isinstance(items, list):
            raise ExperimentError("Historical G2 replay task contract is missing %s." % collection)
        for index, item in enumerate(items):
            if not isinstance(item, dict) or item.get("evidence") != previous_request:
                raise ExperimentError(
                    "Historical G2 replay evidence is not the immutable source request: %s[%d]."
                    % (collection, index)
                )
            item["evidence"] = command
    outputs = task_contract.get("outputs")
    if not isinstance(outputs, list):
        raise ExperimentError("Historical G2 replay task contract is missing outputs.")
    target_folder = str(pair_work)
    for index, item in enumerate(outputs):
        if not isinstance(item, dict):
            raise ExperimentError("Historical G2 replay output contract is invalid: outputs[%d]." % index)
        evidence = item.get("evidence")
        destination = item.get("destination")
        if not isinstance(evidence, str) or evidence not in previous_request or evidence not in command:
            raise ExperimentError(
                "Historical G2 replay output evidence is not stable across requests: outputs[%d]." % index
            )
        if destination in ("default", "not_applicable"):
            continue
        if not isinstance(destination, str) or destination not in previous_request:
            raise ExperimentError(
                "Historical G2 replay output destination is not request-bound: outputs[%d]." % index
            )
        item["destination"] = target_folder
    return task_contract


def rebind_replay_context(
    context_snapshot: dict[str, Any], workflow: dict[str, Any], pair_work: Path,
) -> dict[str, Any]:
    """Rebase prior-round artifact data sources with the replay output contract."""
    output_folders = {
        step.get("arguments", {}).get("output_folder")
        for step in workflow.get("steps", [])
        if isinstance(step, dict)
        and isinstance(step.get("arguments"), dict)
        and isinstance(step["arguments"].get("output_folder"), str)
        and step["arguments"]["output_folder"]
    }
    if len(output_folders) != 1:
        raise ExperimentError("Historical G2 replay workflow must own exactly one output folder.")
    previous_folder = next(iter(output_folders))
    target_folder = str(pair_work)
    folded_folder = previous_folder.casefold()

    def rebind(value):
        if isinstance(value, dict):
            return {key: rebind(item) for key, item in value.items()}
        if isinstance(value, list):
            return [rebind(item) for item in value]
        if isinstance(value, str):
            folded = value.casefold()
            if folded == folded_folder:
                return target_folder
            if any(folded.startswith(folded_folder + separator) for separator in ("\\", "/")):
                return target_folder + value[len(previous_folder):]
        return value

    result = rebind(deepcopy(context_snapshot))
    from arcmap_runtime_py2 import context_fingerprint
    extent = result.get("extent")
    if isinstance(extent, dict):
        for coordinate in ("XMin", "YMin", "XMax", "YMax"):
            if coordinate in extent:
                extent[coordinate] = context_fingerprint.canonical_coordinate(extent[coordinate])
    result["context_hash"] = context_fingerprint.context_hash(result)
    return result


def rebuild_replay_artifact(source: dict[str, Any], command: str, pair_work: Path) -> dict[str, Any]:
    """Rebind one observed G2 baseline to a fresh output directory and current contracts."""
    repository = str(Path(__file__).resolve().parents[2])
    if repository not in sys.path:
        sys.path.insert(0, repository)
    from gateway_py3.catalog_loader import OperationCatalog
    from gateway_py3.plan_artifact import PlanArtifact
    from gateway_py3.planning_engine import planning_policy
    from gateway_py3.task_contract import parse_task_contract
    from gateway_py3.validators import context_hash
    from gateway_py3.workflow_protocol import workflow_protocol
    from gateway_py3.workflow_verifier import WorkflowVerifier

    workflow = deepcopy(source["baseline_workflow"])
    context = rebind_replay_context(source["context_snapshot"], workflow, pair_work)
    task_contract = parse_task_contract(
        rebind_replay_task_contract(source, command, pair_work), command, context,
    )
    for step in workflow.get("steps", []):
        arguments = step.get("arguments") or {}
        if "output_folder" in arguments:
            arguments["output_folder"] = str(pair_work)
    catalog = OperationCatalog()
    report = WorkflowVerifier(catalog).verify(workflow, context, task_contract)
    if not report["ok"] or report.get("prepared_workflow") != workflow:
        raise ExperimentError("Historical G2 replay baseline is not valid under current production contracts: %s" % report.get("hard_violations"))
    protocol = workflow_protocol()
    cards = [catalog.planning_card(item) for item in sorted(catalog.all_operations(), key=lambda item: item["id"])]
    return PlanArtifact(
        command, context, context_hash(context), cards, task_contract,
        workflow, report, planning_policy(catalog, protocol),
    ).as_dict()


def build_manifest(
    dataset,
    args,
    gateway_config,
    planning_policy,
    repository,
    gateway_app_version,
    created_at=None,
):
    frozen_version = getattr(args, "code_version", None)
    frozen_fingerprint = getattr(args, "source_fingerprint", None)
    frozen_dirty = getattr(args, "dirty", None)
    if frozen_version and frozen_fingerprint and frozen_dirty in ("true", "false"):
        version, source_fingerprint = frozen_version, frozen_fingerprint
        dirty = frozen_dirty == "true"
    else:
        version, dirty, source_fingerprint = repository_state(Path(repository))
    return {
        "dataset": str(dataset),
        "modes": args.modes,
        "repetitions": args.repetitions,
        "case_ids": list(getattr(args, "case_ids", []) or []),
        "rounds": list(getattr(args, "rounds", []) or []),
        "timeout_seconds": args.timeout,
        "gateway": args.gateway,
        "gateway_app_version": gateway_app_version,
        "created_at": time.time() if created_at is None else created_at,
        "primary": {
            "provider": getattr(args, "provider", "") or gateway_config.get("primary_provider", ""),
            "model": getattr(args, "model", "") or gateway_config.get("primary_model", ""),
        },
        "planning_policy": planning_policy,
        "catalog_hash": planning_policy["catalog_hash"],
        "protocol_hash": planning_policy["protocol_hash"],
        "code_version": version,
        "dirty": dirty,
        "source_fingerprint": source_fingerprint,
    }


def direct_static_catalog(dataset: Path) -> str:
    aliases = {
        "flood_zones": "当前淹没区", "communities": "社区", "shelters": "避难场所",
        "candidate_sites": "候选地块", "schools": "学校", "industry": "工业区",
        "rivers": "河流", "protected": "保护区", "construction": "建设项目",
        "parcels": "地块", "roads": "道路", "accidents": "事故点",
    }
    grouped: dict[str, list[str]] = {}
    with (dataset / "data_dictionary.csv").open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            grouped.setdefault(row["layer"], []).append(row["field"])
    return "；".join(
        "%s(%s): %s" % (layer, aliases.get(layer, layer), ",".join(fields))
        for layer, fields in grouped.items()
    )


def _artifact_from_run(row: dict[str, Any]) -> dict[str, Any]:
    trace = _run_trace(row)
    artifact = trace.get("plan_artifact")
    if not isinstance(artifact, dict) or artifact.get("artifact_hash") != trace.get("plan_artifact_hash"):
        raise ExperimentError("G2 did not return a self-verifying PlanArtifact.")
    return artifact


def _atomic_write_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def execute_paired_g2(client, args, load_order, task_spec, static_catalog, output_root,
                      manifest, gateway_app_version):
    """Capture the frozen G2 baseline, leaving ArcMap-owned files for process teardown."""
    state = {"schema": "geopilot-paired-baseline", "version": 1, "status": "running", "cases": []}
    state_path = output_root / "paired_baseline.json"
    for repetition in range(1, args.repetitions + 1):
        for case in task_spec["cases"]:
            pair_id = "%s-r%02d" % (case["case_id"], repetition)
            pair_work = output_root / "pair-work" / pair_id
            try:
                reset_pair_workspace(output_root, pair_work)
            except PairWorkspaceError as exc:
                raise ExperimentError(str(exc)) from exc
            require_gateway_identity(client, gateway_app_version, manifest["planning_policy"])
            run_reset(client, load_order, args.timeout, manifest["planning_policy"])
            case_state = {
                "case_id": case["case_id"], "repetition": repetition, "pair_id": pair_id,
                "expected_rounds": len(case["rounds"]), "rounds": [],
            }
            state["cases"].append(case_state)
            g2_failed = False
            for round_spec in case["rounds"]:
                command = task_command(round_spec, pair_work, "g2_constrained", static_catalog)
                if g2_failed:
                    case_state["rounds"].append({
                        "round_spec": round_spec, "command": command, "artifact": None, "row": None,
                        "duration": 0.0, "skip": "G2 dependency round did not complete successfully",
                    })
                    _atomic_write_json(state_path, state)
                    continue
                started = time.monotonic()
                replay_artifact = None
                replay_source = getattr(args, "replay_baseline_record", None)
                if replay_source:
                    source = _replay_source_artifact(replay_source, case["case_id"], round_spec["round"])
                    replay_artifact = rebuild_replay_artifact(source, command, pair_work)
                g2 = wait_for_method_run(
                    client,
                    submit_run(client, args, "g2_constrained", command, replay_artifact),
                    args.timeout,
                )
                duration = time.monotonic() - started
                require_run_policy(g2, manifest["planning_policy"])
                artifact = None
                if method_run_status(g2) == "succeeded":
                    try:
                        artifact = _artifact_from_run(g2)
                    except ExperimentError:
                        pass
                case_state["rounds"].append({
                    "round_spec": round_spec, "command": command, "artifact": artifact, "row": g2,
                    "duration": duration, "skip": "",
                })
                _atomic_write_json(state_path, state)
                g2_failed = method_run_status(g2) != "succeeded"
    state["status"] = "g2_complete"
    _atomic_write_json(state_path, state)


def _load_paired_baseline(output_root: Path) -> dict[str, Any]:
    path = output_root / "paired_baseline.json"
    if not path.is_file():
        raise ExperimentError("Paired G2 baseline is missing.")
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema") != "geopilot-paired-baseline" or state.get("version") != 1:
        raise ExperimentError("Paired G2 baseline schema is invalid.")
    if state.get("status") != "g2_complete":
        raise ExperimentError("Paired G2 baseline is incomplete.")
    return state


def _rebase_score_paths(score: dict[str, Any], source: Path, target: Path) -> dict[str, Any]:
    source_text = str(source)
    target_text = str(target)
    result = deepcopy(score)
    for artifact in result.get("artifacts", []):
        path = artifact.get("path")
        if isinstance(path, str) and (path == source_text or path.startswith(source_text + "\\")):
            artifact["path"] = target_text + path[len(source_text):]
    return result


def paired_post_execution_equality(
    g2_execution: dict[str, Any], g3_execution: dict[str, Any],
) -> dict[str, bool]:
    """Compare reproducible GIS state, not independent ArcMap process identity."""
    g2_next = g2_execution.get("context_next_hash")
    g3_next = g3_execution.get("context_next_hash")
    g2_result = g2_execution.get("result_hash")
    g3_result = g3_execution.get("result_hash")
    g2_snapshot = g2_execution.get("context_next_snapshot_hash")
    g3_snapshot = g3_execution.get("context_next_snapshot_hash")
    result_equal = bool(g2_result and g2_result == g3_result)
    return {
        "context_equal": bool(g2_next and g2_next == g3_next and result_equal),
        "result_equal": result_equal,
        "snapshot_equal": bool(g2_snapshot and g2_snapshot == g3_snapshot),
    }


def execute_paired_g3(client, args, load_order, task_spec, truth, output_root,
                      manifest, gateway_app_version, records):
    """Replay G2 artifacts in a fresh ArcMap process; archive only unlocked G2 output."""
    state = _load_paired_baseline(output_root)
    expected_keys = [
        (case["case_id"], repetition)
        for repetition in range(1, args.repetitions + 1)
        for case in task_spec["cases"]
    ]
    baseline_by_key = {(item["case_id"], item["repetition"]): item for item in state["cases"]}
    if list(baseline_by_key) != expected_keys:
        raise ExperimentError("Paired G2 baseline does not match the selected experiment matrix.")
    completed_pairs = []
    for case_id, repetition in expected_keys:
            case = next(item for item in task_spec["cases"] if item["case_id"] == case_id)
            case_state = baseline_by_key[(case_id, repetition)]
            pair_id = case_state["pair_id"]
            pair_work = output_root / "pair-work" / pair_id
            expected_rounds = len(case["rounds"])
            if case_state.get("expected_rounds") != expected_rounds or len(case_state.get("rounds", [])) != expected_rounds:
                raise ExperimentError("Paired G2 baseline round count changed for %s." % case_id)
            g2_archive = output_root / "g2_constrained" / case_id / ("rep-%02d" % repetition)
            try:
                relocate_pair_workspace(output_root, pair_work, g2_archive)
                pair_work.mkdir(parents=True)
            except PairWorkspaceError as exc:
                raise ExperimentError(str(exc)) from exc
            require_gateway_identity(client, gateway_app_version, manifest["planning_policy"])
            run_reset(client, load_order, args.timeout, manifest["planning_policy"])
            g3_failed = False
            mismatch = False
            exact_replay = getattr(args, "paired_strategy", "production") == "artifact-replay"
            g3_archive = output_root / "g3_audited" / case_id / ("rep-%02d" % repetition)
            for item in case_state["rounds"]:
                spec, g2, artifact = item["round_spec"], item["row"], item["artifact"]
                if g3_failed:
                    g2_score = score_round(spec, g2_archive, truth) if g2 and method_run_status(g2) == "succeeded" else {"round": spec["round"], "ok": False, "artifacts": []}
                    records.append({"mode": "g2_constrained", "case_id": case_id, "repetition": repetition, "round": spec["round"],
                        "run_id": g2.get("id", "") if g2 else "", "run_status": method_run_status(g2) if g2 else "skipped_dependency",
                        "duration_seconds": item["duration"], "scores": [g2_score], "expected_rounds": expected_rounds,
                        "agent_trace": g2.get("agent_trace", []) if g2 else [], "result": g2.get("result") if g2 else None,
                        "failure_stage": run_failure_stage(g2) if g2 else "", "pair_id": pair_id,
                        "paired_artifact_hash": artifact.get("artifact_hash", "") if artifact else "", "baseline_workflow_hash": artifact.get("baseline_workflow_hash", "") if artifact else "",
                        "artifact_equal": False, "context_equal": False, "pair_valid": True, "skip_reason": item["skip"]})
                    records.append({"mode": "g3_audited", "case_id": case_id, "repetition": repetition, "round": spec["round"],
                        "run_id": "", "run_status": "skipped_dependency", "duration_seconds": 0.0,
                        "scores": [{"round": spec["round"], "ok": False, "artifacts": []}], "expected_rounds": expected_rounds,
                        "pair_id": pair_id, "paired_artifact_hash": artifact.get("artifact_hash", "") if artifact else "", "baseline_workflow_hash": artifact.get("baseline_workflow_hash", "") if artifact else "",
                        "artifact_equal": False, "context_equal": False, "pair_valid": True, "skip_reason": "G3 dependency round did not complete successfully"})
                    continue
                started = time.monotonic()
                replay_artifact = artifact if artifact is not None and exact_replay else None
                g3 = wait_for_method_run(
                    client,
                    submit_run(client, args, "g3_audited", item["command"], replay_artifact),
                    args.timeout,
                )
                duration = time.monotonic() - started
                require_run_policy(g3, manifest["planning_policy"])
                trace = _run_trace(g3)
                same_artifact = bool(artifact) and trace.get("plan_artifact_hash") == artifact["artifact_hash"]
                context_equal = bool(artifact) and trace.get("execution_context_hash") == artifact["execution_context_hash"]
                same_baseline = bool(artifact) and trace.get("baseline_workflow_hash") == artifact["baseline_workflow_hash"]
                pair_kind = "exact_artifact" if replay_artifact is not None else (
                    "causal_continuation" if artifact is not None else "production_recovery"
                )
                if replay_artifact is not None:
                    g3_baseline_artifact = artifact
                    valid = same_artifact and context_equal and same_baseline
                else:
                    g3_baseline_artifact = None
                    if method_run_status(g3) == "succeeded":
                        try:
                            g3_baseline_artifact = _artifact_from_run(g3)
                        except ExperimentError:
                            pass
                    valid = method_run_status(g3) != "succeeded" or bool(
                        g3_baseline_artifact
                        and g3_baseline_artifact.get("request") == item["command"]
                        and g3_baseline_artifact.get("execution_context_hash") == trace.get("execution_context_hash")
                        and g3_baseline_artifact.get("planning_policy") == manifest["planning_policy"]
                    )
                g2_execution = (_run_trace(g2).get("execution") or {}) if g2 else {}
                g3_execution = trace.get("execution") or {}
                post_equality = paired_post_execution_equality(g2_execution, g3_execution)
                post_context_equal = post_equality["context_equal"]
                post_result_equal = post_equality["result_equal"]
                post_snapshot_equal = post_equality["snapshot_equal"]
                for mode, row, elapsed, output in (("g2_constrained", g2, item["duration"], g2_archive), ("g3_audited", g3, duration, pair_work)):
                    score = score_round(spec, output, truth) if row and method_run_status(row) == "succeeded" else {"round": spec["round"], "ok": False, "artifacts": []}
                    if mode == "g3_audited":
                        score = _rebase_score_paths(score, pair_work, g3_archive)
                    records.append({"mode": mode, "case_id": case_id, "repetition": repetition, "round": spec["round"], "run_id": row.get("id", "") if row else "",
                        "run_status": method_run_status(row) if row else "skipped_dependency", "duration_seconds": round(elapsed, 3), "scores": [score], "expected_rounds": expected_rounds,
                        "agent_trace": row.get("agent_trace", []) if row else [], "result": row.get("result") if row else None,
                        "failure_stage": run_failure_stage(row) if row else "", "skip_reason": item["skip"] if row is None else "", "pair_id": pair_id,
                        "paired_artifact_hash": artifact.get("artifact_hash", "") if artifact else "", "baseline_workflow_hash": artifact.get("baseline_workflow_hash", "") if artifact else "",
                        "g3_baseline_artifact_hash": g3_baseline_artifact.get("artifact_hash", "") if g3_baseline_artifact else "",
                        "pair_kind": pair_kind, "artifact_equal": same_artifact, "context_equal": context_equal,
                        "post_context_equal": post_context_equal, "post_result_equal": post_result_equal,
                        "post_snapshot_equal": post_snapshot_equal, "pair_valid": valid})
                if not valid:
                    mismatch = True
                    break
                g3_failed = method_run_status(g3) != "succeeded"
                exact_replay = exact_replay and post_context_equal
                write_reports(output_root, records)
            if mismatch:
                raise ExperimentError("paired G2/G3 baseline, command, or context differs.")
            completed_pairs.append({"pair_id": pair_id, "case_id": case_id, "repetition": repetition})
    _atomic_write_json(
        output_root / "paired_g3_complete.json",
        {"schema": "geopilot-paired-g3-complete", "version": 1, "pairs": completed_pairs},
    )


def execute(args: argparse.Namespace) -> int:
    dataset = Path(args.dataset).resolve()
    output_root = Path(args.output).resolve()
    load_order, task_spec, truth = validate_dataset(dataset)
    task_spec = select_cases(task_spec, list(getattr(args, "case_ids", []) or []), list(getattr(args, "rounds", []) or []))
    static_catalog = direct_static_catalog(dataset)
    paired = {"g2_constrained", "g3_audited"}.issubset(args.modes)
    paired_phase = getattr(args, "paired_phase", None)
    if paired and paired_phase not in ("g2", "g3"):
        raise ExperimentError("Paired G2/G3 execution requires an explicit process phase.")
    if not paired and paired_phase is not None:
        raise ExperimentError("A paired process phase requires both G2 and G3 modes.")
    if paired_phase == "g3":
        if not output_root.is_dir():
            raise ExperimentError("Paired G3 phase requires the completed G2 output directory.")
    else:
        if output_root.exists():
            raise ExperimentError("Output directory already exists: %s" % output_root)
        output_root.mkdir(parents=True)
    client = GatewayClient(args.gateway)
    health = client.get("/health")
    if health.get("ok") is not True:
        raise ExperimentError("Gateway health check failed.")
    gateway_app_version = health.get("app_version")
    if not isinstance(gateway_app_version, str) or not gateway_app_version:
        raise ExperimentError("Gateway did not report an app version.")
    capabilities = client.get("/api/capabilities?detail=1")
    config = client.get("/config").get("config", {})
    current_manifest = build_manifest(
        dataset,
        args,
        config,
        capabilities["planning_policy"],
        Path(__file__).resolve().parents[2],
        gateway_app_version,
    )
    manifest_path = output_root / "experiment_manifest.json"
    if paired_phase == "g3":
        if not manifest_path.is_file():
            raise ExperimentError("Paired experiment manifest is missing.")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        comparable_current = dict(current_manifest)
        comparable_current["created_at"] = manifest.get("created_at")
        if comparable_current != manifest:
            raise ExperimentError("Paired G3 phase does not match the frozen G2 experiment manifest.")
    else:
        manifest = current_manifest
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    client.post("/arcmap/permission", {"auto_execute": True, "allow_edits": False})
    records: list[dict[str, Any]] = []
    try:
        if paired:
            if paired_phase == "g2":
                execute_paired_g2(
                    client, args, load_order, task_spec, static_catalog, output_root,
                    manifest, gateway_app_version,
                )
            else:
                execute_paired_g3(
                    client, args, load_order, task_spec, truth, output_root,
                    manifest, gateway_app_version, records,
                )
        for repetition in range(1, args.repetitions + 1):
            for mode in args.modes:
                if paired and mode in ("g2_constrained", "g3_audited"):
                    continue
                for case in task_spec["cases"]:
                    expected_rounds = len(case["rounds"])
                    require_gateway_identity(
                        client,
                        gateway_app_version,
                        manifest["planning_policy"],
                    )
                    run_reset(
                        client,
                        load_order,
                        args.timeout,
                        manifest["planning_policy"],
                    )
                    case_dir = output_root / mode / case["case_id"] / ("rep-%02d" % repetition)
                    case_dir.mkdir(parents=True)
                    for round_spec in case["rounds"]:
                        started = time.monotonic()
                        run_id = ""
                        row: dict[str, Any] = {}
                        runner_error = ""
                        try:
                            require_gateway_identity(
                                client,
                                gateway_app_version,
                                manifest["planning_policy"],
                            )
                            run_id = submit_run(
                                client,
                                args,
                                mode,
                                task_command(round_spec, case_dir, mode, static_catalog),
                            )
                            row = wait_for_method_run(client, run_id, args.timeout)
                            require_run_policy(row, manifest["planning_policy"])
                            score = (
                                score_round(round_spec, case_dir, truth)
                                if method_run_status(row) == "succeeded"
                                else {
                                    "round": round_spec["round"],
                                    "ok": False,
                                    "artifacts": [],
                                }
                            )
                        except (InfrastructureStop, ModelQuotaStop):
                            raise
                        except Exception as exc:
                            runner_error = "%s: %s" % (type(exc).__name__, exc)
                            score = {
                                "round": round_spec["round"],
                                "ok": False,
                                "artifacts": [],
                                "reason": "runner_error",
                            }
                        duration = time.monotonic() - started
                        run_status = "runner_failed" if runner_error else method_run_status(row)
                        records.append(
                            {
                                "mode": mode,
                                "case_id": case["case_id"],
                                "repetition": repetition,
                                "round": round_spec["round"],
                                "run_id": run_id or row.get("id", ""),
                                "run_status": run_status,
                                "duration_seconds": round(duration, 3),
                                "scores": [score],
                                "failure_stage": "runner" if runner_error else run_failure_stage(row),
                                "runner_error": runner_error,
                                "expected_rounds": expected_rounds,
                                "agent_trace": row.get("agent_trace", []),
                                "result": row.get("result"),
                            }
                        )
                        write_reports(output_root, records)
                        if run_status != "succeeded" or not score["ok"]:
                            for remaining in case["rounds"][round_spec["round"]:]:
                                records.append(
                                    {
                                        "mode": mode,
                                        "case_id": case["case_id"],
                                        "repetition": repetition,
                                        "round": remaining["round"],
                                        "run_id": "",
                                        "run_status": "skipped_dependency",
                                        "duration_seconds": 0.0,
                                        "blocked_by_round": round_spec["round"],
                                        "blocked_by_run_id": run_id or row.get("id", ""),
                                        "skip_reason": (
                                            "dependency round did not complete successfully"
                                        ),
                                        "scores": [
                                            {
                                                "round": remaining["round"],
                                                "ok": False,
                                                "artifacts": [],
                                            }
                                        ],
                                        "expected_rounds": expected_rounds,
                                    }
                                )
                            write_reports(output_root, records)
                            if runner_error:
                                raise ExperimentError(runner_error)
                            break
    except InfrastructureStop as exc:
        write_infrastructure_stop(output_root, exc)
        raise
    except ModelQuotaStop as exc:
        write_model_quota_stop(output_root, exc)
        raise
    finally:
        write_reports(output_root, records)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run formal GeoPilot ArcMap ablation experiments.")
    parser.add_argument("--dataset", default="experiments/data/synthetic-city-v1")
    parser.add_argument("--output", required=True)
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--case-ids", nargs="+", default=[])
    parser.add_argument("--rounds", nargs="+", type=int, default=[])
    parser.add_argument("--replay-baseline-record", type=Path)
    parser.add_argument("--paired-phase", choices=("g2", "g3"))
    parser.add_argument("--paired-strategy", choices=("production", "artifact-replay"), default="production")
    parser.add_argument("--code-version")
    parser.add_argument("--source-fingerprint")
    parser.add_argument("--dirty", choices=("true", "false"))
    args = parser.parse_args()
    if args.repetitions <= 0 or args.timeout <= 0:
        parser.error("repetitions and timeout must be positive.")
    if bool(args.provider) != bool(args.model):
        parser.error("--provider and --model must be supplied together.")
    if ("g2_constrained" in args.modes) != ("g3_audited" in args.modes):
        parser.error("g2_constrained and g3_audited must be selected together.")
    if {"g2_constrained", "g3_audited"}.issubset(args.modes) and args.paired_phase is None:
        parser.error("paired G2/G3 modes require --paired-phase g2 or --paired-phase g3.")
    if args.paired_phase is not None and not {"g2_constrained", "g3_audited"}.issubset(args.modes):
        parser.error("--paired-phase requires both g2_constrained and g3_audited.")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(execute(parse_args()))
    except ExperimentError as exc:
        print("Formal experiment failed: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
