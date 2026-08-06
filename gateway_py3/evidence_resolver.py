"""Resolve model claims only against immutable, formally addressed artifact proofs."""
from __future__ import annotations


_FACT_FIELDS = ("requirement_id", "output_id", "step_id", "capability_id", "contract_path", "expected", "actual")
_OUTPUT_PATHS = {"geometry", "spatial_reference", "format", "required_fields", "fields", "kind", "name", "map_publication"}


class EvidenceResolver:
    def __init__(self, artifact):
        self.artifact = artifact
        self.capabilities = {item["id"]: item for item in artifact["capability_snapshot"]}
        self.output_ids = {item["output_id"] for item in artifact["task_contract"]["outputs"]}
        self.requirement_ids = {item["requirement_id"] for item in artifact["task_contract"]["requirements"]}

    def resolve(self, claims, baseline_verifier_report=None):
        report = baseline_verifier_report or self.artifact["baseline_verifier_report"]
        proofs = {}
        for section, id_name in (("hard_violations", "violation_id"), ("review_obligations", "obligation_id"), ("blocking_clarifications", "obligation_id")):
            for proof in report.get(section, []):
                if proof.get(id_name):
                    proofs[proof[id_name]] = (section, proof)
        resolved, rejected = [], []
        for claim in claims:
            source = proofs.get(claim["proof_id"])
            if source is None:
                rejected.append({"claim": claim, "reason": "unknown_proof_id"}); continue
            section, proof = source
            if any(claim.get(field) != proof.get(field) for field in _FACT_FIELDS):
                rejected.append({"claim": claim, "reason": "baseline_proof_mismatch"}); continue
            if not self._official_path(proof):
                rejected.append({"claim": claim, "reason": "unsupported_contract_path"}); continue
            if section == "blocking_clarifications" and claim["kind"] == "revision":
                rejected.append({"claim": claim, "reason": "blocking_proof_is_not_revisable"}); continue
            resolved.append({"claim": claim, "proof": proof, "proof_section": section})
        return {"resolved_claims": resolved, "rejected_claims": rejected}

    def materialize(self, selections, baseline_verifier_report=None):
        """Compile proof selections into canonical claims from immutable evidence only."""
        report = baseline_verifier_report or self.artifact["baseline_verifier_report"]
        proofs = {}
        for section, id_name in (("hard_violations", "violation_id"), ("review_obligations", "obligation_id"),
                                 ("blocking_clarifications", "obligation_id")):
            for proof in report.get(section, []):
                proof_id = proof.get(id_name)
                if isinstance(proof_id, str) and proof_id:
                    proofs[proof_id] = proof
        claims = []
        for selection in selections:
            proof = proofs.get(selection["proof_id"])
            if proof is None:
                raise ValueError("audit selection references an unknown baseline proof.")
            claim = {"kind": selection["kind"], "proof_id": selection["proof_id"],
                     "change_target": selection["change_target"],
                     "required_change": selection["required_change"]}
            for field in _FACT_FIELDS:
                claim[field] = proof.get(field)
            claims.append(claim)
        return claims

    def _official_path(self, proof):
        path, capability = proof.get("contract_path"), proof.get("capability_id")
        if not isinstance(path, str) or not path:
            return False
        if proof.get("output_id") is not None and proof["output_id"] not in self.output_ids:
            return False
        if proof.get("requirement_id") is not None and proof["requirement_id"] not in self.requirement_ids:
            return False
        if capability is None:
            return (len(path.split(".")) == 2 and path.split(".")[0] in {"outputs", "requirements"}) or path == "clarifications"
        card = self.capabilities.get(capability)
        if card is None:
            return False
        if path.startswith("inputs."):
            parts = path.split(".")
            return len(parts) in (2, 3) and parts[1] in {item["parameter"] for item in card["inputs"]}
        if path.startswith("outputs."):
            parts = path.split(".")
            return len(parts) == 2 and parts[1] in _OUTPUT_PATHS
        if path == "authorization.side_effect":
            return True
        return False
