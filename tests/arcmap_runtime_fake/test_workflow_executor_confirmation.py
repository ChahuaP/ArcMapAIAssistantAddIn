import importlib
import pathlib
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class WorkflowExecutorConfirmationTests(unittest.TestCase):
    def setUp(self):
        if str(RUNTIME_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNTIME_ROOT))
        sys.modules["context_reader"] = types.SimpleNamespace(context_hash=lambda context: "hash")
        import workflow_executor
        self.workflow_executor = importlib.reload(workflow_executor)

    def test_edits_data_requires_confirmation(self):
        row = _row()
        self.workflow_executor._load_operations = lambda: {"table.update_rows": _operation()}
        self.workflow_executor._call_estimator = lambda executor, context, arguments, step_outputs: {"summary": "将更新 3 条要素。"}
        self.workflow_executor._call_executor = lambda executor, context, arguments, step_outputs: {"updated": 3}

        with self.assertRaises(Exception):
            self.workflow_executor.execute(row, {}, confirm_callback=lambda message: False)

    def test_edits_data_runs_after_confirmation(self):
        row = _row()
        self.workflow_executor._load_operations = lambda: {"table.update_rows": _operation()}
        self.workflow_executor._call_estimator = lambda executor, context, arguments, step_outputs: {"summary": "将更新 3 条要素。"}
        self.workflow_executor._call_executor = lambda executor, context, arguments, step_outputs: {"updated": 3}

        result = self.workflow_executor.execute(row, {}, confirm_callback=lambda message: "3 条" in message)

        self.assertTrue(result["ok"])
        self.assertEqual(result["steps"][0]["result"]["updated"], 3)

    def test_custom_writes_data_gets_output_path_and_adds_layer(self):
        calls = {}

        class Common(object):
            @staticmethod
            def find_layer(context, layer_value, step_outputs):
                calls["find_layer"] = layer_value
                return "exact-layer-object"

            @staticmethod
            def output_feature_class(context, output_name, output_workspace=None):
                calls["output_feature_class"] = (output_name, output_workspace)
                return r"C:\work\ArcMapAI_Output.gdb\taihucenterpoints"

            @staticmethod
            def add_output_layer(path):
                calls["add_output_layer"] = path
                return {"added": True}

        def executor(executor_path, context, arguments, step_outputs):
            calls["executor_arguments"] = dict(arguments)
            return None

        self.workflow_executor._operations_common = lambda: Common
        self.workflow_executor._call_executor = executor

        operation = self.workflow_executor._canonicalize_operation(_custom_write_operation())
        self.workflow_executor._load_operations = lambda: {"custom.feature_to_point": operation}
        result = self.workflow_executor.execute(_custom_write_row(), {"is_saved": False})

        self.assertEqual(calls["find_layer"], "taihutestarea")
        self.assertEqual(calls["output_feature_class"], ("taihucenterpoints", r"C:\work"))
        self.assertEqual(calls["executor_arguments"]["input_layer"], "exact-layer-object")
        self.assertEqual(calls["executor_arguments"]["output_path"], r"C:\work\ArcMapAI_Output.gdb\taihucenterpoints")
        self.assertEqual(calls["add_output_layer"], r"C:\work\ArcMapAI_Output.gdb\taihucenterpoints")
        self.assertEqual(result["steps"][0]["result"]["output"], r"C:\work\ArcMapAI_Output.gdb\taihucenterpoints")

    def test_custom_write_schema_accepts_managed_output_workspace_when_enabled_spec_is_old(self):
        operation = self.workflow_executor._canonicalize_operation({
            "side_effects": "writes_data",
            "parameters_schema": {
                "type": "object",
                "required": ["input_layer", "output_name"],
                "properties": {
                    "input_layer": {"type": "string", "x-geopilot-kind": "layer"},
                    "output_name": {"type": "string"}
                },
                "additionalProperties": False
            },
            "executor": "custom_tool:tool_1:execute"
        })

        self.workflow_executor._validate_arguments("1", {
            "input_layer": "taihutestarea",
            "output_name": "stars",
            "output_workspace": r"C:\work"
        }, operation["parameters_schema"])

        self.assertIn("output_workspace", operation["parameters_schema"]["properties"])

    def test_custom_executor_receives_arcpy_global(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            tool_dir = root / "tool_1"
            tool_dir.mkdir()
            (tool_dir / "executor.py").write_text(
                "def execute(context, arguments, step_outputs):\n    return {'marker': arcpy.marker}\n",
                encoding="utf-8"
            )
            old_root = self.workflow_executor.CUSTOM_TOOLS_ROOT
            self.workflow_executor.CUSTOM_TOOLS_ROOT = str(root)
            sys.modules["arcpy"] = types.SimpleNamespace(marker="ok")
            try:
                result = self.workflow_executor._call_custom_executor("custom_tool:tool_1:execute", {}, {}, {})
            finally:
                self.workflow_executor.CUSTOM_TOOLS_ROOT = old_root

        self.assertEqual(result["marker"], "ok")


def _row():
    return {
        "context_hash": "hash",
        "workflow": {
            "summary": "直接修改属性。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "table.update_rows",
                    "arguments": {"layer": "nanjing", "where": {"field": "a", "op": "eq", "value": "c"}, "assignments": {"b": "d"}},
                    "reason": "测试确认"
                }
            ]
        }
    }


def _operation():
    return {
        "side_effects": "edits_data",
        "parameters_schema": {
            "type": "object",
            "required": ["layer", "where", "assignments"],
            "properties": {"layer": {}, "where": {}, "assignments": {}},
            "additionalProperties": False
        },
        "executor": "operations.table_ops.update_rows"
    }


def _custom_write_row():
    return {
        "context_hash": "hash",
        "workflow": {
            "summary": "面转点。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "custom.feature_to_point",
                    "arguments": {
                        "input_layer": "taihutestarea",
                        "output_name": "taihucenterpoints",
                        "output_workspace": r"C:\work"
                    },
                    "reason": "测试自建写数据工具"
                }
            ]
        }
    }


def _custom_write_operation():
    return {
        "side_effects": "writes_data",
        "parameters_schema": {
            "input_layer": {"type": "layer", "required": True},
            "output_name": {"type": "string", "required": True},
            "output_workspace": {"type": "string", "required": False}
        },
        "executor": "custom_tool:tool_1:execute"
    }


if __name__ == "__main__":
    unittest.main()
