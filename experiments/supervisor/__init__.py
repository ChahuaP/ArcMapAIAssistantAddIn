"""The only Chapter 3 runtime-gate command.

This module deliberately owns campaign state, dataset verification and the
ArcMap reset contract.  It does not know Kernel internals and communicates
only through the versioned ``RuntimeGateLifecycle`` port.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol, Sequence
from experiments.synthetic_city.source_provenance import repository_state

from gateway_py3.kernel.contracts import TargetSelector
from gateway_py3.runtime.bridge_discovery import list_bridge_targets
from gateway_py3.runtime.bridge_client import RealBridgeClient

PROVIDER = "minimax"
MODEL = "MiniMax-M3"
CASES = ("FLOOD_RESPONSE", "LAND_COMPLIANCE")
ARMS = ("g2", "g3")


class CampaignError(RuntimeError):
    pass


class RuntimeGateLifecycle(Protocol):
    """Deep seam for one isolated arm initialisation.

    ``prepare`` must load exactly the declared source layers into a fresh map,
    clear every selection, assign an arm-private workspace, and return an
    immutable initial-state digest.  ``restore_round`` must make accepted
    prior artifacts available by their logical names before the next prompt.
    """
    def prepare(self, selector: TargetSelector, source_layers: Sequence[Path],
                staging_gdb: Path, expected_target: Mapping[str, Any]) -> str: ...
    def restore_round(self, selector: TargetSelector, initial_digest: str,
                      artifacts: Mapping[str, Mapping[str, Any]]) -> str: ...
    def evaluate(self, selector: TargetSelector, artifacts: Mapping[str, Mapping[str, Any]],
                 expected: Mapping[str, Mapping[str, Any]]) -> Mapping[str, Any]: ...


class BridgeRuntimeGateLifecycle:
    """The production lifecycle port: Py3 -> Bridge -> ArcMap Python 2."""
    def __init__(self, bridge: Optional[RealBridgeClient] = None):
        self._bridge = bridge or RealBridgeClient()

    def prepare(self, selector, source_layers, staging_gdb, expected_target):
        if selector.model_dump(mode="json") != dict(expected_target):
            raise CampaignError("runtime lifecycle target does not match the frozen campaign target")
        staging_gdb.parent.mkdir(parents=True, exist_ok=True)
        return self._bridge.runtime_gate_prepare(selector, source_layers, str(staging_gdb))

    def restore_round(self, selector, initial_digest, artifacts):
        return self._bridge.runtime_gate_restore(selector, initial_digest, dict(artifacts))

    def evaluate(self, selector, artifacts, expected):
        return self._bridge.runtime_gate_evaluate(selector, dict(artifacts), dict(expected))


def _atomic_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".campaign-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except OSError: pass
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _campaign_provenance(dataset: Path, selector: TargetSelector,
                         verified: Mapping[str, Any], supervisor: Any) -> Dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    repository = repository_state(repo_root).as_dict()
    identity = getattr(supervisor, "runtime_identity", None)
    if callable(identity): identity = identity()
    if not isinstance(identity, dict):
        # Test ports have no model runtime. Production ExperimentSupervisor
        # always exposes the complete per-role identity above.
        identity = {"test_port": {"provider": PROVIDER, "model": MODEL,
                                   "connection_id": "test", "endpoint_fingerprint": "test",
                                   "deployment_fingerprint": "test"}}
    for role, binding in identity.items():
        required = ("provider", "model", "connection_id", "endpoint_fingerprint", "deployment_fingerprint")
        if not isinstance(binding, dict) or any(not binding.get(key) for key in required):
            raise CampaignError("formal runtime identity is incomplete for role %s" % role)
        if (binding["provider"], binding["model"]) != (PROVIDER, MODEL):
            raise CampaignError("formal runtime identity drifted from minimax/MiniMax-M3")
    contract_files = [repo_root / "shared_runtime" / "runtime_gate.schema.json",
                      repo_root / "gateway_py3" / "kernel" / "contracts.py",
                      Path(__file__).resolve()]
    contract_hash = _json_digest({str(path.relative_to(repo_root)): _sha256(path) for path in contract_files})
    document = {
        "repository": repository,
        "runtime_identity": identity,
        "arcmap_target": selector.model_dump(mode="json"),
        "dataset_manifest_sha256": _sha256(dataset / "manifest.json"),
        "dataset_manifest": verified["manifest"],
        "experiment_contract_sha256": contract_hash,
    }
    document["digest"] = _json_digest(document)
    return document


def _assert_same_provenance(frozen: Mapping[str, Any], current: Mapping[str, Any]) -> None:
    frozen_body = {key: value for key, value in frozen.items() if key != "digest"}
    current_body = {key: value for key, value in current.items() if key != "digest"}
    if frozen.get("digest") != _json_digest(frozen_body):
        raise CampaignError("campaign checkpoint provenance digest is invalid")
    if current.get("digest") != _json_digest(current_body):
        raise CampaignError("current campaign provenance digest is invalid")
    if frozen_body != current_body:
        keys = sorted(key for key in set(frozen) | set(current)
                      if frozen.get(key) != current.get(key))
        raise CampaignError("campaign provenance drift: %s" % ", ".join(keys))


def verify_dataset(dataset: Path, cases: Sequence[str], seed: int) -> Dict[str, Any]:
    """Verify every manifest member before touching ArcMap or a model."""
    manifest_path = dataset / "manifest.json"
    if not manifest_path.is_file(): raise CampaignError("dataset manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("seed") != seed or manifest.get("rounds_per_case") != 3:
        raise CampaignError("dataset does not match the authoritative three-round seed")
    declared_files = set()
    for entry in manifest.get("files", ()):
        relative = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise CampaignError("manifest has an invalid relative file name")
        normalized_relative = Path(relative).as_posix()
        if normalized_relative in declared_files:
            raise CampaignError("manifest declares a duplicate member")
        declared_files.add(normalized_relative)
        member = (dataset / relative).resolve()
        try: member.relative_to(dataset.resolve())
        except ValueError as exc: raise CampaignError("manifest path escapes dataset") from exc
        if not member.is_file() or member.stat().st_size != entry.get("bytes") or _sha256(member) != entry.get("sha256"):
            raise CampaignError("dataset manifest verification failed: %s" % relative)
    actual_files = {path.relative_to(dataset).as_posix() for path in dataset.rglob("*")
                    if path.is_file() and path != manifest_path}
    if declared_files != actual_files:
        raise CampaignError("dataset manifest does not cover every and only dataset member")
    source = dataset / "source"
    source_layers = tuple(sorted(source.glob("*.shp")))
    declared = manifest.get("source_layers") or {}
    if len(source_layers) != len(declared) or set(path.stem for path in source_layers) != set(declared):
        raise CampaignError("manifest source-layer declaration is not exactly loadable")
    cases_doc = json.loads((dataset / "task_cases.json").read_text(encoding="utf-8"))
    selected = {item.get("case_id"): item for item in cases_doc.get("cases", [])}
    if set(cases) != set(CASES) or any(len(selected.get(case, {}).get("rounds", ())) != 3 for case in cases):
        raise CampaignError("runtime gate requires exactly FLOOD_RESPONSE and LAND_COMPLIANCE with three rounds")
    for case_id in cases:
        case = selected[case_id]
        for round_doc in case.get("rounds", []):
            bindings = round_doc.get("truth_bindings")
            if not isinstance(bindings, list):
                raise CampaignError("each formal round requires explicit truth bindings")
            keys = set(round_doc.get("expected_id_keys", []))
            declared = set()
            for binding in bindings:
                if not isinstance(binding, dict) or set(binding) != {"output_id", "truth_key", "id_field"}:
                    raise CampaignError("truth binding has an invalid contract")
                if binding["truth_key"] not in keys or binding["output_id"] not in round_doc.get("expected_outputs", []) or not binding["id_field"]:
                    raise CampaignError("truth binding does not match its declared output and expected id key")
                declared.add(binding["truth_key"])
            if declared != keys:
                raise CampaignError("truth bindings must cover every expected id key exactly once")
    truth = json.loads((dataset / "truth" / "expected_ids.json").read_text(encoding="utf-8"))
    return {"manifest": manifest, "source_layers": source_layers, "cases": selected, "truth": truth}


def explicit_target(arcmap_pid: Optional[int], hwnd: Optional[int]) -> TargetSelector:
    if not isinstance(arcmap_pid, int) or not isinstance(hwnd, int) or arcmap_pid <= 0 or hwnd <= 0:
        raise CampaignError("--arcmap-pid and --hwnd are both required")
    matches = [item for item in list_bridge_targets()
               if item.get("arcmap_pid") == arcmap_pid and item.get("hwnd") == hwnd]
    if len(matches) != 1: raise CampaignError("explicit ArcMap PID/HWND does not identify exactly one bridge target")
    item = matches[0]
    return TargetSelector(bridge_pid=item["bridge_pid"], bridge_port=item["bridge_port"],
                          arcmap_pid=arcmap_pid, hwnd=hwnd, deployment_hash=item["deployment_hash"])


def _cells() -> list[Dict[str, Any]]:
    return [{"case_id": case, "round": round_no, "arm": arm, "state": "pending"}
            for case in CASES for round_no in (1, 2, 3) for arm in ARMS]


def _new_state(args, dataset: Path, selector: TargetSelector, verified: Mapping[str, Any]) -> Dict[str, Any]:
    return {"schema": "geopilot-runtime-gate-v2", "status": "running", "provider": PROVIDER,
            "model": MODEL, "seed": args.seed, "repetition": 1, "dataset": str(dataset),
            "manifest_hash": _sha256(dataset / "manifest.json"),
            "target": selector.model_dump(mode="json"), "cells": _cells(), "started_at": time.time()}


def campaign_report(state: Mapping[str, Any]) -> Dict[str, Any]:
    cells = list(state["cells"])
    valid = [cell for cell in cells if cell["state"] == "round_valid"]
    paired = {(cell["case_id"], cell["round"]): {} for cell in cells}
    for cell in cells: paired[(cell["case_id"], cell["round"])][cell["arm"]] = cell
    pairs = list(paired.values())
    pair_valid = [pair for pair in pairs if all(pair.get(arm, {}).get("state") == "round_valid" and
                                                pair.get(arm, {}).get("pair_contract_valid") is True for arm in ARMS)]
    evidence_valid = len(valid) == 12
    campaign_valid = evidence_valid and len(pair_valid) == 6
    aggregate_g2 = sum(float(pair["g2"].get("score", 0.0)) for pair in pair_valid)
    aggregate_g3 = sum(float(pair["g3"].get("score", 0.0)) for pair in pair_valid)
    # The formal gate compares aggregate evidence quality only.  It neither
    # manufactures a G2 error nor demands that every audited arm be correct;
    # per-arm accuracy remains a reported measurement.
    gate_passed = campaign_valid and aggregate_g3 >= aggregate_g2
    return {"status": state.get("status"), "rounds_valid": len(valid), "pairs_valid": len(pair_valid),
            "evidence_valid": evidence_valid, "campaign_valid": campaign_valid,
            "gate_passed": gate_passed, "aggregate_g2_score": aggregate_g2,
            "aggregate_g3_score": aggregate_g3,
            "accuracy": {"g2": aggregate_g2 / len(pair_valid) if pair_valid else 0.0,
                         "g3": aggregate_g3 / len(pair_valid) if pair_valid else 0.0}}


def _cell(state: Dict[str, Any], case_id: str, round_no: int, arm: str) -> Dict[str, Any]:
    for cell in state["cells"]:
        if (cell["case_id"], cell["round"], cell["arm"]) == (case_id, round_no, arm):
            return cell
    raise CampaignError("campaign checkpoint has no required cell")


def _prior_artifacts(state: Mapping[str, Any], case_id: str, round_no: int, arm: str) -> Dict[str, Dict[str, Any]]:
    artifacts: Dict[str, Dict[str, Any]] = {}
    for prior in range(1, round_no):
        cell = _cell(dict(state), case_id, prior, arm)
        if cell.get("state") != "round_valid":
            raise CampaignError("cannot start a round before its prior round passed")
        for artifact in cell.get("artifact_manifest", {}).get("artifacts", []):
            if not isinstance(artifact, dict) or not isinstance(artifact.get("output_id"), str):
                raise CampaignError("checkpoint contains malformed accepted artifact")
            artifacts[artifact["output_id"]] = artifact
    return artifacts


def _score_run(export: Mapping[str, Any], expected_outputs: Sequence[str],
               expected_ids: Optional[Mapping[str, Sequence[str]]] = None,
               truth_evidence: Optional[Mapping[str, Any]] = None) -> tuple[float, list[Dict[str, Any]]]:
    report = export.get("acceptance_report")
    if not isinstance(report, dict) or report.get("passed") is not True:
        return 0.0, []
    actual = []
    for item in export.get("artifacts", []):
        if not isinstance(item, dict) or not isinstance(item.get("evidence_hash"), str):
            continue
        actual.append(item)
    actual_ids = [item.get("output_id") for item in actual]
    if len(actual_ids) != len(expected_outputs) or set(actual_ids) != set(expected_outputs):
        return 0.0, []
    expected_ids = expected_ids or {}
    observed_ids = (truth_evidence or {}).get("truth_ids")
    evidence_hash = (truth_evidence or {}).get("evidence_hash")
    manifests = (truth_evidence or {}).get("artifact_manifests")
    if (not isinstance(observed_ids, dict) or not isinstance(manifests, dict) or
            set(manifests) != set(expected_outputs) or
            set(observed_ids) != set(expected_ids) or
            not isinstance(evidence_hash, str) or len(evidence_hash) != 64):
        return 0.0, []
    for output_id, identifiers in expected_ids.items():
        observed = observed_ids.get(output_id)
        if not isinstance(observed, list) or sorted(observed) != sorted(identifiers):
            return 0.0, actual
    return 1.0, actual


def _save_cell(state: Dict[str, Any], state_path: Path, case_id: str, round_no: int,
               arm: str, result: Mapping[str, Any], export: Mapping[str, Any], expected_outputs: Sequence[str],
               expected_ids: Mapping[str, Sequence[str]], truth_evidence: Mapping[str, Any],
               truth_contract: Mapping[str, Mapping[str, Any]]) -> None:
    cell = _cell(state, case_id, round_no, arm)
    score, artifacts = _score_run(export, expected_outputs, expected_ids, truth_evidence)
    outcome = result.get("%s_outcome" % arm) or {}
    run_id = result.get("%s_run_id" % arm)
    if not run_id and not outcome:
        cell["state"] = "pending"
        _atomic_json(state_path, state)
        return
    evidence_complete = (outcome.get("kind") == "Succeeded" and
                         isinstance(export.get("acceptance_report"), dict) and
                         export["acceptance_report"].get("passed") is True and
                         bool(artifacts) and isinstance((truth_evidence or {}).get("truth_ids"), dict))
    cell.update({"state": ("quota_stopped" if outcome.get("kind") == "QuotaStopped" else
                           "round_valid" if evidence_complete else "failed"),
                 "score": score, "run_id": run_id,
                 "artifact_manifest": {"artifacts": artifacts,
                                       "acceptance_report": export.get("acceptance_report"),
                                       "truth_evidence": dict(truth_evidence or {}),
                                       "truth_contract": {key: dict(value) for key, value in truth_contract.items()}},
                 "outcome": outcome, "pair_id": result.get("pair_id"),
                 "pair_contract_valid": result.get("pair_valid") is True,
                 "provenance_digest": (state.get("provenance") or {}).get("digest")})
    _atomic_json(state_path, state)


def run(args: argparse.Namespace, lifecycle: Optional[RuntimeGateLifecycle] = None, runner_factory=None) -> Dict[str, Any]:
    if (args.provider, args.model, args.seed, args.repetition) != (PROVIDER, MODEL, 20260910, 1):
        raise CampaignError("runtime gate requires --provider minimax --model MiniMax-M3 --seed 20260910 --repetition 1")
    dataset, output = Path(args.dataset).resolve(), Path(args.output).resolve()
    verified = verify_dataset(dataset, args.case, args.seed)
    selector = explicit_target(args.arcmap_pid, args.hwnd)
    state_path = output / "campaign_state.json"
    if output.exists() and not state_path.is_file(): raise CampaignError("output exists without a resumable campaign checkpoint")
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("status") != "quota_stopped": raise CampaignError("only a quota-stopped campaign can resume")
        if state.get("manifest_hash") != _sha256(dataset / "manifest.json") or state.get("target") != selector.model_dump(mode="json"):
            raise CampaignError("checkpoint dataset or ArcMap target identity changed")
        state.setdefault("resume_generations", []).append({
            "generation": len(state.get("resume_generations", [])) + 1,
            "requested_at": time.time(),
        })
    else:
        output.mkdir(parents=True); state = _new_state(args, dataset, selector, verified)
    runtime = lifecycle or BridgeRuntimeGateLifecycle()
    # Construct the real Kernel only after dataset, target and lifecycle are
    # verified. A missing Bridge lifecycle therefore spends no model tokens.
    if runner_factory is None:
        from gateway_py3.app import build_kernel
        from gateway_py3.experiments.supervisor import ExperimentSupervisor
        from gateway_py3.kernel.store import JournalStore
        store = JournalStore(output / "runtime_gate.sqlite")
        kernel, _projection, _bridge = build_kernel(store)
        supervisor = ExperimentSupervisor(kernel, output / "evidence")
    else:
        kernel, supervisor = runner_factory(output)
    current_provenance = _campaign_provenance(dataset, selector, verified, supervisor)
    if state_path.is_file():
        frozen = state.get("provenance")
        if not isinstance(frozen, dict):
            raise CampaignError("campaign checkpoint has no frozen provenance")
        _assert_same_provenance(frozen, current_provenance)
    else:
        state["provenance"] = current_provenance
    _atomic_json(state_path, state)
    for case_id in CASES:
        case = verified["cases"][case_id]
        for round_doc in case["rounds"]:
            round_no = int(round_doc["round"])
            pending = [_cell(state, case_id, round_no, arm) for arm in ARMS]
            if all(cell["state"] == "round_valid" for cell in pending):
                continue
            resumable = ((pending[0]["state"] == "quota_stopped" and pending[1]["state"] == "pending") or
                         (pending[0]["state"] == "round_valid" and pending[1]["state"] == "quota_stopped"))
            if not resumable and any(cell["state"] != "pending" for cell in pending):
                raise CampaignError("checkpoint has a non-resumable incomplete pair")
            prior = {arm: _prior_artifacts(state, case_id, round_no, arm) for arm in ARMS}
            sessions = tuple("%s-%s-%s" % (case_id.lower(), round_no, arm) for arm in ARMS)
            # Session identifiers are contracts, so use UUID5 while keeping a
            # stable campaign-local task identity for resumable checkpoints.
            import uuid
            sessions = tuple(str(uuid.uuid5(uuid.NAMESPACE_URL, "%s:%s" % (state_path, arm))) for arm in sessions)
            initial_states = {}
            arm_roots = {arm: (output / "work" / case_id.lower() /
                               ("round-%d" % round_no) / arm).resolve() for arm in ARMS}
            arm_artifact_roots = {arm: str(root / "artifacts") for arm, root in arm_roots.items()}
            runtime_workspaces = {arm: root / "staging.gdb" for arm, root in arm_roots.items()}
            def prepare_arm(arm: str) -> None:
                workspace = runtime_workspaces[arm]
                initial = runtime.prepare(selector, verified["source_layers"], workspace, state["target"])
                if not isinstance(initial, str) or not initial:
                    raise CampaignError("lifecycle returned no initial-state digest")
                if initial_states and initial != next(iter(initial_states.values())):
                    raise CampaignError("G2/G3 runtime-gate initial ArcMap contexts are not identical")
                for prior_round in range(1, round_no):
                    manifest = _cell(state, case_id, prior_round, arm).get("artifact_manifest", {})
                    artifact_map = {item["output_id"]: item for item in manifest.get("artifacts", [])}
                    evidence = runtime.evaluate(selector, artifact_map, manifest.get("truth_contract", {}))
                    if evidence.get("evidence_hash") != manifest.get("truth_evidence", {}).get("evidence_hash"):
                        raise CampaignError("accepted prior-round artifact manifest was changed")
                restored = runtime.restore_round(selector, initial, prior[arm])
                if not isinstance(restored, str) or len(restored) != 64:
                    raise CampaignError("runtime lifecycle returned no verified restore digest")
                if not prior[arm] and restored != initial:
                    raise CampaignError("empty restore changed the prepared ArcMap context")
                if prior[arm] and restored == initial:
                    raise CampaignError("prior-round artifacts were not reflected in restored ArcMap state")
                initial_states[arm] = initial
            result = supervisor.run_pair(round_doc["prompt"], args.seed, target_selector=selector,
                                         provider=PROVIDER, model=MODEL,
                                         outputs=tuple(round_doc["expected_outputs"]),
                                         session_ids=sessions, prepare_arm=prepare_arm,
                                         pair_id=(pending[0].get("pair_id") or pending[1].get("pair_id")) if resumable else None,
                                         resume_runs={arm: cell.get("run_id") for arm, cell in zip(ARMS, pending)
                                                      if cell.get("state") in ("round_valid", "quota_stopped")} if resumable else None,
                                         arm_artifact_roots=arm_artifact_roots)
            for arm in ARMS:
                run_id = result.get("%s_run_id" % arm)
                export = kernel.export_run_journal(run_id) if run_id else {}
                expected = {binding["output_id"]: {"field": binding["id_field"], "ids": verified["truth"][binding["truth_key"]]}
                            for binding in round_doc["truth_bindings"]}
                artifacts = {item["output_id"]: item for item in export.get("artifacts", [])
                             if isinstance(item, dict) and isinstance(item.get("output_id"), str)}
                truth_evidence = runtime.evaluate(selector, artifacts, expected) if (
                    (result.get("%s_outcome" % arm) or {}).get("kind") == "Succeeded") else {}
                _save_cell(state, state_path, case_id, round_no, arm, result, export,
                           tuple(round_doc["expected_outputs"]),
                           {output: value["ids"] for output, value in expected.items()}, truth_evidence, expected)
            if any(_cell(state, case_id, round_no, arm)["state"] == "quota_stopped" for arm in ARMS):
                state["status"] = "quota_stopped"; _atomic_json(state_path, state)
                return campaign_report(state)
            if result.get("pair_valid") is not True:
                state["status"] = "failed"; _atomic_json(state_path, state)
                raise CampaignError("formal G2/G3 fairness contract failed")
            if any(_cell(state, case_id, round_no, arm)["state"] != "round_valid" for arm in ARMS):
                state["status"] = "failed"; _atomic_json(state_path, state)
                raise CampaignError("runtime gate cell failed; campaign stopped without retry")
    report = campaign_report(dict(state, status="completed_valid"))
    if not report["campaign_valid"]:
        state["status"] = "failed"; _atomic_json(state_path, state)
        raise CampaignError("campaign did not satisfy the formal runtime-gate contract")
    state["status"] = "completed_valid"; _atomic_json(state_path, state)
    return campaign_report(state)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Run the only GeoPilot Chapter 3 runtime gate.")
    result.add_argument("--provider", required=True); result.add_argument("--model", required=True)
    result.add_argument("--dataset", type=Path, required=True); result.add_argument("--output", type=Path, required=True)
    result.add_argument("--seed", type=int, required=True); result.add_argument("--repetition", type=int, required=True)
    result.add_argument("--case", action="append", required=True, choices=CASES)
    result.add_argument("--arcmap-pid", type=int, required=True); result.add_argument("--hwnd", type=int, required=True)
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try: report = run(args)
    except Exception as exc:
        print("campaign_failed: %s: %s" % (type(exc).__name__, exc)); return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True)); return 0 if report["gate_passed"] else 3


__all__ = ["BridgeRuntimeGateLifecycle", "CampaignError", "RuntimeGateLifecycle", "campaign_report", "explicit_target", "main", "parser", "run", "verify_dataset"]
