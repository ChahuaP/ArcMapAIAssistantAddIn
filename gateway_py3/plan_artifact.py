"""Frozen, self-verifying planning baselines shared by G2 and G3."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Dict


class PlanArtifactError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class PlanArtifact:
    """Canonical production replay input; its document has no extension fields."""
    schema = "geopilot-plan-artifact"
    version = 4
    _keys = (
        "schema", "version", "request", "context_snapshot", "context_snapshot_hash",
        "execution_context_hash", "capability_snapshot", "capability_hash", "task_contract",
        "task_contract_hash", "baseline_workflow", "baseline_workflow_hash",
        "baseline_verifier_report", "baseline_verifier_report_hash", "planning_policy",
        "planning_policy_hash", "artifact_hash",
    )

    def __init__(self, request, context_snapshot, execution_context_hash, capability_snapshot,
                 task_contract, baseline_workflow, baseline_verifier_report, planning_policy):
        document = {
            "schema": self.schema, "version": self.version, "request": deepcopy(request),
            "context_snapshot": deepcopy(context_snapshot),
            "context_snapshot_hash": canonical_hash(context_snapshot),
            "execution_context_hash": deepcopy(execution_context_hash),
            "capability_snapshot": deepcopy(capability_snapshot),
            "capability_hash": canonical_hash(capability_snapshot),
            "task_contract": deepcopy(task_contract), "task_contract_hash": canonical_hash(task_contract),
            "baseline_workflow": deepcopy(baseline_workflow),
            "baseline_workflow_hash": canonical_hash(baseline_workflow),
            "baseline_verifier_report": deepcopy(baseline_verifier_report),
            "baseline_verifier_report_hash": canonical_hash(baseline_verifier_report),
            "planning_policy": deepcopy(planning_policy),
            "planning_policy_hash": canonical_hash(planning_policy),
        }
        document["artifact_hash"] = canonical_hash(document)
        self._set(document)

    def _set(self, document):
        self._validate(document)
        self._json = canonical_json(document)
        self._document = json.loads(self._json)
        self._hash = self._document["artifact_hash"]

    @classmethod
    def from_dict(cls, value: Dict[str, Any]):
        if not isinstance(value, dict) or tuple(sorted(value)) != tuple(sorted(cls._keys)):
            raise PlanArtifactError("PlanArtifact root keys are invalid.")
        instance = cls.__new__(cls)
        instance._set(deepcopy(value))
        return instance

    @classmethod
    def _validate(cls, document):
        if document.get("schema") != cls.schema or document.get("version") != cls.version:
            raise PlanArtifactError("PlanArtifact schema or version is invalid.")
        if not isinstance(document.get("request"), str) or not document["request"].strip():
            raise PlanArtifactError("request is invalid.")
        if not isinstance(document.get("execution_context_hash"), str) or not document["execution_context_hash"]:
            raise PlanArtifactError("execution_context_hash is invalid.")
        pairs = (("context_snapshot", "context_snapshot_hash"), ("capability_snapshot", "capability_hash"),
                 ("task_contract", "task_contract_hash"), ("baseline_workflow", "baseline_workflow_hash"),
                 ("baseline_verifier_report", "baseline_verifier_report_hash"),
                 ("planning_policy", "planning_policy_hash"))
        for field, hash_field in pairs:
            if document.get(hash_field) != canonical_hash(document.get(field)):
                raise PlanArtifactError(field + " hash is invalid.")
        snapshot = document["capability_snapshot"]
        if not isinstance(snapshot, list):
            raise PlanArtifactError("capability snapshot is invalid.")
        capability_ids = [item.get("id") for item in snapshot if isinstance(item, dict)]
        if len(capability_ids) != len(snapshot) or capability_ids != sorted(set(capability_ids)):
            raise PlanArtifactError("capability snapshot identities are invalid.")
        workflow = document["baseline_workflow"]
        steps = workflow.get("steps") if isinstance(workflow, dict) else None
        if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
            raise PlanArtifactError("baseline workflow is invalid.")
        missing = sorted({step.get("operation") for step in steps} - set(capability_ids))
        if missing:
            raise PlanArtifactError(
                "capability snapshot does not cover baseline workflow operations: %s."
                % ", ".join(str(value) for value in missing)
            )
        report = document["baseline_verifier_report"]
        if not isinstance(report, dict) or report.get("prepared_workflow") != document["baseline_workflow"]:
            raise PlanArtifactError("baseline verifier report does not prove the baseline workflow.")
        if report.get("normalization_events") != []:
            raise PlanArtifactError("baseline verifier report must describe the canonical workflow without normalization.")
        unsigned = {key: document[key] for key in cls._keys if key != "artifact_hash"}
        if document.get("artifact_hash") != canonical_hash(unsigned):
            raise PlanArtifactError("artifact_hash is invalid.")

    @property
    def hash(self):
        return self._hash

    def as_dict(self) -> Dict[str, Any]:
        return json.loads(self._json)

    def canonical_json(self) -> str:
        return self._json
