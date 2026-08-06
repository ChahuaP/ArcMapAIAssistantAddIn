import csv
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import patch

import geopandas as gpd

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.planning_engine import PlanningEngine
from gateway_py3.run_store import RunStore
from gateway_py3.validators import context_hash
from gateway_py3.workflow_protocol import workflow_protocol
from tests.gateway.planner_test_utils import model_wire_response, task_contract


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments" / "synthetic_city" / "run_formal_experiments.py"
SPEC = importlib.util.spec_from_file_location("formal_experiment_runner", MODULE_PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


TASK_CONTRACT = task_contract
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

    def chat_structured(self, messages, contract):
        return model_wire_response(self.replies.pop(0), messages)


class FormalExperimentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.dataset = ROOT / "experiments" / "data" / "synthetic-city-v1"
        self.load_order, self.cases, self.truth = runner.validate_dataset(self.dataset)

    def test_pair_relocation_is_lossless_after_arcmap_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, target = root / "pair-work" / "CASE-r01", root / "archive" / "CASE-r01"
            source.mkdir(parents=True)
            (source / "result.shp").write_text("published", encoding="utf-8")
            (source / "result.shp.session.lock").write_text("live", encoding="utf-8")
            runner.relocate_pair_workspace(root, source, target)
            self.assertEqual("published", (target / "result.shp").read_text(encoding="utf-8"))
            self.assertEqual("live", (target / "result.shp.session.lock").read_text(encoding="utf-8"))
            self.assertFalse(source.exists())

    def test_reset_contract_requires_exact_source_state(self):
        client = runner.GatewayClient("http://127.0.0.1:8765")
        with patch.object(
            client,
            "post",
            return_value={"run": {"id": "reset-run-id"}},
        ) as post:
            run_id = client.submit_reset(self.load_order)

        self.assertEqual(run_id, "reset-run-id")
        post.assert_called_once_with(
            "/experiments/reset",
            {"source_paths": self.load_order},
        )
        self.assertEqual(len(runner.source_layer_names(self.load_order)), 14)

    def test_submit_can_freeze_an_explicit_provider_and_model(self):
        client = runner.GatewayClient("http://127.0.0.1:8765")
        with patch.object(client, "post", return_value={"run": {"id": "run-id"}}) as post:
            run_id = client.submit(
                "g3_audited",
                "analyze",
                provider="qwen",
                model="qwen3.6-flash-2026-04-16",
            )

        self.assertEqual("run-id", run_id)
        post.assert_called_once_with("/runs", {
            "mode": "g3_audited",
            "command": "analyze",
            "execute": True,
            "provider": "qwen",
            "model": "qwen3.6-flash-2026-04-16",
        })

    def test_wait_recognizes_every_terminal_run_status(self):
        client = runner.GatewayClient("http://127.0.0.1:8765")
        for status in ("clarify", "reject"):
            client.get = lambda _path, value=status: {"run": {"status": value}}
            self.assertEqual(client.wait("run-id", 1)["status"], status)

    def test_wait_allows_recovery_protocol_to_resolve_authoritatively(self):
        client = runner.GatewayClient("http://127.0.0.1:8765")
        observations = iter([
            {"run": {"id": "run-id", "status": "recovery_required"}},
            {"run": {"id": "run-id", "status": "succeeded"}},
        ])
        client.get = lambda _path: next(observations)

        with patch.object(runner.time, "sleep"):
            row = client.wait("run-id", 1)

        self.assertEqual("succeeded", row["status"])

    def test_wait_stops_immediately_on_indeterminate_arcmap_execution(self):
        client = runner.GatewayClient("http://127.0.0.1:8765")
        client.get = lambda _path: {"run": {"status": "indeterminate"}}

        with self.assertRaisesRegex(runner.InfrastructureStop, "authoritative execution is unresolved") as raised:
            client.wait("run-id", 1)

        self.assertEqual("run-id", raised.exception.run_id)
        self.assertEqual("indeterminate", raised.exception.status)

    def test_wait_deadline_preserves_the_last_observed_method_run(self):
        client = runner.GatewayClient("http://127.0.0.1:8765")
        observed = {
            "id": "run-id",
            "status": "executing",
            "agent_trace": [{"run": {
                "stages": [{"name": "execution", "status": "running"}],
                "execution_owner": {"heartbeat_at": 1000.0},
            }}],
        }
        client.get = lambda _path: {"run": observed}

        with self.assertRaises(runner.RunDeadlineExceeded) as raised:
            client.wait("run-id", 0)

        self.assertEqual("run-id", raised.exception.run_id)
        self.assertEqual(observed, raised.exception.row)

    def test_method_wait_stops_immediately_on_explicit_model_quota_exhaustion(self):
        client = runner.GatewayClient("http://127.0.0.1:8765")
        quota_row = {
            "id": "quota-run",
            "status": "failed",
            "result": {
                "error": {
                    "type": "ProviderError",
                    "message": "HTTP 403 quota exhausted",
                }
            },
        }
        client.wait = lambda _run_id, _timeout: quota_row

        with self.assertRaises(runner.ModelQuotaStop) as raised:
            runner.wait_for_method_run(client, "quota-run", 10)

        self.assertEqual("quota-run", raised.exception.run_id)
        self.assertEqual("quota exhausted", raised.exception.marker)

        rate_limited = {
            "id": "rate-run",
            "status": "failed",
            "result": {"error": {"message": "HTTP 429 rate limit"}},
        }
        client.wait = lambda _run_id, _timeout: rate_limited
        self.assertIs(rate_limited, runner.wait_for_method_run(client, "rate-run", 10))

    def test_reset_deadline_remains_an_infrastructure_stop(self):
        observed = {
            "id": "reset-run",
            "status": "executing",
            "agent_trace": [{"run": {"stages": [{"name": "execution", "status": "running"}]}}],
        }

        class FakeGateway:
            def submit_reset(self, _load_order):
                return "reset-run"

            def wait(self, run_id, _timeout):
                raise runner.RunDeadlineExceeded(run_id, observed)

        with self.assertRaises(runner.InfrastructureStop) as raised:
            runner.run_reset(FakeGateway(), [], 1, {})

        self.assertEqual("reset-run", raised.exception.run_id)
        self.assertEqual("timeout", raised.exception.status)

    def test_planning_deadline_is_retained_as_a_method_failure(self):
        observed = {
            "id": "method-run",
            "status": "planning",
            "agent_trace": [{"run": {"stages": [{"name": "planning", "status": "running"}]}}],
        }

        class FakeGateway:
            def wait(self, run_id, _timeout):
                raise runner.RunDeadlineExceeded(run_id, observed)

        row = runner.wait_for_method_run(FakeGateway(), "method-run", 17)

        self.assertEqual("experiment_timeout", runner.method_run_status(row))
        self.assertEqual("planning", runner.run_failure_stage(row))
        self.assertEqual(17, row["experiment_outcome"]["timeout_seconds"])
        self.assertNotIn("experiment_outcome", observed)

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
        command = runner.task_command(round_spec, ROOT / "experiments" / "out" / "formal-test", "g0_direct", catalog)

        self.assertIn("suspect_projects.shp", command)
        self.assertIn("construction(建设项目)", command)
        self.assertIn("不得调用 context.*", command)
        self.assertIn("from_step:<步骤 id>", command)
        self.assertIn("CSV、PNG属于文件成果", command)
        self.assertIn("不得使用 from_step 引用", command)

    def test_land_continuous_outputs_score_against_truth_exactly(self):
        rounds = self.cases["cases"][2]["rounds"]

        with tempfile.TemporaryDirectory() as temp:
            outputs = pathlib.Path(temp)
            for round_spec in rounds:
                for output in round_spec["expected_outputs"]:
                    suffix = pathlib.Path(output).suffix.lower()
                    if suffix in (".csv", ".png"):
                        (outputs / output).write_bytes(b"result")
                        continue
                    truth_key = runner.OUTPUT_TRUTH_KEYS[output]
                    identifiers = self.truth[truth_key]
                    frame = gpd.GeoDataFrame(
                        {"RESULT_ID": identifiers},
                        geometry=gpd.points_from_xy(range(len(identifiers)), [0] * len(identifiers)),
                        crs="EPSG:3857",
                    )
                    frame.to_file(outputs / (output + ".shp"))
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
        flow = summaries[("g3_audited", "flow")]
        self.assertEqual(flow["attempted_rounds"], "4")
        self.assertEqual(flow["planning_failures"], "1")
        self.assertEqual(flow["execution_failures"], "0")
        self.assertEqual(flow["scoring_failures"], "1")
        self.assertEqual(flow["dependency_skips"], "2")
        self.assertEqual(float(flow["attempt_success_rate"]), 0.75)
        self.assertAlmostEqual(float(flow["exact_among_succeeded"]), 2 / 3)
        self.assertEqual(float(flow["business_flow_completion_rate"]), 0.0)
        self.assertEqual(float(flow["mean_duration_seconds"]), 1.0)
        self.assertEqual(summaries[("g3_audited", "execution")]["execution_failures"], "1")

    def test_partial_flow_is_not_reported_as_complete(self):
        records = [self._record("partial", 1, 1, "r1", "succeeded", True)]
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp)
            runner.write_reports(output, records)
            with (output / "summary.csv").open(encoding="utf-8-sig") as handle:
                summary = next(csv.DictReader(handle))

        self.assertEqual(float(summary["business_flow_completion_rate"]), 0.0)

    def test_round_scores_preserves_paired_execution_provenance(self):
        record = self._record(
            "paired", 1, 1, "g3", "succeeded", True,
            pair_kind="production_recovery",
            g3_baseline_artifact_hash="g3-artifact",
            post_context_equal=True,
            post_result_equal=False,
            post_snapshot_equal=True,
        )
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp)
            runner.write_reports(output, [record])
            with (output / "round_scores.csv").open(encoding="utf-8-sig") as handle:
                score = next(csv.DictReader(handle))

        self.assertEqual("production_recovery", score["pair_kind"])
        self.assertEqual("g3-artifact", score["g3_baseline_artifact_hash"])
        self.assertEqual("True", score["post_context_equal"])
        self.assertEqual("False", score["post_result_equal"])
        self.assertEqual("True", score["post_snapshot_equal"])

    def test_successful_round_is_never_reported_as_a_planning_failure(self):
        records = [
            self._record(
                "successful",
                1,
                1,
                "r1",
                "succeeded",
                True,
                failure_stage="planning",
            )
        ]
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp)
            runner.write_reports(output, records)
            with (output / "round_scores.csv").open(encoding="utf-8-sig") as handle:
                score = next(csv.DictReader(handle))
            with (output / "summary.csv").open(encoding="utf-8-sig") as handle:
                summary = next(csv.DictReader(handle))

        self.assertEqual(score["run_status"], "succeeded")
        self.assertEqual(score["failure_stage"], "")
        self.assertEqual(summary["planning_failures"], "0")

    def test_write_reports_emits_flow_audit_tool_and_efficiency_evidence(self):
        record = self._record("flow", 1, 1, "run-1", "succeeded", True)
        record["agent_trace"] = [
            {
                "run": {
                    "counts": {"audit_revisions": 1},
                    "audits": [
                        {
                            "decision": "revise",
                            "findings": [{"category": "business_semantic"}],
                        },
                        {"decision": "pass", "findings": []},
                    ],
                    "turns": [
                        {
                            "role": "auditor",
                            "provider_response": {
                                "usage": {"input_tokens": 10, "output_tokens": 2}
                            },
                        }
                    ],
                    "workflow_versions": [
                        {
                            "id": "w1",
                            "hash": "baseline",
                            "workflow": {
                                "steps": [
                                    {
                                        "id": "s1",
                                        "operation": "selection.select_by_attribute",
                                        "arguments": {"layer": "roads"},
                                    }
                                ]
                            },
                        },
                        {
                            "id": "w2",
                            "hash": "final",
                            "workflow": {
                                "steps": [
                                    {
                                        "id": "s1",
                                        "operation": "selection.select_by_attribute",
                                        "arguments": {"layer": "roads"},
                                    }
                                ]
                            },
                        },
                    ],
                }
            }
        ]

        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp)
            runner.write_reports(output, [record])
            with (output / "flow_scores.csv").open(encoding="utf-8-sig") as handle:
                flows = list(csv.DictReader(handle))
            with (output / "audit_scores.csv").open(encoding="utf-8-sig") as handle:
                audits = list(csv.DictReader(handle))
            with (output / "efficiency.csv").open(encoding="utf-8-sig") as handle:
                efficiency = list(csv.DictReader(handle))
            tool_calls = json.loads((output / "tool_calls.json").read_text(encoding="utf-8"))

        self.assertEqual(flows[0]["flow_ok"], "False")
        self.assertEqual(audits[0]["audit_revision_count"], "1")
        self.assertEqual(audits[0]["baseline_workflow_hash"], "baseline")
        self.assertEqual(audits[0]["final_workflow_hash"], "final")
        self.assertEqual(audits[0]["audit_input_tokens"], "10")
        self.assertEqual(efficiency[0]["tool_calls"], "1")
        self.assertEqual(tool_calls[0]["operation"], "selection.select_by_attribute")

    def test_run_policy_must_match_frozen_manifest(self):
        row = {"agent_trace": [{"run": {"planning_policy": {"catalog_hash": "actual"}}}]}

        with self.assertRaisesRegex(runner.ExperimentError, "frozen experiment policy"):
            runner.require_run_policy(row, {"catalog_hash": "expected"})

    def test_run_policy_check_preserves_an_earlier_planning_failure(self):
        row = {
            "status": "failed",
            "agent_trace": [{"run": {
                "failure": {
                    "stage": "planning",
                    "message": "PlanArtifact baseline reverification failed.",
                },
            }}],
        }

        with self.assertRaisesRegex(
            runner.ExperimentError,
            "planning.*PlanArtifact baseline reverification failed",
        ):
            runner.require_run_policy(row, {"catalog_hash": "expected"})

    def test_execute_paired_runs_in_two_process_phases_with_one_logical_work_path(self):
        events, submissions = [], []
        artifacts = []
        for number in (1, 2):
            workflow = {"action": "execute", "summary": str(number), "steps": []}
            artifacts.append({"artifact_hash": "a%d" % number, "baseline_workflow_hash": "w%d" % number,
                              "execution_context_hash": "c%d" % number, "baseline_workflow": workflow})

        class FakeGateway:
            def submit(self, mode, command, plan_artifact=None):
                submissions.append((mode, command, plan_artifact))
                return "run-%d" % len(submissions)
            def wait(self, run_id, _timeout):
                mode, _command, artifact = submissions[int(run_id.split("-")[1]) - 1]
                index = sum(1 for item in submissions[:int(run_id.split("-")[1])] if item[0] == mode) - 1
                frozen = artifacts[index] if mode == "g2_constrained" else artifact
                return {"id": run_id, "status": "succeeded", "agent_trace": [{"run": {
                    "planning_policy": {"p": 1}, "plan_artifact": frozen, "plan_artifact_hash": frozen["artifact_hash"],
                    "execution_context_hash": frozen["execution_context_hash"], "baseline_workflow_hash": frozen["baseline_workflow_hash"],
                    "execution": {"context_next_hash": "next-%d" % index,
                                  "context_next_snapshot_hash": "%s-snapshot-%d" % (mode, index),
                                  "result_hash": "result-%d" % index},
                }}]}

        spec = {"cases": [{"case_id": "case", "rounds": [
            {"round": 1, "prompt": "one", "expected_outputs": []},
            {"round": 2, "prompt": "two", "expected_outputs": []},
        ]}]}
        args = Namespace(repetitions=1, timeout=1, paired_strategy="artifact-replay")
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(runner, "run_reset", side_effect=lambda *_args: events.append("reset")), \
             patch.object(runner, "require_gateway_identity"), \
             patch.object(runner, "require_run_policy"), \
             patch.object(runner, "score_round", side_effect=lambda spec, *_args: {"round": spec["round"], "ok": True, "artifacts": []}):
            root = pathlib.Path(temp)
            runner.execute_paired_g2(FakeGateway(), args, [], spec, "", root, {"planning_policy": {"p": 1}}, "v")
            self.assertTrue((root / "pair-work" / "case-r01").is_dir())
            runner.execute_paired_g3(FakeGateway(), args, [], spec, {}, root, {"planning_policy": {"p": 1}}, "v", records := [])
        self.assertEqual([item[0] for item in submissions], ["g2_constrained", "g2_constrained", "g3_audited", "g3_audited"])
        self.assertEqual(submissions[0][1], submissions[2][1])
        self.assertEqual(submissions[1][1], submissions[3][1])
        self.assertEqual([item[2]["artifact_hash"] for item in submissions[2:]], ["a1", "a2"])
        self.assertEqual(4, len(records))
        self.assertTrue(all(record["pair_valid"] for record in records))
        self.assertTrue(all(record["post_context_equal"] for record in records))
        self.assertTrue(all(not record["post_snapshot_equal"] for record in records))

    def test_replay_rebinds_only_a_closed_full_request_evidence_contract(self):
        previous_folder = r"D:\old\pair-work\CASE-r01"
        target_folder = pathlib.Path(r"D:\new\pair-work\CASE-r01")
        source = {
            "request": "old request; generate result.shp in " + previous_folder,
            "task_contract": {
                "input_entities": [{"evidence": "old request; generate result.shp in " + previous_folder}],
                "outputs": [{
                    "evidence": "result.shp", "destination": previous_folder,
                }],
                "requirements": [{"evidence": "old request; generate result.shp in " + previous_folder}],
                "clarifications": [{"evidence": "old request; generate result.shp in " + previous_folder}],
            },
        }
        new_request = "new request; generate result.shp in " + str(target_folder)

        rebound = runner.rebind_replay_task_contract(source, new_request, target_folder)

        self.assertEqual(previous_folder, source["task_contract"]["outputs"][0]["destination"])
        self.assertEqual(str(target_folder), rebound["outputs"][0]["destination"])
        self.assertTrue(all(
            item["evidence"] == new_request
            for collection in ("input_entities", "requirements", "clarifications")
            for item in rebound[collection]
        ))
        source["task_contract"]["requirements"][0]["evidence"] = "partial evidence"
        with self.assertRaisesRegex(runner.ExperimentError, "immutable source request"):
            runner.rebind_replay_task_contract(source, new_request, target_folder)

    def test_replay_rebinds_prior_output_data_sources_without_touching_similar_paths(self):
        old = r"D:\old\pair-work\ROAD-r01"
        target = pathlib.Path(r"D:\new\pair-work\ROAD-r01")
        workflow = {"steps": [{"arguments": {"output_folder": old}}]}
        context = {
            "extent": {"XMin": 664127.6836158192, "YMin": 3536999.9999999995,
                       "XMax": 695872.3163841808, "YMax": 3558179.661016949},
            "layers": [
                {"dataSource": old + r"\road_accident_join.shp"},
                {"dataSource": old + "-archive" + r"\unrelated.shp"},
            ],
        }

        rebound = runner.rebind_replay_context(context, workflow, target)

        self.assertEqual(old + r"\road_accident_join.shp", context["layers"][0]["dataSource"])
        self.assertEqual(str(target) + r"\road_accident_join.shp", rebound["layers"][0]["dataSource"])
        self.assertEqual(old + "-archive" + r"\unrelated.shp", rebound["layers"][1]["dataSource"])
        self.assertEqual(3537000.0, rebound["extent"]["YMin"])
        from arcmap_runtime_py2.context_fingerprint import context_hash
        self.assertEqual(context_hash(rebound), rebound["context_hash"])
        with self.assertRaisesRegex(runner.ExperimentError, "exactly one output folder"):
            runner.rebind_replay_context(context, {"steps": []}, target)

    def test_post_execution_equality_rejects_business_state_or_result_divergence(self):
        baseline = {
            "context_next_hash": "state",
            "context_next_snapshot_hash": "g2-process-snapshot",
            "result_hash": "result",
        }
        independent_process = {
            "context_next_hash": "state",
            "context_next_snapshot_hash": "g3-process-snapshot",
            "result_hash": "result",
        }

        equal = runner.paired_post_execution_equality(baseline, independent_process)
        state_changed = runner.paired_post_execution_equality(
            baseline, {**independent_process, "context_next_hash": "different-state"})
        result_changed = runner.paired_post_execution_equality(
            baseline, {**independent_process, "result_hash": "different-result"})

        self.assertEqual(
            {"context_equal": True, "result_equal": True, "snapshot_equal": False}, equal)
        self.assertFalse(state_changed["context_equal"])
        self.assertFalse(result_changed["context_equal"])

    def test_paired_g3_uses_production_causal_continuation_after_audited_state_diverges(self):
        submissions = []
        baseline_artifacts = [
            {"artifact_hash": "g2-a1", "baseline_workflow_hash": "g2-w1", "execution_context_hash": "initial", "baseline_workflow": {}},
            {"artifact_hash": "g2-a2", "baseline_workflow_hash": "g2-w2", "execution_context_hash": "g2-next", "baseline_workflow": {}},
        ]

        class FakeGateway:
            def submit(self, mode, command, plan_artifact=None):
                submissions.append((mode, command, plan_artifact))
                return str(len(submissions))

            def wait(self, run_id, _timeout):
                index = int(run_id) - 1
                mode, command, supplied = submissions[index]
                if mode == "g2_constrained":
                    number = sum(1 for item in submissions[:index + 1] if item[0] == mode) - 1
                    artifact = baseline_artifacts[number]
                    next_hash = "g2-next" if number == 0 else "g2-final"
                    trace = {"plan_artifact": artifact, "plan_artifact_hash": artifact["artifact_hash"],
                             "execution_context_hash": artifact["execution_context_hash"],
                             "execution": {"context_next_hash": next_hash, "context_next_snapshot_hash": "g2-snapshot"}}
                elif supplied is not None:
                    artifact = supplied
                    trace = {"plan_artifact": artifact, "plan_artifact_hash": artifact["artifact_hash"],
                             "execution_context_hash": artifact["execution_context_hash"],
                             "baseline_workflow_hash": artifact["baseline_workflow_hash"],
                             "execution": {"context_next_hash": "g3-next", "context_next_snapshot_hash": "g3-snapshot"}}
                else:
                    artifact = {"artifact_hash": "g3-a2", "request": command,
                                "execution_context_hash": "g3-next", "planning_policy": {"p": 1}}
                    trace = {"plan_artifact": artifact, "plan_artifact_hash": artifact["artifact_hash"],
                             "execution_context_hash": "g3-next", "execution": {"context_next_hash": "g3-final"}}
                return {"id": run_id, "status": "succeeded", "agent_trace": [{"run": trace}]}

        spec = {"cases": [{"case_id": "case", "rounds": [
            {"round": 1, "prompt": "one", "expected_outputs": []},
            {"round": 2, "prompt": "two", "expected_outputs": []},
        ]}]}
        args = Namespace(repetitions=1, timeout=1, paired_strategy="artifact-replay")
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "run_reset"), \
             patch.object(runner, "require_gateway_identity"), patch.object(runner, "require_run_policy"), \
             patch.object(runner, "score_round", side_effect=lambda spec, *_args: {"round": spec["round"], "ok": True, "artifacts": []}):
            root = pathlib.Path(temp)
            runner.execute_paired_g2(FakeGateway(), args, [], spec, "", root, {"planning_policy": {"p": 1}}, "v")
            runner.execute_paired_g3(FakeGateway(), args, [], spec, {}, root, {"planning_policy": {"p": 1}}, "v", records := [])

        g3_submissions = [item for item in submissions if item[0] == "g3_audited"]
        self.assertEqual("g2-a1", g3_submissions[0][2]["artifact_hash"])
        self.assertIsNone(g3_submissions[1][2])
        self.assertEqual(["exact_artifact", "exact_artifact", "causal_continuation", "causal_continuation"],
                         [item["pair_kind"] for item in records])
        self.assertTrue(all(item["pair_valid"] for item in records))

    def test_diagnostic_case_selection_preserves_requested_order_and_rejects_unknown_ids(self):
        spec = {"cases": [
            {"case_id": "first", "rounds": []},
            {"case_id": "second", "rounds": []},
        ]}

        selected = runner.select_cases(spec, ["second"])

        self.assertEqual(["second"], [case["case_id"] for case in selected["cases"]])
        with self.assertRaisesRegex(runner.ExperimentError, "Unknown diagnostic case ids"):
            runner.select_cases(spec, ["missing"])

    def test_pair_work_refuses_root_and_pair_root_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            with self.assertRaises(runner.PairWorkspaceError):
                runner.reset_pair_workspace(root, root)
            with self.assertRaises(runner.PairWorkspaceError):
                runner.reset_pair_workspace(root, root / "pair-work")

    def test_paired_phases_run_production_g3_after_g2_cannot_form_an_artifact(self):
        submitted = []
        class FakeGateway:
            def submit(self, mode, command, plan_artifact=None):
                submitted.append((mode, command, plan_artifact))
                return "g2"
            def wait(self, _run_id, _timeout):
                return {"id": "g2", "status": "failed", "agent_trace": [{"run": {"planning_policy": {"p": 1}}}]}
        spec = {"cases": [{"case_id": "case", "rounds": [{"round": 1, "prompt": "one", "expected_outputs": []}]}]}
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "run_reset"), \
             patch.object(runner, "require_gateway_identity"), patch.object(runner, "require_run_policy"):
            root = pathlib.Path(temp)
            records = []
            args = Namespace(repetitions=1, timeout=1, paired_strategy="production")
            runner.execute_paired_g2(FakeGateway(), args, [], spec, "", root, {"planning_policy": {"p": 1}}, "v")
            runner.execute_paired_g3(FakeGateway(), args, [], spec, {}, root, {"planning_policy": {"p": 1}}, "v", records)
        self.assertEqual(["g2_constrained", "g3_audited"], [item[0] for item in submitted])
        self.assertIsNone(submitted[1][2])
        self.assertEqual("failed", records[1]["run_status"])
        self.assertEqual("production_recovery", records[1]["pair_kind"])
        self.assertTrue(records[0]["pair_valid"])

    def test_paired_execution_deadline_counts_against_g2_and_allows_g3_recovery(self):
        submitted = []

        class FakeGateway:
            def submit(self, mode, command, plan_artifact=None):
                submitted.append((mode, command, plan_artifact))
                return mode

            def wait(self, run_id, _timeout):
                if run_id == "g2_constrained":
                    raise runner.RunDeadlineExceeded(run_id, {
                        "id": run_id,
                        "status": "executing",
                        "agent_trace": [{"run": {
                            "planning_policy": {"p": 1},
                            "stages": [{"name": "execution", "status": "running"}],
                        }}],
                    })
                command = submitted[-1][1]
                artifact = {
                    "artifact_hash": "g3-artifact",
                    "request": command,
                    "execution_context_hash": "context",
                    "planning_policy": {"p": 1},
                }
                return {
                    "id": run_id,
                    "status": "succeeded",
                    "agent_trace": [{"run": {
                        "planning_policy": {"p": 1},
                        "plan_artifact": artifact,
                        "plan_artifact_hash": "g3-artifact",
                        "execution_context_hash": "context",
                    }}],
                }

        spec = {"cases": [{"case_id": "case", "rounds": [
            {"round": 1, "prompt": "one", "expected_outputs": []},
        ]}]}
        args = Namespace(repetitions=1, timeout=1, paired_strategy="production")
        with tempfile.TemporaryDirectory() as temp, patch.object(runner, "run_reset"), \
             patch.object(runner, "require_gateway_identity"), patch.object(runner, "require_run_policy"), \
             patch.object(runner, "score_round", return_value={"round": 1, "ok": True, "artifacts": []}):
            root = pathlib.Path(temp)
            runner.execute_paired_g2(FakeGateway(), args, [], spec, "", root, {"planning_policy": {"p": 1}}, "v")
            records = []
            runner.execute_paired_g3(FakeGateway(), args, [], spec, {}, root, {"planning_policy": {"p": 1}}, "v", records)
            runner.write_reports(root, records)
            with (root / "summary.csv").open(encoding="utf-8-sig") as handle:
                summary = {(row["mode"], row["case_id"]): row for row in csv.DictReader(handle)}

        self.assertEqual(["g2_constrained", "g3_audited"], [item[0] for item in submitted])
        self.assertIsNone(submitted[1][2])
        self.assertEqual("experiment_timeout", records[0]["run_status"])
        self.assertEqual("execution", records[0]["failure_stage"])
        self.assertEqual("succeeded", records[1]["run_status"])
        self.assertEqual("production_recovery", records[1]["pair_kind"])
        self.assertEqual("1", summary[("g2_constrained", "case")]["execution_failures"])
        self.assertEqual("0", summary[("g3_audited", "case")]["execution_failures"])

    def test_pair_valid_failed_g3_is_retained_in_method_statistics(self):
        records = [
            {**self._record("paired", 1, 1, "g2", "succeeded", True), "mode": "g2_constrained", "pair_valid": True},
            {**self._record("paired", 1, 1, "g3", "failed", False), "pair_valid": True, "failure_stage": "execution"},
        ]
        with tempfile.TemporaryDirectory() as temp:
            runner.write_reports(pathlib.Path(temp), records)
            with (pathlib.Path(temp) / "summary.csv").open(encoding="utf-8-sig") as handle:
                summary = {(row["mode"], row["case_id"]): row for row in csv.DictReader(handle)}
        self.assertEqual("1", summary[("g3_audited", "paired")]["attempted_rounds"])
        self.assertEqual("1", summary[("g3_audited", "paired")]["execution_failures"])

    def test_dependency_skip_is_excluded_from_paired_method_statistics(self):
        records = [{**self._record("paired", 1, 1, "", "skipped_dependency", False), "pair_valid": False}]
        with tempfile.TemporaryDirectory() as temp:
            runner.write_reports(pathlib.Path(temp), records)
            with (pathlib.Path(temp) / "summary.csv").open(encoding="utf-8-sig") as handle:
                self.assertEqual([], list(csv.DictReader(handle)))

    def test_pair_reset_removes_only_confirmed_child_contents(self):
        with tempfile.TemporaryDirectory() as temp:
            root, child = pathlib.Path(temp), pathlib.Path(temp) / "pair-work" / "case-r01"
            child.mkdir(parents=True)
            (child / "old.txt").write_text("old", encoding="utf-8")
            runner.reset_pair_workspace(root, child)
            self.assertTrue(child.is_dir())
            self.assertEqual([], list(child.iterdir()))

    @unittest.skipUnless(os.name == "nt", "Windows directory-handle semantics")
    def test_pair_output_relocation_keeps_the_logical_workspace_path_reusable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "pair-work" / "CASE-r01"
            target = root / "g2_constrained" / "CASE" / "rep-01"
            source.mkdir(parents=True)
            output = source / "result.shp"
            output.write_bytes(b"result")

            with output.open("rb") as live_handle:
                with self.assertRaises(PermissionError):
                    runner.relocate_pair_workspace(root, source, target)
                self.assertEqual(b"result", live_handle.read())

            runner.relocate_pair_workspace(root, source, target)

            self.assertFalse(source.exists())
            self.assertEqual(b"result", (target / "result.shp").read_bytes())
            source.mkdir()
            self.assertTrue(source.is_dir())
            self.assertTrue((root / "pair-work").exists())

    def test_artifact_extraction_accepts_failed_run_with_frozen_artifact(self):
        artifact = {"artifact_hash": "a"}
        row = {"status": "failed", "agent_trace": [{"run": {"plan_artifact": artifact, "plan_artifact_hash": "a"}}]}
        self.assertEqual(artifact, runner._artifact_from_run(row))

    def _stalled_g3_row(self):
        primary = PlanningClient(
            "primary",
            "planner",
            [
                {"task_contract": TASK_CONTRACT},
                {"workflow_draft": INVALID_WORKFLOW},
                {"workflow_draft": INVALID_WORKFLOW},
            ],
        )
        auditor = PlanningClient("auditor", "auditor", [])
        config = {
            "primary_provider": "primary",
            "primary_model": "planner",
        }

        clients = iter((primary, auditor))

        def create_client(provider, model):
            return next(clients)

        with tempfile.TemporaryDirectory() as temp:
            with patch(
                "gateway_py3.llm_providers.load_config",
                return_value=config,
            ):
                store = RunStore(pathlib.Path(temp) / "runs.sqlite")
                planning_engine = PlanningEngine(
                    OperationCatalog(),
                    store,
                    create_client,
                )
                run = store.create_run("refresh", "g3_audited")
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
                return planning_engine.plan(
                    run["id"],
                    "refresh",
                    PLANNING_CONTEXT,
                    "g3_audited",
                )

    def test_build_manifest_records_server_policy_without_rehashing(self):
        args = Namespace(
            modes=["g3_audited"],
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
        }
        with patch.object(runner, "repository_state", return_value=("head", True, "fingerprint")) as git_state:
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
        self.assertEqual(manifest["source_fingerprint"], "fingerprint")
        self.assertEqual(manifest["timeout_seconds"], 600)
        self.assertEqual(manifest["primary"], {"provider": "p", "model": "pm"})
        self.assertEqual(
            manifest["planning_policy"]["workflow_protocol"]["version"],
            workflow_protocol()["version"],
        )

    def test_dataset_manifest_hashes_are_verified_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            dataset = pathlib.Path(temp)
            source = dataset / "source.txt"
            source.write_text("frozen", encoding="utf-8")
            manifest = {
                "files": [
                    {
                        "path": "source.txt",
                        "bytes": source.stat().st_size,
                        "sha256": runner.sha256_file(source),
                    }
                ]
            }
            runner.verify_dataset_files(dataset, manifest)
            source.write_text("broken", encoding="utf-8")

            with self.assertRaisesRegex(runner.ExperimentError, "hash mismatch"):
                runner.verify_dataset_files(dataset, manifest)

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
            "mode": "g3_audited",
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
