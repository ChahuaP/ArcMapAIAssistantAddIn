"""Single admission rule for replacing a G3 baseline artifact."""
from __future__ import annotations


def _keys(report, section, identity):
    return {item[identity] for item in report.get(section, [])}


def _artifact_outputs(report):
    output_status = {
        item["output_id"]: bool(item.get("satisfied"))
        for item in report.get("output_results", [])
    }
    expected_names = {
        item.get("name"): output_status.get(item.get("output_id"), False)
        for item in report.get("task_contract", {}).get("outputs", [])
    }
    result = {}
    for fact in report.get("facts", []):
        output = fact.get("output")
        if output and output.get("name") and (not output_status or expected_names.get(output.get("name"), False)):
            result[output["name"]] = {
                "kind": output.get("kind"), "name": output.get("name"),
                "format": output.get("format"), "geometry": output.get("geometry"),
                "grain": output.get("cardinality"), "fields": tuple(output.get("fields") or []),
                "spatial_reference": output.get("spatial_reference"),
                "publication": output.get("map_publication"),
            }
    return result


class DominanceGate:
    def admit(self, baseline, candidate, resolved_claims, confirmed_alignment=False):
        b = baseline["baseline_verifier_report"] if "baseline_verifier_report" in baseline else baseline
        c = candidate
        hard_b, hard_c = _keys(b, "hard_violations", "violation_id"), _keys(c, "hard_violations", "violation_id")
        review_b, review_c = _keys(b, "review_obligations", "obligation_id"), _keys(c, "review_obligations", "obligation_id")
        block_b, block_c = _keys(b, "blocking_clarifications", "obligation_id"), _keys(c, "blocking_clarifications", "obligation_id")
        claimed = {item["claim"]["proof_id"] for item in resolved_claims}
        fixed = (hard_b | review_b | block_b) - (hard_c | review_c | block_c)
        introduced = (hard_c | review_c | block_c) - (hard_b | review_b | block_b)
        if confirmed_alignment:
            confirmed_baseline_ids = {
                item["claim"]["proof_id"] for item in resolved_claims
                if item.get("proof", {}).get("code") == "request_alignment.unresolved"
            }
            fixed |= confirmed_baseline_ids
            alignment_requirements = {
                item["proof"].get("requirement_id") for item in resolved_claims
                if item.get("proof", {}).get("code") == "request_alignment.unresolved"
            }
            confirmed_ids = {
                item["obligation_id"] for item in c.get("review_obligations", [])
                if item.get("code") == "request_alignment.unresolved"
                and item.get("requirement_id") in alignment_requirements
            }
            introduced -= confirmed_ids
        b_outputs = {x["output_id"] for x in b.get("output_results", []) if x.get("satisfied")}
        c_outputs = {x["output_id"] for x in c.get("output_results", []) if x.get("satisfied")}
        b_requirements = {x["requirement_id"] for x in b.get("requirements", []) if x.get("satisfied")}
        c_requirements = {x["requirement_id"] for x in c.get("requirements", []) if x.get("satisfied")}
        expanded = sorted(set(c.get("side_effects", [])) - set(b.get("side_effects", [])))
        expanded_scopes = sorted(set(c.get("authorization_scopes", [])) - set(b.get("authorization_scopes", [])))
        baseline_outputs, candidate_outputs = _artifact_outputs(b), _artifact_outputs(c)
        degraded_contracts = sorted(
            name for name, contract in baseline_outputs.items()
            if name not in candidate_outputs or candidate_outputs[name] != contract
        )
        reasons = []
        if not claimed: reasons.append("no_proven_baseline_problem")
        if not claimed <= fixed: reasons.append("claimed_problem_not_eliminated")
        if introduced: reasons.append("introduced_problem")
        if b_outputs - c_outputs: reasons.append("lost_output")
        if b_requirements - c_requirements: reasons.append("lost_requirement")
        if expanded: reasons.append("expanded_side_effects")
        if expanded_scopes: reasons.append("expanded_authorization")
        if degraded_contracts: reasons.append("degraded_output_contract")
        return {"accepted": not reasons, "reasons": reasons, "fixed": sorted(fixed),
                "introduced": sorted(introduced), "lost": {"outputs": sorted(b_outputs-c_outputs), "requirements": sorted(b_requirements-c_requirements)},
                "expanded": {"side_effects": expanded, "authorization_scopes": expanded_scopes},
                "degraded_output_contracts": degraded_contracts}
