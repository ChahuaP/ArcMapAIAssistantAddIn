import unittest

from gateway_py3.evidence_resolver import EvidenceResolver
from gateway_py3.workflow_verifier import WorkflowVerifier
from gateway_py3.catalog_loader import OperationCatalog
from tests.gateway.planner_test_utils import task_contract


class G3ValidationFirstTests(unittest.TestCase):
    def test_task_clarification_blocks_before_audit(self):
        command = "显示图层"
        task = task_contract(command)
        task["clarifications"] = [{"clarification_id": "c1", "question": "选择哪个图层", "evidence": command}]
        report = WorkflowVerifier(OperationCatalog()).verify(
            {"action": "execute", "summary": command, "steps": [{"id": "s1", "operation": "context.list_layers", "arguments": {}, "reason": command}]},
            {"layers": []}, task,
        )
        self.assertFalse(report["ok"])
        self.assertEqual("task.clarification", report["blocking_clarifications"][0]["code"])

    def test_resolver_only_accepts_exact_proof_facts(self):
        proof = {"obligation_id": "p", "requirement_id": "r", "output_id": "o", "step_id": "s", "capability_id": "x", "contract_path": "outputs.geometry", "expected": "a", "actual": None}
        claim = {"proof_id": "p", "requirement_id": "r", "output_id": "o", "step_id": "s", "capability_id": "x", "contract_path": "outputs.geometry", "expected": "a", "actual": None}
        artifact = {"capability_snapshot": [{"id": "x", "inputs": []}], "task_contract": {"outputs": [{"output_id": "o"}], "requirements": [{"requirement_id": "r"}]}}
        self.assertEqual(1, len(EvidenceResolver(artifact).resolve([claim], {"review_obligations": [proof]})["resolved_claims"]))


if __name__ == "__main__": unittest.main()
