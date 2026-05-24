import pathlib
import tempfile
import unittest

from gateway_py3.agent_tools import AgentToolRuntime
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.workflow_store import WorkflowStore

from gateway.tool_builder_test_utils import (
    context as _context,
    custom_writes_data_spec as _custom_writes_data_spec,
    isolated_tool_roots as _isolated_tool_roots,
    review_tests as _review_tests,
)


class ToolBuilderOutputPolicyTests(unittest.TestCase):
    def setUp(self):
        self.catalog = OperationCatalog()

    def test_file_output_tool_can_write_only_runtime_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["id"] = "custom.export_obj"
            spec["output_policy"] = {"type": "file", "extension": ".obj"}

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "OBJ 导出工具",
                    "capability": "把要素写成 OBJ 文件",
                    "operation_spec": spec,
                    "executor_code": (
                        "def execute(context, arguments, step_outputs):\n"
                        "    output_path = arguments['output_path']\n"
                        "    handle = open(output_path, 'w')\n"
                        "    handle.write('o model\\n')\n"
                        "    handle.close()\n"
                        "    return {'output': output_path}\n"
                    ),
                    "tests": _review_tests()
                })

        self.assertTrue(result["ok"], result.get("error"))
        schema = result["tool"]["payload"]["operation_spec"]["parameters_schema"]
        self.assertIn("output_folder", schema["properties"])
        self.assertNotIn("output_workspace", schema["properties"])
        self.assertEqual(result["tool"]["payload"]["operation_spec"]["output_policy"]["type"], "file")

    def test_file_output_tool_rejects_opening_any_other_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["id"] = "custom.bad_file_writer"
            spec["output_policy"] = {"type": "file", "extension": ".obj"}

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误文件工具",
                    "capability": "写错路径",
                    "operation_spec": spec,
                    "executor_code": (
                        "def execute(context, arguments, step_outputs):\n"
                        "    handle = open('D:/tmp/model.obj', 'w')\n"
                        "    handle.write('bad')\n"
                        "    handle.close()\n"
                        "    return {'output': arguments['output_path']}\n"
                    ),
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("open", result["error"])

    def test_file_output_tool_rejects_broad_exception_swallowing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["id"] = "custom.empty_success_obj"
            spec["output_policy"] = {"type": "file", "extension": ".obj"}

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "吞错文件工具",
                    "capability": "错误地吞掉几何异常",
                    "operation_spec": spec,
                    "executor_code": (
                        "def execute(context, arguments, step_outputs):\n"
                        "    try:\n"
                        "        raise ValueError('bad geometry')\n"
                        "    except Exception:\n"
                        "        pass\n"
                        "    handle = open(arguments['output_path'], 'w')\n"
                        "    handle.write('# header\\n')\n"
                        "    handle.close()\n"
                        "    return {'vertex_count': 0, 'face_count': 0}\n"
                    ),
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("broad except", result["error"])

    def test_writes_data_tests_reject_zero_count_assertions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["id"] = "custom.weak_obj_tests"
            spec["output_policy"] = {"type": "file", "extension": ".obj"}

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "弱测试工具",
                    "capability": "测试允许空结果",
                    "operation_spec": spec,
                    "executor_code": (
                        "def execute(context, arguments, step_outputs):\n"
                        "    handle = open(arguments['output_path'], 'w')\n"
                        "    handle.write('o model\\n')\n"
                        "    handle.close()\n"
                        "    return {'output': arguments['output_path'], 'vertex_count': 0}\n"
                    ),
                    "tests": [{
                        "name": "weak test",
                        "arguments": {"input_layer": "layer:test", "output_name": "model"},
                        "expected": {"vertex_count": 0},
                        "assertions": ["vertex_count >= 0"]
                    }]
                })

        self.assertFalse(result["ok"])
        self.assertIn("空成功", result["error"])

    def test_feature_class_tool_rejects_open_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["id"] = "custom.bad_feature_writer"
            spec["output_policy"] = {"type": "feature_class", "formats": ["gdb", "shp"]}

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "错误要素工具",
                    "capability": "要素工具打开文件",
                    "operation_spec": spec,
                    "executor_code": (
                        "def execute(context, arguments, step_outputs):\n"
                        "    handle = open(arguments['output_path'], 'w')\n"
                        "    handle.close()\n"
                        "    return {'output': arguments['output_path']}\n"
                    ),
                    "tests": _review_tests()
                })

        self.assertFalse(result["ok"])
        self.assertIn("file/raster", result["error"])

    def test_raster_output_tool_can_use_copy_raster_to_runtime_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = WorkflowStore(root / "workflows.sqlite")
            runtime = AgentToolRuntime(self.catalog, store, _context())
            spec = _custom_writes_data_spec()
            spec["id"] = "custom.copy_raster_tif"
            spec["parameters_schema"]["required"] = ["input_raster", "output_name"]
            spec["parameters_schema"]["properties"] = {
                "input_raster": {"type": "layer"},
                "output_name": {"type": "string"}
            }
            spec["output_policy"] = {"type": "raster", "formats": ["tif"], "default_format": "tif"}

            with _isolated_tool_roots(root):
                result = runtime.handle("toolbuilder_create_draft", {
                    "name": "TIFF 输出工具",
                    "capability": "把栅格写成 TIFF",
                    "operation_spec": spec,
                    "executor_code": (
                        "def execute(context, arguments, step_outputs):\n"
                        "    arcpy.CopyRaster_management(arguments['input_raster'], arguments['output_path'])\n"
                        "    return {'output': arguments['output_path']}\n"
                    ),
                    "tests": [{
                        "name": "writes tif",
                        "arguments": {"input_raster": "layer:dem", "output_name": "dem_copy"},
                        "expected": {"output": "tif"},
                        "assertions": ["executor writes to arguments['output_path']"]
                    }]
                })

        self.assertTrue(result["ok"], result.get("error"))
        schema = result["tool"]["payload"]["operation_spec"]["parameters_schema"]
        self.assertIn("output_folder", schema["properties"])
        self.assertNotIn("output_workspace", schema["properties"])


if __name__ == "__main__":
    unittest.main()
