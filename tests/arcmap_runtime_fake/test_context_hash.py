import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class ContextHashTests(unittest.TestCase):
    def setUp(self):
        runtime_path = str(RUNTIME_ROOT)
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)

    def test_hash_ignores_existing_context_hash_field(self):
        sys.modules["arcpy"] = types.SimpleNamespace()
        spec = importlib.util.spec_from_file_location("context_reader", RUNTIME_ROOT / "context_reader.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        first = {"mxd_path": "a.mxd", "layers": [{"name": "roads"}]}
        second = dict(first)
        second["context_hash"] = "old"
        self.assertEqual(module.context_hash(first), module.context_hash(second))

    def test_hash_ignores_view_only_state(self):
        sys.modules["arcpy"] = types.SimpleNamespace()
        spec = importlib.util.spec_from_file_location("context_reader_view", RUNTIME_ROOT / "context_reader.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        first = {
            "mxd_path": r"C:\maps\city.mxd",
            "is_saved": True,
            "active_view": "PAGE_LAYOUT",
            "extent": {"XMin": 0, "YMin": 0, "XMax": 1, "YMax": 1},
            "layers": [{"name": "nanjing", "visible": True, "selected_count": 0}]
        }
        second = dict(first)
        second["active_view"] = "DATA_FRAME"
        second["extent"] = {"XMin": 10, "YMin": 10, "XMax": 20, "YMax": 20}
        second["layers"] = [dict(first["layers"][0], visible=False)]
        self.assertEqual(module.context_hash(first), module.context_hash(second))

    def test_hash_ignores_mxd_save_state(self):
        sys.modules["arcpy"] = types.SimpleNamespace()
        spec = importlib.util.spec_from_file_location("context_reader_save_state", RUNTIME_ROOT / "context_reader.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        first = {
            "mxd_path": "",
            "is_saved": False,
            "data_frame": "Layers",
            "layers": [{"name": "nanjing", "dataSource": r"C:\data\nanjing.shp"}]
        }
        second = dict(first)
        second["mxd_path"] = r"C:\maps\project.mxd"
        second["is_saved"] = True
        self.assertEqual(module.context_hash(first), module.context_hash(second))

    def test_hash_tracks_layer_schema_and_selection(self):
        sys.modules["arcpy"] = types.SimpleNamespace()
        spec = importlib.util.spec_from_file_location("context_reader_selection", RUNTIME_ROOT / "context_reader.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        base = {
            "mxd_path": r"C:\maps\city.mxd",
            "is_saved": True,
            "layers": [{
                "name": "nanjing",
                "fields": [{"name": "OBJECTID", "type": "OID"}],
                "selected_count": 0,
                "selection_hash": ""
            }]
        }
        selected = {
            "mxd_path": r"C:\maps\city.mxd",
            "is_saved": True,
            "layers": [{
                "name": "nanjing",
                "fields": [{"name": "OBJECTID", "type": "OID"}],
                "selected_count": 2,
                "selection_hash": module.context_fingerprint.selection_hash("2;1")
            }]
        }
        self.assertNotEqual(module.context_hash(base), module.context_hash(selected))

    def test_gateway_and_runtime_use_same_hash(self):
        sys.modules["arcpy"] = types.SimpleNamespace()
        spec = importlib.util.spec_from_file_location("context_reader_gateway", RUNTIME_ROOT / "context_reader.py")
        runtime_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime_module)
        from gateway_py3.validators import context_hash as gateway_context_hash

        context = {
            "mxd_path": r"C:\maps\city.mxd",
            "is_saved": True,
            "active_view": "PAGE_LAYOUT",
            "layers": [{"name": "nanjing", "selected_count": 0}]
        }
        self.assertEqual(runtime_module.context_hash(context), gateway_context_hash(context))


if __name__ == "__main__":
    unittest.main()
