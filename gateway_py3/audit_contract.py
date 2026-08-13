"""The sole strict interface for proof-bound G3 audit decisions."""
from __future__ import annotations

from copy import deepcopy

from .model_runtime.contracts import StructuredOutputContract


class AuditContractError(ValueError):
    pass


_DECISIONS = {"pass", "revise", "clarify", "reject"}

AUDIT_PROMPT = (
    "Audit only the supplied sealed baseline and its unresolved ProofGraph items. "
    "Return pass, clarify, reject, or one proof-bound scalar argument revision. "
    "A revision cannot change task intent, inputs, outputs, operations, side effects, or risk. "
    "If evidence does not identify one allowed scalar argument path, request clarification."
)


class AuditContract:
    tool_contract = StructuredOutputContract(
        name="submit_audit_result", description="Submit a closed proof-bound GeoPilot G3 decision.",
        schema={"type": "object", "properties": {"audit_result": {
            "type": "object", "properties": {
                "decision": {"type": "string", "enum": sorted(_DECISIONS)},
                "revision": {"type": ["object", "null"], "properties": {
                    "proof_id": {"type": "string", "minLength": 1},
                    "step_id": {"type": "string", "minLength": 1},
                    "path": {"type": "string", "pattern": "^arguments\\.[A-Za-z_][A-Za-z0-9_]*$"},
                    "value": {},
                }, "required": ["proof_id", "step_id", "path", "value"], "additionalProperties": False},
                "clarification": {"type": ["object", "null"], "properties": {
                    "option_id": {"type": "string", "minLength": 1},
                    "question": {"type": "string", "minLength": 1},
                }, "required": ["option_id", "question"], "additionalProperties": False},
            }, "required": ["decision", "revision", "clarification"], "additionalProperties": False}},
            "required": ["audit_result"], "additionalProperties": False},
    )
    prompt = AUDIT_PROMPT

    def validate_shape(self, value):
        if not isinstance(value, dict) or set(value) != {"decision", "revision", "clarification"}:
            raise AuditContractError("audit_result has an invalid closed shape")
        decision = value.get("decision")
        if decision not in _DECISIONS:
            raise AuditContractError("audit decision is invalid")
        revision, clarification = value.get("revision"), value.get("clarification")
        if decision == "pass" and revision is None and clarification is None:
            return value
        if decision == "revise" and isinstance(revision, dict) and clarification is None:
            return value
        if decision == "clarify" and revision is None and isinstance(clarification, dict):
            return value
        if decision == "reject" and revision is None and clarification is None:
            return value
        raise AuditContractError("audit decision payload conflicts with decision")


AUDIT_CONTRACT = AuditContract()


def audit_contract_for_scope(scope, option_ids=()):
    """Specialize the tool schema to verifier-derived unresolved proof scopes."""
    contract = deepcopy(AUDIT_CONTRACT.tool_contract)
    result = contract.schema["properties"]["audit_result"]
    revision = result["properties"]["revision"]
    clarification = result["properties"]["clarification"]
    proof_ids = sorted(scope)
    revision["properties"]["proof_id"] = {"type": "string", "enum": proof_ids}
    clarification["properties"]["option_id"] = {"type": "string", "enum": sorted(option_ids)}
    return contract
