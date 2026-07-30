import importlib
import pathlib
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class RuntimeUnicodePathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(RUNTIME_ROOT))
        sys.modules.setdefault("arcpy", types.SimpleNamespace(ExecuteError=RuntimeError))
        cls.path_utils = importlib.import_module("path_utils")
        cls.workflow_executor = importlib.import_module("workflow_executor")

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


if __name__ == "__main__":
    unittest.main()
