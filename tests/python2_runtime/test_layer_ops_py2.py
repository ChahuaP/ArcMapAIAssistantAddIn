# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import sys
import types
import unittest
import weakref


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class _Mapping(object):
    def __init__(self, layers):
        self.layers = list(layers)
        self.removed_count = 0

    def ListLayers(self, mxd, wildcard, data_frame):
        return list(self.layers)

    def RemoveLayer(self, data_frame, layer):
        self.layers.remove(layer)
        self.removed_count += 1


class _Layer(object):
    pass


class ClearLayersOwnershipTests(unittest.TestCase):
    def test_clear_layers_releases_owned_layer_proxies_before_returning(self):
        first = _Layer()
        second = _Layer()
        references = [weakref.ref(first), weakref.ref(second)]
        mapping = _Mapping([first, second])
        del first
        del second
        fake_arcpy = types.ModuleType("arcpy")
        fake_arcpy.mapping = mapping
        sys.modules["arcpy"] = fake_arcpy

        from arcmap_runtime_py2.operations import common
        from arcmap_runtime_py2.operations import layer_ops

        original_mxd = common.current_mxd
        original_frame = common.active_data_frame
        original_arcpy = layer_ops.arcpy
        try:
            layer_ops.arcpy = fake_arcpy
            common.current_mxd = lambda: object()
            common.active_data_frame = lambda mxd: object()

            result = layer_ops.clear_layers({}, {}, {})
        finally:
            layer_ops.arcpy = original_arcpy
            common.current_mxd = original_mxd
            common.active_data_frame = original_frame

        self.assertEqual({"removed_count": 2}, result)
        self.assertEqual(2, mapping.removed_count)
        self.assertEqual([None, None], [reference() for reference in references])


if __name__ == "__main__":
    unittest.main()
