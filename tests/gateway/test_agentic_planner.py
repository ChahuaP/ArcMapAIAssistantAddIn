import contextlib
import json
import pathlib
import tempfile
import unittest

from gateway_py3 import tool_builder
from gateway_py3.agent_tools import AgentToolError, AgentToolRuntime
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.file_resolver import FileResolver
from gateway_py3.planner import AgenticPlanner, SYSTEM_PROMPT
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

    def test_prompt_treats_explicit_degree_radius_as_actionable(self):
        self.assertIn("外接圆半径0.001度", SYSTEM_PROMPT)
        self.assertIn("radius_unit", SYSTEM_PROMPT)
        self.assertIn("geographic", SYSTEM_PROMPT)

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
            with _temporary_tool_roots(root):
                row = planner.plan("创建工具：将 taihu test area 面图层转换为五角星面图层，每个面根据中心点生成一个五角星面", _context())

        self.assertEqual(row["workflow"]["action"], "answer")
        tool_results = [item for item in row["agent_trace"] if item.get("type") == "tool"]
        self.assertEqual(tool_results[0]["result"]["status"], "wrong_tool_namespace")
        self.assertTrue(tool_results[1]["result"]["ok"])
        self.assertEqual(tool_results[1]["result"]["tool"]["payload"]["operation_spec"]["id"], "custom.polygon_to_star")

    def test_custom_tool_change_request_revises_existing_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            with _temporary_tool_roots(root):
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

    def test_full_agent_project_output_workspace_is_applied_to_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            project = store.create_project("project", str(root))
            client = FakeAgentClient([
                _assistant_tool_call("call_1", "workflow_propose", {
                    "action": "execute",
                    "summary": "将对 nanjing 生成缓冲区。",
                    "steps": [
                        _step("step_1", "analysis.buffer", {
                            "input_layer": "nanjing",
                            "distance": "10 Meters",
                            "output_name": "nanjing_buffer"
                        }, "缓冲")
                    ]
                })
            ])
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
            row = planner.plan("给 nanjing 做 10 米缓冲区", _context(is_saved=False), mode="full_agent", project_id=project["id"])

            arguments = row["workflow"]["steps"][0]["arguments"]
            self.assertEqual(arguments["output_workspace"], str(root / "GeoPilot_Output"))
            self.assertTrue((root / "GeoPilot_Output").exists())

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
                    "output_workspace": r"D:\out"
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


def _layer_with_ref(layer_ref, name):
    layer = _layer(name)
    layer["layer_ref"] = layer_ref
    return layer


def _custom_writes_data_spec():
    return {
        "id": "custom.feature_to_point",
        "version": "0.1.0",
        "category": "custom",
        "summary": "面转点",
        "model_card": "把面图层转换为中心点，并保留属性。",
        "parameters_schema": {
            "type": "object",
            "required": ["input_layer", "output_name"],
            "properties": {
                "input_layer": {"type": "string"},
                "output_name": {"type": "string"},
                "output_workspace": {"type": "string"}
            },
            "additionalProperties": False
        },
        "context_requirements": {"requires_layers": True},
        "side_effects": "writes_data",
        "output_policy": {},
        "executor": "custom_tool:demo:execute",
        "examples": [{"input_layer": "nanjing", "output_name": "nanjing_points"}]
    }


def _star_tool_arguments():
    return {
        "name": "面转五角星面",
        "capability": "将输入面图层的每个面按中心点生成一个五角星面。",
        "operation_spec": {
            "id": "custom.polygon_to_star",
            "version": "0.1.0",
            "category": "custom",
            "summary": "面要素转五角星面",
            "model_card": "将输入面图层的每个面按中心点和外接范围生成一个五角星面。",
            "parameters_schema": {
                "type": "object",
                "required": ["input_layer", "output_name"],
                "properties": {
                    "input_layer": {"type": "layer"},
                    "output_name": {"type": "string"},
                    "output_workspace": {"type": "string"},
                    "radius_ratio": {"type": "number"}
                },
                "additionalProperties": False
            },
            "context_requirements": {"requires_layers": True, "geometry_type": "Polygon"},
            "side_effects": "writes_data",
            "output_policy": {"type": "feature_class", "geometry_type": "Polygon"},
            "executor": "will_be_overridden",
            "examples": [
                {
                    "input_layer": "taihu_test_area",
                    "output_name": "taihu_stars",
                    "radius_ratio": 0.35
                }
            ]
        },
        "executor_code": """# -*- coding: utf-8 -*-
import math
import os

INNER_RATIO = 0.3819660112501051


def execute(context, arguments, step_outputs):
    input_layer = arguments["input_layer"]
    output_path = arguments["output_path"]
    radius_ratio = float(arguments.get("radius_ratio", 0.35))
    spatial_reference = arcpy.Describe(input_layer).spatialReference
    arcpy.CreateFeatureclass_management(os.path.dirname(output_path), os.path.basename(output_path), "POLYGON", "", "DISABLED", "DISABLED", spatial_reference)
    arcpy.AddField_management(output_path, "SRC_OID", "LONG")
    count = 0
    with arcpy.da.SearchCursor(input_layer, ["OID@", "SHAPE@"]) as search_cursor:
        with arcpy.da.InsertCursor(output_path, ["SRC_OID", "SHAPE@"]) as insert_cursor:
            for source_oid, geometry in search_cursor:
                extent = geometry.extent
                radius = min(float(extent.XMax - extent.XMin), float(extent.YMax - extent.YMin)) * radius_ratio
                star = _star_polygon(geometry.trueCentroid.X, geometry.trueCentroid.Y, radius, spatial_reference)
                insert_cursor.insertRow([source_oid, star])
                count += 1
    return {"output": output_path, "created_count": count}


def _star_polygon(center_x, center_y, radius, spatial_reference):
    points = arcpy.Array()
    for index in range(10):
        angle = -math.pi / 2.0 + index * math.pi / 5.0
        current_radius = radius if index % 2 == 0 else radius * INNER_RATIO
        points.add(arcpy.Point(center_x + math.cos(angle) * current_radius, center_y + math.sin(angle) * current_radius))
    points.add(points.getObject(0))
    return arcpy.Polygon(points, spatial_reference)
""",
        "tests": [
            {
                "name": "one star per polygon",
                "arguments": {
                    "input_layer": "layer:taihu test area",
                    "output_name": "taihu_stars",
                    "radius_ratio": 0.35
                },
                "expected": {"output_geometry": "Polygon", "created_count": "same as input polygon count"},
                "assertions": [
                    "output is written to arguments['output_path']",
                    "each source polygon creates one star polygon"
                ]
            }
        ]
    }


def _star_tool_revision_arguments(tool_id):
    arguments = _star_tool_arguments()
    arguments["tool_id"] = tool_id
    arguments["change_summary"] = "默认半径比例从 0.35 改为 0.25"
    arguments["executor_code"] = arguments["executor_code"].replace(
        'arguments.get("radius_ratio", 0.35)',
        'arguments.get("radius_ratio", 0.25)'
    )
    return arguments


@contextlib.contextmanager
def _temporary_tool_roots(root):
    old_pending = tool_builder.PENDING_ROOT
    old_enabled = tool_builder.ENABLED_ROOT
    tool_builder.PENDING_ROOT = root / "pending_tools"
    tool_builder.ENABLED_ROOT = root / "enabled_tools"
    try:
        yield
    finally:
        tool_builder.PENDING_ROOT = old_pending
        tool_builder.ENABLED_ROOT = old_enabled


if __name__ == "__main__":
    unittest.main()
