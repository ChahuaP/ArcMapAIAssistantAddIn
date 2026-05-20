import importlib
import pathlib
import sys
import tempfile
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class SplitByFieldTests(unittest.TestCase):
    def setUp(self):
        if str(RUNTIME_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNTIME_ROOT))
        self.layer = FakeLayer(r"D:\Data\roads.shp", "roads")
        self.calls = {"make": [], "copy": [], "delete": [], "refresh_toc": 0, "refresh_view": 0}

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
        fake_arcpy.ListFields = lambda layer: [FakeField("type", "String")]
        fake_arcpy.Describe = lambda layer: types.SimpleNamespace(path=r"D:\Data")
        fake_arcpy.AddFieldDelimiters = lambda workspace, field: "[%s]" % field
        fake_arcpy.Exists = lambda path: False
        fake_arcpy.MakeFeatureLayer_management = self._make_feature_layer
        fake_arcpy.CopyFeatures_management = self._copy_features
        fake_arcpy.Delete_management = self._delete
        fake_arcpy.RefreshTOC = lambda: self._refresh("refresh_toc")
        fake_arcpy.RefreshActiveView = lambda: self._refresh("refresh_view")
        fake_arcpy.da = types.SimpleNamespace(SearchCursor=FakeSearchCursor)
        sys.modules["arcpy"] = fake_arcpy

        from operations import common
        from operations import condition_utils
        from operations import export_ops
        importlib.reload(common)
        importlib.reload(condition_utils)
        self.export_ops = importlib.reload(export_ops)

    def test_split_by_field_exports_one_shapefile_per_value(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.export_ops.split_by_field(
                {"layers": [{"layer_ref": "layer:0", "name": "roads", "longName": "roads"}]},
                {
                    "layer": "roads",
                    "field": "type",
                    "output_name": "roads_type",
                    "output_format": "shp",
                    "output_folder": directory
                },
                {}
            )

        self.assertEqual(result["count"], 3)
        self.assertEqual([pathlib.Path(item).name for item in result["outputs"]], [
            "roads_type_null.shp",
            "roads_type_A.shp",
            "roads_type_B.shp"
        ])
        self.assertEqual([item[1] for item in self.calls["make"]], [
            "[type] IS NULL",
            "[type] = 'A'",
            "[type] = 'B'"
        ])
        self.assertEqual(len(self.calls["copy"]), 3)
        self.assertEqual(self.calls["refresh_toc"], 1)
        self.assertEqual(self.calls["refresh_view"], 1)

    def test_shapefile_export_accepts_output_workspace_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.export_ops.split_by_field(
                {"layers": [{"layer_ref": "layer:0", "name": "roads", "longName": "roads"}]},
                {
                    "layer": "roads",
                    "field": "type",
                    "output_name": "roads_type",
                    "output_format": "shp",
                    "output_workspace": directory
                },
                {}
            )

        self.assertTrue(result["outputs"][0].endswith(".shp"))

    def _make_feature_layer(self, source, temp_layer, where_clause):
        self.calls["make"].append((source, where_clause))

    def _copy_features(self, temp_layer, output):
        self.calls["copy"].append((temp_layer, output))

    def _delete(self, temp_layer):
        self.calls["delete"].append(temp_layer)

    def _refresh(self, key):
        self.calls[key] += 1


class FakeSearchCursor(object):
    def __init__(self, source, fields):
        self.rows = iter([(None,), ("A",), ("B",), ("A",)])

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.rows)

    next = __next__


class FakeField(object):
    def __init__(self, name, field_type):
        self.name = name
        self.type = field_type


class FakeLayer(object):
    def __init__(self, data_source, name):
        self.dataSource = data_source
        self.name = name
        self.longName = name

    def supports(self, name):
        return name == "DATASOURCE"


if __name__ == "__main__":
    unittest.main()
