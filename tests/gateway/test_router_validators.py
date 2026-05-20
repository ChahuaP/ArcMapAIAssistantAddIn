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

    def test_router_selects_table_update(self):
        selected = self.router.select("把 nanjing 字段 a 等于 c 的要素字段 b 改成 d", _context(is_saved=True))
        ids = [operation["id"] for operation in selected]
        self.assertIn("table.update_rows", ids)

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

    def test_validator_accepts_structured_where(self):
        workflow = {
            "action": "execute",
            "summary": "选择字段 b 在 10 到 20 之间的要素。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "selection.select_by_attribute",
                    "arguments": {"layer": "nanjing", "where": {"field": "b", "op": "between", "values": [10, 20]}},
                    "reason": "按结构化条件选择"
                }
            ]
        }
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

    def test_planner_fills_missing_step_id(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将为 nanjing 添加字段 test。",
            "steps": [
                {
                    "operation": "table.add_field",
                    "arguments": {"layer": "nanjing", "field_name": "test", "field_type": "TEXT"},
                    "reason": "添加字段"
                }
            ]
        })
        row = self._plan_with_fake_client(client, "为南京的图层添加字段test")
        self.assertEqual(row["workflow"]["action"], "execute")
        self.assertEqual(row["workflow"]["steps"][0]["id"], "step_1")

    def test_planner_fills_missing_step_id_without_colliding(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "刷新两次地图。",
            "steps": [
                {"id": "step_1", "operation": "view.refresh_view", "arguments": {}, "reason": "刷新地图"},
                {"operation": "view.refresh_view", "arguments": {}, "reason": "再次刷新地图"}
            ]
        })
        row = self._plan_with_fake_client(client, "刷新两次地图")
        self.assertEqual(row["workflow"]["steps"][1]["id"], "step_2")

    def test_planner_turns_missing_operation_into_chinese_clarification(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将执行任务。",
            "steps": [
                {"arguments": {"foo": "bar"}, "reason": "执行用户任务"}
            ]
        })
        row = self._plan_with_fake_client(client, "帮我处理一下地图")
        self.assertEqual(row["workflow"]["action"], "clarify")
        self.assertNotIn("Step missing", row["workflow"]["summary"])
        self.assertIn("GIS 操作", row["workflow"]["summary"])

    def test_planner_uses_context_default_gdb_when_requested(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将对 nanjing 生成 10 米缓冲区，输出到默认 GDB。",
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
        context = _context(is_saved=False, default_gdb=r"D:\ArcGIS\Default.gdb")
        row = self._plan_with_fake_client(client, "给 nanjing 做 10 米缓冲区，输出到默认gdb中", context)
        self.assertEqual(row["workflow"]["action"], "execute")
        self.assertEqual(row["workflow"]["steps"][0]["arguments"]["output_workspace"], r"D:\ArcGIS\Default.gdb")

    def test_planner_clarifies_default_gdb_when_context_has_none(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将对 nanjing 生成 10 米缓冲区，输出到默认 GDB。",
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
        row = self._plan_with_fake_client(client, "给 nanjing 做 10 米缓冲区，输出到默认gdb中", _context(is_saved=False))
        self.assertEqual(row["workflow"]["action"], "clarify")
        self.assertIn("默认 GDB", row["workflow"]["summary"])

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

    def test_planner_continues_latest_clarification_answer(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将对 nanjing 生成 2 千米缓冲区，输出到 D 盘。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "analysis.buffer",
                    "arguments": {
                        "input_layer": "nanjing",
                        "distance": "2 Kilometers",
                        "output_name": "nanjing_buffer_2km",
                        "output_workspace": "D:\\"
                    },
                    "reason": "生成缓冲区"
                }
            ]
        })
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            store.create_draft(
                "给南京创建2km的缓冲区",
                "hash",
                {"action": "clarify", "summary": "请告诉我输出到哪个文件夹或 GDB。", "steps": []},
                []
            )
            planner = Planner(catalog=self.catalog, router=self.router, client=client, store=store)
            row = planner.plan("输出到d盘", _context(is_saved=False))

        self.assertEqual(row["workflow"]["action"], "execute")
        self.assertIn("给南京创建2km的缓冲区", client.messages[-1]["content"])
        self.assertIn("用户补充", client.messages[-1]["content"])

    def test_planner_skips_orphan_output_location_clarification(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将对 nanjing 生成 2 千米缓冲区，输出到 D 盘。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "analysis.buffer",
                    "arguments": {
                        "input_layer": "nanjing",
                        "distance": "2 Kilometers",
                        "output_name": "nanjing_buffer_2km",
                        "output_workspace": "D:\\"
                    },
                    "reason": "生成缓冲区"
                }
            ]
        })
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            store.create_draft(
                "给南京创建2km的缓冲区",
                "hash",
                {"action": "clarify", "summary": "这个操作会生成新数据，但当前输出位置还不明确。请告诉我输出到哪个文件夹或 GDB。", "steps": []},
                []
            )
            store.create_draft(
                "输出到d盘",
                "hash",
                {"action": "clarify", "summary": "请问您想输出什么内容？例如导出地图为PDF、导出图层属性表为CSV，或者导出选中的要素？", "steps": []},
                []
            )
            planner = Planner(catalog=self.catalog, router=self.router, client=client, store=store)
            row = planner.plan("输出到d盘", _context(is_saved=False))

        self.assertEqual(row["workflow"]["action"], "execute")
        self.assertIn("给南京创建2km的缓冲区", client.messages[-1]["content"])

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

    def test_planner_clarifies_missing_assignment_field(self):
        client = FakeDeepSeekClient({
            "action": "execute",
            "summary": "将直接修改 nanjing 的属性值。",
            "steps": [
                {
                    "id": "step_1",
                    "operation": "table.update_rows",
                    "arguments": {
                        "layer": "nanjing",
                        "where": {"field": "NAME", "op": "eq", "value": "c"},
                        "assignments": {"missing": "d"}
                    },
                    "reason": "批量修改属性"
                }
            ]
        })
        row = self._plan_with_fake_client(client, "把 NAME 等于 c 的要素 missing 改成 d")
        self.assertEqual(row["workflow"]["action"], "clarify")
        self.assertIn("missing", row["workflow"]["summary"])

    def test_planner_resolves_full_file_path_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "nanjing.shp"
            path.write_text("", encoding="utf-8")
            client = FakeDeepSeekClient({"action": "unsupported", "summary": "bad", "steps": []})
            row = self._plan_with_fake_client(client, "打开 %s" % path)
        self.assertEqual(row["workflow"]["action"], "execute")
        self.assertEqual(row["workflow"]["steps"][0]["operation"], "layer.add_layer")

    def test_planner_merges_local_file_prefix_with_ai_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pathlib.Path(directory) / "shp"
            folder.mkdir()
            (folder / "p1.shp").write_text("", encoding="utf-8")
            (folder / "p2.shp").write_text("", encoding="utf-8")
            client = FakeDeepSeekClient({
                "action": "execute",
                "summary": "将对 p1 和 p2 做相交分析。",
                "steps": [
                    {
                        "id": "step_1",
                        "operation": "analysis.intersect",
                        "arguments": {"input_layers": ["p1", "p2"], "output_name": "p1_p2_intersect"},
                        "reason": "对两个图层相交"
                    }
                ]
            })
            row = self._plan_with_fake_client(
                client,
                "打开%s文件夹下所有shp，执行p1和p2的相交，存放在默认gdb" % folder,
                _context(is_saved=False, default_gdb=r"D:\ArcGIS\Default.gdb")
            )

        workflow = row["workflow"]
        self.assertEqual(workflow["action"], "execute")
        self.assertEqual([step["operation"] for step in workflow["steps"]], [
            "layer.add_layer",
            "layer.add_layer",
            "analysis.intersect"
        ])
        self.assertEqual(workflow["steps"][2]["id"], "step_3")
        self.assertEqual(workflow["steps"][2]["arguments"]["output_workspace"], r"D:\ArcGIS\Default.gdb")
        self.assertIn("preplanned_steps=", client.messages[3]["content"])

    def test_planner_keeps_pure_folder_add_local(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pathlib.Path(directory) / "shp"
            folder.mkdir()
            (folder / "p1.shp").write_text("", encoding="utf-8")
            (folder / "p2.shp").write_text("", encoding="utf-8")
            client = FakeDeepSeekClient({"action": "unsupported", "summary": "bad", "steps": []})
            row = self._plan_with_fake_client(client, "打开%s文件夹下所有shp" % folder)

        workflow = row["workflow"]
        self.assertEqual(workflow["action"], "execute")
        self.assertEqual(len(workflow["steps"]), 2)
        self.assertEqual(client.messages, None)

    def test_planner_rejects_basemap_even_with_folder_add(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pathlib.Path(directory) / "shp"
            folder.mkdir()
            (folder / "p1.shp").write_text("", encoding="utf-8")
            client = FakeDeepSeekClient({"action": "execute", "summary": "bad", "steps": []})
            row = self._plan_with_fake_client(client, "添加 OSM WMS 底图，打开%s文件夹下所有shp" % folder)

        self.assertEqual(row["workflow"]["action"], "unsupported")
        self.assertIn("暂不支持自动添加底图", row["workflow"]["summary"])
        self.assertEqual(client.messages, None)

    def test_planner_does_not_treat_operation_words_inside_path_as_followup(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pathlib.Path(directory) / "叠加分析" / "相交"
            folder.mkdir(parents=True)
            (folder / "p1.shp").write_text("", encoding="utf-8")
            (folder / "p2.shp").write_text("", encoding="utf-8")
            client = FakeDeepSeekClient({"action": "unsupported", "summary": "bad", "steps": []})
            row = self._plan_with_fake_client(client, "打开%s下所有shp" % folder)

        workflow = row["workflow"]
        self.assertEqual(workflow["action"], "execute")
        self.assertEqual([step["operation"] for step in workflow["steps"]], [
            "layer.add_layer",
            "layer.add_layer"
        ])
        self.assertEqual(client.messages, None)

    def test_planner_uses_file_prefix_before_ai_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pathlib.Path(directory) / "shp"
            folder.mkdir()
            (folder / "p1.shp").write_text("", encoding="utf-8")
            (folder / "p2.shp").write_text("", encoding="utf-8")
            client = FakeDeepSeekClient({
                "action": "execute",
                "summary": "将对 p1 和 p2 做相交分析。",
                "steps": [
                    {
                        "id": "step_1",
                        "operation": "analysis.intersect",
                        "arguments": {"input_layers": ["p1", "p2"], "output_name": "p1_p2_intersect"},
                        "reason": "对两个图层相交"
                    }
                ]
            })
            row = self._plan_with_fake_client(
                client,
                "打开%s文件夹下所有shp，执行p1和p2的相交，存放在默认gdb" % folder,
                _context(is_saved=False, default_gdb=r"D:\ArcGIS\Default.gdb")
            )

        workflow = row["workflow"]
        self.assertEqual(workflow["action"], "execute")
        self.assertEqual([step["operation"] for step in workflow["steps"]], [
            "layer.add_layer",
            "layer.add_layer",
            "analysis.intersect"
        ])
        self.assertEqual(workflow["steps"][2]["id"], "step_3")
        self.assertIn("Do not repeat or ask about any preplanned steps", client.messages[3]["content"])

    def _plan_with_fake_client(self, client, command, context=None):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            planner = Planner(catalog=self.catalog, router=self.router, client=client, store=store)
            return planner.plan(command, context or _context(is_saved=True))


def _context(is_saved=True, default_gdb=""):
    return {
        "is_saved": is_saved,
        "default_gdb": default_gdb,
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
