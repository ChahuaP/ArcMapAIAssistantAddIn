import importlib.util
import pathlib
import unittest
from types import SimpleNamespace
from unittest import mock

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.planning_engine import digest, planning_policy
from gateway_py3.routes import handle_get
from gateway_py3.workflow_protocol import WORKFLOW_PROTOCOL_VERSION, workflow_protocol


ROOT = pathlib.Path(__file__).resolve().parents[2]
CLI_PATH = ROOT / "agent_integrations" / "geopilot-arcmap" / "scripts" / "geopilot_cli.py"


class GeoPilotCliTests(unittest.TestCase):
    def test_capabilities_exposes_authoritative_planning_policy(self):
        catalog = OperationCatalog()
        payload = handle_get(
            SimpleNamespace(catalog=catalog),
            "/api/capabilities",
            "test",
            {"detail": ["1"]},
        )
        policy = planning_policy(catalog)
        self.assertEqual(payload["workflow_protocol"], workflow_protocol())
        self.assertEqual(payload["planning_policy"], policy)
        self.assertEqual(policy["validation_revisions"], 3)
        self.assertEqual(policy["audit_revisions"], 3)
        self.assertEqual(policy["response_contract_revisions"], 2)
        self.assertEqual(payload["workflow_protocol"]["version"], WORKFLOW_PROTOCOL_VERSION)
        self.assertEqual(policy["protocol_hash"], digest(workflow_protocol()))

    def test_capability_card_limits_examples_and_is_formal_contract(self):
        catalog = OperationCatalog()
        operation = dict(catalog.get("selection.export_selected_features"))
        operation["examples"] = [{"index": index} for index in range(3)]

        card = catalog.planning_card(operation)

        self.assertEqual(card["examples"], [{"index": 0}, {"index": 1}])
        self.assertEqual(card["parameters_schema"], operation["parameters_schema"])
        self.assertEqual(
            card["outputs"],
            catalog.capabilities.get(operation["id"])["outputs"],
        )
        self.assertEqual(
            {"rule": "from_parameter", "parameter": "output_format", "default": "gdb"},
            card["outputs"]["format"],
        )
        self.assertNotIn("legacy_card", card)

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
            ["geopilot_cli.py", "run", "--mode", "g1_context", "--command", "refresh"],
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
