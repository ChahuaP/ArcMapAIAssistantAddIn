import importlib
import json
import pathlib
import sys
import tempfile
import types
import unittest
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class ExportKmlTests(unittest.TestCase):
    def setUp(self):
        if str(RUNTIME_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNTIME_ROOT))
        self.layer = FakeLayer(r"D:\Data\roads.shp", "roads")
        self.data_frame = types.SimpleNamespace(scale=24000)
        self.fid_set = "1;2"
        self.feature_count = 2
        self.rows = [
            (FakeGeometry({"rings": [[
                [119.9, 31.35], [120.08, 31.35], [120.08, 31.52], [119.9, 31.52], [119.9, 31.35]
            ]]}), 0, "Taihu_A"),
            (FakeGeometry({"rings": [[
                [120.12, 31.18], [120.32, 31.18], [120.32, 31.35], [120.12, 31.35], [120.12, 31.18]
            ]]}), 1, "Taihu_B")
        ]
        self.calls = {"count": [], "cursor_sources": [], "kml": []}

        class Mapping(object):
            @staticmethod
            def MapDocument(value):
                return object()

            @staticmethod
            def ListDataFrames(mxd):
                return [self.data_frame]

            @staticmethod
            def ListLayers(mxd, wildcard, data_frame):
                return [self.layer]

        fake_arcpy = types.SimpleNamespace()
        fake_arcpy.mapping = Mapping
        fake_arcpy.Exists = lambda path: False
        fake_arcpy.Describe = self._describe
        fake_arcpy.GetCount_management = self._get_count
        fake_arcpy.ListFields = lambda source: [FakeField("FID", "OID"), FakeField("NAME", "String")]
        fake_arcpy.SpatialReference = lambda code: FakeSpatialReference("GCS_WGS_1984", code)
        fake_arcpy.LayerToKML_conversion = self._layer_to_kml
        fake_arcpy.da = types.SimpleNamespace(SearchCursor=self._search_cursor)
        sys.modules["arcpy"] = fake_arcpy

        from operations import common
        from operations import condition_utils
        from operations import export_ops
        importlib.reload(common)
        importlib.reload(condition_utils)
        self.export_ops = importlib.reload(export_ops)

    def test_export_layer_kml_writes_polygon_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.export_ops.export_layer_kml(
                {"layers": [{"layer_ref": "layer:0", "name": "roads", "longName": "roads"}]},
                {
                    "layer": "roads",
                    "output_name": "roads_kml",
                    "output_folder": directory
                },
                {}
            )
            with zipfile.ZipFile(result["output"], "r") as archive:
                kml = archive.read("doc.kml").decode("utf-8")

        self.assertTrue(result["output"].endswith("roads_kml.kmz"))
        self.assertEqual(result["format"], "kmz")
        self.assertEqual(result["feature_count"], 2)
        self.assertEqual(self.calls["cursor_sources"], [self.layer.dataSource])
        self.assertEqual(self.calls["kml"], [])
        self.assertIn("<Polygon>", kml)
        self.assertIn("<coordinates>119.9,31.35,0", kml)
        self.assertIn("<ExtendedData>", kml)
        self.assertIn('<Data name="NAME"><value>Taihu_A</value></Data>', kml)

    def test_export_selected_layer_kml_uses_layer_selection_source(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.export_ops.export_layer_kml(
                {"layers": [{"layer_ref": "layer:0", "name": "roads", "longName": "roads"}]},
                {
                    "layer": "roads",
                    "output_name": "roads_selected",
                    "output_folder": directory,
                    "selected_only": True
                },
                {}
            )

        self.assertEqual(result["feature_count"], 2)
        self.assertEqual(self.calls["cursor_sources"], [self.layer])

    def test_export_selected_layer_kml_requires_selected_features(self):
        self.fid_set = ""
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Exception) as raised:
                self.export_ops.export_layer_kml(
                    {"layers": [{"layer_ref": "layer:0", "name": "roads", "longName": "roads"}]},
                    {
                        "layer": "roads",
                        "output_name": "roads_selected",
                        "output_folder": directory,
                        "selected_only": True
                    },
                    {}
                )

        self.assertIn("没有已选要素", str(raised.exception))
        self.assertEqual(self.calls["kml"], [])

    def test_export_layer_kml_rejects_empty_feature_layer(self):
        self.feature_count = 0
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(Exception) as raised:
                self.export_ops.export_layer_kml(
                    {"layers": [{"layer_ref": "layer:0", "name": "roads", "longName": "roads"}]},
                    {
                        "layer": "roads",
                        "output_name": "roads_empty",
                        "output_folder": directory
                    },
                    {}
                )

        self.assertIn("没有要素", str(raised.exception))
        self.assertEqual(self.calls["kml"], [])

    def _layer_to_kml(self, layer, output, scale, is_composite):
        self.calls["kml"].append((layer, output, scale, is_composite))

    def _describe(self, layer):
        if layer is self.layer:
            return types.SimpleNamespace(
                dataType="FeatureLayer",
                shapeType="Polygon",
                FIDSet=self.fid_set,
                spatialReference=FakeSpatialReference("GCS_WGS_1984", 4326)
            )
        return types.SimpleNamespace(
            dataType="ShapeFile",
            shapeType="Polygon",
            FIDSet=None,
            spatialReference=FakeSpatialReference("GCS_WGS_1984", 4326)
        )

    def _get_count(self, layer):
        self.calls["count"].append(layer)
        return FakeResult(str(self.feature_count))

    def _delete(self, layer):
        self.calls["delete"].append(layer)

    def _search_cursor(self, source, fields):
        self.calls["cursor_sources"].append(source)
        return FakeSearchCursor(self.rows)


class FakeResult(object):
    def __init__(self, value):
        self.value = value

    def getOutput(self, index):
        return self.value


class FakeSearchCursor(object):
    def __init__(self, rows):
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


class FakeGeometry(object):
    def __init__(self, data):
        self.JSON = json.dumps(data)
        self.isEmpty = False

    def projectAs(self, spatial_reference):
        return self


class FakeField(object):
    def __init__(self, name, field_type):
        self.name = name
        self.type = field_type


class FakeSpatialReference(object):
    def __init__(self, name, factory_code):
        self.name = name
        self.factoryCode = factory_code


class FakeLayer(object):
    def __init__(self, data_source, name):
        self.dataSource = data_source
        self.name = name
        self.longName = name

    def supports(self, name):
        return name == "DATASOURCE"


if __name__ == "__main__":
    unittest.main()
