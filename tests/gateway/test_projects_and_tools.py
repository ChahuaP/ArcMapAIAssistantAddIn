import contextlib
import pathlib
import tempfile
import time
import unittest

from gateway_py3 import tool_builder
from gateway_py3.agent_tools import AgentToolRuntime
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.workflow_store import WorkflowStore


class ProjectAndToolTests(unittest.TestCase):
    def setUp(self):
        self.catalog = OperationCatalog()

    def test_project_list_files_uses_active_workdir(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            data_dir = root / "data"
            data_dir.mkdir()
            shp = data_dir / "roads.shp"
            shp.write_text("", encoding="utf-8")
            store = WorkflowStore(root / "workflows.sqlite")
            project = store.create_project("test", str(root))
            runtime = AgentToolRuntime(self.catalog, store, _context(), project=project)

            result = runtime.handle("project_list_files", {"relative_path": "data", "extensions": ["shp"]})

        self.assertTrue(result["ok"])
        self.assertEqual(result["files"][0]["path"], str(shp))
        self.assertEqual(result["files"][0]["layer_name"], "roads")

    def test_project_memory_is_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            project = store.create_project("test", str(root))
            runtime = AgentToolRuntime(self.catalog, store, _context(), project=project)

            result = runtime.handle("project_remember", {"content": "roads 是道路图层", "kind": "dataset"})
            memories = store.list_project_memories(project["id"])

        self.assertTrue(result["ok"])
        self.assertEqual(memories[0]["content"], "roads 是道路图层")

    def test_project_memory_is_compacted_when_it_grows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            project = store.create_project("test", str(root))

            for index in range(85):
                store.add_project_memory(project["id"], "第 %03d 条项目记忆" % index, kind="note")
            memories = store.list_project_memories(project["id"], limit=100)
            events = store.list_project_events(project["id"], limit=20)

        self.assertLessEqual(len(memories), 40)
        self.assertTrue(any(memory["kind"] == "summary" for memory in memories))
        self.assertTrue(any(event["event_type"] == "memory_compacted" for event in events))

    def test_delete_project_removes_project_state_and_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            project = store.create_project("test", str(root))
            store.add_project_memory(project["id"], "记住 roads", kind="dataset")
            store.add_project_event(project["id"], "custom", {"ok": True})
            store.create_draft("刷新", "hash", {"action": "clarify", "summary": "继续补充。", "steps": []}, [], mode="full_agent", project_id=project["id"])

            result = store.delete_project(project["id"])

            self.assertTrue(result["ok"])
            self.assertEqual(store.list_projects(), [])
            self.assertIsNone(store.get_active_project())
            self.assertEqual(store.list_recent(project_id=project["id"]), [])
            self.assertEqual(store.list_project_memories(project["id"]), [])
            self.assertEqual(store.list_project_events(project["id"]), [])

    def test_project_order_does_not_change_when_activated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            store = WorkflowStore(root / "workflows.sqlite")

            first = store.create_project("first", str(first_dir))
            time.sleep(0.01)
            second = store.create_project("second", str(second_dir))
            store.set_active_project(first["id"])
            projects = store.list_projects()

        self.assertEqual([project["id"] for project in projects], [second["id"], first["id"]])

    def test_workflow_order_does_not_change_when_status_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            first = store.create_draft("first", "hash", {"action": "clarify", "summary": "继续补充。", "steps": []}, [])
            time.sleep(0.01)
            second = store.create_draft("second", "hash", {"action": "clarify", "summary": "继续补充。", "steps": []}, [])

            store.approve(first["id"])
            rows = store.list_recent(limit=2)

        self.assertEqual([row["id"] for row in rows], [second["id"], first["id"]])

    def test_toolbuilder_creates_disabled_pending_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = {
                "id": "custom.demo_tool",
                "version": "0.1.0",
                "category": "custom",
                "summary": "测试工具",
                "model_card": "测试工具。",
                "parameters_schema": {"type": "object", "properties": {}, "additionalProperties": False},
                "context_requirements": {},
                "side_effects": "read_only",
                "output_policy": {},
                "executor": "will_be_overridden",
                "examples": []
            }

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": []
                })

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"]["status"], "pending_review")
        self.assertTrue(result["tool"]["payload"]["operation_spec"]["executor"].startswith("custom_tool:"))

    def test_toolbuilder_rejects_dangerous_executor_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_spec()

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "危险工具",
                    "capability": "删除文件",
                    "operation_spec": spec,
                    "executor_code": "import os\ndef execute(context, arguments, step_outputs):\n    os.remove('x')\n",
                    "tests": []
                })

        self.assertFalse(result["ok"])
        self.assertIn("不安全", result["error"])

    def test_toolbuilder_adds_python2_encoding_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": []
                })
                executor_path = pathlib.Path(result["tool"]["files"]["executor"])
                executor_code = executor_path.read_text(encoding="utf-8")

        self.assertTrue(result["ok"])
        self.assertTrue(executor_code.startswith("# -*- coding: utf-8 -*-\n"))

    def test_toolbuilder_rejects_python3_only_executor_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "使用 Python3 语法",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    raise ValueError(f\"bad {arguments}\")\n",
                    "tests": []
                })

        self.assertFalse(result["ok"])
        self.assertIn("Python 2.7", result["error"])

    def test_toolbuilder_rejects_arcgis_pro_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "使用 ArcGIS Pro API",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.mp.ArcGISProject('CURRENT')\n    return {'ok': True}\n",
                    "tests": []
                })

        self.assertFalse(result["ok"])
        self.assertIn("arcpy.mp", result["error"])

    def test_writes_data_tool_must_use_runtime_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_spec()
            spec["side_effects"] = "writes_data"
            spec["parameters_schema"] = {
                "type": "object",
                "required": ["input_layer", "output_name"],
                "properties": {
                    "input_layer": {"type": "layer"},
                    "output_name": {"type": "string"}
                },
                "additionalProperties": False
            }

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "自己拼输出路径",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'output': arguments['output_name']}\n",
                    "tests": []
                })

        self.assertFalse(result["ok"])
        self.assertIn("output_path", result["error"])

    def test_output_path_full_variable_does_not_satisfy_runtime_output_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "变量名假装 output_path",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    output_path_full = arguments['output_workspace'] + arguments['output_name']\n    return {'output': output_path_full}\n",
                    "tests": []
                })

        self.assertFalse(result["ok"])
        self.assertIn("arguments[\"output_path\"]", result["error"])

    def test_writes_data_executor_must_not_read_output_workspace_or_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "自己拼输出路径",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.CopyFeatures_management(arguments['input_layer'], arguments['output_path'])\n    return {'workspace': arguments['output_workspace']}\n",
                    "tests": []
                })

        self.assertFalse(result["ok"])
        self.assertIn("output_workspace", result["error"])

    def test_toolbuilder_rejects_get_output_on_layer_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "把 Layer 当 Result",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    input_layer = arguments['input_layer']\n    spatial_ref = input_layer.getOutput(0)\n    return {'output': arguments['output_path'], 'spatial_ref': spatial_ref}\n",
                    "tests": []
                })

        self.assertFalse(result["ok"])
        self.assertIn("getOutput", result["error"])

    def test_writes_data_tool_must_require_output_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["parameters_schema"]["required"] = ["input_layer"]

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "输出名未必填",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.FeatureToPoint_management(arguments['input_layer'], arguments['output_path'], 'CENTROID')\n    return {'output': arguments['output_path']}\n",
                    "tests": []
                })

        self.assertFalse(result["ok"])
        self.assertIn("output_name", result["error"])

    def test_toolbuilder_validates_pending_files_before_enable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_spec()
            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": []
                })
                tool = result["tool"]
                executor_path = pathlib.Path(tool["files"]["executor"])
                executor_path.write_text("import subprocess\ndef execute(context, arguments, step_outputs):\n    return {'ok': True}\n", encoding="utf-8")

                with self.assertRaises(Exception):
                    tool_builder.enable_tool(store, tool["id"])

    def test_toolbuilder_deletes_pending_tool_files_and_row(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": []
                })
                tool_id = result["tool"]["id"]
                pending_dir = tool_builder.PENDING_ROOT / tool_id

                deleted = tool_builder.delete_tool(store, tool_id)

                self.assertTrue(deleted["ok"])
                self.assertFalse(pending_dir.exists())
                with self.assertRaises(KeyError):
                    store.get_pending_tool(tool_id)

    def test_toolbuilder_deletes_enabled_tool_and_catalog_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": []
                })
                tool_id = result["tool"]["id"]
                tool_builder.enable_tool(store, tool_id)
                enabled_dir = tool_builder.ENABLED_ROOT / tool_id
                self.assertIn("custom.demo_tool", OperationCatalog().operations)

                deleted = tool_builder.delete_tool(store, tool_id)

                self.assertTrue(deleted["ok"])
                self.assertFalse(enabled_dir.exists())
                self.assertNotIn("custom.demo_tool", OperationCatalog().operations)

    def test_enabled_tool_is_loaded_into_catalog_with_canonical_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_spec()
            spec["parameters_schema"] = {
                "input_layer": {"type": "layer", "required": True, "description": "输入图层"},
                "output_name": {"type": "string", "required": True, "description": "输出名称"},
                "output_workspace": {"type": "string", "required": False, "description": "输出目录"}
            }
            spec["context_requirements"] = "需要图层"
            spec["output_policy"] = "写出新数据"

            with _temporary_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": []
                })
                tool_builder.enable_tool(store, result["tool"]["id"])
                catalog = OperationCatalog()

        operation = catalog.get("custom.demo_tool")
        schema = operation["parameters_schema"]
        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["input_layer", "output_name"])
        self.assertEqual(schema["properties"]["input_layer"]["type"], "string")
        self.assertEqual(operation["context_requirements"], {})
        self.assertEqual(operation["output_policy"], {})


def _context():
    return {"is_saved": True, "layers": []}


def _custom_spec():
    return {
        "id": "custom.demo_tool",
        "version": "0.1.0",
        "category": "custom",
        "summary": "测试工具",
        "model_card": "测试工具。",
        "parameters_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "context_requirements": {},
        "side_effects": "read_only",
        "output_policy": {},
        "executor": "will_be_overridden",
        "examples": []
    }


def _custom_writes_data_spec():
    spec = _custom_spec()
    spec["side_effects"] = "writes_data"
    spec["parameters_schema"] = {
        "type": "object",
        "required": ["input_layer", "output_name"],
        "properties": {
            "input_layer": {"type": "layer"},
            "output_name": {"type": "string"}
        },
        "additionalProperties": False
    }
    return spec


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
