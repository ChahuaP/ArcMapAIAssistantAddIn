import importlib
import pathlib
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class ExportKmlTests(unittest.TestCase):
    def setUp(self):
        if str(RUNTIME_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNTIME_ROOT))
        self.layer = FakeLayer(r"D:\Data\roads.shp", "roads")
        self.calls = {"kml": []}

        class Mapping(object):
            @staticmethod
            def MapDocument(value):
                return object()

            @staticmethod
            def ListDataFrames(mxd):
                return [object()]

            @staticmethod
            def ListLayers(mxd, wildcard, data_frame):
                return [self.layer]

        fake_arcpy = types.SimpleNamespace()
        fake_arcpy.mapping = Mapping
        fake_arcpy.Exists = lambda path: False
        fake_arcpy.LayerToKML_conversion = self._layer_to_kml
        sys.modules["arcpy"] = fake_arcpy

        from operations import common
        from operations import condition_utils
        from operations import export_ops
        importlib.reload(common)
        importlib.reload(condition_utils)
        self.export_ops = importlib.reload(export_ops)

    def test_export_layer_kml_writes_kmz(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.export_ops.export_layer_kml(
                {"layers": [{"layer_ref": "layer:0", "name": "roads", "longName": "roads"}]},
                {
                    "layer": "roads",
                    "output_name": "roads_kml",
                    "output_folder": directory,
                    "selected_only": True
                },
                {}
            )

        self.assertTrue(result["output"].endswith("roads_kml.kmz"))
        self.assertEqual(result["format"], "kmz")
        self.assertEqual(self.calls["kml"], [(self.layer, result["output"], 0, "NO_COMPOSITE")])

    def _layer_to_kml(self, layer, output, scale, is_composite):
        self.calls["kml"].append((layer, output, scale, is_composite))


class FakeLayer(object):
    def __init__(self, data_source, name):
        self.dataSource = data_source
        self.name = name
        self.longName = name

    def supports(self, name):
        return name == "DATASOURCE"


if __name__ == "__main__":
    unittest.main()
