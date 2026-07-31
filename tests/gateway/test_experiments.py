import json
import tempfile
import unittest
from pathlib import Path

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.experiments import ContractError, ExperimentRunner
from gateway_py3.run_store import RunStore
from gateway_py3.validators import context_hash


CONTEXT = {"layers": []}
WORKFLOW = {
    "action": "execute",
    "summary": "refresh",
    "steps": [
        {
            "id": "s1",
            "operation": "view.refresh_view",
            "arguments": {},
            "reason": "refresh",
        }
    ],
}
SEMANTICS = {
    "goal": "refresh",
    "inputs": [],
    "constraints": [],
    "success_criteria": [],
}


class FakeModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, provider, model):
        self.provider_id = provider
        self.model_id = model
        return self

    def chat_json(self, messages):
        self.calls.append(messages)
        if hasattr(self, "events"):
            role = messages[0]["content"].split("GeoPilot ")[1].split(" role")[0]
            self.events.append(role)
        return self.replies.pop(0)


class ExperimentTests(unittest.TestCase):
    def runner(self, replies):
        self.temp = tempfile.TemporaryDirectory()
        return ExperimentRunner(
            OperationCatalog(),
            RunStore(Path(self.temp.name) / "runs.sqlite"),
            FakeModel(replies),
        )

    def tearDown(self):
        getattr(self, "temp", None) and self.temp.cleanup()

    def test_g0_hides_context_but_validator_receives_it(self):
        runner = self.runner([{"workflow_draft": WORKFLOW}])
        row = plan_bound(runner, "refresh", CONTEXT, "direct_single")
        request = json.loads(runner.client_factory.calls[0][1]["content"])
        self.assertNotIn("context", request)
        self.assertEqual(row["agent_trace"][0]["run"]["context_hash"], row["context_hash"])

    def test_g1_has_one_context_role(self):
        runner = self.runner([{"workflow_draft": WORKFLOW}])
        plan_bound(runner, "refresh", CONTEXT, "context_single")
        self.assertIn('"context"', runner.client_factory.calls[0][1]["content"])
        self.assertIn("context role", runner.client_factory.calls[0][0]["content"])

    def test_g2_repairs_in_same_role(self):
        invalid = dict(
            WORKFLOW,
            steps=[
                {
                    "id": "s1",
                    "operation": "missing.operation",
                    "arguments": {},
                    "reason": "invalid",
                }
            ],
        )
        runner = self.runner(
            [
                {"task_semantics": SEMANTICS, "workflow_draft": invalid},
                {"workflow_draft": WORKFLOW},
            ]
        )
        row = plan_bound(runner, "refresh", CONTEXT, "constrained_single")
        self.assertEqual(row["agent_trace"][0]["run"]["counts"]["validation_revisions"], 1)
        self.assertTrue(
            all(
                "constrained role" in call[0]["content"]
                for call in runner.client_factory.calls
            )
        )

    def test_g2_repairs_invalid_response_contract_before_workflow_validation(self):
        invalid = {
            "task_semantics": SEMANTICS,
            "workflow_draft": dict(WORKFLOW, unexpected="model noise"),
        }
        runner = self.runner([
            invalid,
            {"task_semantics": SEMANTICS, "workflow_draft": WORKFLOW},
        ])

        row = plan_bound(runner, "refresh", CONTEXT, "constrained_single")

        trace = row["agent_trace"][0]["run"]
        self.assertEqual(row["status"], "planned")
        self.assertEqual(trace["counts"]["contract_revisions"], 1)
        self.assertEqual(trace["counts"]["validation_revisions"], 0)
        repair_request = json.loads(runner.client_factory.calls[1][1]["content"])
        self.assertEqual(
            repair_request["response_contract_repair"]["kind"],
            "response_contract",
        )
        self.assertIn(
            "workflow_draft",
            repair_request["response_contract_repair"]["message"],
        )

    def test_response_contract_stops_after_the_bounded_revision_limit(self):
        invalid = {
            "task_semantics": SEMANTICS,
            "workflow_draft": dict(WORKFLOW, unexpected="model noise"),
        }
        runner = self.runner([invalid, invalid, invalid])

        with self.assertRaisesRegex(ContractError, "failed after 3 attempts"):
            plan_bound(runner, "refresh", CONTEXT, "constrained_single")

        row = runner.store.list_recent(limit=1, include_trace=True)[0]
        trace = row["agent_trace"][0]["run"]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(trace["counts"]["contract_revisions"], 2)
        self.assertEqual(len(trace["contract_diagnostics"]), 3)
        self.assertTrue(
            all(stage["status"] == "contract_rejected" for stage in trace["stages"])
        )

    def test_g3_isolates_roles_and_requires_audit_pass(self):
        passed = {"decision": "pass", "issues": [], "revision_requirements": []}
        self.temp = tempfile.TemporaryDirectory()
        events = []
        primary = FakeModel(
            [
                {"task_semantics": SEMANTICS},
                {"workflow_draft": WORKFLOW},
            ]
        )
        reviewer = FakeModel([{"audit_result": passed}])
        primary.events = reviewer.events = events
        reviewer.provider_id, reviewer.model_id = "minimax", "reviewer"
        def create_client(provider, model):
            if provider == "minimax":
                return reviewer
            return primary

        runner = ExperimentRunner(
            OperationCatalog(),
            RunStore(Path(self.temp.name) / "runs.sqlite"),
            create_client,
        )
        row = plan_bound(runner, "refresh", CONTEXT, "multi_agent")
        trace = row["agent_trace"][0]["run"]
        self.assertEqual(trace["counts"]["audit_revisions"], 0)
        self.assertEqual(trace["audits"][-1]["decision"], "pass")
        self.assertEqual(events, ["semantic", "planner", "auditor"])

    def test_contract_fails_fast_and_store_can_cancel_export(self):
        runner = self.runner([{"workflow_draft": WORKFLOW}])
        with self.assertRaises(ContractError):
            runner.plan("not-created", "refresh", CONTEXT, "bad")
        row = plan_bound(runner, "refresh", CONTEXT, "direct_single")
        self.assertEqual(runner.store.cancel(row["id"])["status"], "cancelled")
        self.assertEqual(len(runner.store.export_runs()["runs"]), 1)


def plan_bound(runner, command, context, mode):
    run = runner.store.create_run(command, mode)
    runner.store.bind_context(
        run["id"],
        {
            "context": context,
            "context_hash": context_hash(context),
            "bridge": {
                "bridge_pid": 1,
                "bridge_port": 8766,
                "arcmap_pid": 10,
                "hwnd": 1,
            },
            "captured_at": 1.0,
        },
    )
    return runner.plan(run["id"], command, context, mode)
