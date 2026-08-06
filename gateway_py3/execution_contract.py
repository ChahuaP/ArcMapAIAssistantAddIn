"""Minimal, hash-bound facts that the ArcMap runtime must verify during execution."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping
from arcmap_runtime_py2.capability_contract_protocol import resolve_output_cardinality

from .plan_artifact import canonical_hash


SCHEMA = "geopilot-execution-contract/v1"
_ROOT_KEYS = {
    "schema", "workflow_hash", "context_hash", "capability_hash",
    "cardinality_proofs", "contract_hash",
}
_PROOF_KEYS = {
    "proof_id", "proof_kind", "step_id", "capability_id", "contract_path", "expected",
}


class ExecutionContractError(ValueError):
    pass


def build_execution_contract(
    workflow: Dict[str, Any],
    context_hash: str,
    capability_hash: str,
    capabilities: Mapping[str, Dict[str, Any]],
) -> Dict[str, Any]:
    proofs = []
    for step in workflow.get("steps", []):
        step_id = step.get("id")
        capability_id = step.get("operation")
        capability = capabilities.get(capability_id)
        if not isinstance(step_id, str) or not step_id or not isinstance(capability, dict):
            raise ExecutionContractError("workflow cannot be bound to the capability catalog.")
        outputs = capability.get("outputs") or {}
        try:
            expected = resolve_output_cardinality(
                outputs.get("cardinality"), step.get("arguments") or {},
                capability.get("parameters_schema") or {}, ExecutionContractError,
                "%s.outputs.cardinality" % capability_id,
            )
        except KeyError:
            raise ExecutionContractError("capability output cardinality is missing.")
        proofs.append({
            "proof_id": "execution:%s:%s:outputs.cardinality" % (step_id, capability_id),
            "proof_kind": "validated_capability_output",
            "step_id": step_id,
            "capability_id": capability_id,
            "contract_path": "outputs.cardinality",
            "expected": expected,
        })
    document = {
        "schema": SCHEMA,
        "workflow_hash": canonical_hash(workflow),
        "context_hash": context_hash,
        "capability_hash": capability_hash,
        "cardinality_proofs": proofs,
    }
    document["contract_hash"] = canonical_hash(document)
    return validate_execution_contract(document, workflow)


def validate_execution_contract(document: Dict[str, Any], workflow: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not isinstance(document, dict) or set(document) != _ROOT_KEYS:
        raise ExecutionContractError("execution contract root is invalid.")
    if document.get("schema") != SCHEMA:
        raise ExecutionContractError("execution contract schema is invalid.")
    for key in ("workflow_hash", "context_hash", "capability_hash", "contract_hash"):
        if not isinstance(document.get(key), str) or not document[key]:
            raise ExecutionContractError("execution contract %s is invalid." % key)
    unsigned = {key: document[key] for key in _ROOT_KEYS if key != "contract_hash"}
    if document["contract_hash"] != canonical_hash(unsigned):
        raise ExecutionContractError("execution contract hash is invalid.")
    if workflow is not None and document["workflow_hash"] != canonical_hash(workflow):
        raise ExecutionContractError("execution contract does not match the workflow.")
    proofs = document.get("cardinality_proofs")
    if not isinstance(proofs, list):
        raise ExecutionContractError("execution cardinality proofs are invalid.")
    identities = set()
    workflow_steps = {
        step.get("id"): step.get("operation")
        for step in (workflow or {}).get("steps", [])
    }
    for proof in proofs:
        if not isinstance(proof, dict) or set(proof) != _PROOF_KEYS:
            raise ExecutionContractError("execution cardinality proof is invalid.")
        if proof.get("proof_kind") != "validated_capability_output" or proof.get("contract_path") != "outputs.cardinality":
            raise ExecutionContractError("execution cardinality proof type is invalid.")
        if not all(isinstance(proof.get(key), str) and proof[key] for key in ("proof_id", "step_id", "capability_id", "expected")):
            raise ExecutionContractError("execution cardinality proof fields are invalid.")
        identity = (proof["step_id"], proof["capability_id"])
        if identity in identities:
            raise ExecutionContractError("execution cardinality proof is duplicated.")
        identities.add(identity)
        if workflow is not None and workflow_steps.get(proof["step_id"]) != proof["capability_id"]:
            raise ExecutionContractError("execution cardinality proof does not match the workflow step.")
    if workflow is not None and len(proofs) != len(workflow_steps):
        raise ExecutionContractError("execution contract does not cover every workflow step.")
    return deepcopy(document)
