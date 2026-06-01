import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "agent_integrations" / "geopilot-arcmap" / "scripts" / "geopilot_cli.py"


class GeoPilotCliTests(unittest.TestCase):
    def test_latest_draft_workflow_id_picks_first_draft(self):
        cli = _load_cli()
        workflows = [
            {"id": "done", "status": "succeeded"},
            {"id": "draft-1", "status": "draft"},
            {"id": "draft-2", "status": "draft"},
        ]

        self.assertEqual(cli._latest_draft_workflow_id(workflows), "draft-1")

    def test_latest_draft_workflow_id_returns_empty_when_missing(self):
        cli = _load_cli()

        self.assertEqual(cli._latest_draft_workflow_id([{"id": "done", "status": "succeeded"}]), "")

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


def _load_cli():
    spec = importlib.util.spec_from_file_location("geopilot_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
