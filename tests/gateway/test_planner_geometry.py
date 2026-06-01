import json
import pathlib
import tempfile
import unittest

from gateway_py3.agent_tools import AgentToolRuntime
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.planner import AgenticPlanner
from gateway_py3.workflow_store import WorkflowStore

try:
    from gateway.planner_test_utils import FakeAgentClient, assistant_tool_call, context as _context
except ImportError:
    from tests.gateway.planner_test_utils import FakeAgentClient, assistant_tool_call, context as _context


class PlannerGeometryTests(unittest.TestCase):
    def test_planner_repairs_model_array_shape_error_for_rectangle(self):
        bad_workflow = {
            "action": "execute",
            "summary": "创建矩形面。",
            "steps": [
                {
                    "id": "create_rectangle",
                    "operation": "edit.create_polygon_feature",
                    "arguments": {
                        "coordinates": {
                            "item": [
                                {"x": 120, "y": 30},
                                {"x": 125, "y": 30},
                                {"x": 125, "y": 20},
                                {"x": 120, "y": 20}
                            ]
                        },
                        "wkid": 4326,
                        "output_name": "rectangle_120_30_125_20"
                    },
                    "reason": "根据两角点创建矩形。"
                }
            ]
        }
        repaired_workflow = {
            "action": "execute",
            "summary": "创建 WGS84 矩形面。",
            "steps": [
                {
                    "id": "create_rectangle",
                    "operation": "edit.create_rectangle_polygon",
                    "arguments": {
                        "left": 120,
                        "top": 30,
                        "right": 125,
                        "bottom": 20,
                        "wkid": 4326,
                        "output_name": "rectangle_120_30_125_20"
                    },
                    "reason": "根据左上角和右下角创建一个 WGS84 矩形面。"
                }
            ]
        }
        client = FakeAgentClient([
            assistant_tool_call("call_1", "workflow_validate", {"workflow_json": json.dumps(bad_workflow, ensure_ascii=False)}),
            assistant_tool_call("call_2", "workflow_propose", {"workflow_json": json.dumps(repaired_workflow, ensure_ascii=False)}),
        ])

        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            planner = AgenticPlanner(catalog=OperationCatalog(), client=client, store=store)

            row = planner.plan(
                "一个正方形左上角 120 30右下角 125 20，WGS84",
                _context(is_saved=True),
                mode="semi_agent",
                request_id="test-request",
            )

        workflow = row["workflow"]
        self.assertEqual(workflow["action"], "execute")
        self.assertEqual(workflow["steps"][0]["operation"], "edit.create_rectangle_polygon")
        self.assertEqual(workflow["steps"][0]["arguments"]["wkid"], 4326)
        tool_feedback = client.calls[1]["messages"][-1]["content"]
        self.assertIn("must be array", tool_feedback)
        self.assertIn("不要向用户追问", tool_feedback)

    def test_workflow_validate_uses_json_string_and_rejects_item_wrapped_arrays(self):
        runtime = AgentToolRuntime(OperationCatalog(), WorkflowStore(":memory:"), _context(is_saved=True))
        workflow = {
            "action": "execute",
            "summary": "创建矩形面。",
            "steps": [
                {
                    "id": "create_rectangle",
                    "operation": "edit.create_polygon_feature",
                    "arguments": {
                        "coordinates": {"item": [{"x": 120, "y": 30}, {"x": 125, "y": 30}, {"x": 125, "y": 20}]},
                        "wkid": 4326,
                        "output_name": "bad_rectangle"
                    },
                    "reason": "坏数组结构应由模型修复。"
                }
            ]
        }

        result = runtime.handle("workflow_validate", {"workflow_json": json.dumps(workflow, ensure_ascii=False)})

        self.assertFalse(result["ok"])
        self.assertTrue(result["repairable"])
        self.assertIn("must be array", result["error"])
        self.assertIn("不要向用户追问", result["error"])

    def test_workflow_tools_expose_json_string_contract(self):
        runtime = AgentToolRuntime(OperationCatalog(), WorkflowStore(":memory:"), _context(is_saved=True))
        tools = {tool["function"]["name"]: tool["function"]["parameters"] for tool in runtime.tools()}

        self.assertEqual(tools["workflow_validate"]["required"], ["workflow_json"])
        self.assertEqual(tools["workflow_propose"]["required"], ["workflow_json"])
        self.assertEqual(tools["workflow_validate"]["properties"]["workflow_json"]["type"], "string")


if __name__ == "__main__":
    unittest.main()
