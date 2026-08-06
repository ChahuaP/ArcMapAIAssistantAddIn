import unittest

from gateway_py3.audit_contract import AUDIT_CONTRACT, AuditContractError, audit_contract_for_report
from gateway_py3.evidence_resolver import EvidenceResolver


PROOF = {"obligation_id": "o1", "requirement_id": "r1", "output_id": "out1",
         "step_id": "s1", "capability_id": "analysis.dissolve", "contract_path": "outputs.spatial_reference",
         "expected": "EPSG:3857", "actual": None}


def claim(**changes):
    result = {"kind": "revision", "proof_id": "o1", "change_target": "workflow",
              "required_change": "supply the required spatial reference"}
    result.update(changes)
    return result


ARTIFACT = {"capability_snapshot": [{"id": "analysis.dissolve", "inputs": []}],
            "task_contract": {"outputs": [{"output_id": "out1"}], "requirements": [{"requirement_id": "r1"}]}}


class AuditContractTests(unittest.TestCase):
    def test_pass_has_no_claims_and_revision_claim_is_strict(self):
        self.assertEqual("pass", AUDIT_CONTRACT.validate_shape({"decision": "pass", "claims": []})["decision"])
        self.assertEqual("revise", AUDIT_CONTRACT.validate_shape({"decision": "revise", "claims": [claim()]})["decision"])
        with self.assertRaises(AuditContractError):
            AUDIT_CONTRACT.validate_shape({"decision": "pass", "claims": [claim()]})
        with self.assertRaises(AuditContractError):
            AUDIT_CONTRACT.validate_shape({"decision": "revise", "claims": [claim(kind="clarification")]})
        with self.assertRaises(AuditContractError):
            AUDIT_CONTRACT.validate_shape({"decision": "revise", "claims": [claim(change_target="none")]})

    def test_unreproducible_claim_is_rejected_without_a_repair_instruction(self):
        materialized = EvidenceResolver(ARTIFACT).materialize([claim()], {"review_obligations": [PROOF]})
        self.assertEqual(PROOF["capability_id"], materialized[0]["capability_id"])
        self.assertEqual(PROOF["contract_path"], materialized[0]["contract_path"])
        materialized[0]["actual"] = "invented"
        result = EvidenceResolver(ARTIFACT).resolve(materialized, {"review_obligations": [PROOF]})
        self.assertEqual([], result["resolved_claims"])
        self.assertEqual(1, len(result["rejected_claims"]))

    def test_dynamic_audit_schema_closes_proof_selection_to_baseline(self):
        schema = audit_contract_for_report({"review_obligations": [PROOF]}).schema
        proof = schema["properties"]["audit_result"]["properties"]["claims"]["items"]["properties"]["proof_id"]
        self.assertEqual(["o1"], proof["enum"])

    def test_auditor_receives_the_typed_selector_selection_semantics(self):
        self.assertIn("selector_selection", AUDIT_CONTRACT.prompt)
        self.assertIn("current selection", AUDIT_CONTRACT.prompt)


if __name__ == "__main__":
    unittest.main()
