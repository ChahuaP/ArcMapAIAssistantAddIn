import contextlib
import pathlib
import tempfile
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
