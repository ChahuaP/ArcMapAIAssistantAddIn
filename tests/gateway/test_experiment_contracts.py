import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.experiments import ContractError, ExperimentRunner, _prompt
from gateway_py3.run_store import RunStore
from gateway_py3.validators import context_hash


WORKFLOW = {
    "action": "execute",
    "summary": "refresh",
    "steps": [
        {
            "id": "s",
            "operation": "context.list_layers",
            "arguments": {},
            "reason": "refresh",
        }
    ],
}


class FakeClient:
    provider_id = "primary"
    model_id = "model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, provider, model):
        return _ClientView(self, provider, model)

    def chat_json(self, messages):
        self.calls.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _ClientView:
    def __init__(self, owner, provider, model):
        self.owner, self.provider_id, self.model_id = owner, provider, model

    def chat_json(self, messages):
        return self.owner.chat_json(messages)


class PromptContractTests(unittest.TestCase):
    def test_every_workflow_role_uses_the_exact_step_contract(self):
        for role in ("direct", "context", "constrained", "planner"):
            prompt = _prompt(role)
            self.assertIn("exactly four fields: id", prompt)
            self.assertIn("operation (exact registered operation id)", prompt)
            self.assertIn("arguments (object matching parameters_schema)", prompt)
            self.assertIn("Use arguments, never parameters", prompt)
            self.assertIn("action MUST be exactly execute and steps MUST be a non-empty array", prompt)

    def test_role_prompts_require_the_wrapped_response_root(self):
        expected = {
            "direct": "one key: workflow_draft",
            "context": "one key: workflow_draft",
            "constrained": "exactly two keys: task_semantics and workflow_draft",
            "semantic": "one key: task_semantics",
            "planner": "one key: workflow_draft",
            "auditor": "one key: audit_result",
        }
        for role, marker in expected.items():
            with self.subTest(role=role):
                self.assertIn(marker, _prompt(role))


class ExperimentContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.temp.name) / "runs.sqlite")
        self.model_config = _fixture_model_config()
        self.config_patcher = mock.patch(
            "gateway_py3.llm_providers.load_config",
            return_value=self.model_config,
        )
        self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        self.temp.cleanup()

    def test_g0_hides_context_cards(self):
        client = FakeClient([{"workflow_draft": WORKFLOW}])
        plan_bound(ExperimentRunner(
            OperationCatalog(),
            self.store,
            client,
        ), "x", {"layers": []}, "direct_single")
        payload = client.calls[0][1]["content"]
        self.assertNotIn('"context"', payload)
        self.assertNotIn('context.list_layers', payload)
        self.assertIn('Allowed where ops', payload)

    def test_g2_validates_once_when_valid(self):
        client = FakeClient([{
            "task_semantics": {
                "goal": "x",
                "inputs": [],
                "constraints": [],
                "success_criteria": [],
            },
            "workflow_draft": WORKFLOW,
        }])
        row = plan_bound(ExperimentRunner(
            OperationCatalog(),
            self.store,
            client,
        ), "x", {"layers": []}, "constrained_single")
        self.assertEqual(len(row["agent_trace"][0]["run"]["validations"]), 1)

    def test_trace_records_model_metadata(self):
        client = FakeClient([{"workflow_draft": WORKFLOW}])
        row = plan_bound(ExperimentRunner(
            OperationCatalog(),
            self.store,
            client,
        ), "x", {"layers": []}, "direct_single")
        self.assertEqual(
            row["agent_trace"][0]["run"]["turns"][0]["provider"],
            self.model_config["primary_provider"],
        )

    def test_invalid_request_does_not_persist(self):
        runner = ExperimentRunner(OperationCatalog(), self.store, FakeClient([]))
        with self.assertRaises(Exception):
            runner.plan("not-created", "x", {}, "bad")
        self.assertEqual(self.store.export_runs()["runs"], [])

    def test_g3_semantic_payload_has_context(self):
        semantic = {
            "task_semantics": {
                "goal": "x",
                "inputs": [],
                "constraints": [],
                "success_criteria": [],
            }
        }
        client = FakeClient([
            semantic,
            {"workflow_draft": WORKFLOW},
            {"audit_result": {"decision": "pass", "issues": [], "revision_requirements": []}},
        ])
        row = plan_bound(ExperimentRunner(
            OperationCatalog(),
            self.store,
            client,
        ), "x", {"layers": []}, "multi_agent")
        self.assertIn('"context"', client.calls[0][1]["content"])
        trace = row["agent_trace"][0]["run"]
        self.assertEqual(trace["workflow_versions"][0]["source_role"], "planner")
        self.assertEqual(trace["audits"][0]["version_id"], "w1")

    def test_g3_repairs_a_flat_audit_response(self):
        semantic = {
            "task_semantics": {
                "goal": "x",
                "inputs": [],
                "constraints": [],
                "success_criteria": [],
            }
        }
        flat_audit = {"decision": "pass", "issues": [], "revision_requirements": []}
        wrapped_audit = {"audit_result": flat_audit}
        client = FakeClient([semantic, {"workflow_draft": WORKFLOW}, flat_audit, wrapped_audit])

        row = plan_bound(ExperimentRunner(
            OperationCatalog(),
            self.store,
            client,
        ), "x", {"layers": []}, "multi_agent")

        trace = row["agent_trace"][0]["run"]
        self.assertEqual(row["status"], "planned")
        self.assertEqual(trace["counts"]["contract_revisions"], 1)
        self.assertEqual(trace["audits"][-1]["decision"], "pass")
        self.assertEqual(trace["validations"][0]["version_id"], "w1")

    def test_terminal_workflow_is_rejected_before_execution(self):
        terminal = {"workflow_draft": {"action": "answer", "summary": "no-op", "steps": []}}
        client = FakeClient([terminal, terminal, terminal])

        with self.assertRaisesRegex(ContractError, "failed after 3 attempts"):
            plan_bound(ExperimentRunner(
                OperationCatalog(),
                self.store,
                client,
            ), "x", {"layers": []}, "direct_single")

    def test_run_starts_running_before_planning(self):
        row = self.store.create_run("x", "context_single")
        self.assertEqual(row["status"], "running")
        self.assertEqual(row["context_hash"], "")
        self.assertNotIn("context", self.store.run_trace(row["id"]))

    def test_cancelled_run_is_not_executable(self):
        row = self.store.create_run("x", "context_single")
        self.store.cancel(row["id"])
        self.assertEqual(self.store.get(row["id"])["status"], "cancelled")

    def test_drafts_are_recorded_at_their_producing_role(self):
        cases = (
            ("direct_single", [{"workflow_draft": WORKFLOW}], "direct"),
            ("context_single", [{"workflow_draft": WORKFLOW}], "context"),
            (
                "constrained_single",
                [{
                    "task_semantics": {
                        "goal": "x",
                        "inputs": [],
                        "constraints": [],
                        "success_criteria": [],
                    },
                    "workflow_draft": WORKFLOW,
                }],
                "constrained",
            ),
        )
        for mode, responses, source_role in cases:
            with self.subTest(mode=mode):
                client = FakeClient(responses)
                row = plan_bound(ExperimentRunner(
                    OperationCatalog(),
                    self.store,
                    client,
                ), "x", {"layers": []}, mode)
                trace = row["agent_trace"][0]["run"]
                self.assertEqual(trace["workflow_versions"][0]["source_role"], source_role)
                self.assertEqual(trace["validations"][0]["version_id"], "w1")

    def test_external_artifact_entrypoint_is_absent(self):
        self.assertFalse(hasattr(ExperimentRunner, "plan_from_artifacts"))
        self.assertFalse(hasattr(ExperimentRunner, "run"))

    def test_model_call_failure_closes_failed_stage_without_plain_error(self):
        client = FakeClient([RuntimeError("credential=secret")])
        runner = ExperimentRunner(OperationCatalog(), self.store, client)
        with self.assertRaises(RuntimeError):
            plan_bound(runner, "x", {"layers": []}, "direct_single")

        row = self.store.list_recent(limit=1, include_trace=True)[0]
        trace = row["agent_trace"][0]["run"]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(trace["stages"][-1]["status"], "failed")
        self.assertEqual(trace["turns"][-1]["error"]["type"], "RuntimeError")
        self.assertNotIn("credential=secret", str(trace["turns"][-1]["error"]))
        self.assertNotIn("credential=secret", str(trace["failure"]))

    def test_primary_slot_model_overrides_provider_default_in_trace(self):
        client = FakeClient([{"workflow_draft": WORKFLOW}])
        client.provider_id = self.model_config["primary_provider"]
        client.model_id = self.model_config["primary_model"]

        with mock.patch(
            "gateway_py3.experiments.create_provider",
            return_value=client,
        ) as factory:
            row = plan_bound(
                ExperimentRunner(OperationCatalog(), self.store),
                "x",
                {"layers": []},
                "direct_single",
            )

        factory.assert_called_once_with(
            provider_id=self.model_config["primary_provider"],
            model_id=self.model_config["primary_model"],
        )
        self.assertEqual(
            row["agent_trace"][0]["run"]["turns"][0]["model"],
            self.model_config["primary_model"],
        )

    def test_multi_agent_uses_configured_independent_reviewer(self):
        primary = FakeClient([
            {
                "task_semantics": {
                    "goal": "x",
                    "inputs": [],
                    "constraints": [],
                    "success_criteria": [],
                }
            },
            {"workflow_draft": WORKFLOW},
        ])
        primary.provider_id = self.model_config["primary_provider"]
        primary.model_id = self.model_config["primary_model"]
        reviewer = FakeClient(
            [
                {
                    "audit_result": {
                        "decision": "pass",
                        "issues": [],
                        "revision_requirements": [],
                    }
                }
            ]
        )
        reviewer.provider_id = self.model_config["reviewer_provider"]
        reviewer.model_id = self.model_config["reviewer_model"]

        with mock.patch(
            "gateway_py3.experiments.create_provider",
            side_effect=[primary, reviewer],
        ) as factory:
            row = plan_bound(
                ExperimentRunner(OperationCatalog(), self.store),
                "x",
                {"layers": []},
                "multi_agent",
            )

        self.assertEqual(
            factory.call_args_list,
            [
                mock.call(
                    provider_id=self.model_config["primary_provider"],
                    model_id=self.model_config["primary_model"],
                ),
                mock.call(
                    provider_id=self.model_config["reviewer_provider"],
                    model_id=self.model_config["reviewer_model"],
                ),
            ],
        )
        turns = row["agent_trace"][0]["run"]["turns"]
        self.assertEqual(
            [turn["model"] for turn in turns],
            [
                self.model_config["primary_model"],
                self.model_config["primary_model"],
                self.model_config["reviewer_model"],
            ],
        )


def plan_bound(runner, command, context, mode):
    run = runner.store.create_run(command, mode)
    runner.store.bind_context(run["id"], {
        "context": context,
        "context_hash": context_hash(context),
        "bridge": {"bridge_pid": 1, "bridge_port": 8766, "arcmap_pid": 10, "hwnd": 1},
        "captured_at": 1.0,
    })
    return runner.plan(run["id"], command, context, mode)


def _fixture_model_config():
    return {
        "primary_provider": "fixture-primary",
        "primary_model": "fixture-primary-model",
        "reviewer_provider": "fixture-reviewer",
        "reviewer_model": "fixture-reviewer-model",
    }
