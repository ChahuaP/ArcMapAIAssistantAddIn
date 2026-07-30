import importlib.util
import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "experiments" / "synthetic_city" / "run_formal_experiments.py"
SPEC = importlib.util.spec_from_file_location("formal_experiment_runner", MODULE_PATH)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class FormalExperimentRunnerTests(unittest.TestCase):
    def setUp(self):
        self.dataset = ROOT / "out" / "synthetic-city-v1"
        self.load_order, self.cases, self.truth = runner.validate_dataset(self.dataset)

    def test_reset_contract_requires_exact_source_state(self):
        command = runner.reset_command(self.load_order)

        self.assertIn("layer.clear_layers", command)
        self.assertIn("context.list_layers", command)
        self.assertEqual(len(runner.source_layer_names(self.load_order)), 14)

    def test_task_command_makes_outputs_and_g0_boundary_explicit(self):
        round_spec = self.cases["cases"][2]["rounds"][0]
        catalog = runner.direct_static_catalog(self.dataset)
        command = runner.task_command(round_spec, ROOT / "out" / "formal-test", "direct_single", catalog)

        self.assertIn("suspect_projects.shp", command)
        self.assertIn("construction(建设项目)", command)
        self.assertIn("不得调用 context.*", command)

    def test_land_continuous_outputs_score_against_truth_exactly(self):
        outputs = ROOT / "out" / "experiment-preflight" / "multi_agent" / "land_continuous"
        rounds = self.cases["cases"][2]["rounds"]

        scores = [runner.score_round(round_spec, outputs, self.truth) for round_spec in rounds]

        self.assertTrue(all(score["ok"] for score in scores))


if __name__ == "__main__":
    unittest.main()
