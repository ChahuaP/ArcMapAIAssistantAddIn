import importlib
import pathlib
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class AddRgbRasterLayerTests(unittest.TestCase):
    def setUp(self):
        if str(RUNTIME_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNTIME_ROOT))
        self.calls = {"make_raster": [], "add_layer": [], "refresh_toc": 0, "refresh_view": 0}
        self.created_layer = FakeLayer(r"D:\Data\rgb.tif", "rgb.tif")
        self.live_layers = []
        self.auto_add_make_raster_output = False

        class Mapping(object):
            @staticmethod
            def MapDocument(value):
                return object()

            @staticmethod
            def ListDataFrames(mxd):
                return [object()]

            @staticmethod
            def ListLayers(mxd, wildcard, data_frame):
                return list(self.live_layers)

            @staticmethod
            def Layer(path):
                return FakeLayer(path)

            @staticmethod
            def AddLayer(data_frame, layer, position):
                self.calls["add_layer"].append((layer, position))
                self.live_layers.append(layer)

        fake_arcpy = types.SimpleNamespace()
        fake_arcpy.mapping = Mapping
        fake_arcpy.env = types.SimpleNamespace(addOutputsToMap=True)
        fake_arcpy.Exists = lambda path: False
        fake_arcpy.Describe = self._describe
        fake_arcpy.MakeRasterLayer_management = self._make_raster_layer
        fake_arcpy.RefreshTOC = lambda: self._refresh("refresh_toc")
        fake_arcpy.RefreshActiveView = lambda: self._refresh("refresh_view")
        sys.modules["arcpy"] = fake_arcpy

        from operations import common
        from operations import layer_ops
        importlib.reload(common)
        self.layer_ops = importlib.reload(layer_ops)

    def test_three_band_tiff_loads_as_rgb_raster_layer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "rgb.tif"
            path.write_text("fake", encoding="utf-8")

            result = self.layer_ops.add_layer({}, {"path": str(path)}, {})

        self.assertEqual(self.calls["make_raster"][0][4], "1;2;3")
        self.assertIs(self.calls["add_layer"][0][0], self.created_layer)
        self.assertEqual(result["layer_name"], "rgb.tif")
        self.assertTrue(sys.modules["arcpy"].env.addOutputsToMap)
        self.assertEqual(self.calls["refresh_toc"], 1)
        self.assertEqual(self.calls["refresh_view"], 1)

    def test_three_band_tiff_skips_add_layer_when_geoprocessing_auto_added_it(self):
        self.auto_add_make_raster_output = True
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "rgb.tif"
            path.write_text("fake", encoding="utf-8")

            result = self.layer_ops.add_layer({}, {"path": str(path)}, {})

        self.assertEqual(self.calls["make_raster"][0][4], "1;2;3")
        self.assertEqual(self.calls["add_layer"], [])
        self.assertEqual(self.live_layers, [self.created_layer])
        self.assertEqual(result["layer_name"], "rgb.tif")

    def test_single_band_tiff_uses_default_layer_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "dem.tif"
            path.write_text("fake", encoding="utf-8")

            result = self.layer_ops.add_layer({}, {"path": str(path)}, {})

        self.assertEqual(self.calls["make_raster"], [])
        self.assertEqual(result["layer_name"], "dem")

    def _describe(self, path):
        count = 1 if str(path).lower().endswith("dem.tif") else 3
        return types.SimpleNamespace(bandCount=count)

    def _make_raster_layer(self, path, layer_name, where_clause, envelope, band_index):
        self.assertFalse(sys.modules["arcpy"].env.addOutputsToMap)
        self.calls["make_raster"].append((path, layer_name, where_clause, envelope, band_index))
        if self.auto_add_make_raster_output:
            self.live_layers.append(self.created_layer)
        return FakeResult(self.created_layer)

    def _refresh(self, key):
        self.calls[key] += 1


class FakeResult(object):
    def __init__(self, output):
        self.output = output

    def getOutput(self, index):
        return self.output


class FakeLayer(object):
    def __init__(self, data_source, name=None):
        self.dataSource = data_source
        self.name = name or pathlib.Path(data_source).stem
        self.longName = self.name

    def supports(self, name):
        return name == "DATASOURCE"


if __name__ == "__main__":
    unittest.main()
