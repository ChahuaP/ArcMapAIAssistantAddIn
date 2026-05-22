import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"
CONTEXT_READER_PATH = RUNTIME_ROOT / "context_reader.py"


class ContextReaderTests(unittest.TestCase):
    def test_read_context_includes_default_gdb_from_current_workspace(self):
        if str(RUNTIME_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNTIME_ROOT))

        data_frame = types.SimpleNamespace(name="Layers", spatialReference=None, extent=None)
        mxd = types.SimpleNamespace(filePath="", activeView="DATA_VIEW")

        class Mapping(object):
            @staticmethod
            def MapDocument(value):
                return mxd

            @staticmethod
            def ListDataFrames(document):
                return [data_frame]

            @staticmethod
            def ListLayers(document, wildcard, frame):
                return []

        fake_arcpy = types.SimpleNamespace(
            mapping=Mapping,
            env=types.SimpleNamespace(workspace=r"D:\ArcGIS\Default.gdb")
        )
        sys.modules["arcpy"] = fake_arcpy

        spec = importlib.util.spec_from_file_location("context_reader_default_gdb", CONTEXT_READER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        context = module.read_context()

        self.assertEqual(context["default_gdb"], r"D:\ArcGIS\Default.gdb")

    def test_read_context_includes_attribute_value_samples(self):
        if str(RUNTIME_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNTIME_ROOT))

        data_frame = types.SimpleNamespace(name="Layers", spatialReference=None, extent=None)
        mxd = types.SimpleNamespace(filePath="", activeView="DATA_VIEW")
        layer = FakeLayer("a")

        class Mapping(object):
            @staticmethod
            def MapDocument(value):
                return mxd

            @staticmethod
            def ListDataFrames(document):
                return [data_frame]

            @staticmethod
            def ListLayers(document, wildcard, frame):
                return [layer]

        class Da(object):
            @staticmethod
            def SearchCursor(cursor_layer, fields):
                return iter([
                    ("xxx区k街道", "乔木用地"),
                    ("yyy区m街道", "灌木用地"),
                    ("xxx区k街道", "乔木用地")
                ])

        fake_arcpy = types.SimpleNamespace(
            mapping=Mapping,
            env=types.SimpleNamespace(workspace=""),
            Describe=lambda item: types.SimpleNamespace(shapeType="Polygon", FIDSet=""),
            ListFields=lambda item: [FakeField("b", "String"), FakeField("c", "String")],
            da=Da
        )
        sys.modules["arcpy"] = fake_arcpy

        spec = importlib.util.spec_from_file_location("context_reader_value_samples", CONTEXT_READER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        context = module.read_context()

        fields = {field["name"]: field for field in context["layers"][0]["fields"]}
        self.assertEqual(fields["b"]["value_samples"], ["xxx区k街道", "yyy区m街道"])
        self.assertEqual(fields["c"]["value_samples"], ["乔木用地", "灌木用地"])


class FakeLayer(object):
    def __init__(self, name):
        self.name = name
        self.longName = name
        self.visible = True
        self.isFeatureLayer = True

    def supports(self, value):
        return False


class FakeField(object):
    def __init__(self, name, field_type):
        self.name = name
        self.type = field_type


if __name__ == "__main__":
    unittest.main()
