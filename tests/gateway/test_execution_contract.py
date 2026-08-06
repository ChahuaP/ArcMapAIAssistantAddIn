import copy
import tempfile
import unittest
from pathlib import Path

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.execution_contract import (
    ExecutionContractError,
    build_execution_contract,
    validate_execution_contract,
)
from gateway_py3.run_store import RunStore


class ExecutionContractTests(unittest.TestCase):
    def test_planned_run_exposes_a_hash_bound_cardinality_proof_to_arcmap(self):
        catalog = OperationCatalog()
        workflow = {
            "action": "execute",
            "summary": "intersect",
            "steps": [{
                "id": "intersect",
                "operation": "analysis.intersect",
                "arguments": {
                    "input_layers": ["layer:0", "layer:1"],
                    "output_name": "conflicts",
                },
                "reason": "intersect",
            }],
        }
        contract = build_execution_contract(workflow, "context-hash", "capability-hash", catalog.capabilities)

        with tempfile.TemporaryDirectory() as temp:
            store = RunStore(Path(temp) / "runs.sqlite")
            run = store.create_run("intersect", "g2_constrained")
            trace = store.run_trace(run["id"])
            trace["execution_contract"] = contract
            store.update_run(run["id"], "planned", workflow=workflow, trace=trace)

            row = store.get(run["id"])

        self.assertEqual(contract, row["execution_contract"])
        proof = row["execution_contract"]["cardinality_proofs"][0]
        self.assertEqual("intersect", proof["step_id"])
        self.assertEqual("analysis.intersect", proof["capability_id"])
        self.assertEqual("one_or_more_per_input_feature", proof["expected"])
        self.assertEqual(contract, validate_execution_contract(contract, workflow))

        tampered = copy.deepcopy(contract)
        tampered["cardinality_proofs"][0]["expected"] = "one"
        with self.assertRaises(ExecutionContractError):
            validate_execution_contract(tampered, workflow)

    def test_dissolve_cardinality_proof_is_resolved_from_group_fields(self):
        catalog = OperationCatalog()
        workflow = {
            "action": "execute", "summary": "dissolve", "steps": [
                {
                    "id": "all", "operation": "analysis.dissolve",
                    "arguments": {"input_layer": "layer:0", "output_name": "all"},
                    "reason": "all",
                },
                {
                    "id": "groups", "operation": "analysis.dissolve",
                    "arguments": {
                        "input_layer": "layer:0", "output_name": "groups",
                        "dissolve_fields": ["CLASS"],
                    },
                    "reason": "groups",
                },
            ],
        }

        contract = build_execution_contract(
            workflow, "context-hash", "capability-hash", catalog.capabilities,
        )

        self.assertEqual(
            ["reduced", "one_per_aggregate_group"],
            [proof["expected"] for proof in contract["cardinality_proofs"]],
        )


if __name__ == "__main__":
    unittest.main()
