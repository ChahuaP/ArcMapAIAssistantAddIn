import importlib
import pathlib
import sys
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


if __name__ == "__main__":
    unittest.main()
