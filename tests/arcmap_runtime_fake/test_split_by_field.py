import importlib
import pathlib
import sys
import tempfile
import types
import unittest
import zipfile


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
        fake_arcpy.env = types.SimpleNamespace(addOutputsToMap=True)
        fake_arcpy.ListFields = lambda layer: [FakeField("type", "String"), FakeField("村名", "String")]
        fake_arcpy.Describe = lambda layer: types.SimpleNamespace(
            path=r"D:\Data",
            spatialReference=types.SimpleNamespace(name="GCS_WGS_1984", factoryCode=4326)
        )
        fake_arcpy.SpatialReference = lambda code: types.SimpleNamespace(name="GCS_WGS_1984", factoryCode=code)
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
        self.assertTrue(sys.modules["arcpy"].env.addOutputsToMap)

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

    def test_kmz_export_uses_name_template_and_does_not_copy_shapefiles(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.export_ops.split_by_field(
                {"layers": [{"layer_ref": "layer:0", "name": "roads", "longName": "roads"}]},
                {
                    "layer": "roads",
                    "field": "村名",
                    "output_name": "community_kmz",
                    "output_format": "kmz",
                    "output_folder": directory,
                    "name_template": "{value_base}永农"
                },
                {}
            )

            names = [pathlib.Path(item).name for item in result["outputs"]]
            self.assertEqual(names, ["红光永农.kmz", "钱仓永农.kmz"])
            self.assertEqual(self.calls["make"], [])
            self.assertEqual(self.calls["copy"], [])
            with zipfile.ZipFile(result["outputs"][0], "r") as archive:
                kml_text = archive.read("doc.kml").decode("utf-8")

        self.assertIn("<Polygon>", kml_text)
        self.assertIn("红光社区", kml_text)
        self.assertTrue(sys.modules["arcpy"].env.addOutputsToMap)

    def _make_feature_layer(self, source, temp_layer, where_clause):
        self.assertFalse(sys.modules["arcpy"].env.addOutputsToMap)
        self.calls["make"].append((source, where_clause))

    def _copy_features(self, temp_layer, output):
        self.assertFalse(sys.modules["arcpy"].env.addOutputsToMap)
        self.calls["copy"].append((temp_layer, output))

    def _delete(self, temp_layer):
        self.calls["delete"].append(temp_layer)

    def _refresh(self, key):
        self.calls[key] += 1


class FakeSearchCursor(object):
    def __init__(self, source, fields, where_clause=None, *args):
        if fields == ["type"]:
            rows = [(None,), ("A",), ("B",), ("A",)]
        elif fields == ["村名"]:
            rows = [("红光社区",), ("钱仓社区",), ("红光社区",)]
        elif fields and fields[0] == "SHAPE@":
            value = "钱仓社区" if where_clause and "钱仓社区" in where_clause else "红光社区"
            rows = [(FakeGeometry(), "A", value)]
        else:
            rows = []
        self.rows = iter(rows)

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


class FakeGeometry(object):
    JSON = '{"rings":[[[118.0,32.0],[118.1,32.0],[118.1,32.1],[118.0,32.0]]]}'


class FakeLayer(object):
    def __init__(self, data_source, name):
        self.dataSource = data_source
        self.name = name
        self.longName = name

    def supports(self, name):
        return name == "DATASOURCE"


if __name__ == "__main__":
    unittest.main()
