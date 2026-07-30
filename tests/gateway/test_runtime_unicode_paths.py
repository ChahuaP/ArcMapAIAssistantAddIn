import importlib
import pathlib
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class RuntimeUnicodePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(RUNTIME_ROOT))
        sys.modules.setdefault("arcpy", types.SimpleNamespace(ExecuteError=RuntimeError))
        cls.path_utils = importlib.import_module("path_utils")
        cls.workflow_executor = importlib.import_module("workflow_executor")
        cls.common = importlib.import_module("operations.common")
        cls.map_ops = importlib.import_module("operations.map_ops")

    def test_path_utils_keeps_chinese_windows_paths_as_text(self):
        path = r"C:\Users\于佳民\Desktop\demo\农房三维模型.obj"

        text = self.path_utils.to_unicode_path(path)

        self.assertIsInstance(text, str)
        self.assertIn("于佳民", text)
        self.assertEqual(self.path_utils.basename(text), "农房三维模型.obj")
        self.assertTrue(self.path_utils.join_path(r"C:\Users\于佳民", "Desktop").endswith("Desktop"))

    def test_custom_tool_open_writes_chinese_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = pathlib.Path(directory) / "中文目录"
            output_dir.mkdir()
            output_path = output_dir / "农房三维模型.obj"
            opener = self.workflow_executor._custom_tool_open_factory({"output_path": str(output_path)})

            with opener(str(output_path), "w") as handle:
                handle.write("# Wavefront OBJ\n")
                handle.write("v 0 0 0\n")

            self.assertIn("v 0 0 0", output_path.read_text(encoding="utf-8"))

    def test_custom_tool_os_path_wrapper_handles_chinese_path(self):
        custom_os = self.workflow_executor._custom_tool_os()
        path = r"C:\Users\于佳民\Desktop\demo\农房三维模型.obj"

        self.assertEqual(custom_os.path.basename(path), "农房三维模型.obj")
        self.assertTrue(custom_os.path.join(r"C:\Users\于佳民", "Desktop").endswith("Desktop"))

    def test_layer_reference_keeps_snapshot_identity_after_map_mutation(self):
        context = {
            "layers": [{
                "layer_ref": "layer:3",
                "name": "suspect_projects",
                "longName": "suspect_projects",
                "dataSource": r"D:\\experiment\\suspect_projects.shp",
            }]
        }
        expected = object()

        with patch.object(self.common, "_find_live_layer_exact", return_value=expected) as find_live, \
                patch.object(self.common, "_find_layer_by_ref") as find_by_index:
            actual = self.common.find_layer(context, "layer:3")

        self.assertIs(actual, expected)
        find_live.assert_called_once_with(r"D:\\experiment\\suspect_projects.shp")
        find_by_index.assert_not_called()

    def test_list_layers_returns_live_map_state_not_workflow_snapshot(self):
        snapshot = {"layers": [{"layer_ref": "layer:0", "name": "removed", "visible": True}]}
        live_context = {"layers": [{"layer_ref": "layer:0", "name": "construction", "visible": True}]}

        with patch.object(self.map_ops.context_reader, "read_context", return_value=live_context):
            result = self.map_ops.list_layers(snapshot, {}, {})

        self.assertEqual(result["layers"], live_context["layers"])


if __name__ == "__main__":
    unittest.main()
