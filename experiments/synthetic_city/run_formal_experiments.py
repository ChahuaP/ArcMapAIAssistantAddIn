#!/usr/bin/env python3
"""Run and score reproducible GeoPilot/ArcMap ablation experiments.

Every task is executed through the GeoPilot Gateway.  The runner never uses
ArcPy or UI automation: before each mode/case/repetition it asks GeoPilot to
clear ArcMap and reload the immutable source layers, then verifies the fresh
ArcMap context returned by the Bridge.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import geopandas as gpd


MODES = ("direct_single", "context_single", "constrained_single", "multi_agent")
TERMINAL_STATUSES = {
    "succeeded",
    "failed",
    "context_failed",
    "cancelled",
    "clarify",
    "reject",
}
INFRASTRUCTURE_STOP_STATUSES = {"recovery_required", "indeterminate"}
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

    def submit(self, mode: str, command: str) -> str:
        response = self.post("/runs", {"mode": mode, "command": command, "execute": True})
        run = response.get("run") if isinstance(response.get("run"), dict) else {}
        run_id = run.get("id")
        if not isinstance(run_id, str) or not run_id:
            raise ExperimentError("Gateway did not return a run id.")
        return run_id

    def wait(self, run_id: str, timeout_seconds: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            row = self.get("/runs/" + run_id).get("run")
            if not isinstance(row, dict):
                raise ExperimentError("Gateway returned an invalid run record: %s" % run_id)
            if row.get("status") in INFRASTRUCTURE_STOP_STATUSES:
                raise InfrastructureStop(run_id, row["status"])
            if row.get("status") in TERMINAL_STATUSES:
                return row
            time.sleep(0.5)
        raise ExperimentError("Run timed out: %s" % run_id)


def source_layer_names(load_order: list[str]) -> set[str]:
    return {Path(item).stem for item in load_order}


def reset_command(load_order: list[str]) -> str:
    paths = "；".join(load_order)
    return (
        "正式实验环境复位。必须先调用 layer.clear_layers 清空当前 ArcMap 数据框中的全部图层；"
        "随后严格按以下路径各调用一次 layer.add_layer 加载源数据：%s。"
        "不得改写、删除或覆盖任何源数据。最后调用 context.list_layers。"
        "成功条件是地图中恰好保留这14个源图层。" % paths
    )


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
    if mode == "direct_single":
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
    row = client.wait(client.submit("context_single", reset_command(load_order)), timeout_seconds)
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


def require_run_policy(row: dict[str, Any], expected_policy: dict[str, Any]) -> None:
    actual = run_trace(row).get("planning_policy")
    if actual != expected_policy:
        raise ExperimentError("Run planning policy does not match the frozen experiment policy.")


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
                    "failure_stage": record.get("failure_stage", ""),
                    "blocked_by_round": record.get("blocked_by_round", ""),
                    "blocked_by_run_id": record.get("blocked_by_run_id", ""),
                    "skip_reason": record.get("skip_reason", ""),
                    "expected_rounds": record.get("expected_rounds", ""),
                    "runner_error": record.get("runner_error", ""),
                }
            )
    _write_csv(output_root / "round_scores.csv", rows)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["mode"], row["case_id"]), []).append(row)
    summary = []
    for (mode, case_id), values in sorted(groups.items()):
        attempted = [row for row in values if row["run_status"] != "skipped_dependency"]
        succeeded = [row for row in attempted if row["run_status"] == "succeeded"]
        flows: dict[int, list[dict[str, Any]]] = {}
        for row in values:
            flows.setdefault(row["repetition"], []).append(row)
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
    _write_csv(output_root / "summary.csv", summary)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = list(rows[0]) if rows else []
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


def validate_dataset(dataset: Path) -> tuple[list[str], dict[str, Any], dict[str, list[str]]]:
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


def _git_state(repository: Path) -> tuple[str, bool]:
    version = _git_output(repository, "rev-parse", "HEAD")
    dirty = bool(_git_output(repository, "status", "--porcelain"))
    return version, dirty


def _git_output(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def build_manifest(
    dataset,
    args,
    gateway_config,
    planning_policy,
    repository,
    gateway_app_version,
    created_at=None,
):
    version, dirty = _git_state(Path(repository))
    return {
        "dataset": str(dataset),
        "modes": args.modes,
        "repetitions": args.repetitions,
        "timeout_seconds": args.timeout,
        "gateway": args.gateway,
        "gateway_app_version": gateway_app_version,
        "created_at": time.time() if created_at is None else created_at,
        "primary": {
            "provider": gateway_config.get("primary_provider", ""),
            "model": gateway_config.get("primary_model", ""),
        },
        "reviewer": {
            "provider": gateway_config.get("reviewer_provider", ""),
            "model": gateway_config.get("reviewer_model", ""),
        },
        "planning_policy": planning_policy,
        "catalog_hash": planning_policy["catalog_hash"],
        "protocol_hash": planning_policy["protocol_hash"],
        "code_version": version,
        "dirty": dirty,
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


def execute(args: argparse.Namespace) -> int:
    dataset = Path(args.dataset).resolve()
    output_root = Path(args.output).resolve()
    load_order, task_spec, truth = validate_dataset(dataset)
    static_catalog = direct_static_catalog(dataset)
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
    manifest = build_manifest(
        dataset,
        args,
        config,
        capabilities["planning_policy"],
        Path(__file__).resolve().parents[2],
        gateway_app_version,
    )
    (output_root / "experiment_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    client.post("/arcmap/permission", {"auto_execute": True, "allow_edits": False})
    records: list[dict[str, Any]] = []
    try:
        for repetition in range(1, args.repetitions + 1):
            for mode in args.modes:
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
                            run_id = client.submit(
                                mode,
                                task_command(round_spec, case_dir, mode, static_catalog),
                            )
                            row = client.wait(run_id, args.timeout)
                            require_run_policy(row, manifest["planning_policy"])
                            score = (
                                score_round(round_spec, case_dir, truth)
                                if row.get("status") == "succeeded"
                                else {
                                    "round": round_spec["round"],
                                    "ok": False,
                                    "artifacts": [],
                                }
                            )
                        except InfrastructureStop:
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
                        run_status = "runner_failed" if runner_error else row.get("status")
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
    finally:
        write_reports(output_root, records)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run formal GeoPilot ArcMap ablation experiments.")
    parser.add_argument("--dataset", default="out/synthetic-city-v1")
    parser.add_argument("--output", required=True)
    parser.add_argument("--gateway", default=DEFAULT_GATEWAY)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    args = parser.parse_args()
    if args.repetitions <= 0 or args.timeout <= 0:
        parser.error("repetitions and timeout must be positive.")
    return args


if __name__ == "__main__":
    try:
        raise SystemExit(execute(parse_args()))
    except ExperimentError as exc:
        print("Formal experiment failed: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
