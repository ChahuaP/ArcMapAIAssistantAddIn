# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import sys
import types
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


class _Layer(object):
    def __init__(self, path):
        self.name = path
        self.longName = path
        self.dataSource = path
        self.visible = True
        self.isFeatureLayer = True
        self._selection = set()

    def supports(self, capability):
        return capability == "DATASOURCE"

    def getSelectionSet(self):
        return set(self._selection)

    def setSelectionSet(self, method, values):
        self._selection = set(values)


class _Mapping(object):
    def __init__(self):
        self.layers = []

    def Layer(self, path):
        return _Layer(path)

    def MapDocument(self, value):
        return "mxd"

    def ListDataFrames(self, mxd):
        return ["df"]

    def ListLayers(self, mxd, wildcard, data_frame):
        return list(self.layers)

    def AddLayer(self, data_frame, layer, position):
        self.layers.append(layer)


FAKE_ARCPY = types.ModuleType("arcpy")
FAKE_ARCPY.ExecuteError = RuntimeError
FAKE_ARCPY.env = type("Environment", (object,), {"addOutputsToMap": True})()
FAKE_ARCPY.mapping = _Mapping()
PY2 = sys.version_info[0] == 2
if PY2:
    sys.modules["arcpy"] = FAKE_ARCPY
    from arcmap_runtime_py2 import execution_session
    from arcmap_runtime_py2 import output_publisher


@unittest.skipUnless(PY2, "ArcMap Python 2.7 runtime test")
class ExecutionSessionPython27Tests(unittest.TestCase):
    def setUp(self):
        FAKE_ARCPY.mapping.layers = []
        FAKE_ARCPY.env.addOutputsToMap = True

    def test_from_step_layer_is_detached_and_all_outputs_are_published(self):
        with execution_session.ExecutionSession() as session:
            session.register_output("s1", r"D:\out\intermediate.shp")
            session.register_output("s2", r"D:\out\final.shp")
            layer = session.layer_for_output("s1", r"D:\out\intermediate.shp")
            layer.visible = False
            layer.setSelectionSet("NEW", [3, 1])
            paths = session.publication_plan().paths
            records = session.publication_plan().records
            self.assertEqual(FAKE_ARCPY.mapping.layers, [])
            self.assertFalse(FAKE_ARCPY.env.addOutputsToMap)

        self.assertEqual(layer.dataSource, r"D:\out\intermediate.shp")
        self.assertEqual(paths, [r"D:\out\intermediate.shp", r"D:\out\final.shp"])
        self.assertTrue(FAKE_ARCPY.env.addOutputsToMap)
        output_publisher.publish(execution_session.PublicationPlan.from_records(records))
        self.assertEqual([item.dataSource for item in FAKE_ARCPY.mapping.layers], paths)
        self.assertFalse(FAKE_ARCPY.mapping.layers[0].visible)
        self.assertEqual(FAKE_ARCPY.mapping.layers[0].getSelectionSet(), set([1, 3]))


if __name__ == "__main__":
    unittest.main()
