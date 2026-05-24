import json
import pathlib
import tempfile
import unittest

from gateway_py3.agent_tools import AgentToolError, AgentToolRuntime
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.file_resolver import FileResolver
from gateway_py3.output_folder_resolver import OutputFolderResolver
from gateway_py3.planner import AgenticPlanner, SYSTEM_PROMPT
from gateway_py3.validators import ValidationError, prepare_workflow, validate_workflow
from gateway_py3.workflow_store import WorkflowStore

from gateway.planner_test_utils import (
    FakeAgentClient,
    assistant_tool_call as _assistant_tool_call,
    context as _context,
    custom_writes_data_spec as _custom_writes_data_spec,
    isolated_tool_roots as _isolated_tool_roots,
    layer as _layer,
    layer_with_ref as _layer_with_ref,
    star_tool_arguments as _star_tool_arguments,
    star_tool_revision_arguments as _star_tool_revision_arguments,
    step as _step,
)


class AgenticPlannerTests(unittest.TestCase):
    def setUp(self):
        self.catalog = OperationCatalog()

    def test_prompt_treats_explicit_degree_radius_as_actionable(self):
        self.assertIn("外接圆半径0.001度", SYSTEM_PROMPT)
        self.assertIn("radius_unit", SYSTEM_PROMPT)
        self.assertIn("geographic", SYSTEM_PROMPT)
        self.assertIn("only exception is executor_code inside toolbuilder_create_draft", SYSTEM_PROMPT)
        self.assertIn('"op":"and","conditions"', SYSTEM_PROMPT)
        self.assertIn('Never use shorthand', SYSTEM_PROMPT)
        self.assertIn('folder_path is only for the file_resolve tool', SYSTEM_PROMPT)
        self.assertIn('output_folder_resolve', SYSTEM_PROMPT)
        self.assertIn('Never use file_resolve for output folders', SYSTEM_PROMPT)
        self.assertIn('you must decide output_name yourself from user_request', SYSTEM_PROMPT)
        self.assertIn('GeoPilot will not infer names from user text for you', SYSTEM_PROMPT)
        self.assertIn('outputfolder', SYSTEM_PROMPT)
        self.assertIn('already enabled capability', SYSTEM_PROMPT)

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
                }),
                _assistant_tool_call("call_3", "workflow_propose", {
                    "action": "execute",
                    "summary": "将添加 p1、p2，并执行相交分析。",
                    "steps": [
                        _step("step_1", "layer.add_layer", {"path": str(p1)}, "添加 p1"),
                        _step("step_2", "layer.add_layer", {"path": str(p2)}, "添加 p2"),
                        _step("step_3", "analysis.intersect", {
                            "input_layers": ["p1", "p2"],
                            "output_name": "p1_p2_intersect"
                        }, "对 p1 和 p2 相交")
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

    def test_output_folder_resolve_uses_known_desktop_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            target = root / "test"
            target.mkdir()
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            runtime.output_folder_resolver = OutputFolderResolver({"desktop": root})

            result = runtime.handle("output_folder_resolve", {"known_folder": "desktop", "folder_name": "test"})

        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["path"], str(target))

    def test_output_folder_resolve_rejects_missing_folder_before_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            runtime.output_folder_resolver = OutputFolderResolver({"desktop": root})

            result = runtime.handle("output_folder_resolve", {"known_folder": "desktop", "folder_name": "missing"})

        self.assertEqual(result["status"], "clarify")
        self.assertIn("输出文件夹不存在", result["question"])

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

    def test_workflow_validate_missing_reason_is_repair_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            workflow = {
                "action": "execute",
                "summary": "列出图层",
                "steps": [
                    {"id": "1", "operation": "context.list_layers", "arguments": {}}
                ]
            }

            result = runtime.handle("workflow_validate", {"workflow": workflow})

        self.assertFalse(result["ok"])
        self.assertIn("reason", result["error"])
        self.assertIn("不要向用户追问", result["error"])
        self.assertNotIn("输出位置", result["error"])

    def test_workflow_validate_missing_op_is_repair_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            workflow = {
                "action": "execute",
                "summary": "按名称选择。",
                "steps": [
                    _step("step_1", "selection.select_by_attribute", {
                        "layer": "nanjing",
                        "where": {"field": "NAME", "value": "南京"}
                    }, "选择")
                ]
            }

            result = runtime.handle("workflow_validate", {"workflow": workflow})

        self.assertFalse(result["ok"])
        self.assertIn('"op":"and"', result["error"])
        self.assertIn("叶子条件必须写 op", result["error"])
        self.assertIn("不要向用户追问", result["error"])

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

    def test_plain_markdown_response_is_stored_as_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            client = FakeAgentClient([
                {
                    "role": "assistant",
                    "content": "<think>读取项目历史。</think>## 之前完成的事\n\n- 已打开 **nanjing.shp**。"
                }
            ])
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
            row = planner.plan("你之前干了啥", _context())

        self.assertEqual(row["workflow"]["action"], "answer")
        self.assertIn("## 之前完成的事", row["workflow"]["summary"])
        self.assertEqual(row["workflow"]["steps"], [])

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
            store.create_draft("全代理旧任务", "hash", {"action": "answer", "summary": "不应进入半代理上下文。", "steps": []}, [], mode="full_agent", project_id="project_1")
            client = FakeAgentClient([
                _assistant_tool_call("call_1", "workflow_propose", {
                    "action": "execute",
                    "summary": "将对 p1 和 p2 执行相交分析。",
                    "steps": [
                        _step("step_1", "analysis.intersect", {"input_layers": ["p1", "p2"]}, "对 p1 和 p2 相交")
                    ]
                }),
                _assistant_tool_call("call_2", "workflow_propose", {
                    "action": "execute",
                    "summary": "将对 p1 和 p2 执行相交分析。",
                    "steps": [
                        _step("step_1", "analysis.intersect", {
                            "input_layers": ["p1", "p2"],
                            "output_name": "p1_p2_intersect"
                        }, "对 p1 和 p2 相交")
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
        self.assertIn("打开数据并相交", first_user_message)
        self.assertNotIn("全代理旧任务", first_user_message)
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

    def test_validation_error_summary_is_not_returned_to_user(self):
        client = FakeAgentClient([
            _assistant_tool_call("call_1", "workflow_validate", {
                "workflow": {
                    "summary": "按属性选择。",
                    "steps": [
                        _step("step_1", "selection.select_by_attribute", {
                            "layer": "nanjing",
                            "where": {"field": "NAME", "value": "南京"}
                        }, "选择")
                    ]
                }
            }),
            _assistant_tool_call("call_2", "workflow_propose", {
                "action": "clarify",
                "summary": "属性条件缺少 op。",
                "steps": []
            }),
            _assistant_tool_call("call_3", "arcgis_get_layer_profile", {"layer": "nanjing"}),
            _assistant_tool_call("call_4", "workflow_propose", {
                "action": "execute",
                "summary": "按名称选择南京。",
                "steps": [
                    _step("step_1", "selection.select_by_attribute", {
                        "layer": "nanjing",
                        "where": {"field": "NAME", "op": "eq", "value": "南京"}
                    }, "选择")
                ]
            })
        ])
        row = self._plan(client, "选择南京", _context())

        self.assertEqual(len(client.calls), 4)
        self.assertEqual(row["workflow"]["action"], "execute")
        self.assertEqual(row["workflow"]["steps"][0]["arguments"]["where"]["op"], "eq")

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

    def test_toolbuilder_catalog_misuse_can_recover_to_star_tool_draft(self):
        client = FakeAgentClient([
            _assistant_tool_call("call_1", "catalog_get_operation_schema", {"operation_id": "toolbuilder.create_draft"}),
            _assistant_tool_call("call_2", "toolbuilder_create_draft", _star_tool_arguments()),
            {"role": "assistant", "content": "已生成面转五角星面自定义工具草稿，审核启用后可以执行。"}
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
            with _isolated_tool_roots(root):
                row = planner.plan("创建工具：将 taihu test area 面图层转换为五角星面图层，每个面根据中心点生成一个五角星面", _context())

        self.assertEqual(row["workflow"]["action"], "answer")
        tool_results = [item for item in row["agent_trace"] if item.get("type") == "tool"]
        self.assertEqual(tool_results[0]["result"]["status"], "wrong_tool_namespace")
        self.assertTrue(tool_results[1]["result"]["ok"])
        self.assertEqual(tool_results[1]["result"]["tool"]["payload"]["operation_spec"]["id"], "custom.polygon_to_star")

    def test_generic_unsupported_is_rejected_and_model_creates_custom_tool(self):
        client = FakeAgentClient([
            _assistant_tool_call("call_1", "workflow_propose", {
                "action": "unsupported",
                "summary": "当前版本还不支持这个操作。请换成已有能力，或告诉我你想完成的 GIS 处理目标。",
                "steps": []
            }),
            _assistant_tool_call("call_2", "toolbuilder_create_draft", _star_tool_arguments()),
            {"role": "assistant", "content": "已生成自定义工具草稿，审核启用后可以执行。"}
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
            with _isolated_tool_roots(root):
                row = planner.plan("创建工具：把面图层转换成五角星面", _context())

        self.assertEqual(row["workflow"]["action"], "answer")
        tool_results = [item for item in row["agent_trace"] if item.get("type") == "tool"]
        self.assertEqual(tool_results[0]["name"], "toolbuilder_create_draft")
        self.assertTrue(tool_results[0]["result"]["ok"])
        self.assertIn("toolbuilder_create_draft", json.dumps(client.calls[1]["messages"], ensure_ascii=False))

    def test_toolbuilder_validation_error_forces_tool_repair_not_user_clarify(self):
        bad_arguments = _star_tool_arguments()
        bad_arguments["executor_code"] = bad_arguments["executor_code"].replace(
            'output_path = arguments["output_path"]',
            'output_path = arguments["output_path"]\n    output_folder = arguments["outputfolder"]'
        )
        client = FakeAgentClient([
            _assistant_tool_call("call_1", "toolbuilder_create_draft", bad_arguments),
            _assistant_tool_call("call_2", "workflow_propose", {
                "action": "clarify",
                "summary": 'writesdata 自定义工具执行代码不能读取 arguments["outputfolder"]；请只使用 GeoPilot 生成的 arguments["output_path"]。\n\n信息不够，当前不会执行任何操作。',
                "steps": []
            }),
            _assistant_tool_call("call_3", "toolbuilder_create_draft", _star_tool_arguments()),
            {"role": "assistant", "content": "已生成面转五角星面自定义工具草稿，审核启用后可以执行。"}
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
            with _isolated_tool_roots(root):
                row = planner.plan("创建工具：把面图层转换成五角星面", _context())

        self.assertEqual(row["workflow"]["action"], "answer")
        tool_results = [item for item in row["agent_trace"] if item.get("type") == "tool"]
        self.assertFalse(tool_results[0]["result"]["ok"])
        self.assertTrue(tool_results[0]["result"]["repairable"])
        self.assertEqual(tool_results[0]["result"]["status"], "toolbuilder_validation_error")
        self.assertTrue(tool_results[1]["result"]["ok"])
        self.assertNotIn("信息不够", row["workflow"]["summary"])

    def test_internal_feedback_after_toolbuilder_success_is_not_user_visible(self):
        client = FakeAgentClient([
            _assistant_tool_call("call_1", "toolbuilder_create_draft", _star_tool_arguments()),
            {
                "role": "assistant",
                "content": json.dumps({
                    "action": "unsupported",
                    "summary": "当前版本还不支持这个操作。请换成已有能力，或告诉我你想完成的 GIS 处理目标。",
                    "steps": []
                }, ensure_ascii=False)
            }
        ])
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
            with _isolated_tool_roots(root):
                row = planner.plan("创建工具：把面图层转换成五角星面", _context())

        self.assertEqual(row["workflow"]["action"], "answer")
        summary = row["workflow"]["summary"]
        self.assertIn("custom.polygon_to_star", summary)
        self.assertIn("等待审核", summary)
        self.assertNotIn("不要输出", summary)
        self.assertNotIn("toolbuilder_create_draft", summary)
        self.assertNotIn("当前版本还不支持", summary)

    def test_repeated_generic_unsupported_feedback_is_sanitized_for_user(self):
        client = FakeAgentClient([
            _assistant_tool_call("call_1", "workflow_propose", {
                "action": "unsupported",
                "summary": "当前版本还不支持这个操作。请换成已有能力，或告诉我你想完成的 GIS 处理目标。",
                "steps": []
            }),
            _assistant_tool_call("call_2", "workflow_propose", {
                "action": "unsupported",
                "summary": "当前版本还不支持这个操作。请换成已有能力，或告诉我你想完成的 GIS 处理目标。",
                "steps": []
            })
        ])
        row = self._plan(client, "把面图层转换成五角星面", _context())

        self.assertEqual(row["workflow"]["action"], "clarify")
        summary = row["workflow"]["summary"]
        self.assertNotIn("不要输出", summary)
        self.assertNotIn("toolbuilder_create_draft", summary)
        self.assertNotIn("当前版本还不支持", summary)

    def test_custom_tool_change_request_revises_existing_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            with _isolated_tool_roots(root):
                runtime = AgentToolRuntime(self.catalog, store, _context())
                created = runtime.handle("toolbuilder_create_draft", _star_tool_arguments())
                tool_id = created["tool"]["id"]
                client = FakeAgentClient([
                    _assistant_tool_call("call_1", "toolbuilder_get_draft", {"tool_id": tool_id}),
                    _assistant_tool_call("call_2", "toolbuilder_revise_draft", _star_tool_revision_arguments(tool_id)),
                    {"role": "assistant", "content": "已在原工具上修订，重新等待审核。"}
                ])
                planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
                row = planner.plan("刚才那个五角星工具半径太大，改小一点，不要新建工具", _context())

        self.assertEqual(row["workflow"]["action"], "answer")
        tool_results = [item for item in row["agent_trace"] if item.get("type") == "tool"]
        self.assertEqual(tool_results[0]["name"], "toolbuilder_get_draft")
        self.assertEqual(tool_results[1]["name"], "toolbuilder_revise_draft")
        self.assertEqual(tool_results[1]["result"]["tool"]["id"], tool_id)
        self.assertEqual(tool_results[1]["result"]["tool"]["payload"]["revision"]["number"], 2)

    def test_custom_tool_failure_repair_request_revises_existing_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            with _isolated_tool_roots(root):
                runtime = AgentToolRuntime(self.catalog, store, _context())
                created = runtime.handle("toolbuilder_create_draft", _star_tool_arguments())
                tool_id = created["tool"]["id"]
                client = FakeAgentClient([
                    _assistant_tool_call("call_1", "toolbuilder_get_draft", {"tool_id": "custom.polygon_to_star"}),
                    _assistant_tool_call("call_2", "toolbuilder_revise_draft", _star_tool_revision_arguments(tool_id)),
                    {"role": "assistant", "content": "已根据执行失败信息修订原工具，等待审核。"}
                ])
                planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
                row = planner.plan(
                    "进入自定义工具开发修复流程。上一次执行 custom.polygon_to_star 失败：ERROR 000840: 该值不是 空间参考。请读取原工具并修订同一个工具。",
                    _context()
                )

        self.assertEqual(row["workflow"]["action"], "answer")
        tool_results = [item for item in row["agent_trace"] if item.get("type") == "tool"]
        self.assertEqual(tool_results[0]["name"], "toolbuilder_get_draft")
        self.assertEqual(tool_results[1]["name"], "toolbuilder_revise_draft")
        self.assertEqual(tool_results[1]["result"]["tool"]["id"], tool_id)

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

    def test_custom_unknown_argument_feedback_names_tool_and_allowed_arguments(self):
        self.catalog.operations["custom.feature_to_point"] = _custom_writes_data_spec()
        workflow = {
            "action": "execute",
            "summary": "bad",
            "steps": [
                _step("step_1", "custom.feature_to_point", {
                    "input_layer": "nanjing",
                    "output_name": "out",
                    "bad_argument": "x"
                }, "bad")
            ]
        }

        with self.assertRaisesRegex(ValidationError, "custom.feature_to_point.*bad_argument.*input_layer"):
            prepare_workflow(workflow, self.catalog, _context())

    def test_attribute_condition_rejects_contains_before_runtime_execution(self):
        workflow = {
            "action": "execute",
            "summary": "按名称选择。",
            "steps": [
                _step("step_1", "selection.select_by_attribute", {
                    "layer": "nanjing",
                    "where": {"field": "NAME", "op": "contains", "value": "京"}
                }, "选择名称包含京的要素")
            ]
        }
        with self.assertRaisesRegex(ValidationError, "contains"):
            prepare_workflow(workflow, self.catalog, _context())

    def test_attribute_condition_accepts_like_for_text_contains(self):
        workflow = {
            "action": "execute",
            "summary": "按名称选择。",
            "steps": [
                _step("step_1", "selection.select_by_attribute", {
                    "layer": "nanjing",
                    "where": {"field": "NAME", "op": "like", "value": "%京%"}
                }, "选择名称包含京的要素")
            ]
        }
        prepared = prepare_workflow(workflow, self.catalog, _context())

        self.assertEqual(prepared["steps"][0]["arguments"]["where"]["op"], "like")

    def test_attribute_condition_is_canonicalized_before_validation(self):
        workflow = {
            "action": "execute",
            "summary": "按名称和编号选择。",
            "steps": [
                _step("step_1", "selection.select_by_attribute", {
                    "layer": "nanjing",
                    "where": {
                        "and": [
                            {"field": "#NAME", "operator": "=", "value": "南京"},
                            {"field": "OBJECTID", "operator": ">", "value": "10"}
                        ]
                    }
                }, "选择")
            ]
        }
        prepared = prepare_workflow(workflow, self.catalog, _context())

        where = prepared["steps"][0]["arguments"]["where"]
        self.assertEqual(where["op"], "and")
        self.assertEqual(where["conditions"][0]["op"], "eq")
        self.assertNotIn("operator", where["conditions"][0])
        self.assertEqual(where["conditions"][0]["field"], "NAME")
        self.assertEqual(where["conditions"][1]["op"], "gt")

    def test_attribute_condition_rejects_like_on_numeric_field(self):
        workflow = {
            "action": "execute",
            "summary": "按编号模糊选择。",
            "steps": [
                _step("step_1", "selection.select_by_attribute", {
                    "layer": "nanjing",
                    "where": {"field": "OBJECTID", "op": "like", "value": "%1%"}
                }, "选择")
            ]
        }

        with self.assertRaisesRegex(ValidationError, "like 条件只能用于文本字段"):
            prepare_workflow(workflow, self.catalog, _context())

    def test_attribute_condition_rejects_non_numeric_value_for_numeric_field(self):
        workflow = {
            "action": "execute",
            "summary": "按编号选择。",
            "steps": [
                _step("step_1", "selection.select_by_attribute", {
                    "layer": "nanjing",
                    "where": {"field": "OBJECTID", "op": "gt", "value": "abc"}
                }, "选择")
            ]
        }

        with self.assertRaisesRegex(ValidationError, "必须是数字"):
            prepare_workflow(workflow, self.catalog, _context())

    def test_select_by_attribute_validates_condition_field(self):
        workflow = {
            "action": "execute",
            "summary": "按不存在字段选择。",
            "steps": [
                _step("step_1", "selection.select_by_attribute", {
                    "layer": "nanjing",
                    "where": {"field": "MISSING", "op": "eq", "value": "x"}
                }, "选择")
            ]
        }
        with self.assertRaisesRegex(ValidationError, "字段"):
            prepare_workflow(workflow, self.catalog, _context())

    def test_layer_profile_tool_returns_field_value_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            context = _context()
            context["layers"][0]["fields"] = [
                {"name": "b", "type": "String", "value_samples": ["xxx区k街道"]},
                {"name": "c", "type": "String", "value_samples": ["乔木用地"]}
            ]
            runtime = AgentToolRuntime(self.catalog, store, context)

            result = runtime.handle("arcgis_get_layer_profile", {"layer": "nanjing"})

        self.assertTrue(result["ok"])
        self.assertEqual(result["layer"]["fields"][0]["value_samples"], ["xxx区k街道"])

    def test_attribute_where_requires_layer_profile_before_final_workflow(self):
        context = _context()
        context["layers"][0]["fields"] = [
            {"name": "b", "type": "String", "value_samples": ["xxx区k街道"]},
            {"name": "c", "type": "String", "value_samples": ["乔木用地"]}
        ]
        client = FakeAgentClient([
            _assistant_tool_call("call_1", "workflow_propose", {
                "action": "execute",
                "summary": "选择 k 街道乔木。",
                "steps": [
                    _step("step_1", "selection.select_by_attribute", {
                        "layer": "nanjing",
                        "where": {"field": "b", "op": "like", "value": "%k街道%"}
                    }, "选择")
                ]
            }),
            _assistant_tool_call("call_2", "arcgis_get_layer_profile", {"layer": "nanjing"}),
            _assistant_tool_call("call_3", "workflow_propose", {
                "action": "execute",
                "summary": "选择 k 街道乔木。",
                "steps": [
                    _step("step_1", "selection.select_by_attribute", {
                        "layer": "nanjing",
                        "where": {
                            "op": "and",
                            "conditions": [
                                {"field": "b", "op": "like", "value": "%k街道%"},
                                {"field": "c", "op": "like", "value": "%乔木%"}
                            ]
                        }
                    }, "基于字段值样例选择 k 街道乔木")
                ]
            })
        ])

        row = self._plan(client, "查 nanjing 图层所有 k 街道的乔木", context)

        self.assertEqual(len(client.calls), 3)
        where = row["workflow"]["steps"][0]["arguments"]["where"]
        self.assertEqual(where["conditions"][1]["field"], "c")

    def test_ui_layer_and_field_markers_are_normalized(self):
        workflow = {
            "action": "execute",
            "summary": "按名称选择。",
            "steps": [
                _step("step_1", "selection.select_by_attribute", {
                    "layer": "@nanjing",
                    "where": {"field": "#NAME", "op": "like", "value": "%京%"}
                }, "选择")
            ]
        }
        prepared = prepare_workflow(workflow, self.catalog, _context())

        self.assertEqual(prepared["steps"][0]["arguments"]["layer"], "layer:nanjing")
        self.assertEqual(prepared["steps"][0]["arguments"]["where"]["field"], "NAME")

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

    def test_generated_output_add_layer_step_is_removed(self):
        self.catalog.operations["custom.feature_to_point"] = _custom_writes_data_spec()
        workflow = {
            "action": "execute",
            "summary": "将面图层转换为中心点。",
            "steps": [
                _step("step_1", "custom.feature_to_point", {
                    "input_layer": "nanjing",
                    "output_name": "taihucenterpoints",
                    "output_workspace": r"D:\Data\GeoPilotComplexTest\output"
                }, "面转点"),
                _step("step_2", "layer.add_layer", {
                    "path": r"D:\Data\GeoPilotComplexTest\output\ArcMapAI_Output.gdb\taihucenterpoints"
                }, "添加生成结果")
            ]
        }

        prepared = prepare_workflow(workflow, self.catalog, _context(is_saved=False))

        self.assertEqual(len(prepared["steps"]), 1)
        self.assertEqual(prepared["steps"][0]["operation"], "custom.feature_to_point")
        self.assertRegex(prepared["steps"][0]["arguments"]["output_name"], r"^taihucenterpoints_\d{8}_\d{6}$")

    def test_layer_reference_is_exact_and_normalized_to_layer_ref(self):
        self.catalog.operations["custom.feature_to_point"] = _custom_writes_data_spec()
        with tempfile.TemporaryDirectory() as directory:
            context = {
                "is_saved": False,
                "layers": [
                    _layer_with_ref("layer:0", "taihu_test_area_select"),
                    _layer_with_ref("layer:1", "taihu_test_area")
                ]
            }
            workflow = {
                "action": "execute",
                "summary": "将 taihu_test_area 转为中心点。",
                "steps": [
                    _step("step_1", "custom.feature_to_point", {
                        "input_layer": "taihu_test_area",
                        "output_name": "taihu_center_points",
                        "output_workspace": directory
                    }, "面转点")
                ]
            }

            prepared = prepare_workflow(workflow, self.catalog, context)

        self.assertEqual(prepared["steps"][0]["arguments"]["input_layer"], "layer:1")

    def test_layer_reference_does_not_fuzzy_match_similar_layer_names(self):
        self.catalog.operations["custom.feature_to_point"] = _custom_writes_data_spec()
        context = {
            "is_saved": False,
            "layers": [
                _layer_with_ref("layer:0", "taihu_test_area_select"),
                _layer_with_ref("layer:1", "taihu_test_area")
            ]
        }
        workflow = {
            "action": "execute",
            "summary": "将 taihu_test_area 转为中心点。",
            "steps": [
                _step("step_1", "custom.feature_to_point", {
                    "input_layer": "taihutestarea",
                    "output_name": "taihu_center_points",
                    "output_workspace": r"D:\out"
                }, "面转点")
            ]
        }

        with self.assertRaisesRegex(ValidationError, "精确匹配"):
            prepare_workflow(workflow, self.catalog, context)

    def test_added_layer_name_is_normalized_to_step_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            raster = pathlib.Path(directory) / "gblu(太湖).tif"
            raster.write_text("", encoding="utf-8")
            workflow = {
                "action": "execute",
                "summary": "打开 gblu(太湖).tif 并缩放到其位置。",
                "steps": [
                    {
                        "id": 1,
                        "operation": "layer.add_layer",
                        "arguments": {"path": str(raster)},
                        "reason": "添加图层"
                    },
                    {
                        "id": 2,
                        "operation": "view.zoom_to_layer",
                        "arguments": {"layer": "gblu(太湖)"},
                        "reason": "缩放到图层"
                    }
                ]
            }
            prepared = prepare_workflow(workflow, self.catalog, _context(is_saved=True))

        self.assertEqual(prepared["steps"][0]["id"], "1")
        self.assertEqual(prepared["steps"][1]["id"], "2")
        self.assertEqual(prepared["steps"][1]["arguments"]["layer"], "from_step:1")

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


if __name__ == "__main__":
    unittest.main()
