import pathlib
import tempfile
import unittest

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.planner import Planner
from gateway_py3.router import OperationRouter
from gateway_py3.validators import ValidationError, validate_workflow
from gateway_py3.workflow_store import WorkflowStore


class FakeDeepSeekClient:
    def __init__(self, response):
        self.response = response
        self.messages = None

    def chat_json(self, messages):
        self.messages = messages
        return dict(self.response)


class RouterAndValidatorTests(unittest.TestCase):
    def setUp(self):
        self.catalog = OperationCatalog()
        self.router = OperationRouter(self.catalog)

    def test_router_selects_zoom_without_sending_full_catalog(self):
        selected = self.router.select("缩放到 roads 图层", {"layers": [{"name": "roads"}]})
        ids = [operation["id"] for operation in selected]
        self.assertIn("view.zoom_to_layer", ids)
        self.assertLessEqual(len(ids), 8)

    def test_router_selects_buffer(self):
        selected = self.router.select("给 roads 做100米缓冲区", {"layers": [{"name": "roads"}]})
        ids = [operation["id"] for operation in selected]
        self.assertIn("analysis.buffer", ids)
        self.assertLessEqual(len(ids), 8)

    def test_validator_rejects_unknown_operation(self):
        workflow = {
            "summary": "bad",
            "steps": [
                {"id": "step_1", "operation": "unknown.tool", "arguments": {}, "reason": "bad"}
            ]
        }
        with self.assertRaises(Exception):
            validate_workflow(workflow, self.catalog)

    def test_validator_rejects_missing_required_arg(self):
        workflow = {
            "summary": "bad",
            "steps": [
                {"id": "step_1", "operation": "analysis.buffer", "arguments": {"input_layer": "roads"}, "reason": "bad"}
            ]
        }
        with self.assertRaises(ValidationError):
            validate_workflow(workflow, self.catalog)

    def test_router_fallback_keeps_tool_prompt_small(self):
        selected = self.router.fallback("你好，随便试一下", {"layers": []})
        self.assertGreater(len(selected), 0)
        self.assertLessEqual(len(selected), 8)

    def test_validator_allows_clarify_without_steps(self):
        workflow = {"action": "clarify", "summary": "你想操作哪个图层？", "steps": []}
        validate_workflow(workflow, self.catalog)

    def test_validator_allows_unsupported_without_steps(self):
        workflow = {"action": "unsupported", "summary": "当前没有地理编码操作。", "steps": []}
        validate_workflow(workflow, self.catalog)

    def test_planner_accepts_ai_clarification(self):
        client = FakeDeepSeekClient({"action": "clarify", "summary": "你想操作哪个图层？", "steps": []})
        row = self._plan_with_fake_client(client, "帮我处理一下")
        self.assertEqual(row["workflow"]["action"], "clarify")
        self.assertIn("catalog_index=", client.messages[1]["content"])

    def test_planner_accepts_ai_unsupported_response(self):
        client = FakeDeepSeekClient({"action": "unsupported", "summary": "当前没有地理编码操作。", "steps": []})
        row = self._plan_with_fake_client(client, "帮我做地址匹配")
        self.assertEqual(row["workflow"]["action"], "unsupported")

    def test_planner_normalizes_model_action_with_valid_steps(self):
        client = FakeDeepSeekClient({
            "action": "buffer",
            "summary": "将对 nanjing 生成 100 米缓冲区，输出 rods。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "analysis.buffer",
                    "arguments": {
                        "input_layer": "nanjing",
                        "distance": "100 Meters",
                        "output_name": "rods"
                    },
                    "reason": "生成 100 米缓冲区"
                }
            ]
        })
        row = self._plan_with_fake_client(client, "给nanjing做100米缓冲区，输出名rods")
        self.assertEqual(row["workflow"]["action"], "execute")

    def test_planner_normalizes_step_parameters_alias(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将对 nanjing 生成 100 米缓冲区，输出 rods。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "analysis.buffer",
                    "parameters": {
                        "input_layer": "nanjing",
                        "distance": "100 Meters",
                        "output_name": "rods"
                    },
                    "reason": "生成 100 米缓冲区"
                }
            ]
        })
        row = self._plan_with_fake_client(client, "给nanjing做100米缓冲区，输出名rods")
        self.assertEqual(row["workflow"]["steps"][0]["arguments"]["input_layer"], "nanjing")

    def test_planner_normalizes_step_parameters_alias_without_reason(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将对 nanjing 生成 10 米缓冲区。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "analysis.buffer",
                    "parameters": {
                        "input_layer": "nanjing",
                        "distance": "10 Meters",
                        "output_name": "nanjing_buffer_10m"
                    }
                }
            ]
        })
        row = self._plan_with_fake_client(client, "给nanjing做一个10米的缓冲区")
        self.assertTrue(row["workflow"]["steps"][0]["reason"])

    def test_planner_infers_flat_step_arguments(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将对 nanjing 生成 100 米缓冲区，输出 rods。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "analysis.buffer",
                    "input_layer": "nanjing",
                    "distance": "100 Meters",
                    "output_name": "rods",
                    "reason": "生成 100 米缓冲区"
                }
            ]
        })
        row = self._plan_with_fake_client(client, "给nanjing做100米缓冲区，输出名rods")
        self.assertEqual(row["workflow"]["steps"][0]["arguments"]["output_name"], "rods")

    def test_planner_fills_missing_step_reason(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "刷新地图。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "view.refresh_view",
                    "arguments": {}
                }
            ]
        })
        row = self._plan_with_fake_client(client, "刷新地图")
        self.assertTrue(row["workflow"]["steps"][0]["reason"])

    def test_planner_clarifies_output_location_when_unsaved_mxd_writes_data(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将对 nanjing 生成 10 米缓冲区。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "analysis.buffer",
                    "arguments": {
                        "input_layer": "nanjing",
                        "distance": "10 Meters",
                        "output_name": "nanjing_buffer_10m"
                    },
                    "reason": "生成缓冲区"
                }
            ]
        })
        row = self._plan_with_fake_client(client, "给 nanjing 做 10 米缓冲区", _context(is_saved=False))
        self.assertEqual(row["workflow"]["action"], "clarify")
        self.assertEqual(row["workflow"]["steps"], [])
        self.assertIn("输出", row["workflow"]["summary"])

    def test_planner_clarifies_unknown_layer(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将对 beijing 生成 10 米缓冲区。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "analysis.buffer",
                    "arguments": {
                        "input_layer": "beijing",
                        "distance": "10 Meters",
                        "output_name": "beijing_buffer_10m"
                    },
                    "reason": "生成缓冲区"
                }
            ]
        })
        row = self._plan_with_fake_client(client, "给 beijing 做 10 米缓冲区", _context(is_saved=True))
        self.assertEqual(row["workflow"]["action"], "clarify")
        self.assertEqual(row["workflow"]["steps"], [])
        self.assertIn("nanjing", row["workflow"]["summary"])

    def test_planner_allows_unsaved_mxd_when_output_workspace_is_explicit(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将对 nanjing 生成 10 米缓冲区。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "analysis.buffer",
                    "arguments": {
                        "input_layer": "nanjing",
                        "distance": "10 Meters",
                        "output_name": "nanjing_buffer_10m",
                        "output_workspace": "D:/data/out.gdb"
                    },
                    "reason": "生成缓冲区"
                }
            ]
        })
        row = self._plan_with_fake_client(client, "给 nanjing 做 10 米缓冲区，输出到 D:/data/out.gdb", _context(is_saved=False))
        self.assertEqual(row["workflow"]["action"], "execute")

    def test_planner_rejects_open_attribute_table_as_unsupported(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "bad",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "export.table_csv",
                    "arguments": {"layer": "nanjing", "output_name": "nanjing"},
                    "reason": "bad"
                }
            ]
        })
        row = self._plan_with_fake_client(client, "请你打开nanjing的属性表")
        self.assertEqual(row["workflow"]["action"], "unsupported")
        self.assertEqual(row["workflow"]["steps"], [])

    def _plan_with_fake_client(self, client, command, context=None):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            planner = Planner(catalog=self.catalog, router=self.router, client=client, store=store)
            return planner.plan(command, context or _context(is_saved=True))


def _context(is_saved=True):
    return {
        "is_saved": is_saved,
        "layers": [
            {
                "layer_ref": "layer:0",
                "name": "nanjing",
                "longName": "nanjing",
                "fields": [{"name": "OBJECTID"}, {"name": "NAME"}],
                "selected_count": 0,
                "geometry_type": "Polygon"
            }
        ]
    }


if __name__ == "__main__":
    unittest.main()
