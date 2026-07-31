import csv
import importlib.util
import json
import pathlib
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import patch

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.experiments import ExperimentRunner
from gateway_py3.run_store import RunStore
from gateway_py3.validators import context_hash
from gateway_py3.workflow_protocol import workflow_protocol


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments" / "synthetic_city" / "run_formal_experiments.py"
SPEC = importlib.util.spec_from_file_location("formal_experiment_runner", MODULE_PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


SEMANTICS = {
    "goal": "refresh",
    "inputs": [],
    "constraints": [],
    "success_criteria": [],
}
INVALID_WORKFLOW = {
    "action": "execute",
    "summary": "bad",
    "steps": [
        {
            "id": "s1",
            "operation": "selection.select_by_attribute",
            "arguments": {
                "layer": "roads",
                "where": {
                    "field": "TYPE",
                    "op": "in",
                    "value": ["A"],
                },
            },
            "reason": "bad",
        }
    ],
}
PLANNING_CONTEXT = {
    "is_saved": True,
    "layers": [
        {
            "layer_ref": "layer:roads",
            "name": "roads",
            "longName": "roads",
            "fields": [{"name": "TYPE", "type": "String"}],
        }
    ],
}


class PlanningClient:
    def __init__(self, provider, model, replies):
        self.provider_id = provider
        self.model_id = model
        self.replies = list(replies)

    def chat_json(self, messages):
        return self.replies.pop(0)


class FormalExperimentRunnerTests(unittest.TestCase):
    def setUp(self):
        self.dataset = ROOT / "out" / "synthetic-city-v1"
        self.load_order, self.cases, self.truth = runner.validate_dataset(self.dataset)

    def test_reset_contract_requires_exact_source_state(self):
        command = runner.reset_command(self.load_order)

        self.assertIn("layer.clear_layers", command)
        self.assertIn("context.list_layers", command)
        self.assertEqual(len(runner.source_layer_names(self.load_order)), 14)

    def test_wait_recognizes_every_terminal_run_status(self):
        client = runner.GatewayClient("http://127.0.0.1:8765")
        for status in ("clarify", "reject"):
            client.get = lambda _path, value=status: {"run": {"status": value}}
            self.assertEqual(client.wait("run-id", 1)["status"], status)

    def test_wait_stops_immediately_on_unresolved_arcmap_execution(self):
        client = runner.GatewayClient("http://127.0.0.1:8765")
        for status in ("recovery_required", "indeterminate"):
            client.get = lambda _path, value=status: {"run": {"status": value}}
            with self.assertRaisesRegex(runner.InfrastructureStop, "authoritative execution is unresolved") as raised:
                client.wait("run-id", 1)
            self.assertEqual(raised.exception.run_id, "run-id")
            self.assertEqual(raised.exception.status, status)

    def test_infrastructure_stop_is_written_outside_method_statistics(self):
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp)
            runner.write_infrastructure_stop(
                output,
                runner.InfrastructureStop("run-id", "recovery_required"),
            )
            payload = json.loads((output / "infrastructure_stop.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["run_id"], "run-id")
        self.assertEqual(payload["status"], "recovery_required")
        self.assertTrue(payload["excluded_from_method_statistics"])

    def test_task_command_makes_outputs_and_g0_boundary_explicit(self):
        round_spec = self.cases["cases"][2]["rounds"][0]
        catalog = runner.direct_static_catalog(self.dataset)
        command = runner.task_command(round_spec, ROOT / "out" / "formal-test", "direct_single", catalog)

        self.assertIn("suspect_projects.shp", command)
        self.assertIn("construction(建设项目)", command)
        self.assertIn("不得调用 context.*", command)
        self.assertIn("from_step:<步骤 id>", command)
        self.assertIn("CSV、PNG属于文件成果", command)
        self.assertIn("不得使用 from_step 引用", command)

    def test_land_continuous_outputs_score_against_truth_exactly(self):
        outputs = ROOT / "out" / "experiment-preflight" / "multi_agent" / "land_continuous"
        rounds = self.cases["cases"][2]["rounds"]

        scores = [runner.score_round(round_spec, outputs, self.truth) for round_spec in rounds]

        self.assertTrue(all(score["ok"] for score in scores))

    def test_write_reports_separates_failures_and_dependency_skips(self):
        planning_row = self._stalled_g3_row()
        planning_failure_stage = runner.run_failure_stage(planning_row)
        execution_row = {
            "agent_trace": [{"run": {"failure": {"stage": "execution"}}}]
        }

        planning_trace = planning_row["agent_trace"][0]["run"]
        self.assertEqual(planning_trace["terminal"]["stage"], "planning")
        self.assertEqual(planning_failure_stage, "planning")
        self.assertEqual(runner.run_failure_stage(execution_row), "execution")

        records = [
            self._record("flow", 1, 1, "r1-1", "succeeded", True),
            self._record(
                "flow",
                1,
                2,
                "r1-2",
                "failed",
                False,
                failure_stage=planning_failure_stage,
            ),
            self._record(
                "flow",
                1,
                3,
                "",
                "skipped_dependency",
                False,
                blocked_by_round=2,
                blocked_by_run_id="r1-2",
                skip_reason="planning failed",
            ),
            self._record("flow", 2, 1, "r2-1", "succeeded", True),
            self._record("flow", 2, 2, "r2-2", "succeeded", False),
            self._record(
                "flow",
                2,
                3,
                "",
                "skipped_dependency",
                False,
                blocked_by_round=2,
                blocked_by_run_id="r2-2",
                skip_reason="scoring failed",
            ),
            self._record(
                "execution",
                1,
                1,
                "e1-1",
                "context_failed",
                False,
                failure_stage=runner.run_failure_stage(execution_row),
            ),
        ]
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp)
            runner.write_reports(output, records)
            with (output / "round_scores.csv").open(encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            with (output / "summary.csv").open(encoding="utf-8-sig") as handle:
                summaries = {
                    (row["mode"], row["case_id"]): row
                    for row in csv.DictReader(handle)
                }
        by_run = {row["run_id"]: row for row in rows}
        self.assertEqual(by_run["r1-2"]["failure_stage"], "planning")
        skipped = next(row for row in rows if row["blocked_by_run_id"] == "r1-2")
        self.assertEqual(skipped["blocked_by_round"], "2")
        self.assertEqual(skipped["skip_reason"], "planning failed")
        flow = summaries[("multi_agent", "flow")]
        self.assertEqual(flow["attempted_rounds"], "4")
        self.assertEqual(flow["planning_failures"], "1")
        self.assertEqual(flow["execution_failures"], "0")
        self.assertEqual(flow["scoring_failures"], "1")
        self.assertEqual(flow["dependency_skips"], "2")
        self.assertEqual(float(flow["attempt_success_rate"]), 0.75)
        self.assertAlmostEqual(float(flow["exact_among_succeeded"]), 2 / 3)
        self.assertEqual(float(flow["business_flow_completion_rate"]), 0.0)
        self.assertEqual(float(flow["mean_duration_seconds"]), 1.0)
        self.assertEqual(summaries[("multi_agent", "execution")]["execution_failures"], "1")

    def test_partial_flow_is_not_reported_as_complete(self):
        records = [self._record("partial", 1, 1, "r1", "succeeded", True)]
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp)
            runner.write_reports(output, records)
            with (output / "summary.csv").open(encoding="utf-8-sig") as handle:
                summary = next(csv.DictReader(handle))

        self.assertEqual(float(summary["business_flow_completion_rate"]), 0.0)

    def test_run_policy_must_match_frozen_manifest(self):
        row = {"agent_trace": [{"run": {"planning_policy": {"catalog_hash": "actual"}}}]}

        with self.assertRaisesRegex(runner.ExperimentError, "frozen experiment policy"):
            runner.require_run_policy(row, {"catalog_hash": "expected"})

    def _stalled_g3_row(self):
        primary = PlanningClient(
            "primary",
            "planner",
            [
                {"task_semantics": SEMANTICS},
                {"workflow_draft": INVALID_WORKFLOW},
                {"workflow_draft": INVALID_WORKFLOW},
            ],
        )
        reviewer = PlanningClient("reviewer", "auditor", [])
        config = {
            "primary_provider": "primary",
            "primary_model": "planner",
            "reviewer_provider": "reviewer",
            "reviewer_model": "auditor",
        }

        def create_client(provider, model):
            if provider == "primary":
                return primary
            return reviewer

        with tempfile.TemporaryDirectory() as temp:
            with patch(
                "gateway_py3.llm_providers.load_config",
                return_value=config,
            ):
                store = RunStore(pathlib.Path(temp) / "runs.sqlite")
                experiment_runner = ExperimentRunner(
                    OperationCatalog(),
                    store,
                    create_client,
                )
                run = store.create_run("refresh", "multi_agent")
                store.bind_context(
                    run["id"],
                    {
                        "context": PLANNING_CONTEXT,
                        "context_hash": context_hash(PLANNING_CONTEXT),
                        "bridge": {
                            "bridge_pid": 1,
                            "bridge_port": 1,
                            "arcmap_pid": 1,
                            "hwnd": 1,
                        },
                        "captured_at": 1,
                    },
                )
                return experiment_runner.plan(
                    run["id"],
                    "refresh",
                    PLANNING_CONTEXT,
                    "multi_agent",
                )

    def test_build_manifest_records_server_policy_without_rehashing(self):
        args = Namespace(
            modes=["multi_agent"],
            repetitions=2,
            timeout=600,
            gateway="http://gateway",
        )
        policy = {
            "validation_revisions": 3,
            "audit_revisions": 3,
            "response_contract_revisions": 2,
            "catalog_hash": "catalog",
            "protocol_hash": "protocol",
            "workflow_protocol": workflow_protocol(),
        }
        config = {
            "primary_provider": "p",
            "primary_model": "pm",
            "reviewer_provider": "r",
            "reviewer_model": "rm",
        }
        with patch.object(runner, "_git_state", return_value=("head", True)) as git_state:
            manifest = runner.build_manifest(
                self.dataset,
                args,
                config,
                policy,
                ROOT,
                "1.0.2",
                created_at=1.0,
            )
        git_state.assert_called_once_with(ROOT)
        self.assertIs(manifest["planning_policy"], policy)
        self.assertEqual(manifest["catalog_hash"], "catalog")
        self.assertEqual(manifest["protocol_hash"], "protocol")
        self.assertEqual(manifest["gateway_app_version"], "1.0.2")
        self.assertEqual(manifest["timeout_seconds"], 600)
        self.assertEqual(manifest["primary"], {"provider": "p", "model": "pm"})
        self.assertEqual(
            manifest["planning_policy"]["workflow_protocol"]["version"],
            workflow_protocol()["version"],
        )

    @staticmethod
    def _record(
        case_id,
        repetition,
        round_number,
        run_id,
        status,
        ok,
        **extra,
    ):
        return {
            "mode": "multi_agent",
            "case_id": case_id,
            "repetition": repetition,
            "round": round_number,
            "run_id": run_id,
            "run_status": status,
            "duration_seconds": 1.0,
            "expected_rounds": 3,
            "scores": [{"round": round_number, "ok": ok, "artifacts": []}],
            **extra,
        }


if __name__ == "__main__":
    unittest.main()
