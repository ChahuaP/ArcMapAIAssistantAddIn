import importlib.util
import pathlib
import sys
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


class FakeLayer(object):
    def __init__(self, data_source):
        self.dataSource = data_source

    def supports(self, name):
        return name == "DATASOURCE"


if __name__ == "__main__":
    unittest.main()
