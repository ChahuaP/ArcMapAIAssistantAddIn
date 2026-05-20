import importlib.util
import pathlib
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMON_PATH = ROOT / "arcmap_runtime_py2" / "operations" / "common.py"


class OutputLayerTests(unittest.TestCase):
    def test_add_output_layer_skips_existing_data_source(self):
        calls = {"add_layer": 0, "refresh_toc": 0, "refresh_view": 0}
        existing_layer = FakeLayer(r"C:\work\ArcMapAI_Output.gdb\nanjing_buffer")

        class Mapping(object):
            @staticmethod
            def MapDocument(value):
                return object()

            @staticmethod
            def ListDataFrames(mxd):
                return [object()]

            @staticmethod
            def ListLayers(mxd, wildcard, data_frame):
                return [existing_layer]

            @staticmethod
            def Layer(path):
                return FakeLayer(path)

            @staticmethod
            def AddLayer(data_frame, layer, position):
                calls["add_layer"] += 1

        fake_arcpy = types.SimpleNamespace(mapping=Mapping)

        def refresh_toc():
            calls["refresh_toc"] += 1

        def refresh_view():
            calls["refresh_view"] += 1

        fake_arcpy.RefreshTOC = refresh_toc
        fake_arcpy.RefreshActiveView = refresh_view
        sys.modules["arcpy"] = fake_arcpy

        spec = importlib.util.spec_from_file_location("common_output_layer", COMMON_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        result = module.add_output_layer(r"C:\work\ArcMapAI_Output.gdb\nanjing_buffer")

        self.assertEqual(calls["add_layer"], 0)
        self.assertEqual(calls["refresh_toc"], 1)
        self.assertEqual(calls["refresh_view"], 1)
        self.assertTrue(result["already_visible"])

    def test_output_workspace_folder_creates_default_gdb(self):
        calls = {"created": []}

        fake_arcpy = types.SimpleNamespace()
        fake_arcpy.Exists = lambda path: False

        def create_file_gdb(folder, name):
            calls["created"].append((folder, name))

        fake_arcpy.CreateFileGDB_management = create_file_gdb
        sys.modules["arcpy"] = fake_arcpy

        spec = importlib.util.spec_from_file_location("common_output_workspace", COMMON_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as directory:
            output = module.output_feature_class({"mxd_path": ""}, "nanjing_buffer", directory)

        self.assertTrue(output.endswith(r"ArcMapAI_Output.gdb\nanjing_buffer"))
        self.assertEqual(calls["created"][0][1], "ArcMapAI_Output.gdb")

    def test_output_workspace_default_gdb_token_uses_context_default_gdb(self):
        fake_arcpy = types.SimpleNamespace()
        fake_arcpy.Exists = lambda path: path.lower().endswith(".gdb")
        fake_arcpy.CreateFileGDB_management = lambda folder, name: None
        sys.modules["arcpy"] = fake_arcpy

        spec = importlib.util.spec_from_file_location("common_default_gdb", COMMON_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as directory:
            output = module.output_feature_class(
                {"default_gdb": str(pathlib.Path(directory) / "Default.gdb")},
                "nanjing_buffer",
                "默认gdb"
            )

        self.assertTrue(output.endswith(r"Default.gdb\nanjing_buffer"))

    def test_output_workspace_default_gdb_token_requires_context_default_gdb(self):
        fake_arcpy = types.SimpleNamespace()
        fake_arcpy.Exists = lambda path: False
        fake_arcpy.CreateFileGDB_management = lambda folder, name: None
        sys.modules["arcpy"] = fake_arcpy

        spec = importlib.util.spec_from_file_location("common_default_gdb_missing", COMMON_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with self.assertRaises(Exception):
            module.output_feature_class({}, "nanjing_buffer", "默认gdb")

    def test_find_layer_can_use_layer_added_by_previous_step_name(self):
        added_layer = FakeLayer(r"D:\Data\p1.shp", "p1")

        class Mapping(object):
            @staticmethod
            def MapDocument(value):
                return object()

            @staticmethod
            def ListDataFrames(mxd):
                return [object()]

            @staticmethod
            def ListLayers(mxd, wildcard, data_frame):
                return [added_layer]

        sys.modules["arcpy"] = types.SimpleNamespace(mapping=Mapping)
        spec = importlib.util.spec_from_file_location("common_find_added_name", COMMON_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        found = module.find_layer({"layers": []}, "p1", {})

        self.assertIs(found, added_layer)

    def test_find_layer_can_use_from_step_output(self):
        added_layer = FakeLayer(r"D:\Data\p1.shp", "p1")

        class Mapping(object):
            @staticmethod
            def MapDocument(value):
                return object()

            @staticmethod
            def ListDataFrames(mxd):
                return [object()]

            @staticmethod
            def ListLayers(mxd, wildcard, data_frame):
                return [added_layer]

        sys.modules["arcpy"] = types.SimpleNamespace(mapping=Mapping)
        spec = importlib.util.spec_from_file_location("common_find_from_step", COMMON_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        found = module.find_layer({"layers": []}, "from_step:step_1", {"step_1": {"added_layer": r"D:\Data\p1.shp"}})

        self.assertIs(found, added_layer)


class FakeLayer(object):
    def __init__(self, data_source, name=None):
        self.dataSource = data_source
        self.name = name or pathlib.Path(data_source).stem
        self.longName = self.name

    def supports(self, name):
        return name == "DATASOURCE"


if __name__ == "__main__":
    unittest.main()
