"""The sole strict, proof-bound G3 audit interface."""
from __future__ import annotations

from copy import deepcopy

from .llm_providers import StructuredOutputContract


class AuditContractError(ValueError):
    pass


class AuditContractExhausted(AuditContractError):
    pass


_DECISIONS = {"pass", "revise", "clarify", "reject"}
_KINDS = {"revision": "revise", "clarification": "clarify", "rejection": "reject"}
_CHANGE_TARGETS = {"workflow", "task_contract", "none"}
_CLAIM_FIELDS = {"kind", "proof_id", "change_target", "required_change"}


AUDIT_PROMPT = (
    "Return exactly {audit_result}. audit_result has decision and claims. A pass has no claims. "
    "Every claim must select one supplied baseline verifier proof_id. Do not reproduce or invent "
    "proof metadata: GeoPilot resolves the selected proof deterministically. "
    "kind must exactly match decision (revision/revise, clarification/clarify, rejection/reject). "
    "Every claim needs one concrete required_change and one change_target. For a revision, use "
    "change_target=task_contract only when the bound TaskContract itself conflicts with the immutable "
    "request; use change_target=workflow when the TaskContract is correct but the workflow fails to "
    "implement it. Clarification and rejection claims must use change_target=none. "
    "Audit only the supplied PlanArtifact baseline. "
    "For every request_alignment.unresolved proof, compare the complete immutable request with the "
    "bound predicate, including pronouns, derived-output lineage, operation direction, filters and "
    "success conditions. Select that proof for revision when the predicate changes the user's meaning; "
    "do not pass merely because the workflow matches a mistaken TaskContract. Requirements are ordered. "
    "When a satisfied spatial_filter proof contains selector_selection, its selector denotes the exact "
    "current selection established by that cited earlier filter requirement, not every feature in the "
    "underlying input entity. Do not demand an invented intermediate entity or a changed selector for "
    "semantics already proven by selector_selection."
)


class AuditContract:
    tool_contract = StructuredOutputContract(
        name="submit_audit_result", description="Submit proof-bound GeoPilot G3 audit claims.",
        schema={"type": "object", "properties": {"audit_result": {
            "type": "object", "properties": {
                "decision": {"type": "string", "enum": sorted(_DECISIONS)},
                "claims": {"type": "array", "items": {"type": "object", "properties": {
                    "kind": {"type": "string", "enum": sorted(_KINDS)},
                    "proof_id": {"type": "string", "minLength": 1},
                    "change_target": {"type": "string", "enum": sorted(_CHANGE_TARGETS)},
                    "required_change": {"type": "string", "minLength": 1},
                }, "required": sorted(_CLAIM_FIELDS), "additionalProperties": False}},
            }, "required": ["decision", "claims"], "additionalProperties": False}},
            "required": ["audit_result"], "additionalProperties": False},
    )
    prompt = AUDIT_PROMPT

    def validate_shape(self, value):
        if not isinstance(value, dict) or set(value) != {"decision", "claims"}:
            raise AuditContractError("audit_result must contain exactly decision and claims.")
        decision, claims = value["decision"], value["claims"]
        if decision not in _DECISIONS or not isinstance(claims, list):
            raise AuditContractError("audit_result has invalid decision or claims.")
        if decision == "pass":
            if claims:
                raise AuditContractError("pass cannot contain claims.")
            return value
        if not claims:
            raise AuditContractError("non-pass decision requires claims.")
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or set(claim) != _CLAIM_FIELDS:
                raise AuditContractError("audit claim %d has invalid fields." % index)
            if claim["kind"] not in _KINDS or _KINDS[claim["kind"]] != decision:
                raise AuditContractError("claim kind conflicts with decision.")
            if not isinstance(claim["proof_id"], str) or not claim["proof_id"]:
                raise AuditContractError("claim proof_id is invalid.")
            target = claim["change_target"]
            if target not in _CHANGE_TARGETS:
                raise AuditContractError("claim change_target is invalid.")
            if (claim["kind"] == "revision") != (target != "none"):
                raise AuditContractError("claim change_target conflicts with kind.")
            if not isinstance(claim["required_change"], str) or not claim["required_change"].strip():
                raise AuditContractError("claim required_change is invalid.")
        return value


AUDIT_CONTRACT = AuditContract()


def audit_contract_for_report(report):
    """Close the auditor's selectable evidence to the immutable baseline report."""
    proof_ids = sorted({
        item[id_name]
        for section, id_name in (("hard_violations", "violation_id"), ("review_obligations", "obligation_id"),
                                 ("blocking_clarifications", "obligation_id"))
        for item in report.get(section, [])
        if isinstance(item.get(id_name), str) and item[id_name]
    })
    contract = deepcopy(AUDIT_CONTRACT.tool_contract)
    claim = contract.schema["properties"]["audit_result"]["properties"]["claims"]["items"]
    claim["properties"]["proof_id"] = {"type": "string", "enum": proof_ids}
    return contract
