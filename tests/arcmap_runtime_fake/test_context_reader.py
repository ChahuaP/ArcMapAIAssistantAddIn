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


if __name__ == "__main__":
    unittest.main()
