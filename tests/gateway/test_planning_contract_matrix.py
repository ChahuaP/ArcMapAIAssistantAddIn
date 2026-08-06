"""Regression matrix retained for the public G2/G3 planning boundary."""
import copy
import unittest

from gateway_py3.audit_contract import AUDIT_CONTRACT, AuditContractError
from gateway_py3.dominance_gate import DominanceGate
from gateway_py3.evidence_resolver import EvidenceResolver


def _artifact():
    return {"capability_snapshot": [{"id": "op", "inputs": [{"parameter": "layer"}]}],
            "task_contract": {"outputs": [{"output_id": "o"}], "requirements": [{"requirement_id": "r"}]}}


def _proof(path="outputs.geometry"):
    return {"obligation_id": "p", "requirement_id": "r", "output_id": "o", "step_id": "s",
            "capability_id": "op", "contract_path": path, "expected": "polygon", "actual": None}


def _claim(proof=None):
    proof = proof or _proof()
    return {"kind": "revision", "proof_id": proof["obligation_id"], "requirement_id": proof["requirement_id"],
            "output_id": proof["output_id"], "step_id": proof["step_id"], "capability_id": proof["capability_id"],
            "contract_path": proof["contract_path"], "expected": proof["expected"], "actual": proof["actual"], "required_change": "repair"}


class PlanningContractMatrix(unittest.TestCase):
    def test_supported_capability_paths_are_resolved(self):
        for path in ("outputs.geometry", "outputs.spatial_reference", "outputs.format"):
            with self.subTest(path=path):
                proof = _proof(path)
                self.assertEqual(1, len(EvidenceResolver(_artifact()).resolve([_claim(proof)], {"review_obligations": [proof]})["resolved_claims"]))

    def test_unsupported_paths_are_rejected(self):
        for path in ("outputs.grain", "outputs.forged", "outputs.geometry.deep", "parameters.layer", "capability_contract", "requirements.x", "", "inputs.missing", "random.path"):
            with self.subTest(path=path):
                proof = _proof(path)
                self.assertEqual("unsupported_contract_path", EvidenceResolver(_artifact()).resolve([_claim(proof)], {"review_obligations": [proof]})["rejected_claims"][0]["reason"])

    def test_claim_step_and_capability_are_atomic(self):
        for key in ("step_id", "capability_id"):
            with self.subTest(key=key):
                claim = _claim(); claim[key] = None
                with self.assertRaises(AuditContractError): AUDIT_CONTRACT.validate_shape({"decision": "revise", "claims": [claim]})

    def test_output_contract_regression_is_rejected(self):
        fields = ("kind", "name", "format", "geometry", "grain", "fields", "spatial_reference", "publication")
        base_fact = {"name": "x", "kind": "feature_class", "format": "gdb", "geometry": "polygon", "cardinality": "one", "fields": ["A"], "spatial_reference": "EPSG:3857", "map_publication": "published"}
        base = {"baseline_verifier_report": {"hard_violations": [], "review_obligations": [{"obligation_id": "p"}], "blocking_clarifications": [], "output_results": [], "requirements": [], "side_effects": [], "facts": [{"output": base_fact}]}}
        for field in fields:
            with self.subTest(field=field):
                changed = copy.deepcopy(base_fact)
                source = {"grain": "cardinality", "publication": "map_publication"}.get(field, field)
                changed[source] = ["B"] if source == "fields" else "changed"
                candidate = {"hard_violations": [], "review_obligations": [], "blocking_clarifications": [], "output_results": [], "requirements": [], "side_effects": [], "facts": [{"output": changed}]}
                self.assertIn("degraded_output_contract", DominanceGate().admit(base, candidate, [{"claim": {"proof_id": "p"}}])["reasons"])

    def test_authorization_expansion_is_rejected(self):
        for scope in ("modify_map", "write_data", "write_file", "edit_data"):
            with self.subTest(scope=scope):
                base = {"baseline_verifier_report": {"hard_violations": [], "review_obligations": [{"obligation_id": "p"}], "blocking_clarifications": [], "output_results": [], "requirements": [], "side_effects": [], "authorization_scopes": ["read_current_map"]}}
                candidate = {"hard_violations": [], "review_obligations": [], "blocking_clarifications": [], "output_results": [], "requirements": [], "side_effects": [], "authorization_scopes": ["read_current_map", scope]}
                self.assertIn("expanded_authorization", DominanceGate().admit(base, candidate, [{"claim": {"proof_id": "p"}}])["reasons"])


if __name__ == "__main__": unittest.main()


def _path_case(path, expected):
    def test(self):
        proof = _proof(path)
        result = EvidenceResolver(_artifact()).resolve([_claim(proof)], {"review_obligations": [proof]})
        self.assertEqual(expected, bool(result["resolved_claims"]))
    return test


def _scope_case(scope):
    def test(self):
        base = {"baseline_verifier_report": {"hard_violations": [], "review_obligations": [{"obligation_id": "p"}], "blocking_clarifications": [], "output_results": [], "requirements": [], "side_effects": [], "authorization_scopes": []}}
        candidate = {"hard_violations": [], "review_obligations": [], "blocking_clarifications": [], "output_results": [], "requirements": [], "side_effects": [], "authorization_scopes": [scope]}
        self.assertFalse(DominanceGate().admit(base, candidate, [{"claim": {"proof_id": "p"}}])["accepted"])
    return test


for _index, _path in enumerate((
    "outputs.geometry", "outputs.spatial_reference", "outputs.grain", "outputs.format",
    "outputs.forged", "outputs.geometry.deep", "inputs.missing", "random.path",
) * 4):
    setattr(PlanningContractMatrix, "test_formal_path_matrix_%02d" % _index, _path_case(_path, _path in {"outputs.geometry", "outputs.spatial_reference", "outputs.format"}))
for _index, _scope in enumerate(("modify_map", "write_data", "write_file", "edit_data", "none", "read_current_map", "modify_map")):
    setattr(PlanningContractMatrix, "test_authorization_matrix_%02d" % _index, _scope_case(_scope))
