import pathlib
import tempfile
import unittest

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.output_folder_resolver import OutputFolderResolver
from gateway_py3.planner import AgenticPlanner
from gateway_py3.validators import ValidationError, prepare_workflow
from gateway_py3.workflow_store import WorkflowStore

from gateway.planner_test_utils import (
    FakeAgentClient,
    assistant_tool_call as _assistant_tool_call,
    context as _context,
    step as _step,
)


class AgenticPlannerOutputTests(unittest.TestCase):
    def setUp(self):
        self.catalog = OperationCatalog()

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

    def test_analysis_write_can_target_shapefile_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = {
                "action": "execute",
                "summary": "将缓冲区导出为 shp。",
                "steps": [
                    _step("step_1", "analysis.buffer", {
                        "input_layer": "nanjing",
                        "distance": "10 Meters",
                        "output_name": "nanjing_buffer",
                        "output_format": "shp",
                        "output_folder": directory
                    }, "缓冲并写出 shp")
                ]
            }

            prepared = prepare_workflow(workflow, self.catalog, _context(is_saved=False))

        arguments = prepared["steps"][0]["arguments"]
        self.assertEqual(arguments["output_format"], "shp")
        self.assertEqual(arguments["output_folder"], directory)

    def test_write_workflow_requires_model_chosen_output_name(self):
        workflow = {
            "action": "execute",
            "summary": "将对 nanjing 生成缓冲区。",
            "steps": [
                _step("step_1", "analysis.buffer", {
                    "input_layer": "nanjing",
                    "distance": "10 Meters"
                }, "缓冲")
            ]
        }
        with self.assertRaisesRegex(ValidationError, "output_name"):
            prepare_workflow(workflow, self.catalog, _context(is_saved=True))

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

    def test_output_folder_resolve_result_is_used_for_export_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            desktop = root / "Desktop"
            output = desktop / "test"
            output.mkdir(parents=True)
            store = WorkflowStore(root / "workflows.sqlite")
            client = FakeAgentClient([
                _assistant_tool_call("call_1", "output_folder_resolve", {
                    "known_folder": "desktop",
                    "folder_name": "test"
                }),
                _assistant_tool_call("call_2", "workflow_propose", {
                    "action": "execute",
                    "summary": "按社区导出 KMZ。",
                    "steps": [
                        _step("step_1", "export.split_by_field", {
                            "layer": "nanjing",
                            "field": "NAME",
                            "output_name": "community_kmz",
                            "output_format": "kmz",
                            "output_folder": str(output),
                            "name_template": "{value}永农"
                        }, "按字段拆分导出 KMZ")
                    ]
                })
            ])
            folder_resolver = OutputFolderResolver({"desktop": desktop})
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store, output_folder_resolver=folder_resolver)
            row = planner.plan("按社区分别导出为 kml 到桌面的 test 文件夹", _context(is_saved=False))

        self.assertEqual(row["workflow"]["action"], "execute")
        self.assertEqual(row["workflow"]["steps"][0]["arguments"]["output_folder"], str(output))
        tool_names = [item["name"] for item in row["agent_trace"] if item.get("type") == "tool"]
        self.assertEqual(tool_names, ["output_folder_resolve"])

    def test_full_agent_rejects_invalid_user_output_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            project = store.create_project("project", str(root))
            client = FakeAgentClient([
                _assistant_tool_call("call_1", "workflow_propose", {
                    "action": "execute",
                    "summary": "按社区导出 KMZ。",
                    "steps": [
                        _step("step_1", "export.split_by_field", {
                            "layer": "nanjing",
                            "field": "NAME",
                            "output_name": "community_kmz",
                            "output_format": "kmz",
                            "output_folder": str(root / "missing" / "test"),
                            "name_template": "{value}永农"
                        }, "按字段拆分导出 KMZ")
                    ]
                }),
                _assistant_tool_call("call_2", "workflow_propose", {
                    "action": "clarify",
                    "summary": "桌面的 test 文件夹不存在。请先创建该文件夹，或告诉我一个已有输出文件夹。",
                    "steps": []
                })
            ])
            planner = AgenticPlanner(catalog=self.catalog, client=client, store=store)
            row = planner.plan("按社区导出 kml", _context(is_saved=False), mode="full_agent", project_id=project["id"])

            self.assertEqual(row["workflow"]["action"], "clarify")
            self.assertIn("test 文件夹不存在", row["workflow"]["summary"])

    def test_split_by_field_workflow_is_prepared_with_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = {
                "action": "execute",
                "summary": "按字段拆分导出。",
                "steps": [
                    _step("step_1", "export.split_by_field", {
                        "layer": "nanjing",
                        "field": "NAME",
                        "output_name": "nanjing_by_name",
                        "output_format": "shp",
                        "output_folder": directory
                    }, "按 NAME 字段拆分导出")
                ]
            }
            prepared = prepare_workflow(workflow, self.catalog, _context(is_saved=True))

        output_name = prepared["steps"][0]["arguments"]["output_name"]
        self.assertRegex(output_name, r"^nanjing_by_name_\d{8}_\d{6}$")

    def test_split_by_field_kmz_allows_chinese_name_template(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = {
                "action": "execute",
                "summary": "按社区拆分导出 KML。",
                "steps": [
                    _step("step_1", "export.split_by_field", {
                        "layer": "nanjing",
                        "field": "NAME",
                        "output_name": "community_kmz",
                        "output_format": "kmz",
                        "output_folder": directory,
                        "name_template": "{value}永农"
                    }, "按 NAME 字段拆分导出 KMZ")
                ]
            }
            prepared = prepare_workflow(workflow, self.catalog, _context(is_saved=True))

        arguments = prepared["steps"][0]["arguments"]
        self.assertEqual(arguments["output_format"], "kmz")
        self.assertEqual(arguments["name_template"], "{value}永农")
        self.assertRegex(arguments["output_name"], r"^community_kmz_\d{8}_\d{6}$")

    def test_missing_output_folder_is_rejected_before_runtime_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = {
                "action": "execute",
                "summary": "按社区拆分导出 KML。",
                "steps": [
                    _step("step_1", "export.split_by_field", {
                        "layer": "nanjing",
                        "field": "NAME",
                        "output_name": "community_kmz",
                        "output_format": "kmz",
                        "output_folder": str(pathlib.Path(directory) / "missing")
                    }, "按 NAME 字段拆分导出 KMZ")
                ]
            }

            with self.assertRaisesRegex(ValidationError, "输出文件夹不存在"):
                prepare_workflow(workflow, self.catalog, _context(is_saved=True))


if __name__ == "__main__":
    unittest.main()
