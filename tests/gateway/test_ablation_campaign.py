import importlib.util
import json
import pathlib
import tempfile
import unittest
from argparse import Namespace
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments" / "synthetic_city" / "run_ablation_campaign.py"
SPEC = importlib.util.spec_from_file_location("ablation_campaign", MODULE_PATH)
campaign = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(campaign)


class AblationCampaignTests(unittest.TestCase):
    def test_bridge_source_identity_must_match_the_running_experiment_repository(self):
        expected = campaign.hashlib.sha256(campaign.BRIDGE_SOURCE.read_bytes()).hexdigest()
        campaign.validate_bridge_source_identity({"summary": {"source_sha256": expected}})

        with self.assertRaisesRegex(campaign.CampaignError, "source identity"):
            campaign.validate_bridge_source_identity({"summary": {"source_sha256": "0" * 64}})

    def test_installed_execution_assets_must_match_runtime_and_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            runtime_source = root / "source" / "arcmap_runtime_py2"
            catalog_source = root / "source" / "operation_catalog"
            installed = root / "installed"
            (runtime_source / "operations").mkdir(parents=True)
            catalog_source.mkdir(parents=True)
            (installed / "arcmap_runtime_py2" / "operations").mkdir(parents=True)
            (installed / "operation_catalog").mkdir(parents=True)
            (runtime_source / "runtime.py").write_text("runtime", encoding="utf-8")
            (runtime_source / "operations" / "tool.py").write_text("tool", encoding="utf-8")
            (catalog_source / "catalog.json").write_text("{}", encoding="utf-8")
            (installed / "arcmap_runtime_py2" / "runtime.py").write_text("runtime", encoding="utf-8")
            (installed / "arcmap_runtime_py2" / "operations" / "tool.py").write_text("tool", encoding="utf-8")
            (installed / "operation_catalog" / "catalog.json").write_text("{}", encoding="utf-8")

            identity = campaign.validate_execution_deployment_identity(
                runtime_source, catalog_source, installed,
            )

            self.assertEqual(identity["runtime"]["source_sha256"], identity["runtime"]["installed_sha256"])
            self.assertEqual(identity["catalog"]["source_sha256"], identity["catalog"]["installed_sha256"])
            self.assertEqual(2, identity["runtime"]["file_count"])
            self.assertEqual(1, identity["catalog"]["file_count"])
            (installed / "operation_catalog" / "catalog.json").write_text("stale", encoding="utf-8")
            with self.assertRaisesRegex(campaign.CampaignError, "operation catalog identity"):
                campaign.validate_execution_deployment_identity(
                    runtime_source, catalog_source, installed,
                )

    def test_arcmap_window_geometry_is_frozen_before_context_capture(self):
        calls = []

        class User32:
            moved = False

            def ShowWindow(self, hwnd, command):
                calls.append(("show", hwnd, command))
                return 1

            def MoveWindow(self, hwnd, x, y, width, height, repaint):
                calls.append(("move", hwnd, x, y, width, height, repaint))
                self.moved = True
                self.outer = (width, height)
                return 1

            def GetWindowRect(self, hwnd, pointer):
                rectangle = campaign.ctypes.cast(
                    pointer, campaign.ctypes.POINTER(campaign.wintypes.RECT)).contents
                rectangle.left = 0
                rectangle.top = 0
                rectangle.right, rectangle.bottom = getattr(self, "outer", (1940, 1100))
                return 1

            def GetClientRect(self, hwnd, pointer):
                rectangle = campaign.ctypes.cast(
                    pointer, campaign.ctypes.POINTER(campaign.wintypes.RECT)).contents
                rectangle.left = 0
                rectangle.top = 0
                if self.moved:
                    rectangle.right = campaign.ARCMAP_CLIENT_WIDTH
                    rectangle.bottom = campaign.ARCMAP_CLIENT_HEIGHT
                else:
                    rectangle.right = 1924
                    rectangle.bottom = 1061
                return 1

        geometry = campaign.normalize_arcmap_window(123, User32(), settle=lambda _: None)

        self.assertEqual(
            {"client_width": 1600, "client_height": 900,
             "outer_width": 1616, "outer_height": 939}, geometry)
        self.assertEqual(("show", 123, 9), calls[0])
        self.assertEqual(("move", 123, 0, 0, 1616, 939, True), calls[1])

    def test_method_order_keeps_g2_g3_as_one_paired_execution_unit(self):
        modes = ("g0_direct", "g1_context", "g2_constrained", "g3_audited")
        first = campaign.build_method_order(
            seeds=(11, 12, 13, 14),
            repetitions=3,
            modes=modes,
            order_seed=99,
        )
        second = campaign.build_method_order(
            seeds=(11, 12, 13, 14),
            repetitions=3,
            modes=modes,
            order_seed=99,
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 4 * 3 * 3)
        identities = {
            (item["seed"], item["repetition"], item["mode"])
            for item in first
        }
        self.assertEqual(len(identities), len(first))
        self.assertEqual(
            {item["mode"] for item in first},
            {"g0_direct", "g1_context", "g2_g3_paired"},
        )
        self.assertEqual(
            campaign.runner_modes_for("g2_g3_paired"),
            ("g2_constrained", "g3_audited"),
        )
        first_positions = {mode: 0 for mode in ("g0_direct", "g1_context", "g2_g3_paired")}
        for item in first:
            if item["position"] == 1:
                first_positions[item["mode"]] += 1
        self.assertEqual(set(first_positions.values()), {4})

    def test_paired_cell_uses_two_fresh_runtime_phases_before_finalization(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            args = Namespace(
                output=root, datasets_root=root, dataset_template="dataset-{seed}",
                python=pathlib.Path("python.exe"), runner=pathlib.Path("runner.py"),
                gateway="http://127.0.0.1:8765", timeout=1, case_ids=[], rounds=[],
                replay_baseline_record=None, paired_strategy="production",
                code_version="head", source_fingerprint="fingerprint", dirty=True,
                execution_deployment={"runtime": {"source_sha256": "runtime"},
                                      "catalog": {"source_sha256": "catalog"}},
                provider="qwen", model="qwen3.6-flash-2026-04-16",
            )
            item = {"cell_id": "pair", "mode": campaign.PAIRED_EXECUTION_UNIT, "seed": 7, "repetition": 1}
            phases = []

            def execute_phase(_args, _run_dir, _log_dir, command, phase):
                phases.append((phase, command))
                return {"status": "completed", "runtime_identity": {"phase": phase}, "log_dir": str(_log_dir)}

            with patch.object(campaign, "_execute_runtime_phase", side_effect=execute_phase), \
                 patch.object(campaign, "finalize_paired_outputs") as finalize, \
                 patch.object(campaign, "installed_execution_deployment_identity",
                              return_value=args.execution_deployment):
                result = campaign.execute_cell(args, item, 1)

            self.assertEqual([item[0] for item in phases], ["g2", "g3"])
            self.assertEqual("g2", phases[0][1][phases[0][1].index("--paired-phase") + 1])
            self.assertEqual("g3", phases[1][1][phases[1][1].index("--paired-phase") + 1])
            self.assertEqual("production", phases[0][1][phases[0][1].index("--paired-strategy") + 1])
            self.assertEqual("qwen", phases[0][1][phases[0][1].index("--provider") + 1])
            self.assertEqual("qwen3.6-flash-2026-04-16", phases[0][1][phases[0][1].index("--model") + 1])
            finalize.assert_called_once()
            self.assertEqual("completed", result["status"])

    def test_finalize_paired_outputs_publishes_only_declared_unlocked_workspaces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source = root / "pair-work" / "CASE-r01"
            source.mkdir(parents=True)
            (source / "result.shp").write_bytes(b"result")
            (root / "paired_g3_complete.json").write_text(
                json.dumps({
                    "schema": "geopilot-paired-g3-complete", "version": 1,
                    "pairs": [{"pair_id": "CASE-r01", "case_id": "CASE", "repetition": 1}],
                }),
                encoding="utf-8",
            )

            campaign.finalize_paired_outputs(root)

            self.assertEqual(b"result", (root / "g3_audited" / "CASE" / "rep-01" / "result.shp").read_bytes())
            self.assertFalse((root / "pair-work").exists())
            self.assertTrue((root / "paired_complete.json").is_file())

    def test_runtime_startup_failure_is_excluded_for_clean_session_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            args = Namespace(gateway="http://127.0.0.1:8765", session_timeout=1)
            with patch.object(campaign, "start_runtime", side_effect=campaign.CampaignError("Bridge startup timed out")), \
                 patch.object(campaign, "cleanup_runtime"):
                result = campaign._execute_runtime_phase(
                    args, root / "run", root / "logs", ["runner"], "g3",
                )

            self.assertEqual("infrastructure_excluded", result["status"])
            self.assertIn("Bridge startup", result["error"])

    def test_model_quota_stop_is_not_classified_for_infrastructure_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            run_dir = root / "run"
            log_dir = root / "logs"
            run_dir.mkdir()
            log_dir.mkdir()
            args = Namespace(gateway="http://127.0.0.1:8765", session_timeout=1)

            def runner_call(*_args, **_kwargs):
                (run_dir / "model_quota_stop.json").write_text(
                    json.dumps({"run_id": "quota-run", "marker": "quota exhausted"}),
                    encoding="utf-8",
                )
                return type("Completed", (), {"returncode": 1})()

            with patch.object(campaign, "start_runtime", return_value=({"gateway_pid": 1}, [])), \
                 patch.object(campaign.subprocess, "run", side_effect=runner_call), \
                 patch.object(campaign, "cleanup_runtime"), \
                 patch.object(campaign, "copy_session_evidence"):
                result = campaign._execute_runtime_phase(
                    args, run_dir, log_dir, ["runner"], "g2",
                )

            self.assertEqual("quota_exhausted", result["status"])


if __name__ == "__main__":
    unittest.main()
