import importlib.util
import pathlib
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "agent_integrations" / "geopilot-arcmap" / "scripts" / "geopilot_cli.py"


class GeoPilotCliTests(unittest.TestCase):
    def test_cli_exposes_arcmap_selection_commands(self):
        text = CLI_PATH.read_text(encoding="utf-8")

        self.assertIn("arcmap-list", text)
        self.assertIn("arcmap-select", text)
        self.assertIn("doctor", text)
        self.assertIn("/arcmap/bridges", text)
        self.assertIn("/arcmap/active", text)
        self.assertIn("/agent/diagnostics", text)

    def test_cli_can_request_detailed_capability_schema(self):
        text = CLI_PATH.read_text(encoding="utf-8")

        self.assertIn('"--detail"', text)
        self.assertIn("/api/capabilities?detail=1", text)

    def test_run_omits_unselected_provider_and_model(self):
        cli = _load_cli()
        captured = {}

        def post(base_url, path, payload):
            captured["base_url"] = base_url
            captured["path"] = path
            captured["payload"] = payload
            return {"ok": True}

        with mock.patch.object(
            cli.sys,
            "argv",
            ["geopilot_cli.py", "run", "--mode", "context_single", "--command", "refresh"],
        ):
            with mock.patch.object(cli, "_post", side_effect=post):
                with mock.patch.object(cli, "_print", return_value=0):
                    self.assertEqual(cli.main(), 0)

        self.assertEqual(captured["path"], "/runs")
        self.assertNotIn("provider", captured["payload"])
        self.assertNotIn("model", captured["payload"])


def _load_cli():
    spec = importlib.util.spec_from_file_location("geopilot_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
