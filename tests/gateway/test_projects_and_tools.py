import pathlib
import tempfile
import time
import unittest

from gateway_py3 import tool_builder
from gateway_py3.agent_tools import AgentToolRuntime
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.workflow_store import WorkflowStore

from gateway.tool_builder_test_utils import (
    context as _context,
    custom_spec as _custom_spec,
    custom_writes_data_spec as _custom_writes_data_spec,
    isolated_tool_roots as _isolated_tool_roots,
    polygon_to_star_executor_code as _polygon_to_star_executor_code,
    polygon_to_star_spec as _polygon_to_star_spec,
    polygon_to_star_tests as _polygon_to_star_tests,
    review_tests as _review_tests,
)


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

    def test_clear_project_conversation_removes_project_context_but_keeps_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            project = store.create_project("test", str(root))
            store.add_project_memory(project["id"], "记住 roads", kind="dataset")
            store.add_project_event(project["id"], "custom", {"ok": True})
            store.create_draft("刷新", "hash", {"action": "clarify", "summary": "继续补充。", "steps": []}, [], mode="full_agent", project_id=project["id"])

            result = store.clear_workflows(project_id=project["id"], mode="full_agent")

            self.assertTrue(result["ok"])
            self.assertEqual(result["cleared"]["workflows"], 1)
            self.assertEqual(result["cleared"]["project_memories"], 1)
            self.assertEqual(result["cleared"]["project_events"], 3)
            self.assertIsNotNone(store.get_project(project["id"]))
            self.assertEqual(store.get_active_project()["id"], project["id"])
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
                "examples": [{"output_name": "demo_output"}]
            }

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
                })

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"]["status"], "pending_review")
        self.assertTrue(result["tool"]["payload"]["operation_spec"]["executor"].startswith("custom_tool:"))
        self.assertEqual(result["tool"]["payload"]["review"]["contract_version"], "2026-05-23")

    def test_toolbuilder_rejects_empty_review_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": []
                })

        self.assertFalse(result["ok"])
        self.assertIn("review test", result["error"])

    def test_toolbuilder_rejects_empty_operation_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_spec()
            spec["examples"] = []

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("examples", result["error"])

    def test_toolbuilder_get_and_revise_updates_same_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                created = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
                })
                tool_id = created["tool"]["id"]
                loaded = runtime.handle("toolbuilder_get_draft", {"tool_id": tool_id})
                revised_spec = _custom_spec()
                revised_spec["summary"] = "修订后的测试工具"
                revised = runtime.handle("toolbuilder_revise_draft", {
                    "tool_id": tool_id,
                    "change_summary": "修正返回字段",
                    "name": "测试工具",
                    "capability": "执行修订后的测试能力",
                    "operation_spec": revised_spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True, 'revision': 2}\n",
                    "tests": _review_tests()
                })
                pending_count = len(store.list_pending_tools())

        self.assertTrue(loaded["ok"])
        self.assertIn("def execute", loaded["tool"]["executor_code"])
        self.assertTrue(revised["ok"], revised.get("error"))
        self.assertEqual(revised["tool"]["id"], tool_id)
        self.assertEqual(revised["tool"]["status"], "pending_review")
        self.assertEqual(revised["tool"]["payload"]["revision"]["number"], 2)
        self.assertEqual(pending_count, 1)

    def test_toolbuilder_get_and_revise_accept_operation_id(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                created = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
                })
                loaded = runtime.handle("toolbuilder_get_draft", {"tool_id": "custom.demo_tool"})
                revised_spec = _custom_spec()
                revised_spec["summary"] = "按 operation id 修订"
                revised = runtime.handle("toolbuilder_revise_draft", {
                    "tool_id": "custom.demo_tool",
                    "change_summary": "按 operation id 修订工具",
                    "name": "测试工具",
                    "capability": "执行修订后的测试能力",
                    "operation_spec": revised_spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True, 'by_operation_id': True}\n",
                    "tests": _review_tests()
                })

        self.assertTrue(loaded["ok"], loaded.get("error"))
        self.assertEqual(loaded["tool"]["id"], created["tool"]["id"])
        self.assertTrue(revised["ok"], revised.get("error"))
        self.assertEqual(revised["tool"]["id"], created["tool"]["id"])
        self.assertEqual(revised["tool"]["payload"]["revision"]["number"], 2)

    def test_toolbuilder_get_accepts_custom_executor_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                created = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
                })
                executor = created["tool"]["payload"]["operation_spec"]["executor"]
                loaded = runtime.handle("toolbuilder_get_draft", {"tool_id": executor})

        self.assertTrue(loaded["ok"], loaded.get("error"))
        self.assertEqual(loaded["tool"]["id"], created["tool"]["id"])
        self.assertIn("def execute", loaded["tool"]["executor_code"])

    def test_toolbuilder_duplicate_operation_id_revises_existing_tool(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                first = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
                })
                second_spec = _custom_spec()
                second_spec["summary"] = "同 ID 修订"
                second = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行同 ID 修订能力",
                    "operation_spec": second_spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True, 'same_id': True}\n",
                    "tests": _review_tests()
                })
                pending_count = len(store.list_pending_tools())

        self.assertTrue(second["ok"], second.get("error"))
        self.assertEqual(second["tool"]["id"], first["tool"]["id"])
        self.assertEqual(second["tool"]["payload"]["revision"]["number"], 2)
        self.assertEqual(pending_count, 1)

    def test_toolbuilder_revising_enabled_tool_removes_enabled_copy_until_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                created = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
                })
                tool_id = created["tool"]["id"]
                tool_builder.enable_tool(store, tool_id)
                self.assertTrue((tool_builder.ENABLED_ROOT / tool_id).exists())

                revised = runtime.handle("toolbuilder_revise_draft", {
                    "tool_id": tool_id,
                    "change_summary": "修复启用后发现的问题",
                    "name": "测试工具",
                    "capability": "修复后的能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True, 'fixed': True}\n",
                    "tests": _review_tests()
                })
                enabled_exists_after_revision = (tool_builder.ENABLED_ROOT / tool_id).exists()

        self.assertTrue(revised["ok"], revised.get("error"))
        self.assertEqual(revised["tool"]["status"], "pending_review")
        self.assertFalse(enabled_exists_after_revision)

    def test_catalog_schema_lookup_corrects_toolbuilder_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            result = runtime.handle("catalog_get_operation_schema", {"operation_id": "toolbuilder.create_draft"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "wrong_tool_namespace")
        self.assertIn("toolbuilder_create_draft", result["error"])

    def test_catalog_schema_lookup_for_pending_custom_tool_reports_not_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                created = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
                })
                result = runtime.handle("catalog_get_operation_schema", {"operation_id": "custom.demo_tool"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "custom_tool_not_enabled")
        self.assertEqual(result["operation_id"], "custom.demo_tool")
        self.assertEqual(result["tool_id"], created["tool"]["id"])
        self.assertNotIn("error", result)
        self.assertIn("不要说当前版本不支持", result["instruction"])

    def test_toolbuilder_rejects_dangerous_executor_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_spec()

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "危险工具",
                    "capability": "删除文件",
                    "operation_spec": spec,
                    "executor_code": "import os\ndef execute(context, arguments, step_outputs):\n    os.remove('x')\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("不安全", result["error"])

    def test_toolbuilder_adds_python2_encoding_header(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
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

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "使用 Python3 语法",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    raise ValueError(f\"bad {arguments}\")\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("Python 2.7", result["error"])

    def test_toolbuilder_rejects_python3_exception_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "使用 Python3 异常",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    try:\n        return {'ok': True}\n    except FileNotFoundError:\n        return {'ok': False}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("FileNotFoundError", result["error"])
        self.assertIn("Python 2.7", result["error"])

    def test_toolbuilder_rejects_python3_os_makedirs_exist_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "使用 Python3 API",
                    "operation_spec": _custom_spec(),
                    "executor_code": "import os\ndef execute(context, arguments, step_outputs):\n    os.makedirs('x', exist_ok=True)\n    return {'ok': True}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("exist_ok", result["error"])

    def test_toolbuilder_rejects_undefined_helper_function(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "调用不存在的 helper",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return build_output(arguments)\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("未定义函数", result["error"])
        self.assertIn("build_output", result["error"])

    def test_toolbuilder_rejects_unknown_arcpy_function(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "编造 ArcPy 函数",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return arcpy.MakeStarPolygon_management('x')\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("未确认的 ArcMap ArcPy 函数", result["error"])

    def test_toolbuilder_rejects_arcgis_pro_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "使用 ArcGIS Pro API",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.mp.ArcGISProject('CURRENT')\n    return {'ok': True}\n",
                    "tests": _review_tests()
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

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "自己拼输出路径",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'output': arguments['output_name']}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("output_path", result["error"])

    def test_output_path_full_variable_does_not_satisfy_runtime_output_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "变量名假装 output_path",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    output_path_full = arguments['output_workspace'] + arguments['output_name']\n    return {'output': output_path_full}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("arguments[\"output_path\"]", result["error"])

    def test_writes_data_executor_must_not_read_output_workspace_or_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "自己拼输出路径",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.CopyFeatures_management(arguments['input_layer'], arguments['output_path'])\n    return {'workspace': arguments['output_workspace']}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("output_workspace", result["error"])

    def test_writes_data_executor_rejects_misspelled_output_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "读取拼错的输出目录参数",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    output_path = arguments['output_path']\n    output_folder = arguments['outputfolder']\n    arcpy.CopyFeatures_management(arguments['input_layer'], output_path)\n    return {'output': output_path, 'folder': output_folder}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertTrue(result["repairable"])
        self.assertIn("outputfolder", result["error"])

    def test_toolbuilder_rejects_distance_parameter_without_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["parameters_schema"]["properties"]["radius"] = {"type": "number", "description": "外接圆半径"}

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "半径没有单位",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.CopyFeatures_management(arguments['input_layer'], arguments['output_path'])\n    return {'output': arguments['output_path']}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("radius_unit", result["error"])

    def test_toolbuilder_rejects_unit_test_missing_distance_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["parameters_schema"]["properties"]["radius"] = {"type": "number", "description": "外接圆半径"}
            spec["parameters_schema"]["properties"]["radius_unit"] = {
                "type": "string",
                "enum": ["map_units", "degrees", "meters"],
                "description": "半径单位"
            }
            tests = _review_tests()
            tests[0]["arguments"]["radius"] = 0.001

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "测试没有覆盖单位",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.CopyFeatures_management(arguments['input_layer'], arguments['output_path'])\n    return {'output': arguments['output_path']}\n",
                    "tests": tests
                })

        self.assertFalse(result["ok"])
        self.assertIn("radius_unit", result["error"])

    def test_toolbuilder_accepts_distance_parameter_with_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["parameters_schema"]["properties"]["radius"] = {"type": "number", "description": "外接圆半径"}
            spec["parameters_schema"]["properties"]["radius_unit"] = {
                "type": "string",
                "enum": ["map_units", "degrees", "meters"],
                "description": "半径单位"
            }
            tests = _review_tests()
            tests[0]["arguments"]["radius"] = 0.001
            tests[0]["arguments"]["radius_unit"] = "degrees"

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "正确工具",
                    "capability": "半径带单位",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.CopyFeatures_management(arguments['input_layer'], arguments['output_path'])\n    return {'output': arguments['output_path']}\n",
                    "tests": tests
                })

        self.assertTrue(result["ok"], result.get("error"))

    def test_toolbuilder_rejects_get_output_on_layer_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "把 Layer 当 Result",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    input_layer = arguments['input_layer']\n    spatial_ref = input_layer.getOutput(0)\n    return {'output': arguments['output_path'], 'spatial_ref': spatial_ref}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("getOutput", result["error"])

    def test_toolbuilder_rejects_create_featureclass_invalid_spatial_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            executor_code = """def execute(context, arguments, step_outputs):
    import os
    input_layer = arguments["input_layer"]
    output_path = arguments["output_path"]
    spatial_reference = context.get("spatial_reference")
    arcpy.CreateFeatureclass_management(os.path.dirname(output_path), os.path.basename(output_path), "POLYGON", "", "DISABLED", "DISABLED", spatial_reference)
    return {"output": output_path}
"""

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_writes_data_spec(),
                    "executor_code": executor_code,
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("arcpy.Describe", result["error"])
        self.assertIn("spatial_reference", result["error"])

    def test_toolbuilder_rejects_create_featureclass_full_output_path_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            executor_code = """def execute(context, arguments, step_outputs):
    input_layer = arguments["input_layer"]
    output_path = arguments["output_path"]
    spatial_reference = arcpy.Describe(input_layer).spatialReference
    arcpy.CreateFeatureclass_management(output_path, "bad_name", "POLYGON", "", "DISABLED", "DISABLED", spatial_reference)
    return {"output": output_path}
"""

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_writes_data_spec(),
                    "executor_code": executor_code,
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("os.path.dirname", result["error"])

    def test_toolbuilder_rejects_reserved_oid_field_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "创建系统字段",
                    "operation_spec": _custom_writes_data_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.CopyFeatures_management(arguments['input_layer'], arguments['output_path'])\n    arcpy.AddField_management(arguments['output_path'], 'OBJECTID', 'LONG')\n    return {'output': arguments['output_path']}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("系统字段", result["error"])

    def test_writes_data_tool_must_require_output_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["parameters_schema"]["required"] = ["input_layer"]

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误工具",
                    "capability": "输出名未必填",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.FeatureToPoint_management(arguments['input_layer'], arguments['output_path'], 'CENTROID')\n    return {'output': arguments['output_path']}\n",
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("output_name", result["error"])

    def test_toolbuilder_validates_pending_files_before_enable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_spec()
            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
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

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
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

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": _custom_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
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

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "测试工具",
                    "capability": "执行测试能力",
                    "operation_spec": spec,
                    "executor_code": "def execute(context, arguments, step_outputs):\n    return {'ok': True}\n",
                    "tests": _review_tests()
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

    def test_writes_data_tool_gets_managed_output_workspace_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "写数据工具",
                    "capability": "写出数据",
                    "operation_spec": _custom_writes_data_spec(),
                    "executor_code": "def execute(context, arguments, step_outputs):\n    arcpy.CopyFeatures_management(arguments['input_layer'], arguments['output_path'])\n    return {'output': arguments['output_path']}\n",
                    "tests": _review_tests()
                })
                tool_builder.enable_tool(store, result["tool"]["id"])
                catalog = OperationCatalog()

        schema = catalog.get("custom.demo_tool")["parameters_schema"]
        self.assertIn("output_workspace", schema["properties"])
        self.assertNotIn("output_workspace", schema["required"])

    def test_polygon_to_star_custom_tool_contract_can_be_created_and_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "面转五角星面",
                    "capability": "将每个输入面按中心点转换为一个五角星面。",
                    "operation_spec": _polygon_to_star_spec(),
                    "executor_code": _polygon_to_star_executor_code(),
                    "tests": _polygon_to_star_tests()
                })
                self.assertTrue(result["ok"], result.get("error"))
                tool_builder.enable_tool(store, result["tool"]["id"])
                catalog = OperationCatalog()

        operation = catalog.get("custom.polygon_to_star")
        self.assertEqual(operation["side_effects"], "writes_data")
        schema = operation["parameters_schema"]
        self.assertIn("input_layer", schema["required"])
        self.assertIn("output_name", schema["required"])
        self.assertEqual(schema["properties"]["input_layer"]["x-geopilot-kind"], "layer")


if __name__ == "__main__":
    unittest.main()
