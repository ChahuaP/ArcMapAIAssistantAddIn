import json
import pathlib
import tempfile
import unittest

from gateway_py3.agent_tools import AgentToolError, AgentToolRuntime
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.file_resolver import FileResolver
from gateway_py3.planner import AgenticPlanner
from gateway_py3.validators import ValidationError, prepare_workflow, validate_workflow
from gateway_py3.workflow_store import WorkflowStore


class FakeAgentClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def chat_agent(self, messages, tools):
        self.calls.append({"messages": list(messages), "tools": list(tools)})
        if not self.responses:
            raise AssertionError("No fake DeepSeek response left.")
        return {"message": self.responses.pop(0), "usage": {}}


class AgenticPlannerTests(unittest.TestCase):
    def setUp(self):
        self.catalog = OperationCatalog()

    def test_agentic_planner_uses_file_tool_then_proposes_intersect_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = pathlib.Path(directory) / "叠加分析" / "相交"
            folder.mkdir(parents=True)
            p1 = folder / "p1.shp"
            p2 = folder / "p2.shp"
            p1.write_text("", encoding="utf-8")
            p2.write_text("", encoding="utf-8")
            command = "打开%s下所有shp，并执行相交" % folder
            client = FakeAgentClient([
                _assistant_tool_call("call_1", "file_resolve", {"path": str(folder), "extensions": ["shp"]}),
                _assistant_tool_call("call_2", "workflow_propose", {
                    "action": "execute",
                    "summary": "将添加 p1、p2，并执行相交分析。",
                    "steps": [
                        _step("step_1", "layer.add_layer", {"path": str(p1)}, "添加 p1"),
                        _step("step_2", "layer.add_layer", {"path": str(p2)}, "添加 p2"),
                        _step("step_3", "analysis.intersect", {"input_layers": ["p1", "p2"]}, "对 p1 和 p2 相交")
                    ]
                })
            ])
            row = self._plan(client, command, _context(is_saved=True))

        workflow = row["workflow"]
        self.assertEqual(workflow["action"], "execute")
        self.assertEqual([step["operation"] for step in workflow["steps"]], [
            "layer.add_layer",
            "layer.add_layer",
            "analysis.intersect"
        ])
        self.assertRegex(workflow["steps"][2]["arguments"]["output_name"], r"^p1_p2_intersect_\d{8}_\d{6}$")
        tool_result_messages = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
        self.assertIn("p1", tool_result_messages[0]["content"])

    def test_file_resolve_tool_returns_drive_directory_question_without_planning_locally(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "Data").mkdir()
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context(), file_resolver=None)
            runtime.file_resolver.drive_roots = {"D": root}
            result = runtime.handle("file_resolve", {"drive": "D", "file_name": "nanjing.shp"})

        self.assertEqual(result["status"], "clarify")
        self.assertIn("范围太大", result["question"])
        self.assertIn("Data", result["child_directories"])
        self.assertEqual(result["files"], [])

    def test_file_resolve_tool_rejects_natural_language_text_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with self.assertRaises(AgentToolError):
                runtime.handle("file_resolve", {"text": "打开 d 盘下的 nanjing.shp"})

    def test_workflow_validate_rejects_original_command_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with self.assertRaises(AgentToolError):
                runtime.handle("workflow_validate", {
                    "original_command": "刷新地图",
                    "workflow": {
                        "action": "execute",
                        "summary": "刷新地图。",
                        "steps": [_step("step_1", "view.refresh_view", {}, "刷新地图")]
                    }
                })

    def test_tool_clarification_question_is_preserved_when_model_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / "Data").mkdir()
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            resolver = FileResolver(drive_roots={"D": root})
            client = FakeAgentClient([
                _assistant_tool_call("call_1", "file_resolve", {"drive": "D", "file_name": "nanjing.shp"}),
                {"role": "assistant", "content": "需要更多信息。"},
                {"role": "assistant", "content": "需要更多信息。"},
                {"role": "assistant", "content": "需要更多信息。"}
            ])
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store, file_resolver=resolver)
            row = planner.plan("打开d盘下的nanjing.shp", _context())

        self.assertEqual(row["workflow"]["action"], "clarify")
        self.assertIn("范围太大", row["workflow"]["summary"])
        self.assertIn("nanjing.shp", row["workflow"]["summary"])

    def test_premature_file_question_is_nudged_back_to_tool_search(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            folder = root / "Data" / "shapefile"
            folder.mkdir(parents=True)
            target = folder / "nanjing.shp"
            target.write_text("", encoding="utf-8")
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            resolver = FileResolver(drive_roots={"D": root})
            client = FakeAgentClient([
                _assistant_tool_call("call_1", "file_resolve", {"drive": "D", "file_name": "nanjing.shp"}),
                {"role": "assistant", "content": "需要更多信息。"},
                _assistant_tool_call("call_2", "file_resolve", {
                    "drive": "D",
                    "directory_parts": ["Data", "shapefile"],
                    "file_name": "nanjing.shp"
                }),
                _assistant_tool_call("call_3", "workflow_propose", {
                    "action": "execute",
                    "summary": "将添加 nanjing 图层。",
                    "steps": [
                        _step("step_1", "layer.add_layer", {"path": str(target)}, "添加 nanjing")
                    ]
                })
            ])
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store, file_resolver=resolver)
            row = planner.plan("打开d盘下的nanjing.shp", _context())

        self.assertEqual(row["workflow"]["action"], "execute")
        self.assertEqual([item["name"] for item in row["agent_trace"] if item.get("type") == "tool"], [
            "file_resolve",
            "file_resolve",
        ])

    def test_model_can_continue_search_from_child_directories_before_clarifying(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            folder = root / "Data" / "shapefile"
            folder.mkdir(parents=True)
            target = folder / "nanjing.shp"
            target.write_text("", encoding="utf-8")
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            resolver = FileResolver(drive_roots={"D": root})
            client = FakeAgentClient([
                _assistant_tool_call("call_1", "file_resolve", {"drive": "D", "file_name": "nanjing.shp"}),
                _assistant_tool_call("call_2", "file_resolve", {
                    "drive": "D",
                    "directory_parts": ["Data", "shapefile"],
                    "file_name": "nanjing.shp"
                }),
                _assistant_tool_call("call_3", "workflow_propose", {
                    "action": "execute",
                    "summary": "将添加 nanjing 图层。",
                    "steps": [
                        _step("step_1", "layer.add_layer", {"path": str(target)}, "添加 nanjing")
                    ]
                })
            ])
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store, file_resolver=resolver)
            row = planner.plan("打开d盘下的nanjing.shp", _context())

        self.assertEqual(row["workflow"]["action"], "execute")
        self.assertEqual(row["workflow"]["steps"][0]["operation"], "layer.add_layer")
        tool_names = [item["name"] for item in row["agent_trace"] if item.get("type") == "tool"]
        self.assertEqual(tool_names, ["file_resolve", "file_resolve"])

    def test_recent_conversation_is_sent_to_model_for_short_followup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            store.create_draft("打开数据并相交", "hash", {"action": "clarify", "summary": "请确认要对哪些图层相交。", "steps": []}, [])
            client = FakeAgentClient([
                _assistant_tool_call("call_1", "workflow_propose", {
                    "action": "execute",
                    "summary": "将对 p1 和 p2 执行相交分析。",
                    "steps": [
                        _step("step_1", "analysis.intersect", {"input_layers": ["p1", "p2"]}, "对 p1 和 p2 相交")
                    ]
                })
            ])
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
            row = planner.plan("p1、p2", {
                "is_saved": True,
                "layers": [_layer("p1"), _layer("p2")]
            })

        first_user_message = client.calls[0]["messages"][1]["content"]
        self.assertIn("recent_conversation", first_user_message)
        self.assertRegex(row["workflow"]["steps"][0]["arguments"]["output_name"], r"^p1_p2_intersect_\d{8}_\d{6}$")

    def test_bad_workflow_is_fed_back_once_then_repaired(self):
        client = FakeAgentClient([
            _assistant_tool_call("call_1", "workflow_propose", {
                "action": "execute",
                "summary": "坏工作流。",
                "steps": [{"id": "step_1", "arguments": {}, "reason": "缺 operation"}]
            }),
            _assistant_tool_call("call_2", "workflow_propose", {
                "action": "execute",
                "summary": "刷新地图。",
                "steps": [_step("step_1", "view.refresh_view", {}, "刷新地图")]
            })
        ])
        row = self._plan(client, "刷新地图", _context())

        feedback_messages = [m for m in client.calls[1]["messages"] if m.get("role") == "tool"]
        self.assertIn("还不能确定", feedback_messages[0]["content"])
        self.assertEqual(row["workflow"]["steps"][0]["operation"], "view.refresh_view")

    def test_tool_loop_allows_repair_after_fourth_assistant_turn(self):
        client = FakeAgentClient([
            _assistant_tool_call("call_1", "catalog_list_operations", {}),
            _assistant_tool_call("call_2", "arcgis_get_context", {}),
            _assistant_tool_call("call_3", "catalog_get_operation_schema", {"operation_id": "view.refresh_view"}),
            _assistant_tool_call("call_4", "workflow_validate", {
                "workflow": {
                    "action": "execute",
                    "summary": "刷新地图。",
                    "steps": [_step("step_1", "view.refresh_view", {"bad_argument": "x"}, "刷新地图")]
                }
            }),
            _assistant_tool_call("call_5", "workflow_propose", {
                "action": "execute",
                "summary": "刷新地图。",
                "steps": [_step("step_1", "view.refresh_view", {}, "刷新地图")]
            })
        ])
        row = self._plan(client, "刷新地图", _context())

        self.assertEqual(len(client.calls), 5)
        self.assertEqual(row["workflow"]["action"], "execute")
        self.assertEqual(row["workflow"]["steps"][0]["operation"], "view.refresh_view")

    def test_unknown_operation_is_rejected_by_validation_boundary(self):
        workflow = {
            "action": "execute",
            "summary": "bad",
            "steps": [_step("step_1", "unknown.tool", {}, "bad")]
        }
        with self.assertRaises(Exception):
            validate_workflow(workflow, self.catalog)

    def test_unknown_argument_is_rejected_by_validation_boundary(self):
        workflow = {
            "action": "execute",
            "summary": "bad",
            "steps": [_step("step_1", "view.refresh_view", {"python": "print(1)"}, "bad")]
        }
        with self.assertRaises(ValidationError):
            prepare_workflow(workflow, self.catalog, _context())

    def test_unsaved_mxd_write_without_output_location_clarifies(self):
        workflow = {
            "action": "execute",
            "summary": "将对 nanjing 生成缓冲区。",
            "steps": [
                _step("step_1", "analysis.buffer", {
                    "input_layer": "nanjing",
                    "distance": "10 Meters",
                    "output_name": "nanjing_buffer"
                }, "缓冲")
            ]
        }
        with self.assertRaisesRegex(ValidationError, "输出位置"):
            prepare_workflow(workflow, self.catalog, _context(is_saved=False))

    def test_split_by_field_workflow_is_prepared_with_timestamp(self):
        workflow = {
            "action": "execute",
            "summary": "按字段拆分导出。",
            "steps": [
                _step("step_1", "export.split_by_field", {
                    "layer": "nanjing",
                    "field": "NAME",
                    "output_name": "nanjing_by_name",
                    "output_format": "shp",
                    "output_folder": r"D:\exports"
                }, "按 NAME 字段拆分导出")
            ]
        }
        prepared = prepare_workflow(workflow, self.catalog, _context(is_saved=True))

        output_name = prepared["steps"][0]["arguments"]["output_name"]
        self.assertRegex(output_name, r"^nanjing_by_name_\d{8}_\d{6}$")

    def test_chinese_output_name_is_rejected_before_runtime_execution(self):
        workflow = {
            "action": "execute",
            "summary": "执行相交。",
            "steps": [
                _step("step_1", "analysis.intersect", {
                    "input_layers": ["p1", "p2"],
                    "output_name": "相交结果"
                }, "执行相交")
            ]
        }
        with self.assertRaisesRegex(ValidationError, "输出名称"):
            prepare_workflow(workflow, self.catalog, {
                "is_saved": True,
                "layers": [_layer("p1"), _layer("p2")]
            })

    def test_basemap_remains_unsupported_without_executable_steps(self):
        client = FakeAgentClient([
            _assistant_tool_call("call_1", "workflow_propose", {
                "action": "unsupported",
                "summary": "当前版本暂不支持自动添加底图。",
                "steps": []
            })
        ])
        row = self._plan(client, "添加高德底图", _context())

        self.assertEqual(row["workflow"]["action"], "unsupported")
        self.assertEqual(row["workflow"]["steps"], [])

    def _plan(self, client, command, context):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
            return planner.plan(command, context)


def _assistant_tool_call(call_id, name, arguments):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False)
                }
            }
        ]
    }


def _step(step_id, operation, arguments, reason):
    return {
        "id": step_id,
        "operation": operation,
        "arguments": arguments,
        "reason": reason
    }


def _context(is_saved=True):
    return {
        "is_saved": is_saved,
        "default_gdb": r"D:\ArcGIS\Default.gdb",
        "layers": [_layer("nanjing")]
    }


def _layer(name):
    return {
        "layer_ref": "layer:%s" % name,
        "name": name,
        "longName": name,
        "fields": [{"name": "OBJECTID"}, {"name": "NAME"}],
        "selected_count": 0,
        "geometry_type": "Polygon"
    }


if __name__ == "__main__":
    unittest.main()
