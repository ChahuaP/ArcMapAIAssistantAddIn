from __future__ import annotations

import json
import tempfile
import argparse
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.supervisor import CampaignError, _save_cell, _score_run, campaign_report, explicit_target, main, run, verify_dataset
from gateway_py3.kernel.contracts import TargetSelector


class RuntimeGateTest(unittest.TestCase):
    @property
    def dataset(self):
        return Path(__file__).resolve().parents[2] / "experiments" / "data" / "synthetic-city-formal-20260910"

    def test_verified_dataset_contains_exactly_fourteen_loadable_sources(self):
        document = verify_dataset(self.dataset, ("FLOOD_RESPONSE", "LAND_COMPLIANCE"), 20260910)
        self.assertEqual(14, len(document["source_layers"]))

    def test_manifest_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = {"seed": 20260910, "rounds_per_case": 3,
                        "source_layers": {}, "files": [{"path": "x", "bytes": 1, "sha256": "0" * 64}]}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "task_cases.json").write_text('{"cases": []}', encoding="utf-8")
            (root / "truth").mkdir(); (root / "truth" / "expected_ids.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(CampaignError): verify_dataset(root, ("FLOOD_RESPONSE", "LAND_COMPLIANCE"), 20260910)

    def test_target_never_auto_selects(self):
        with self.assertRaises(CampaignError): explicit_target(None, None)
        with patch("experiments.supervisor.list_bridge_targets", return_value=[]):
            with self.assertRaises(CampaignError): explicit_target(1, 2)

    def test_campaign_valid_requires_all_twelve_rounds_and_six_pairs(self):
        cells = [{"case_id": case, "round": round_no, "arm": arm, "state": "round_valid", "score": 1.0, "pair_contract_valid": True}
                 for case in ("FLOOD_RESPONSE", "LAND_COMPLIANCE") for round_no in (1, 2, 3) for arm in ("g2", "g3")]
        report = campaign_report({"status": "completed_valid", "cells": cells})
        self.assertTrue(report["campaign_valid"])
        self.assertTrue(report["evidence_valid"])
        self.assertTrue(report["gate_passed"])
        cells[0]["state"] = "not_evaluable"
        self.assertFalse(campaign_report({"status": "completed_valid", "cells": cells})["campaign_valid"])

    def test_aggregate_scores_are_reported_separately_from_accuracy(self):
        cells = [{"case_id": case, "round": round_no, "arm": arm, "state": "round_valid", "score": 1.0, "pair_contract_valid": True}
                 for case in ("FLOOD_RESPONSE", "LAND_COMPLIANCE") for round_no in (1, 2, 3) for arm in ("g2", "g3")]
        state = {"status": "completed_valid", "cells": cells}
        export = {"acceptance_report": {"passed": True}, "artifacts": [
            {"output_id": "answer", "evidence_hash": "a" * 64}]}
        with tempfile.TemporaryDirectory() as temporary:
            def save(case, round_no, arm, observed):
                evidence = {"truth_ids": {"answer": observed},
                            "artifact_manifests": {"answer": {"passed": True}},
                            "evidence_hash": "e" * 64}
                _save_cell(state, Path(temporary) / "state.json", case, round_no, arm,
                           {"pair_valid": True, "pair_id": "pair", "%s_run_id" % arm: "run",
                            "%s_outcome" % arm: {"kind": "Succeeded"}},
                           export, ("answer",), {"answer": ["correct"]}, evidence,
                           {"answer": {"field": "ID", "ids": ["correct"]}})
            save("FLOOD_RESPONSE", 1, "g2", ["wrong"])
            save("FLOOD_RESPONSE", 2, "g3", ["wrong"])
        report = campaign_report(state)
        self.assertEqual(5.0, report["aggregate_g2_score"])
        self.assertEqual(5.0, report["aggregate_g3_score"])
        self.assertEqual(5.0 / 6.0, report["accuracy"]["g2"])
        self.assertEqual(5.0 / 6.0, report["accuracy"]["g3"])
        self.assertTrue(report["campaign_valid"])
        self.assertTrue(report["gate_passed"])

    def test_gate_requires_non_regressing_aggregate_evidence(self):
        cells = [{"case_id": case, "round": round_no, "arm": arm, "state": "round_valid",
                  "score": 0.0, "pair_contract_valid": True}
                 for case in ("FLOOD_RESPONSE", "LAND_COMPLIANCE")
                 for round_no in (1, 2, 3) for arm in ("g2", "g3")]
        state = {"status": "completed_valid", "cells": cells}
        tied_wrong = campaign_report(state)
        self.assertTrue(tied_wrong["campaign_valid"])
        self.assertTrue(tied_wrong["gate_passed"])
        next(cell for cell in cells if cell["case_id"] == "FLOOD_RESPONSE" and
             cell["round"] == 1 and cell["arm"] == "g3")["score"] = 1.0
        advantage = campaign_report(state)
        self.assertEqual(0.0, advantage["aggregate_g2_score"])
        self.assertEqual(1.0, advantage["aggregate_g3_score"])
        self.assertTrue(advantage["gate_passed"])

    def test_cli_exit_zero_requires_gate_passed(self):
        argv = ["--provider", "minimax", "--model", "MiniMax-M3", "--dataset", str(self.dataset),
                "--output", str(self.dataset.parent / "unused"), "--seed", "20260910",
                "--repetition", "1", "--case", "FLOOD_RESPONSE", "--case", "LAND_COMPLIANCE",
                "--arcmap-pid", "1", "--hwnd", "2"]
        with patch("experiments.supervisor.run", return_value={"campaign_valid": True, "gate_passed": False}):
            self.assertEqual(3, main(argv))
        with patch("experiments.supervisor.run", return_value={"campaign_valid": True, "gate_passed": True}):
            self.assertEqual(0, main(argv))

    def test_same_declared_output_with_wrong_truth_ids_is_rejected(self):
        export = {"acceptance_report": {"passed": True}, "artifacts": [
            {"output_id": "affected_comm", "evidence_hash": "a" * 64},
        ]}
        score, _ = _score_run(export, ("affected_comm",), {"affected_comm": ["C1", "C2"]},
                              {"truth_ids": {"affected_comm": ["C1", "C3"]},
                               "artifact_manifests": {"affected_comm": {"passed": True}},
                               "evidence_hash": "e" * 64})
        self.assertEqual(0.0, score)

    def test_pair_contract_failure_is_never_pair_valid(self):
        cells = [{"case_id": case, "round": round_no, "arm": arm, "state": "round_valid",
                  "score": 1.0, "pair_contract_valid": not (case == "FLOOD_RESPONSE" and round_no == 1)}
                 for case in ("FLOOD_RESPONSE", "LAND_COMPLIANCE")
                 for round_no in (1, 2, 3) for arm in ("g2", "g3")]
        report = campaign_report({"status": "completed_valid", "cells": cells})
        self.assertFalse(report["campaign_valid"])
        self.assertEqual(5, report["pairs_valid"])

    def test_root_supervisor_stops_when_pair_runner_returns_false(self):
        class Lifecycle:
            def prepare(self, *args): return "a" * 64
            def restore_round(self, selector, initial, artifacts): return ("c" if artifacts else "a") * 64
            def evaluate(self, selector, artifacts, expected):
                return {"truth_ids": {key: value["ids"] for key, value in expected.items()},
                        "artifact_manifests": {key: {"manifest_digest": key} for key in artifacts},
                        "evidence_hash": "b" * 64}
        class Kernel:
            def __init__(self): self.exports = {}
            def export_run_journal(self, run_id): return self.exports[run_id]
        class Supervisor:
            def __init__(self, kernel): self.kernel = kernel
            def run_pair(self, task, seed, **kwargs):
                result = {"pair_valid": False, "pair_id": "pair"}
                for arm in ("g2", "g3"):
                    run_id = "pair-" + arm
                    result["%s_run_id" % arm] = run_id
                    result["%s_outcome" % arm] = {"kind": "Succeeded"}
                    self.kernel.exports[run_id] = {"acceptance_report": {"passed": True}, "artifacts": [
                        {"output_id": output, "evidence_hash": "a" * 64} for output in kwargs["outputs"]]}
                return result
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"
            kernel = Kernel(); supervisor = Supervisor(kernel)
            args = argparse.Namespace(provider="minimax", model="MiniMax-M3", dataset=self.dataset,
                                      output=output, seed=20260910, repetition=1,
                                      case=["FLOOD_RESPONSE", "LAND_COMPLIANCE"], arcmap_pid=1, hwnd=2)
            selector = TargetSelector(bridge_pid=3, bridge_port=4, arcmap_pid=1, hwnd=2,
                                      deployment_hash="a" * 64)
            with patch("experiments.supervisor.explicit_target", return_value=selector):
                with self.assertRaisesRegex(CampaignError, "fairness contract"):
                    run(args, Lifecycle(), lambda root: (kernel, supervisor))
            state = json.loads((output / "campaign_state.json").read_text(encoding="utf-8"))
            self.assertEqual("failed", state["status"])
            self.assertFalse(any(cell.get("pair_contract_valid") for cell in state["cells"]))

    def test_quota_checkpoint_resumes_only_unfinished_g3_arm(self):
        class Lifecycle:
            def __init__(self): self.empty_evaluations = 0
            def prepare(self, *args): return "a" * 64
            def restore_round(self, selector, initial, artifacts): return ("c" if artifacts else "a") * 64
            def evaluate(self, selector, artifacts, expected):
                if not artifacts:
                    self.empty_evaluations += 1
                    raise RuntimeError("production evaluator rejects an empty ArtifactManifest")
                return {"truth_ids": {key: value["ids"] for key, value in expected.items()},
                        "artifact_manifests": {key: {"manifest_digest": key} for key in artifacts},
                        "evidence_hash": "b" * 64}
        class Kernel:
            def __init__(self): self.exports = {}
            def export_run_journal(self, run_id): return self.exports[run_id]
        class Supervisor:
            def __init__(self, kernel): self.kernel = kernel; self.calls = []; self.stopped = False
            def run_pair(self, task, seed, **kwargs):
                self.calls.append(kwargs.get("resume_runs"))
                pair = kwargs.get("pair_id") or "pair-" + str(len(self.calls))
                result = {"pair_valid": True, "pair_id": pair}
                for arm in ("g2", "g3"):
                    run_id = "%s-%s" % (pair, arm); result["%s_run_id" % arm] = run_id
                    quota = arm == "g3" and not self.stopped
                    result["%s_outcome" % arm] = {"kind": "QuotaStopped"} if quota else {"kind": "Succeeded"}
                    outputs = kwargs["outputs"]
                    self.kernel.exports[run_id] = {"acceptance_report": {"passed": not quota}, "artifacts": [
                        {"output_id": output, "evidence_hash": "a" * 64} for output in outputs]}
                self.stopped = True
                return result
        with tempfile.TemporaryDirectory() as temporary:
            kernel = Kernel(); supervisor = Supervisor(kernel)
            lifecycle = Lifecycle()
            args = argparse.Namespace(provider="minimax", model="MiniMax-M3", dataset=self.dataset,
                                      output=Path(temporary) / "out", seed=20260910, repetition=1,
                                      case=["FLOOD_RESPONSE", "LAND_COMPLIANCE"], arcmap_pid=1, hwnd=2)
            selector = TargetSelector(bridge_pid=3, bridge_port=4, arcmap_pid=1, hwnd=2, deployment_hash="a" * 64)
            with patch("experiments.supervisor.explicit_target", return_value=selector):
                first = run(args, lifecycle, lambda output: (kernel, supervisor))
                self.assertEqual("quota_stopped", first["status"])
                self.assertEqual(0, lifecycle.empty_evaluations)
                second = run(args, lifecycle, lambda output: (kernel, supervisor))
            self.assertTrue(second["campaign_valid"])
            self.assertIsNone(supervisor.calls[0])
            self.assertEqual({"g2", "g3"}, set(supervisor.calls[1]))

    def test_quota_checkpoint_resumes_the_same_unfinished_g2_run(self):
        class Lifecycle:
            def prepare(self, *args): return "a" * 64
            def restore_round(self, selector, initial, artifacts): return ("c" if artifacts else "a") * 64
            def evaluate(self, selector, artifacts, expected):
                return {"truth_ids": {key: value["ids"] for key, value in expected.items()},
                        "artifact_manifests": {key: {"manifest_digest": key} for key in artifacts},
                        "evidence_hash": "b" * 64}
        class Kernel:
            def __init__(self): self.exports = {}
            def export_run_journal(self, run_id): return self.exports[run_id]
        class Supervisor:
            def __init__(self, kernel): self.kernel = kernel; self.calls = []; self.first = True
            def run_pair(self, task, seed, **kwargs):
                self.calls.append(kwargs.get("resume_runs"))
                pair = kwargs.get("pair_id") or "pair-1"
                if self.first:
                    self.first = False
                    run_id = "pair-1-g2"
                    self.kernel.exports[run_id] = {}
                    return {"pair_valid": False, "pair_id": pair, "g2_run_id": run_id,
                            "g2_outcome": {"kind": "QuotaStopped"},
                            "g3_run_id": None, "g3_outcome": None}
                result = {"pair_valid": True, "pair_id": pair}
                for arm in ("g2", "g3"):
                    run_id = "pair-1-g2" if arm == "g2" and kwargs.get("resume_runs") else "%s-%s-%d" % (pair, arm, len(self.calls))
                    result["%s_run_id" % arm] = run_id
                    result["%s_outcome" % arm] = {"kind": "Succeeded"}
                    self.kernel.exports[run_id] = {"acceptance_report": {"passed": True}, "artifacts": [
                        {"output_id": output, "evidence_hash": "a" * 64} for output in kwargs["outputs"]]}
                return result
        with tempfile.TemporaryDirectory() as temporary:
            kernel = Kernel(); supervisor = Supervisor(kernel)
            args = argparse.Namespace(provider="minimax", model="MiniMax-M3", dataset=self.dataset,
                                      output=Path(temporary) / "out", seed=20260910, repetition=1,
                                      case=["FLOOD_RESPONSE", "LAND_COMPLIANCE"], arcmap_pid=1, hwnd=2)
            selector = TargetSelector(bridge_pid=3, bridge_port=4, arcmap_pid=1, hwnd=2,
                                      deployment_hash="a" * 64)
            with patch("experiments.supervisor.explicit_target", return_value=selector):
                self.assertEqual("quota_stopped", run(args, Lifecycle(), lambda output: (kernel, supervisor))["status"])
                report = run(args, Lifecycle(), lambda output: (kernel, supervisor))
            self.assertTrue(report["campaign_valid"])
            self.assertEqual({"g2": "pair-1-g2"}, supervisor.calls[1])

    def test_nonquota_checkpoint_is_rejected_before_runner_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "out"; output.mkdir()
            (output / "campaign_state.json").write_text('{"status":"failed"}', encoding="utf-8")
            args = argparse.Namespace(provider="minimax", model="MiniMax-M3", dataset=self.dataset,
                                      output=output, seed=20260910, repetition=1,
                                      case=["FLOOD_RESPONSE", "LAND_COMPLIANCE"], arcmap_pid=1, hwnd=2)
            selector = TargetSelector(bridge_pid=3, bridge_port=4, arcmap_pid=1, hwnd=2, deployment_hash="a" * 64)
            with patch("experiments.supervisor.explicit_target", return_value=selector):
                with self.assertRaises(CampaignError):
                    run(args, runner_factory=lambda unused: self.fail("runner must not be constructed"))

    def test_runtime_gate_lifecycle_is_fenced_end_to_end(self):
        root = Path(__file__).resolve().parents[2]
        bridge = (root / "ArcMapBridgeExternal" / "Program.cs").read_text(encoding="utf-8")
        runtime = (root / "arcmap_runtime_py2" / "runtime.py").read_text(encoding="utf-8")
        lifecycle = (root / "arcmap_runtime_py2" / "runtime_gate.py").read_text(encoding="utf-8")
        self.assertIn('"/runtime-gate"', bridge)
        for required in ("bridgePid != CurrentProcessId()", "arcmapPid != ArcMapProcessId(hwnd)",
                         "lifecycleId", "ReadRuntimeGateResult"):
            self.assertIn(required, bridge)
        self.assertIn('action == "runtime_gate"', runtime)
        self.assertIn("def _prepare", lifecycle)
        self.assertIn("def _restore", lifecycle)
        self.assertIn("context_hash", lifecycle)

    def test_removed_superseded_formal_path_cannot_reenter_experiments(self):
        root = Path(__file__).resolve().parents[2]
        sources = [root / "gateway_py3" / "experiments" / "supervisor.py",
                   root / "gateway_py3" / "kernel" / "contracts.py",
                   root / "experiments" / "supervisor" / "__init__.py"]
        legacy_name = "planning" + "_gate"
        self.assertFalse(any(legacy_name in path.read_text(encoding="utf-8") for path in sources))
