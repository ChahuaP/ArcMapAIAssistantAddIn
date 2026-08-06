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

    def test_bridge_never_binds_execution_heartbeat_to_com_dispatch_outcome(self):
        source = BRIDGE_PATH.read_text(encoding="utf-8")
        dispatch = source.split("private void ExecuteArcMapCommand", 1)[1].split(
            "private IApplication", 1
        )[0]

        self.assertIn("heartbeat.Start();", dispatch)
        self.assertIn("item.Execute();", dispatch)
        self.assertNotIn("heartbeat.Cancel();", dispatch)


if __name__ == "__main__":
    unittest.main()
