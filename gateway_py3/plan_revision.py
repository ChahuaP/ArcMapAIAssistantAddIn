"""Closed proof-bound G3 revisions and their monotonic proof guard."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Tuple


class PlanRevisionError(ValueError):
    pass


@dataclass(frozen=True)
class PlanRevision:
    """One scalar argument replacement justified by one unresolved proof."""

    proof_id: str
    step_id: str
    path: str
    value: Any

    def __post_init__(self):
        if not all(isinstance(value, str) and value for value in
                   (self.proof_id, self.step_id, self.path)):
            raise PlanRevisionError("revision requires proof_id, step_id, and path")
        if not self.path.startswith("arguments.") or self.path == "arguments.":
            raise PlanRevisionError("revision path must name one argument")
        if isinstance(self.value, (dict, list, tuple, set)):
            raise PlanRevisionError("revision may replace only one scalar argument value")


def revision_scope(workflow: Dict[str, Any], report: Dict[str, Any], catalog) -> Dict[str, Tuple[str, ...]]:
    """Derive the only mutable scalar paths from unresolved proof evidence.

    A proof without a concrete responsible step is not machine-revisable and
    must be clarified. Input-layer and output arguments are immutable.
    """
    steps = {step.get("id"): step for step in workflow.get("steps", [])
             if isinstance(step, dict) and isinstance(step.get("id"), str)}
    scope = {}
    for proof in report.get("proof_graph", []):
        if not isinstance(proof, dict) or proof.get("status") != "Unresolved":
            continue
        detail = proof.get("detail") if isinstance(proof.get("detail"), dict) else {}
        step_id = detail.get("step_id")
        step = steps.get(step_id)
        if not isinstance(step, dict):
            scope[proof["proof_id"]] = ()
            continue
        spec = catalog.capabilities.get(step.get("operation"), {})
        properties = spec.get("parameters_schema", {}).get("properties", {})
        paths = []
        for name, value in step.get("arguments", {}).items():
            kind = properties.get(name, {}).get("x-geopilot-kind")
            if name.startswith("output_") or kind == "layer" or isinstance(value, (dict, list, tuple, set)):
                continue
            paths.append("arguments." + name)
        scope[proof["proof_id"]] = tuple(sorted(paths))
    return scope


class MonotonicPlanValidator:
    @staticmethod
    def apply(workflow: Dict[str, Any], revision: PlanRevision,
              allowed_scope: Dict[str, Tuple[str, ...]]) -> Dict[str, Any]:
        allowed = allowed_scope.get(revision.proof_id, ())
        if revision.path not in allowed:
            raise PlanRevisionError("revision path is outside its unresolved proof scope")
        result = deepcopy(workflow)
        step = next((item for item in result.get("steps", [])
                     if item.get("id") == revision.step_id), None)
        if step is None:
            raise PlanRevisionError("revision step is absent")
        name = revision.path.split(".", 1)[1]
        if name not in step.get("arguments", {}):
            raise PlanRevisionError("revision argument is absent")
        step["arguments"][name] = deepcopy(revision.value)
        return result

    @staticmethod
    def validate(baseline: Dict[str, Any], candidate: Dict[str, Any],
                 baseline_report: Dict[str, Any], candidate_report: Dict[str, Any],
                 proof_id: str, revision: PlanRevision) -> None:
        if baseline.get("action") != candidate.get("action") or baseline.get("summary") != candidate.get("summary"):
            raise PlanRevisionError("revision changed workflow identity")
        if [step.get("id") for step in baseline.get("steps", [])] != [step.get("id") for step in candidate.get("steps", [])]:
            raise PlanRevisionError("revision changed workflow steps")
        if [step.get("operation") for step in baseline.get("steps", [])] != [step.get("operation") for step in candidate.get("steps", [])]:
            raise PlanRevisionError("revision changed workflow operations")
        for old_step, new_step in zip(baseline.get("steps", []), candidate.get("steps", [])):
            for name in set(old_step.get("arguments", {})) | set(new_step.get("arguments", {})):
                old_value = old_step.get("arguments", {}).get(name)
                new_value = new_step.get("arguments", {}).get(name)
                allowed_change = (old_step.get("id") == revision.step_id and
                                  "arguments." + name == revision.path)
                if old_value != new_value and not allowed_change:
                    raise PlanRevisionError("revision changed immutable input, output, or argument")
        if set(candidate_report.get("side_effects", ())) - set(baseline_report.get("side_effects", ())):
            raise PlanRevisionError("revision increased side effects")
        old = {item["proof_id"] for item in baseline_report.get("proof_graph", []) if item.get("status") == "Proven"}
        new = {item["proof_id"] for item in candidate_report.get("proof_graph", []) if item.get("status") == "Proven"}
        if not old.issubset(new):
            raise PlanRevisionError("revision lost a proven fact")
        candidate_status = {item.get("proof_id"): item.get("status") for item in candidate_report.get("proof_graph", [])}
        if candidate_status.get(proof_id) in {"Unresolved", "Violated"}:
            raise PlanRevisionError("revision did not resolve its target proof")
        old_bad = sum(item.get("status") != "Proven" for item in baseline_report.get("proof_graph", []))
        new_bad = sum(item.get("status") != "Proven" for item in candidate_report.get("proof_graph", []))
        if new_bad >= old_bad:
            raise PlanRevisionError("revision did not strictly improve proof coverage")
        if any(item.get("status") in {"Unresolved", "Violated"}
               for item in candidate_report.get("proof_graph", [])):
            raise PlanRevisionError("revision left unresolved or violated proof obligations")
