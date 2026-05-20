import importlib
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class ConditionUtilsTests(unittest.TestCase):
    def setUp(self):
        if str(RUNTIME_ROOT) not in sys.path:
            sys.path.insert(0, str(RUNTIME_ROOT))
        fake_arcpy = types.SimpleNamespace()
        fake_arcpy.ListFields = lambda layer: [
            FakeField("a", "String"),
            FakeField("b", "Integer"),
            FakeField("name", "String")
        ]
        fake_arcpy.Describe = lambda layer: types.SimpleNamespace(path=r"C:\data")
        fake_arcpy.AddFieldDelimiters = lambda workspace, field: "[%s]" % field
        sys.modules["arcpy"] = fake_arcpy
        from operations import common
        from operations import condition_utils
        importlib.reload(common)
        self.condition_utils = importlib.reload(condition_utils)

    def test_compiles_between_condition(self):
        where = self.condition_utils.compile_where(
            object(),
            {"field": "b", "op": "between", "values": [10, 20]}
        )
        self.assertEqual(where, "[b] BETWEEN 10 AND 20")

    def test_compiles_nested_and_condition(self):
        where = self.condition_utils.compile_where(
            object(),
            {
                "op": "and",
                "conditions": [
                    {"field": "a", "op": "eq", "value": "c"},
                    {"field": "b", "op": "gt", "value": 3}
                ]
            }
        )
        self.assertEqual(where, "([a] = 'c') AND ([b] > 3)")

    def test_rejects_unknown_field(self):
        with self.assertRaises(Exception):
            self.condition_utils.compile_where(object(), {"field": "missing", "op": "eq", "value": "x"})


class FakeField:
    def __init__(self, name, field_type):
        self.name = name
        self.type = field_type


if __name__ == "__main__":
    unittest.main()
