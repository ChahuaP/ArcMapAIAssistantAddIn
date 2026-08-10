import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
ADDIN_PATH = ROOT / "ArcMapAIAssistantAddIn" / "Install" / "ArcMapAIAssistant_addin.py"
RUNTIME_PATH = ROOT / "arcmap_runtime_py2" / "runtime.py"
BRIDGE_PATH = ROOT / "ArcMapBridgeExternal" / "Program.cs"


class AddInRuntimeLifecycleTests(unittest.TestCase):
    def test_button_reuses_one_runtime_instance_for_process_lifetime(self):
        pythonaddins = types.ModuleType("pythonaddins")
        pythonaddins.MessageBox = mock.Mock()
        with mock.patch.dict(sys.modules, {"pythonaddins": pythonaddins}):
            spec = importlib.util.spec_from_file_location("addin_runtime_lifecycle", ADDIN_PATH)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

        runtime = mock.Mock()
        module.load_runtime_module = mock.Mock(return_value=runtime)
        button = module.OpenAssistantButton()
        button.onClick()
        button.onClick()

        module.load_runtime_module.assert_called_once_with()
        self.assertEqual(runtime.open_or_handle_bridge_command.call_count, 2)

    def test_runtime_does_not_hot_reload_arcmap_modules(self):
        source = RUNTIME_PATH.read_text(encoding="utf-8")
        self.assertNotIn("reload(", source)

    def test_open_assistant_starts_bridge_before_reading_arcmap_context(self):
        source = RUNTIME_PATH.read_text(encoding="utf-8")
        body = source.split("def open_assistant():", 1)[1].split(
            "def _run_silent_command", 1
        )[0]
        gateway = body.index("gateway_client.ensure_running()")
        bridge = body.index("bridge_process.ensure_running(BRIDGE_EXE)")
        context = body.index("_sync_current_context()")
        self.assertLess(gateway, bridge)
        self.assertLess(bridge, context)

    def test_bridge_uses_ready_file_discovery_without_registration_side_channel(self):
        source = (ROOT / "ArcMapBridgeExternal/Program.cs").read_text(encoding="utf-8")
        self.assertNotIn("RegisterWithGateway", source)
        self.assertNotIn('"/arcmap/register"', source)

    def test_bridge_ready_file_is_written_as_strict_utf8_without_bom(self):
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        writer = source.split("private void WriteReadyFile()", 1)[1].split(
            "private static void DeleteReadyFile", 1
        )[0]
        self.assertIn("Utf8NoBom", writer)
        self.assertNotIn("Encoding.UTF8", writer)

    def test_bridge_liveness_is_independent_of_arcmap_com_discovery(self):
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        start = source.split("public void Start()", 1)[1].split(
            "public void Run()", 1
        )[0]
        health = source.split("private string HealthJson()", 1)[1].split(
            "private string TargetsJson()", 1
        )[0]

        self.assertNotIn("RefreshArcMapPresence();", start)
        self.assertLess(start.index("_listenerThread.Start();"), start.index("WriteReadyFile();"))
        self.assertNotIn("ListArcMapTargets()", health)
        self.assertIn('request.Path == "/targets"', source)
        self.assertIn("private const int BridgePort = 8766;", source)
        self.assertNotIn("LastPort", source)

    def test_bridge_never_binds_execution_heartbeat_to_com_dispatch_outcome(self):
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        dispatch = source.split("private void ExecuteArcMapCommand", 1)[1].split(
            "private IApplication", 1
        )[0]

        self.assertIn("heartbeat.Start();", dispatch)
        self.assertIn("item.Execute();", dispatch)
        self.assertNotIn("heartbeat.Cancel();", dispatch)

    def test_bridge_preserves_sealed_execution_context_object(self):
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        execute = source.split('if (request.Action == "execute")', 1)[1].split(
            'if (request.Action == "acceptance_probe")', 1
        )[0]

        self.assertIn(
            'string contextJson = ExtractObjectJson(request.Body, "context_snapshot");',
            execute,
        )
        self.assertNotIn(
            'ExtractString(request.Body, "context_snapshot")',
            execute,
        )


if __name__ == "__main__":
    unittest.main()
