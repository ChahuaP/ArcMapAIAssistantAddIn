import importlib
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class CartographyOpsTests(unittest.TestCase):
    def setUp(self):
        if str(RUNTIME_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNTIME_ROOT))
        self.layer = FakeLayer(r"D:\Data\roads.shp", "roads")
        self.source_layer = FakeLayer(r"D:\Data\style.lyr", "style")
        self.calls = {"update": [], "make_raster": [], "add": [], "remove": [], "save_layer": [], "refresh_toc": 0, "refresh_view": 0}

        class Mapping(object):
            @staticmethod
            def MapDocument(value):
                return object()

            @staticmethod
            def ListDataFrames(mxd):
                return [object()]

            @staticmethod
            def ListLayers(mxd, wildcard, data_frame):
                return [self.layer, self.source_layer]

            @staticmethod
            def Layer(path):
                return FakeLayer(path, pathlib.Path(path).name)

            @staticmethod
            def UpdateLayer(data_frame, target, source, symbology_only):
                self.calls["update"].append((target, source, symbology_only))

            @staticmethod
            def AddLayer(data_frame, layer, position):
                self.calls["add"].append((layer, position))

            @staticmethod
            def RemoveLayer(data_frame, layer):
                self.calls["remove"].append(layer)

        fake_arcpy = types.SimpleNamespace()
        fake_arcpy.mapping = Mapping
        fake_arcpy.Exists = lambda path: str(path).endswith(".lyr")
        fake_arcpy.Describe = lambda source: types.SimpleNamespace(path=r"D:\Data", bandCount=4)
        fake_arcpy.AddFieldDelimiters = lambda workspace, field: "[%s]" % field
        fake_arcpy.ListFields = lambda layer: [FakeField("TYPE", "String"), FakeField("POP", "Double")]
        fake_arcpy.MakeRasterLayer_management = self._make_raster_layer
        fake_arcpy.SaveToLayerFile_management = self._save_layer_file
        fake_arcpy.RefreshTOC = lambda: self._refresh("refresh_toc")
        fake_arcpy.RefreshActiveView = lambda: self._refresh("refresh_view")
        sys.modules["arcpy"] = fake_arcpy

        from operations import common
        from operations import condition_utils
        from operations import cartography_ops
        importlib.reload(common)
        importlib.reload(condition_utils)
        self.cartography_ops = importlib.reload(cartography_ops)

    def test_apply_symbology_from_existing_layer(self):
        result = self.cartography_ops.apply_symbology_from_layer(
            _context(),
            {"target_layer": "roads", "source_layer": "style"},
            {}
        )

        self.assertEqual(result["layer"], "roads")
        self.assertEqual(self.calls["update"], [(self.layer, self.source_layer, True)])
        self.assertEqual(self.calls["refresh_toc"], 1)

    def test_unique_values_updates_existing_renderer(self):
        self.layer.symbologyType = "UNIQUE_VALUES"
        self.layer.symbology = FakeUniqueValuesSymbology()

        result = self.cartography_ops.set_unique_values(
            _context(),
            {"layer": "roads", "field": "TYPE", "show_other_values": False},
            {}
        )

        self.assertEqual(result["renderer"], "UNIQUE_VALUES")
        self.assertEqual(self.layer.symbology.valueField, "TYPE")
        self.assertTrue(self.layer.symbology.added_all_values)
        self.assertFalse(self.layer.symbology.showOtherValues)

    def test_raster_bands_recreates_layer_with_requested_bands(self):
        self.layer.dataSource = r"D:\Data\image.tif"
        self.layer.name = "image.tif"

        result = self.cartography_ops.set_raster_bands(
            _context(),
            {"layer": "roads", "band_indices": [4, 3, 2], "replace": True},
            {}
        )

        self.assertEqual(result["band_index"], "4;3;2")
        self.assertEqual(self.calls["make_raster"][0][4], "4;3;2")
        self.assertEqual(self.calls["remove"], [self.layer])

    def test_save_layer_file_writes_lyr(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            result = self.cartography_ops.save_layer_file(
                _context_with_mxd(),
                {
                    "layer": "roads",
                    "output_name": "roads_style",
                    "output_folder": directory,
                    "is_relative_path": "RELATIVE"
                },
                {}
            )

        self.assertTrue(result["output"].endswith("roads_style.lyr"))
        self.assertEqual(result["format"], "lyr")
        self.assertEqual(self.calls["save_layer"], [(self.layer, result["output"], "RELATIVE")])

    def _make_raster_layer(self, path, layer_name, where_clause, envelope, band_index):
        self.calls["make_raster"].append((path, layer_name, where_clause, envelope, band_index))
        return FakeResult(FakeLayer(path, layer_name))

    def _save_layer_file(self, layer, output, is_relative_path):
        self.calls["save_layer"].append((layer, output, is_relative_path))

    def _refresh(self, key):
        self.calls[key] += 1


def _context():
    return {
        "layers": [
            {"layer_ref": "layer:0", "name": "roads", "longName": "roads"},
            {"layer_ref": "layer:1", "name": "style", "longName": "style"}
        ]
    }


def _context_with_mxd():
    context = _context()
    context["mxd_path"] = r"D:\Data\map.mxd"
    return context


class FakeResult(object):
    def __init__(self, output):
        self.output = output

    def getOutput(self, index):
        return self.output


class FakeField(object):
    def __init__(self, name, field_type):
        self.name = name
        self.type = field_type


class FakeUniqueValuesSymbology(object):
    def __init__(self):
        self.valueField = None
        self.showOtherValues = True
        self.added_all_values = False

    def addAllValues(self):
        self.added_all_values = True


class FakeLayer(object):
    def __init__(self, data_source, name=None):
        self.dataSource = data_source
        self.name = name or pathlib.Path(data_source).stem
        self.longName = self.name
        self.symbologyType = "OTHER"
        self.symbology = None

    def supports(self, name):
        return name == "DATASOURCE"


if __name__ == "__main__":
    unittest.main()
