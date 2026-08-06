# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import re
import shutil
import sys
import tempfile
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
        self.catalogPath = path

    def supports(self, capability):
        return capability == "DATASOURCE"


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
        added = _Layer(layer.dataSource)
        added.name = layer.name
        added.longName = layer.longName
        added.visible = layer.visible
        added.isFeatureLayer = layer.isFeatureLayer
        added._selection = set(layer._selection)
        self.layers.append(added)

    def RemoveLayer(self, data_frame, layer):
        self.layers.remove(layer)


def _describe(layer):
    return type("Description", (object,), {
        "FIDSet": "; ".join(str(oid) for oid in sorted(layer._selection)),
        "OIDFieldName": "OBJECTID",
        "catalogPath": layer.catalogPath,
    })()


def _select(layer, selection_type, where_clause=None):
    if selection_type == "CLEAR_SELECTION":
        layer._selection = set()
        return
    values = set(int(value) for value in re.search(r"\((.*)\)", where_clause).group(1).split(","))
    if selection_type == "NEW_SELECTION":
        layer._selection = values
    elif selection_type == "ADD_TO_SELECTION":
        layer._selection.update(values)
    else:
        raise RuntimeError("unexpected selection type: %s" % selection_type)


FAKE_ARCPY = types.ModuleType("arcpy")
FAKE_ARCPY.ExecuteError = RuntimeError
FAKE_ARCPY.env = type("Environment", (object,), {"addOutputsToMap": True})()
FAKE_ARCPY.mapping = _Mapping()
FAKE_ARCPY.Describe = _describe
FAKE_ARCPY.delimiter_calls = []
FAKE_ARCPY.selection_calls = []
FAKE_ARCPY.map_refreshes = []
FAKE_ARCPY.RefreshTOC = lambda: FAKE_ARCPY.map_refreshes.append("toc")
FAKE_ARCPY.RefreshActiveView = lambda: FAKE_ARCPY.map_refreshes.append("view")


def _field_delimiters(data_source, field):
    if not isinstance(data_source, basestring) or not data_source:
        raise RuntimeError("data source must be a non-empty string")
    FAKE_ARCPY.delimiter_calls.append((data_source, field))
    return field


FAKE_ARCPY.AddFieldDelimiters = _field_delimiters
FAKE_ARCPY.SelectLayerByAttribute_management = _select
FAKE_ARCPY.MakeFeatureLayer_management = lambda path, name: type(
    "FeatureLayerResult", (object,), {"getOutput": lambda self, index: _Layer(path)}
)()
FAKE_ARCPY.MakeRasterLayer_management = lambda path, name: type(
    "RasterLayerResult", (object,), {"getOutput": lambda self, index: _Layer(path)}
)()
FAKE_ARCPY.Delete_management = lambda name: None
PY2 = sys.version_info[0] == 2
if PY2:
    sys.modules["arcpy"] = FAKE_ARCPY
    from arcmap_runtime_py2 import execution_session
    from arcmap_runtime_py2 import exception_text
    from arcmap_runtime_py2 import output_publisher
    from arcmap_runtime_py2 import workflow_executor
    from arcmap_runtime_py2 import arcmap_desktop_selection
    from arcmap_runtime_py2 import artifact_observation
    from arcmap_runtime_py2 import context_reader
    from arcmap_runtime_py2 import map_state_observation
    from arcmap_runtime_py2.operations import common as runtime_common
    from arcmap_runtime_py2.operations import condition_utils as runtime_condition_utils

    _OPERATIONS = types.ModuleType("operations")
    _OPERATIONS.common = runtime_common
    _OPERATIONS.condition_utils = runtime_condition_utils
    sys.modules["operations"] = _OPERATIONS
    sys.modules["operations.common"] = runtime_common
    sys.modules["operations.condition_utils"] = runtime_condition_utils
    from arcmap_runtime_py2.operations import export_ops
    from arcmap_runtime_py2.operations import layer_ops
    from arcmap_runtime_py2.operations import layout_ops
    from arcmap_runtime_py2.operations import map_ops
    from arcmap_runtime_py2.operations import selection_ops
    sys.modules["operations.layer_ops"] = layer_ops
    sys.modules["operations.layout_ops"] = layout_ops
    sys.modules["operations.map_ops"] = map_ops
    sys.modules["operations.selection_ops"] = selection_ops


@unittest.skipUnless(PY2, "ArcMap Python 2.7 runtime test")
class ExecutionSessionPython27Tests(unittest.TestCase):
    def setUp(self):
        FAKE_ARCPY.mapping.layers = []
        FAKE_ARCPY.env.addOutputsToMap = True
        FAKE_ARCPY.delimiter_calls = []
        FAKE_ARCPY.selection_calls = []
        FAKE_ARCPY.map_refreshes = []
        FAKE_ARCPY.SelectLayerByAttribute_management = _select

    def test_workflow_executor_commits_only_map_mutating_steps(self):
        workflow_executor._commit_map_state_if_needed({"side_effects": "read_only"})
        self.assertEqual([], FAKE_ARCPY.map_refreshes)

        workflow_executor._commit_map_state_if_needed({"id": "selection.clear_selection", "side_effects": "changes_map"})
        self.assertEqual(["toc", "view"], FAKE_ARCPY.map_refreshes)

        workflow_executor._commit_map_state_if_needed({"id": "layer.clear_layers", "side_effects": "changes_map"})
        self.assertEqual(["toc", "view", "toc", "view"], FAKE_ARCPY.map_refreshes)

    def test_public_workflow_rejects_unverifiable_generic_map_state_contract(self):
        original_load = workflow_executor._load_operations
        original_call = workflow_executor._call_executor
        try:
            workflow_executor._load_operations = lambda: {
                "legacy.map_change": {
                    "executor": "legacy",
                    "parameters_schema": {"type": "object", "properties": {}},
                    "side_effects": "changes_map",
                    "output_policy": {"writes_output": False},
                    "capability_contract": {
                        "outputs": {
                            "kind": "map_state",
                            "geometry": {"rule": "not_applicable"},
                            "fields": {"effect": "not_applicable"},
                            "spatial_reference": {"rule": "not_applicable"},
                            "cardinality": {"rule": "fixed", "value": "one_snapshot"},
                            "selection_state": "not_applicable",
                            "map_publication": "map_state_updated",
                        },
                        "postconditions": [{
                            "kind": "unverifiable_generic_map_state",
                            "target": "map",
                            "expectation": {
                                "kind": {"ref": "outputs.kind"},
                                "geometry": {"ref": "outputs.geometry"},
                                "fields": {"ref": "outputs.fields"},
                                "spatial_reference": {"ref": "outputs.spatial_reference"},
                                "cardinality": {"ref": "outputs.cardinality"},
                                "selection_state": {"ref": "outputs.selection_state"},
                                "map_publication": {"ref": "outputs.map_publication"},
                            },
                        }],
                    },
                },
            }
            workflow_executor._call_executor = lambda executor, context, arguments, outputs: {"changed": True}
            context = {"layers": [], "is_saved": True}
            row = {
                "context_hash": context_reader.context_hash(context),
                "workflow": {
                    "summary": "legacy map mutation",
                    "steps": [{"id": "legacy", "operation": "legacy.map_change", "arguments": {}}],
                },
            }

            with self.assertRaises(workflow_executor.WorkflowExecutionError) as caught:
                workflow_executor.execute(row, context)

            self.assertEqual("capability_contract.postconditions[0].kind", caught.exception.contract_path)
            self.assertEqual("supported map-state postcondition", caught.exception.expected)
        finally:
            workflow_executor._load_operations = original_load
            workflow_executor._call_executor = original_call

    def test_zoom_to_layer_executes_through_public_workflow_and_verifies_map_state(self):
        class Extent(object):
            def __init__(self, xmin, ymin, xmax, ymax):
                self.XMin = xmin
                self.YMin = ymin
                self.XMax = xmax
                self.YMax = ymax

        class DataFrame(object):
            name = "Layers"

            def __init__(self, normalized_extent):
                self._extent = Extent(0, 0, 1, 1)
                self.normalized_extent = normalized_extent

            @property
            def extent(self):
                return self._extent

            @extent.setter
            def extent(self, value):
                self._extent = self.normalized_extent

        target_extent = Extent(
            671072.7249374051,
            3543847.6274563023,
            682970.4522825248,
            3553669.6540463837,
        )
        normalized_extent = Extent(
            670544.2617720847,
            3543837.7754516313,
            683498.9154478451,
            3553679.5060510547,
        )
        data_frame = DataFrame(normalized_extent)
        mxd = type("MapDocument", (object,), {"activeView": "Layers"})()
        layer = _Layer(u"D:\\data\\final_sites.shp")
        layer.name = "final_sites"
        layer.longName = "final_sites"
        layer.getExtent = lambda: target_extent
        context = {
            "layers": [{
                "layer_ref": "layer:0",
                "name": "final_sites",
                "longName": "final_sites",
                "dataSource": layer.dataSource,
            }],
            "is_saved": True,
        }
        row = {
            "context_hash": context_reader.context_hash(context),
            "workflow": {
                "summary": "zoom to final sites",
                "steps": [{
                    "id": "zoom",
                    "operation": "view.zoom_to_layer",
                    "arguments": {"layer": "layer:0"},
                }],
            },
        }
        original_document = FAKE_ARCPY.mapping.MapDocument
        original_frames = FAKE_ARCPY.mapping.ListDataFrames
        original_layers = FAKE_ARCPY.mapping.ListLayers
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        try:
            FAKE_ARCPY.mapping.MapDocument = lambda value: mxd
            FAKE_ARCPY.mapping.ListDataFrames = lambda value: [data_frame]
            FAKE_ARCPY.mapping.ListLayers = lambda document, wildcard, frame: [layer]
            FAKE_ARCPY.Exists = lambda value: value is not layer

            outcome = workflow_executor.execute(row, context)

            self.assertTrue(outcome.result["ok"])
            self.assertIs(data_frame.extent, normalized_extent)
            observation = outcome.result["steps"][0]["result"]["observation"]
            self.assertEqual("map_state", observation["kind"])
            self.assertEqual("passed", observation["contract"]["verdict"])
        finally:
            FAKE_ARCPY.mapping.MapDocument = original_document
            FAKE_ARCPY.mapping.ListDataFrames = original_frames
            FAKE_ARCPY.mapping.ListLayers = original_layers
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists

    def test_zoom_extent_accepts_arcmap_viewport_fit_but_rejects_excess_scale(self):
        expected = {
            "XMin": 671072.7249374051,
            "YMin": 3543847.6274563023,
            "XMax": 682970.4522825248,
            "YMax": 3553669.6540463837,
        }
        arcmap_fit = {
            "XMin": 670544.2617720847,
            "YMin": 3543837.7754516313,
            "XMax": 683498.9154478451,
            "YMax": 3553679.5060510547,
        }
        tall_arcmap_fit = {
            "XMin": 667961.6449945566,
            "YMin": 3538442.173112339,
            "XMax": 687858.3550054434,
            "YMax": 3553557.826887661,
        }
        tall_expected = {
            "XMin": 672267.751588101,
            "YMin": 3538447.751588101,
            "XMax": 683552.248411899,
            "YMax": 3553552.248411899,
        }
        excessive = {
            "XMin": 600000.0,
            "YMin": 3470000.0,
            "XMax": 754043.1772199299,
            "YMax": 3627517.281502686,
        }
        off_center = dict(arcmap_fit)
        off_center["XMin"] += 100.0
        off_center["XMax"] += 100.0
        does_not_cover = dict(arcmap_fit)
        does_not_cover["XMin"] = expected["XMin"] + 1.0

        self.assertTrue(map_state_observation._extent_is_fitted(arcmap_fit, expected))
        self.assertTrue(map_state_observation._extent_is_fitted(tall_arcmap_fit, tall_expected))
        self.assertFalse(map_state_observation._extent_is_fitted(excessive, expected))
        self.assertFalse(map_state_observation._extent_is_fitted(off_center, expected))
        self.assertFalse(map_state_observation._extent_is_fitted(does_not_cover, expected))

    def test_set_visibility_executes_through_public_workflow_and_verifies_map_state(self):
        data_frame = type("DataFrame", (object,), {"name": "Layers"})()
        mxd = type("MapDocument", (object,), {"activeView": "Layers"})()
        layer = _Layer(u"D:\\data\\final_sites.shp")
        layer.name = "final_sites"
        layer.longName = "final_sites"
        context = {
            "layers": [{
                "layer_ref": "layer:0",
                "name": "final_sites",
                "longName": "final_sites",
                "dataSource": layer.dataSource,
            }],
            "is_saved": True,
        }
        row = {
            "context_hash": context_reader.context_hash(context),
            "workflow": {
                "summary": "hide final sites",
                "steps": [{
                    "id": "hide",
                    "operation": "layer.set_visibility",
                    "arguments": {"layer": "layer:0", "visible": False},
                }],
            },
        }
        original_document = FAKE_ARCPY.mapping.MapDocument
        original_frames = FAKE_ARCPY.mapping.ListDataFrames
        original_layers = FAKE_ARCPY.mapping.ListLayers
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        try:
            FAKE_ARCPY.mapping.MapDocument = lambda value: mxd
            FAKE_ARCPY.mapping.ListDataFrames = lambda value: [data_frame]
            FAKE_ARCPY.mapping.ListLayers = lambda document, wildcard, frame: [layer]
            FAKE_ARCPY.Exists = lambda value: value is not layer

            outcome = workflow_executor.execute(row, context)

            self.assertFalse(layer.visible)
            observation = outcome.result["steps"][0]["result"]["observation"]
            self.assertEqual("map_state", observation["kind"])
            self.assertEqual("passed", observation["contract"]["verdict"])
        finally:
            FAKE_ARCPY.mapping.MapDocument = original_document
            FAKE_ARCPY.mapping.ListDataFrames = original_frames
            FAKE_ARCPY.mapping.ListLayers = original_layers
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists

    def test_move_layer_executes_through_public_workflow_and_verifies_requested_position(self):
        data_frame = type("DataFrame", (object,), {"name": "Layers"})()
        mxd = type("MapDocument", (object,), {"activeView": "Layers"})()
        roads = _Layer(u"D:\\data\\roads.shp")
        rivers = _Layer(u"D:\\data\\rivers.shp")
        boundary = _Layer(u"D:\\data\\boundary.shp")
        for layer, name in ((roads, "roads"), (rivers, "rivers"), (boundary, "boundary")):
            layer.name = name
            layer.longName = name
        layers = [roads, rivers, boundary]
        context = {
            "layers": [{
                "layer_ref": "layer:%d" % index,
                "name": layer.name,
                "longName": layer.longName,
                "dataSource": layer.dataSource,
            } for index, layer in enumerate(layers)],
            "is_saved": True,
        }
        row = {
            "context_hash": context_reader.context_hash(context),
            "workflow": {
                "summary": "move rivers up one position",
                "steps": [{
                    "id": "move",
                    "operation": "layer.move_layer",
                    "arguments": {"layer": "layer:1", "position": "UP"},
                }],
            },
        }
        original_document = FAKE_ARCPY.mapping.MapDocument
        original_frames = FAKE_ARCPY.mapping.ListDataFrames
        original_layers = FAKE_ARCPY.mapping.ListLayers
        original_move = getattr(FAKE_ARCPY.mapping, "MoveLayer", None)
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        try:
            FAKE_ARCPY.mapping.MapDocument = lambda value: mxd
            FAKE_ARCPY.mapping.ListDataFrames = lambda value: [data_frame]
            FAKE_ARCPY.mapping.ListLayers = lambda document, wildcard, frame: list(layers)

            def move_layer(frame, reference, layer, position):
                layers.remove(layer)
                reference_index = layers.index(reference)
                insertion_index = reference_index if position == "BEFORE" else reference_index + 1
                layers.insert(insertion_index, layer)

            FAKE_ARCPY.mapping.MoveLayer = move_layer
            FAKE_ARCPY.Exists = lambda value: value is not rivers

            outcome = workflow_executor.execute(row, context)

            self.assertEqual(["rivers", "roads", "boundary"], [layer.name for layer in layers])
            observation = outcome.result["steps"][0]["result"]["observation"]
            self.assertEqual("layer_position_matches_request", observation["map_state_check"]["kind"])
            self.assertEqual("passed", observation["contract"]["verdict"])
        finally:
            FAKE_ARCPY.mapping.MapDocument = original_document
            FAKE_ARCPY.mapping.ListDataFrames = original_frames
            FAKE_ARCPY.mapping.ListLayers = original_layers
            if original_move is None:
                delattr(FAKE_ARCPY.mapping, "MoveLayer")
            else:
                FAKE_ARCPY.mapping.MoveLayer = original_move
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists

    def test_zoom_to_selection_executes_through_public_workflow_and_verifies_extent(self):
        class Extent(object):
            def __init__(self, xmin, ymin, xmax, ymax):
                self.XMin = xmin
                self.YMin = ymin
                self.XMax = xmax
                self.YMax = ymax

        selected_extent = Extent(12, 22, 28, 38)
        data_frame = type("DataFrame", (object,), {
            "name": "Layers",
            "extent": Extent(0, 0, 1, 1),
        })()
        mxd = type("MapDocument", (object,), {"activeView": "Layers"})()
        layer = _Layer(u"D:\\data\\final_sites.shp")
        layer.name = "final_sites"
        layer.longName = "final_sites"
        layer.getSelectedExtent = lambda: selected_extent
        context = {
            "layers": [{
                "layer_ref": "layer:0",
                "name": "final_sites",
                "longName": "final_sites",
                "dataSource": layer.dataSource,
            }],
            "is_saved": True,
        }
        row = {
            "context_hash": context_reader.context_hash(context),
            "workflow": {
                "summary": "zoom to selected final sites",
                "steps": [{
                    "id": "zoom_selection",
                    "operation": "view.zoom_to_selection",
                    "arguments": {"layer": "layer:0"},
                }],
            },
        }
        original_document = FAKE_ARCPY.mapping.MapDocument
        original_frames = FAKE_ARCPY.mapping.ListDataFrames
        original_layers = FAKE_ARCPY.mapping.ListLayers
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        try:
            FAKE_ARCPY.mapping.MapDocument = lambda value: mxd
            FAKE_ARCPY.mapping.ListDataFrames = lambda value: [data_frame]
            FAKE_ARCPY.mapping.ListLayers = lambda document, wildcard, frame: [layer]
            FAKE_ARCPY.Exists = lambda value: value is not layer

            outcome = workflow_executor.execute(row, context)

            self.assertIs(data_frame.extent, selected_extent)
            observation = outcome.result["steps"][0]["result"]["observation"]
            self.assertEqual("extent_matches_selection", observation["map_state_check"]["kind"])
            self.assertEqual("passed", observation["contract"]["verdict"])
        finally:
            FAKE_ARCPY.mapping.MapDocument = original_document
            FAKE_ARCPY.mapping.ListDataFrames = original_frames
            FAKE_ARCPY.mapping.ListLayers = original_layers
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists

    def test_set_active_view_executes_through_public_workflow_and_verifies_requested_view(self):
        mxd = type("MapDocument", (object,), {"activeView": "Layers"})()
        context = {"layers": [], "is_saved": True}
        row = {
            "context_hash": context_reader.context_hash(context),
            "workflow": {
                "summary": "switch to page layout",
                "steps": [{
                    "id": "layout_view",
                    "operation": "layout.set_active_view",
                    "arguments": {"view_mode": "PAGE_LAYOUT"},
                }],
            },
        }
        original_document = FAKE_ARCPY.mapping.MapDocument
        try:
            FAKE_ARCPY.mapping.MapDocument = lambda value: mxd

            outcome = workflow_executor.execute(row, context)

            self.assertEqual("PAGE_LAYOUT", mxd.activeView)
            observation = outcome.result["steps"][0]["result"]["observation"]
            self.assertEqual("active_view_matches_request", observation["map_state_check"]["kind"])
            self.assertEqual("passed", observation["contract"]["verdict"])
        finally:
            FAKE_ARCPY.mapping.MapDocument = original_document

    def test_set_layout_text_executes_through_public_workflow_and_verifies_text(self):
        mxd = type("MapDocument", (object,), {"activeView": "PAGE_LAYOUT"})()
        element = type("TextElement", (object,), {
            "name": "ReportTitle",
            "text": "Old title",
        })()
        context = {"layers": [], "is_saved": True}
        row = {
            "context_hash": context_reader.context_hash(context),
            "workflow": {
                "summary": "set report title",
                "steps": [{
                    "id": "title",
                    "operation": "layout.set_text",
                    "arguments": {
                        "element_name": "ReportTitle",
                        "match": "EXACT",
                        "text": "Facility siting results",
                    },
                }],
            },
        }
        original_document = FAKE_ARCPY.mapping.MapDocument
        original_elements = getattr(FAKE_ARCPY.mapping, "ListLayoutElements", None)
        try:
            FAKE_ARCPY.mapping.MapDocument = lambda value: mxd
            FAKE_ARCPY.mapping.ListLayoutElements = lambda document, kind, *args: [element]

            outcome = workflow_executor.execute(row, context)

            self.assertEqual("Facility siting results", element.text)
            observation = outcome.result["steps"][0]["result"]["observation"]
            self.assertEqual("layout_text_matches_request", observation["map_state_check"]["kind"])
            self.assertEqual("passed", observation["contract"]["verdict"])
        finally:
            FAKE_ARCPY.mapping.MapDocument = original_document
            if original_elements is None:
                delattr(FAKE_ARCPY.mapping, "ListLayoutElements")
            else:
                FAKE_ARCPY.mapping.ListLayoutElements = original_elements

    def test_read_only_map_queries_execute_through_public_workflow_and_verify_live_state(self):
        extent = type("Extent", (object,), {
            "XMin": 10.0, "YMin": 20.0, "XMax": 30.0, "YMax": 40.0,
        })()
        spatial_reference = type("SpatialReference", (object,), {
            "name": "WGS_1984_UTM_Zone_50N", "factoryCode": 32650,
        })()
        data_frame = type("DataFrame", (object,), {
            "name": "Layers", "extent": extent, "spatialReference": spatial_reference,
        })()
        mxd = type("MapDocument", (object,), {
            "activeView": "Layers", "filePath": "", "defaultGeodatabase": "",
        })()
        context = {"layers": [], "is_saved": True}
        row = {
            "context_hash": context_reader.context_hash(context),
            "workflow": {
                "summary": "inspect the current map",
                "steps": [
                    {"id": "layers", "operation": "context.list_layers", "arguments": {}},
                    {"id": "extent", "operation": "context.get_map_extent", "arguments": {}},
                    {"id": "spatial_reference", "operation": "context.get_spatial_reference", "arguments": {}},
                    {"id": "layout", "operation": "layout.list_elements", "arguments": {"element_type": "ALL"}},
                ],
            },
        }
        original_document = FAKE_ARCPY.mapping.MapDocument
        original_frames = FAKE_ARCPY.mapping.ListDataFrames
        original_layers = FAKE_ARCPY.mapping.ListLayers
        original_elements = getattr(FAKE_ARCPY.mapping, "ListLayoutElements", None)
        try:
            FAKE_ARCPY.mapping.MapDocument = lambda value: mxd
            FAKE_ARCPY.mapping.ListDataFrames = lambda value: [data_frame]
            FAKE_ARCPY.mapping.ListLayers = lambda document, wildcard, frame: []
            FAKE_ARCPY.mapping.ListLayoutElements = lambda document, kind, *args: []

            outcome = workflow_executor.execute(row, context)

            observations = dict(
                (step["step_id"], step["result"]["observation"]["map_state_check"])
                for step in outcome.result["steps"]
            )
            self.assertEqual("layer_inventory_matches_live_map", observations["layers"]["kind"])
            self.assertEqual("map_extent_matches_live_view", observations["extent"]["kind"])
            self.assertEqual(
                "spatial_reference_matches_live_frame",
                observations["spatial_reference"]["kind"],
            )
            self.assertEqual("layout_elements_match_live_layout", observations["layout"]["kind"])
            self.assertTrue(all(check["verdict"] == "passed" for check in observations.values()))
        finally:
            FAKE_ARCPY.mapping.MapDocument = original_document
            FAKE_ARCPY.mapping.ListDataFrames = original_frames
            FAKE_ARCPY.mapping.ListLayers = original_layers
            if original_elements is None:
                delattr(FAKE_ARCPY.mapping, "ListLayoutElements")
            else:
                FAKE_ARCPY.mapping.ListLayoutElements = original_elements

    def test_layer_queries_execute_through_public_workflow_and_verify_live_state(self):
        spatial_reference = type("SpatialReference", (object,), {
            "name": "WGS_1984_UTM_Zone_50N", "factoryCode": 32650,
        })()
        data_frame = type("DataFrame", (object,), {
            "name": "Layers", "extent": None, "spatialReference": spatial_reference,
        })()
        mxd = type("MapDocument", (object,), {
            "activeView": "Layers", "filePath": "", "defaultGeodatabase": "",
        })()
        layer = _Layer(u"D:\\data\\parcels.shp")
        layer.name = "parcels"
        layer.longName = "parcels"
        layer._selection = set([1, 3])
        fields = [
            type("Field", (object,), {"name": "OBJECTID", "type": "OID"})(),
            type("Field", (object,), {"name": "ZONE", "type": "String"})(),
        ]
        context = {
            "layers": [{
                "layer_ref": "layer:0",
                "name": layer.name,
                "longName": layer.longName,
                "dataSource": layer.dataSource,
            }],
            "is_saved": True,
        }
        row = {
            "context_hash": context_reader.context_hash(context),
            "workflow": {
                "summary": "inspect parcels",
                "steps": [
                    {"id": "description", "operation": "context.describe_layer", "arguments": {"layer": "layer:0"}},
                    {"id": "fields", "operation": "context.list_fields", "arguments": {"layer": "layer:0"}},
                    {"id": "selection", "operation": "context.get_selection_count", "arguments": {"layer": "layer:0"}},
                ],
            },
        }
        original_document = FAKE_ARCPY.mapping.MapDocument
        original_frames = FAKE_ARCPY.mapping.ListDataFrames
        original_layers = FAKE_ARCPY.mapping.ListLayers
        original_describe = FAKE_ARCPY.Describe
        original_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_da = getattr(FAKE_ARCPY, "da", None)
        try:
            FAKE_ARCPY.mapping.MapDocument = lambda value: mxd
            FAKE_ARCPY.mapping.ListDataFrames = lambda value: [data_frame]
            FAKE_ARCPY.mapping.ListLayers = lambda document, wildcard, frame: [layer]
            FAKE_ARCPY.Describe = lambda value: type("Description", (object,), {
                "FIDSet": "; ".join(str(oid) for oid in sorted(value._selection)),
                "OIDFieldName": "OBJECTID",
                "catalogPath": value.catalogPath,
                "shapeType": "Polygon",
                "spatialReference": spatial_reference,
            })()
            FAKE_ARCPY.ListFields = lambda value: list(fields)
            FAKE_ARCPY.da = type("DataAccess", (object,), {
                "SearchCursor": lambda self, value, field_names: iter([
                    tuple(1 if name == "OBJECTID" else "A" for name in field_names),
                    tuple(3 if name == "OBJECTID" else "B" for name in field_names),
                ]),
            })()

            outcome = workflow_executor.execute(row, context)

            observations = dict(
                (step["step_id"], step["result"]["observation"]["map_state_check"])
                for step in outcome.result["steps"]
            )
            self.assertEqual("layer_description_matches_live_map", observations["description"]["kind"])
            self.assertEqual("field_inventory_matches_live_layer", observations["fields"]["kind"])
            self.assertEqual("selection_count_matches_live_layer", observations["selection"]["kind"])
            self.assertTrue(all(check["verdict"] == "passed" for check in observations.values()))
        finally:
            FAKE_ARCPY.mapping.MapDocument = original_document
            FAKE_ARCPY.mapping.ListDataFrames = original_frames
            FAKE_ARCPY.mapping.ListLayers = original_layers
            FAKE_ARCPY.Describe = original_describe
            if original_fields is None:
                delattr(FAKE_ARCPY, "ListFields")
            else:
                FAKE_ARCPY.ListFields = original_fields
            if original_da is None:
                delattr(FAKE_ARCPY, "da")
            else:
                FAKE_ARCPY.da = original_da

    def test_clear_selection_executes_through_public_workflow_and_verifies_live_count(self):
        data_frame = type("DataFrame", (object,), {"name": "Layers"})()
        mxd = type("MapDocument", (object,), {"activeView": "Layers"})()
        layer = _Layer(u"D:\\data\\parcels.shp")
        layer.name = "parcels"
        layer.longName = "parcels"
        layer._selection = set([1, 3])
        context = {
            "layers": [{
                "layer_ref": "layer:0",
                "name": layer.name,
                "longName": layer.longName,
                "dataSource": layer.dataSource,
            }],
            "is_saved": True,
        }
        row = {
            "context_hash": context_reader.context_hash(context),
            "workflow": {
                "summary": "clear the current parcel selection",
                "steps": [{
                    "id": "clear",
                    "operation": "selection.clear_selection",
                    "arguments": {"layer": "layer:0"},
                }],
            },
        }
        original_document = FAKE_ARCPY.mapping.MapDocument
        original_frames = FAKE_ARCPY.mapping.ListDataFrames
        original_layers = FAKE_ARCPY.mapping.ListLayers
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        try:
            FAKE_ARCPY.mapping.MapDocument = lambda value: mxd
            FAKE_ARCPY.mapping.ListDataFrames = lambda value: [data_frame]
            FAKE_ARCPY.mapping.ListLayers = lambda document, wildcard, frame: [layer]
            FAKE_ARCPY.Exists = lambda value: value is not layer

            outcome = workflow_executor.execute(row, context)

            self.assertEqual(set(), layer._selection)
            result = outcome.result["steps"][0]["result"]
            self.assertEqual(0, result["selected_count"])
            self.assertEqual(
                "selection_count_matches_result",
                result["observation"]["map_state_check"]["kind"],
            )
        finally:
            FAKE_ARCPY.mapping.MapDocument = original_document
            FAKE_ARCPY.mapping.ListDataFrames = original_frames
            FAKE_ARCPY.mapping.ListLayers = original_layers
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists

    def test_layer_membership_mutations_execute_through_public_workflow_and_verify_live_map(self):
        data_frame = type("DataFrame", (object,), {"name": "Layers"})()
        mxd = type("MapDocument", (object,), {"activeView": "Layers"})()
        roads = _Layer(u"D:\\data\\roads.shp")
        boundary = _Layer(u"D:\\data\\boundary.shp")
        for layer, name in ((roads, "roads"), (boundary, "boundary")):
            layer.name = name
            layer.longName = name
        layers = [roads, boundary]
        context = {
            "layers": [{
                "layer_ref": "layer:%d" % index,
                "name": layer.name,
                "longName": layer.longName,
                "dataSource": layer.dataSource,
            } for index, layer in enumerate(layers)],
            "is_saved": True,
        }
        added_path = u"D:\\data\\rivers.shp"
        row = {
            "context_hash": context_reader.context_hash(context),
            "workflow": {
                "summary": "add, remove, and clear map layers",
                "steps": [
                    {"id": "add", "operation": "layer.add_layer", "arguments": {"path": added_path}},
                    {"id": "remove", "operation": "layer.remove_layer", "arguments": {"layer": "layer:0"}},
                    {"id": "clear", "operation": "layer.clear_layers", "arguments": {}},
                ],
            },
        }
        original_document = FAKE_ARCPY.mapping.MapDocument
        original_frames = FAKE_ARCPY.mapping.ListDataFrames
        original_layers = FAKE_ARCPY.mapping.ListLayers
        original_layer = FAKE_ARCPY.mapping.Layer
        original_add = FAKE_ARCPY.mapping.AddLayer
        original_remove = getattr(FAKE_ARCPY.mapping, "RemoveLayer", None)
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        try:
            FAKE_ARCPY.mapping.MapDocument = lambda value: mxd
            FAKE_ARCPY.mapping.ListDataFrames = lambda value: [data_frame]
            FAKE_ARCPY.mapping.ListLayers = lambda document, wildcard, frame: list(layers)
            FAKE_ARCPY.mapping.Layer = lambda path: _Layer(path)
            FAKE_ARCPY.mapping.AddLayer = lambda frame, layer, position: layers.insert(0, layer)
            FAKE_ARCPY.mapping.RemoveLayer = lambda frame, layer: layers.remove(layer)
            FAKE_ARCPY.Exists = lambda value: value == added_path

            outcome = workflow_executor.execute(row, context)

            self.assertEqual([], layers)
            checks = dict(
                (step["step_id"], step["result"]["observation"]["map_state_check"])
                for step in outcome.result["steps"]
            )
            self.assertEqual("layer_added_from_path", checks["add"]["kind"])
            self.assertEqual("layer_removed_from_map", checks["remove"]["kind"])
            self.assertEqual("all_layers_removed", checks["clear"]["kind"])
            self.assertTrue(all(check["verdict"] == "passed" for check in checks.values()))
        finally:
            FAKE_ARCPY.mapping.MapDocument = original_document
            FAKE_ARCPY.mapping.ListDataFrames = original_frames
            FAKE_ARCPY.mapping.ListLayers = original_layers
            FAKE_ARCPY.mapping.Layer = original_layer
            FAKE_ARCPY.mapping.AddLayer = original_add
            if original_remove is None:
                delattr(FAKE_ARCPY.mapping, "RemoveLayer")
            else:
                FAKE_ARCPY.mapping.RemoveLayer = original_remove
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists

    def test_nonempty_spaced_fid_set_is_captured_and_restored_on_live_layer(self):
        with execution_session.ExecutionSession() as session:
            session.register_output("s1", r"D:\out\intermediate.shp")
            session.register_output("s2", r"D:\out\final.shp")
            layer = session.layer_for_output("s1", r"D:\out\intermediate.shp")
            layer.visible = False
            layer._selection = set([3, 1])
            paths = session.publication_plan().paths
            records = session.publication_plan().records
            self.assertEqual(FAKE_ARCPY.mapping.layers, [layer])
            self.assertFalse(FAKE_ARCPY.env.addOutputsToMap)

        self.assertEqual(paths, [r"D:\out\intermediate.shp", r"D:\out\final.shp"])
        self.assertTrue(FAKE_ARCPY.env.addOutputsToMap)
        output_publisher.publish(execution_session.PublicationPlan.from_records(records))
        self.assertEqual([item.dataSource for item in FAKE_ARCPY.mapping.layers], paths)
        self.assertTrue(FAKE_ARCPY.mapping.layers[0].visible)
        self.assertEqual(FAKE_ARCPY.mapping.layers[0]._selection, set([1, 3]))
        self.assertIsNot(FAKE_ARCPY.mapping.layers[0], layer)
        self.assertEqual(FAKE_ARCPY.delimiter_calls[0], (r"D:\out\intermediate.shp", "OBJECTID"))

    def test_empty_selection_is_cleared_and_verified(self):
        layer = _Layer(r"D:\out\empty.shp")
        layer._selection = set([7])
        output_publisher._apply_state(
            layer, execution_session.PublicationItem(layer.dataSource, selection_oids=[]))
        self.assertEqual(layer._selection, set())

    def test_selection_restore_is_batched_with_new_then_add(self):
        layer = _Layer(r"D:\out\many.shp")
        expected = list(range(arcmap_desktop_selection.OID_BATCH_SIZE + 1))

        def recording_select(*args):
            FAKE_ARCPY.selection_calls.append(args[1])
            return _select(*args)

        FAKE_ARCPY.SelectLayerByAttribute_management = recording_select
        output_publisher._apply_state(
            layer, execution_session.PublicationItem(layer.dataSource, selection_oids=expected))
        self.assertEqual(FAKE_ARCPY.selection_calls, ["NEW_SELECTION", "ADD_TO_SELECTION"])
        self.assertEqual(layer._selection, set(expected))

    def test_selection_restore_verification_failure_aborts(self):
        layer = _Layer(r"D:\out\incorrect.shp")
        FAKE_ARCPY.SelectLayerByAttribute_management = lambda *args: None
        self.assertRaises(
            RuntimeError,
            output_publisher._apply_state,
            layer,
            execution_session.PublicationItem(layer.dataSource, selection_oids=[1]))

    def test_publication_replay_is_idempotent(self):
        item = execution_session.PublicationItem(r"D:\out\replay.shp", visible=False, selection_oids=[2])
        plan = execution_session.PublicationPlan([item])
        self.assertEqual(output_publisher.publish(plan)["published"], 1)
        self.assertEqual(output_publisher.publish(plan), {"published": 0, "already_visible": 1})
        self.assertEqual(len(FAKE_ARCPY.mapping.layers), 1)

    def test_registered_path_query_uses_only_runtime_layer_identity(self):
        source = u"D:\\成果\\final_sites.shp"
        with execution_session.ExecutionSession() as session:
            session.register_output("final_sites", source)
            detached = session.layer_for_output("final_sites", source)
            self.assertEqual(session.registered_path_for_detached_layer(detached), source)
            self.assertIsNone(session.registered_path_for_detached_layer(_Layer(source)))

    def test_runtime_layer_is_hidden_map_owned_and_stable_for_later_steps(self):
        original_make = FAKE_ARCPY.MakeFeatureLayer_management
        try:
            FAKE_ARCPY.MakeFeatureLayer_management = lambda *args: (_ for _ in ()).throw(
                RuntimeError("transient feature layers are forbidden"))
            with execution_session.ExecutionSession() as session:
                session.register_output("clip", r"D:\\out\\comm_in_flood.shp")
                layer = session.layer_for_output("clip", r"D:\\out\\comm_in_flood.shp")
                self.assertIn(layer, FAKE_ARCPY.mapping.layers)
                self.assertFalse(layer.visible)
                self.assertTrue(layer.name.startswith("geopilot_"))
                self.assertIs(layer, session.layer_for_output("clip", r"D:\\out\\comm_in_flood.shp"))
            self.assertNotIn(layer, FAKE_ARCPY.mapping.layers)
        finally:
            FAKE_ARCPY.MakeFeatureLayer_management = original_make

    def test_detached_layer_observation_separates_dataset_reads_from_selection_state(self):
        source = u"D:\\out\\comm_in_flood.shp"
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        original_describe = FAKE_ARCPY.Describe
        original_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_count = getattr(FAKE_ARCPY, "GetCount_management", None)
        count_sources = []
        valid = {"layer": True}
        try:
            with execution_session.ExecutionSession() as session:
                session.register_output("clip", source)
                layer = session.layer_for_output("clip", source)

                FAKE_ARCPY.Exists = lambda value: valid["layer"] if value is layer else value == source
                FAKE_ARCPY.Describe = lambda value: type(
                    "Description",
                    (object,),
                    {
                        "dataType": "FeatureClass",
                        "shapeType": "Point",
                        "spatialReference": type("SR", (object,), {"name": "WGS 1984"})(),
                        "FIDSet": "",
                        "OIDFieldName": "OBJECTID",
                        "catalogPath": source,
                    },
                )()
                FAKE_ARCPY.ListFields = lambda value: [
                    type("Field", (object,), {"name": "OBJECTID", "type": "OID"})(),
                    type("Field", (object,), {"name": "POP", "type": "Integer"})(),
                ]

                def get_count(value):
                    count_sources.append(value)
                    if value is layer:
                        valid["layer"] = False
                    return type("Count", (object,), {"getOutput": lambda self, index: "5"})()

                FAKE_ARCPY.GetCount_management = get_count

                observation = artifact_observation._observe(layer, "feature_class")

                self.assertEqual([source], count_sources)
                self.assertTrue(valid["layer"])
                self.assertEqual(5, observation["feature_count"])
                self.assertEqual(0, observation["selection_count"])
        finally:
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists
            FAKE_ARCPY.Describe = original_describe
            if original_fields is None:
                delattr(FAKE_ARCPY, "ListFields")
            else:
                FAKE_ARCPY.ListFields = original_fields
            if original_count is None:
                delattr(FAKE_ARCPY, "GetCount_management")
            else:
                FAKE_ARCPY.GetCount_management = original_count

    def test_dataset_output_observation_does_not_query_layer_selection_state(self):
        source = u"D:\\out\\empty_result.shp"
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        original_describe = FAKE_ARCPY.Describe
        original_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_count = getattr(FAKE_ARCPY, "GetCount_management", None)
        original_capture = artifact_observation.arcmap_desktop_selection.capture_oids
        try:
            FAKE_ARCPY.Exists = lambda value: value == source
            FAKE_ARCPY.Describe = lambda value: type(
                "Description",
                (object,),
                {
                    "dataType": "ShapeFile",
                    "shapeType": "Point",
                    "spatialReference": type("SR", (object,), {"name": "WGS 1984"})(),
                },
            )()
            FAKE_ARCPY.ListFields = lambda value: [
                type("Field", (object,), {"name": "FID", "type": "OID"})(),
            ]
            FAKE_ARCPY.GetCount_management = lambda value: type(
                "Count", (object,), {"getOutput": lambda self, index: "0"}
            )()
            artifact_observation.arcmap_desktop_selection.capture_oids = lambda value: (
                _ for _ in ()
            ).throw(AssertionError("dataset paths have no ArcMap selection state"))

            observation = artifact_observation._observe(source, "feature_class")

            self.assertEqual(0, observation["feature_count"])
            self.assertIsNone(observation["selection_count"])
        finally:
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists
            FAKE_ARCPY.Describe = original_describe
            if original_fields is None:
                delattr(FAKE_ARCPY, "ListFields")
            else:
                FAKE_ARCPY.ListFields = original_fields
            if original_count is None:
                delattr(FAKE_ARCPY, "GetCount_management")
            else:
                FAKE_ARCPY.GetCount_management = original_count
            artifact_observation.arcmap_desktop_selection.capture_oids = original_capture

    def test_runtime_layer_cleanup_failure_is_reported(self):
        original_remove = FAKE_ARCPY.mapping.RemoveLayer
        try:
            FAKE_ARCPY.mapping.RemoveLayer = lambda data_frame, layer: (_ for _ in ()).throw(
                RuntimeError("cleanup failed"))
            with self.assertRaisesRegexp(RuntimeError, "Runtime teardown failed"):
                with execution_session.ExecutionSession() as session:
                    session.register_output("s1", r"D:\\out\\result.shp")
                    session.layer_for_output("s1", r"D:\\out\\result.shp")
        finally:
            FAKE_ARCPY.mapping.RemoveLayer = original_remove

    def test_observation_verifies_join_count_and_field_edits(self):
        layer = _Layer(r"D:\out\joined.shp")
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        original_describe = FAKE_ARCPY.Describe
        original_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_count = getattr(FAKE_ARCPY, "GetCount_management", None)
        try:
            FAKE_ARCPY.Exists = lambda value: value is layer
            FAKE_ARCPY.Describe = lambda value: type("Description", (object,), {"dataType": "FeatureClass", "shapeType": "Polygon", "spatialReference": type("SR", (object,), {"name": "WGS 1984"})(), "FIDSet": ""})()
            field_names = ["OBJECTID", "Join_Count", "TARGET_FID"]
            FAKE_ARCPY.ListFields = lambda value: [type("Field", (object,), {"name": name})() for name in field_names]
            FAKE_ARCPY.GetCount_management = lambda value: type("Count", (object,), {"getOutput": lambda self, index: "2"})()
            operation = {"capability_contract": {"outputs": {"kind": "feature_class", "geometry": {"rule": "inherit", "value": "target_layer"}, "fields": {"effect": "inherit_target_merge_join", "target": "target_layer", "static_fields": ["Join_Count"]}, "spatial_reference": {"rule": "inherit", "input": "target_layer"}, "cardinality": {"rule": "fixed", "value": "one_per_target_feature"}, "selection_state": "not_applicable", "map_publication": "none"}, "postconditions": [{"expectation": dict((name, {"ref": "outputs." + name}) for name in ("kind", "geometry", "fields", "spatial_reference", "cardinality", "selection_state", "map_publication"))}]}}
            snapshot = {"inputs": {"target_layer": {"fields": ["OBJECTID"], "geometry": "Polygon", "spatial_reference": "WGS 1984", "feature_count": 2}}}
            observed = artifact_observation.observe_and_verify(operation, {}, {"output": layer}, {}, {}, "none", snapshot)
            self.assertEqual(observed["contract"]["verdict"], "passed")
            bad_snapshot = {"inputs": {"target_layer": {"fields": ["OBJECTID"], "geometry": "Point", "spatial_reference": "WGS 1984", "feature_count": 3}}}
            self.assertRaises(artifact_observation.ArtifactVerificationError, artifact_observation.observe_and_verify, operation, {}, {"output": layer}, {}, {}, "none", bad_snapshot)
            count_only = {"capability_contract": {"outputs": dict(operation["capability_contract"]["outputs"], geometry={"rule": "not_applicable"}), "postconditions": operation["capability_contract"]["postconditions"]}}
            self.assertRaises(artifact_observation.ArtifactVerificationError, artifact_observation.observe_and_verify, count_only, {}, {"output": layer}, {}, {}, "none", {"inputs": {"target_layer": {"fields": ["OBJECTID"], "geometry": "Polygon", "spatial_reference": "WGS 1984", "feature_count": 3}}})
            add = {"capability_contract": {"outputs": {"kind": "none", "geometry": {"rule": "not_applicable"}, "fields": {"effect": "add_parameter_field", "target": "layer", "parameter_field": "field_name"}, "spatial_reference": {"rule": "not_applicable"}, "cardinality": {"rule": "fixed", "value": "in_place"}, "selection_state": "not_applicable", "map_publication": "none"}, "postconditions": [{"expectation": dict((name, {"ref": "outputs." + name}) for name in ("kind", "geometry", "fields", "spatial_reference", "cardinality", "selection_state", "map_publication"))}]}}
            observed = artifact_observation.observe_and_verify(add, {"field_name": "Join_Count", "layer": layer}, {}, {}, {}, "none", {"inputs": {"layer": {"fields": ["OBJECTID"], "feature_count": 2}}})
            self.assertEqual(observed["contract"]["verdict"], "passed")
            delete = {"capability_contract": {"outputs": dict(add["capability_contract"]["outputs"], fields={"effect": "delete_parameter_field", "target": "layer", "parameter_field": "field_name"}), "postconditions": add["capability_contract"]["postconditions"]}}
            field_names[:] = ["OBJECTID"]
            observed = artifact_observation.observe_and_verify(delete, {"field_name": "Join_Count", "layer": layer}, {}, {}, {}, "none", {"inputs": {"layer": {"fields": ["OBJECTID", "Join_Count"], "feature_count": 2}}})
            self.assertEqual(observed["contract"]["verdict"], "passed")
        finally:
            if original_exists is None: delattr(FAKE_ARCPY, "Exists")
            else: FAKE_ARCPY.Exists = original_exists
            if original_fields is None: delattr(FAKE_ARCPY, "ListFields")
            else: FAKE_ARCPY.ListFields = original_fields
            if original_count is None: delattr(FAKE_ARCPY, "GetCount_management")
            else: FAKE_ARCPY.GetCount_management = original_count
            FAKE_ARCPY.Describe = original_describe

    def test_csv_observation_proves_inherited_header_fields(self):
        folder = tempfile.mkdtemp(prefix="arcmap_csv_observation_")
        output = os.path.join(folder, "roads.csv")
        try:
            with open(output, "wb") as stream:
                stream.write(b"RID,CLASS\r\n1,A\r\n")
            names = ("kind", "geometry", "fields", "spatial_reference", "cardinality", "selection_state", "map_publication")
            operation = {"capability_contract": {
                "outputs": {
                    "kind": "file", "geometry": {"rule": "not_applicable"},
                    "fields": {"effect": "inherit_tabular_fields", "target": "layer", "static_fields": [], "parameter_field": "not_applicable"},
                    "spatial_reference": {"rule": "not_applicable"},
                    "cardinality": {"rule": "fixed", "value": "one"},
                    "selection_state": "not_applicable", "map_publication": "none",
                },
                "postconditions": [{"expectation": dict((name, {"ref": "outputs." + name}) for name in names)}],
            }}
            snapshot = {"inputs": {"layer": {
                "fields": ["RID", "Shape", "CLASS"],
                "field_types": {"RID": "OID", "Shape": "Geometry", "CLASS": "String"},
            }}}

            observed = artifact_observation.observe_and_verify(
                operation, {"layer": "roads"}, {"output": output}, {}, {}, "none", snapshot)

            self.assertEqual(["RID", "CLASS"], observed["fields"])
            self.assertEqual("passed", observed["contract"]["verdict"])
        finally:
            shutil.rmtree(folder)

    def test_failed_postcondition_stops_workflow_and_removes_only_registered_output(self):
        folder = tempfile.mkdtemp(prefix="arcmap_observation_")
        output = os.path.join(folder, "only_this.txt")
        untouched = os.path.join(folder, "untouched.txt")
        open(untouched, "w").close()
        original_load = workflow_executor._load_operations
        original_call = workflow_executor._call_executor
        calls = []
        try:
            contract = {"outputs": {"kind": "feature_class", "geometry": {"rule": "not_applicable"}, "fields": {"effect": "not_applicable"}, "spatial_reference": {"rule": "not_applicable"}, "cardinality": {"rule": "fixed", "value": "one"}, "selection_state": "not_applicable", "map_publication": "none"}, "postconditions": [{"expectation": {"kind": {"ref": "outputs.kind"}, "geometry": {"ref": "outputs.geometry"}, "fields": {"ref": "outputs.fields"}, "spatial_reference": {"ref": "outputs.spatial_reference"}, "cardinality": {"ref": "outputs.cardinality"}, "selection_state": {"ref": "outputs.selection_state"}, "map_publication": {"ref": "outputs.map_publication"}}}]}
            workflow_executor._load_operations = lambda: {"bad": {"executor": "bad", "parameters_schema": {}, "side_effects": "writes_data", "output_policy": {"type": "file"}, "capability_contract": contract}, "later": {"executor": "later", "parameters_schema": {}, "side_effects": "read_only", "output_policy": {}}}
            def call(executor, context, arguments, outputs):
                calls.append(executor)
                if executor == "bad": open(output, "w").close(); return {"output": output}
                return {"ok": True}
            workflow_executor._call_executor = call
            row = {"context_hash": context_reader.context_hash({"layers": [], "is_saved": True}), "workflow": {"summary": "failure", "steps": [{"id": "bad", "operation": "bad", "arguments": {}}, {"id": "later", "operation": "later", "arguments": {}}]}}
            self.assertRaises(workflow_executor.WorkflowExecutionError, workflow_executor.execute, row, {"layers": [], "is_saved": True})
            self.assertEqual(calls, ["bad"])
            self.assertFalse(os.path.exists(output))
            self.assertTrue(os.path.exists(untouched))
        finally:
            workflow_executor._load_operations = original_load
            workflow_executor._call_executor = original_call
            shutil.rmtree(folder)

    def test_changes_map_publication_uses_the_closed_capability_contract(self):
        operation = {
            "side_effects": "changes_map",
            "capability_contract": {"outputs": {"map_publication": "published"}},
        }

        self.assertEqual("published", workflow_executor._publication_state(operation, {"layer_path": "roads.shp"}))

    def test_selected_feature_cardinality_is_measured_from_the_input_selection(self):
        observation = {
            "feature_count": 4,
            "input_snapshot": {"inputs": {"layer": {"selection_count": 4}}},
        }

        check = artifact_observation._check(
            "cardinality", "selected_feature_count", observation, {}, None,
        )

        self.assertEqual("passed", check["verdict"])
        self.assertEqual({"output": 4, "selected_input": 4}, check["actual"])

    def test_idempotent_selection_is_an_applied_selection_not_a_failed_change(self):
        observation = {
            "selection_count": 5,
            "input_snapshot": {"inputs": {"layer": {"selection_count": 5}}},
        }

        check = artifact_observation._check(
            "selection_state", "applied", observation,
            {"selection_type": "SUBSET_SELECTION"}, None,
        )

        self.assertEqual("passed", check["verdict"])

    def test_one_per_input_cardinality_is_measured_from_the_input_layer(self):
        observation = {
            "feature_count": 5,
            "input_snapshot": {"inputs": {"input_layer": {"feature_count": 5}}},
        }

        check = artifact_observation._check(
            "cardinality", "one_per_input_feature", observation, {}, None,
        )

        self.assertEqual("passed", check["verdict"])

    def test_runtime_layer_creation_requires_exact_map_owned_layer(self):
        original_add = FAKE_ARCPY.mapping.AddLayer
        try:
            FAKE_ARCPY.mapping.AddLayer = lambda data_frame, layer, position: None
            with self.assertRaisesRegexp(RuntimeError, "exactly one session-owned layer"):
                with execution_session.ExecutionSession() as session:
                    session.register_output("raster", r"D:\\out\\surface.tif", "raster")
                    session.layer_for_output("raster", r"D:\\out\\surface.tif")
        finally:
            FAKE_ARCPY.mapping.AddLayer = original_add

    def test_teardown_removes_all_layers_after_a_remove_failure_and_restores_environment(self):
        removed = []
        original_remove = FAKE_ARCPY.mapping.RemoveLayer
        try:
            def remove(data_frame, layer):
                removed.append(layer)
                if len(removed) == 1:
                    raise RuntimeError("first remove failed")
                FAKE_ARCPY.mapping.layers.remove(layer)
            FAKE_ARCPY.mapping.RemoveLayer = remove
            session = execution_session.ExecutionSession()
            with self.assertRaisesRegexp(RuntimeError, "Runtime teardown failed"):
                with session:
                    session.register_output("one", r"D:\\out\\one.shp")
                    session.register_output("two", r"D:\\out\\two.shp")
                    session.layer_for_output("one", r"D:\\out\\one.shp")
                    session.layer_for_output("two", r"D:\\out\\two.shp")
            self.assertEqual(len(removed), 2)
            self.assertEqual(session._runtime_layers, {})
            self.assertTrue(FAKE_ARCPY.env.addOutputsToMap)
            self.assertIsNone(execution_session.current())
        finally:
            FAKE_ARCPY.mapping.RemoveLayer = original_remove

    def test_primary_exception_survives_multiple_layer_remove_failures(self):
        original_remove = FAKE_ARCPY.mapping.RemoveLayer
        try:
            FAKE_ARCPY.mapping.RemoveLayer = lambda data_frame, layer: (_ for _ in ()).throw(
                RuntimeError("remove " + layer.name))
            primary = RuntimeError("business failure")
            with self.assertRaisesRegexp(RuntimeError, "business failure") as raised:
                with execution_session.ExecutionSession() as session:
                    session.register_output("one", r"D:\\out\\one.shp")
                    session.register_output("two", r"D:\\out\\two.shp")
                    session.layer_for_output("one", r"D:\\out\\one.shp")
                    session.layer_for_output("two", r"D:\\out\\two.shp")
                    raise primary
            self.assertIs(raised.exception, primary)
            self.assertIn("one", primary.runtime_teardown_error)
            self.assertIn("two", primary.runtime_teardown_error)
            self.assertIn(u"runtime teardown", exception_text.exception_text(primary))
        finally:
            FAKE_ARCPY.mapping.RemoveLayer = original_remove

    def test_primary_exception_survives_environment_restore_failure(self):
        original_env = FAKE_ARCPY.env
        class _FailingEnv(object):
            def __init__(self): self.value = True; self.fail_restore = False
            @property
            def addOutputsToMap(self): return self.value
            @addOutputsToMap.setter
            def addOutputsToMap(self, value):
                if self.fail_restore: raise RuntimeError("restore failed")
                self.value = value
        env = _FailingEnv()
        FAKE_ARCPY.env = env
        try:
            primary = ValueError("business failure")
            with self.assertRaisesRegexp(ValueError, "business failure") as raised:
                with execution_session.ExecutionSession():
                    env.fail_restore = True
                    raise primary
            self.assertIs(raised.exception, primary)
            self.assertIn("restore addOutputsToMap", primary.runtime_teardown_error)
            self.assertIsNone(execution_session.current())
        finally:
            FAKE_ARCPY.env = original_env

    def test_environment_restore_failure_without_primary_clears_active_session(self):
        original_env = FAKE_ARCPY.env
        class _FailingEnv(object):
            def __init__(self): self.value = True; self.fail_restore = False
            @property
            def addOutputsToMap(self): return self.value
            @addOutputsToMap.setter
            def addOutputsToMap(self, value):
                if self.fail_restore: raise RuntimeError("restore failed")
                self.value = value
        env = _FailingEnv()
        FAKE_ARCPY.env = env
        try:
            with self.assertRaisesRegexp(RuntimeError, "restore addOutputsToMap"):
                with execution_session.ExecutionSession():
                    env.fail_restore = True
            self.assertIsNone(execution_session.current())
        finally:
            FAKE_ARCPY.env = original_env

    def test_execute_keeps_clip_output_selectable_and_exportable_by_from_step(self):
        """Public execution seam: clip -> select(from_step) -> export."""
        source = _Layer(u"D:\\data\\shelters.shp")
        boundary = _Layer(u"D:\\data\\service_area.shp")
        FAKE_ARCPY.mapping.layers = [source, boundary]
        copied = []
        materialized_outputs = []
        original_load = workflow_executor._load_operations
        original_call = workflow_executor._call_executor
        original_copy = getattr(FAKE_ARCPY, "CopyFeatures_management", None)
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        original_make_layer = FAKE_ARCPY.MakeFeatureLayer_management
        original_add_layer = FAKE_ARCPY.mapping.AddLayer
        original_describe = FAKE_ARCPY.Describe
        original_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_count = getattr(FAKE_ARCPY, "GetCount_management", None)
        original_select = FAKE_ARCPY.SelectLayerByAttribute_management
        original_compile = getattr(selection_ops.condition_utils, "compile_where", None)
        output_folder = tempfile.mkdtemp(prefix="arcmap_from_step_")
        runtime_state = {"layer": None, "valid": True}
        count_sources = []
        existing_paths = set([
            source.dataSource,
            boundary.dataSource,
            u"D:\\out\\shelters_in_service_area.shp",
        ])
        try:
            class _CopyResult(object):
                def __init__(self, output):
                    self.output = output

                def getOutput(self, index):
                    materialized_outputs.append(index)
                    return self.output

            def copy_features(layer, output):
                copied.append((layer, output))
                return _CopyResult(output)
            FAKE_ARCPY.CopyFeatures_management = copy_features
            FAKE_ARCPY.Exists = lambda value: (
                runtime_state["valid"] if value is runtime_state["layer"] else value in existing_paths
            )
            FAKE_ARCPY.MakeFeatureLayer_management = lambda *args: (_ for _ in ()).throw(
                RuntimeError("transient feature layers are forbidden"))
            def add_layer(data_frame, source_layer, position):
                original_add_layer(data_frame, source_layer, position)
                if position == "BOTTOM" and source_layer.name.startswith("geopilot_"):
                    runtime_state["layer"] = FAKE_ARCPY.mapping.layers[-1]
            FAKE_ARCPY.mapping.AddLayer = add_layer
            FAKE_ARCPY.Describe = lambda value: (
                original_describe(value) if isinstance(value, _Layer) else type(
                    "Description",
                    (object,),
                    {
                        "dataType": "FeatureClass", "shapeType": "Point",
                        "spatialReference": type("SR", (object,), {"name": "WGS 1984"})(),
                        "FIDSet": "", "OIDFieldName": "OBJECTID", "catalogPath": value,
                    },
                )()
            )
            FAKE_ARCPY.ListFields = lambda value: [
                type("Field", (object,), {"name": "OBJECTID", "type": "OID"})(),
            ]

            def get_count(value):
                count_sources.append(value)
                if value is runtime_state["layer"]:
                    runtime_state["valid"] = False
                return type("Count", (object,), {"getOutput": lambda self, index: "1"})()

            def select(value, selection_type, where_clause=None):
                if value is runtime_state["layer"] and not runtime_state["valid"]:
                    raise IOError(u"runtime layer no longer exists")
                return original_select(value, selection_type, where_clause)

            FAKE_ARCPY.GetCount_management = get_count
            FAKE_ARCPY.SelectLayerByAttribute_management = select
            selection_ops.condition_utils.compile_where = lambda layer, where: "OBJECTID IN (1)"
            operations = {
                "analysis.clip": {"executor": "clip", "parameters_schema": {}, "side_effects": "writes_data", "output_policy": {"type": "feature_class", "add_to_map": True}},
                "selection.select_by_attribute": {"executor": "select", "parameters_schema": {"type": "object", "properties": {"layer": {"type": "string", "x-geopilot-kind": "layer"}}}, "side_effects": "read_only", "output_policy": {}},
                "selection.export_selected_features": {"executor": "export", "parameters_schema": {"type": "object", "properties": {"layer": {"type": "string", "x-geopilot-kind": "layer"}}}, "side_effects": "writes_data", "output_policy": {"type": "feature_class", "add_to_map": True}},
            }
            workflow_executor._load_operations = lambda: operations
            def call(executor, context, arguments, outputs):
                if executor == "clip":
                    return {"output": u"D:\\out\\shelters_in_service_area.shp"}
                if executor == "select":
                    return selection_ops.select_by_attribute(context, arguments, outputs)
                return selection_ops.export_selected_features(context, arguments, outputs)
            workflow_executor._call_executor = call
            context = {"layers": [
                {"layer_ref": "shelters", "name": source.name, "longName": source.longName, "dataSource": source.dataSource},
                {"layer_ref": "service_area", "name": boundary.name, "longName": boundary.longName, "dataSource": boundary.dataSource},
            ], "is_saved": True, "document_path": u"D:\\map.mxd"}
            workflow = {"summary": "chain", "steps": [
                {"id": "clip", "operation": "analysis.clip", "arguments": {}},
                {"id": "select", "operation": "selection.select_by_attribute", "arguments": {"layer": "from_step:clip", "where": {}}},
                {"id": "export", "operation": "selection.export_selected_features", "arguments": {"layer": "from_step:clip", "output_name": "selected", "output_folder": output_folder, "output_format": "shp"}},
            ]}
            result = workflow_executor.execute({"workflow": workflow, "context_hash": context_reader.context_hash(context)}, context)
            self.assertTrue(result.result["ok"])
            self.assertEqual(
                result.result["steps"][1]["result"]["layer"],
                "from_step:clip",
            )
            self.assertEqual(len(copied), 1)
            self.assertEqual([0], materialized_outputs)
            self.assertEqual(source._selection, set())
            self.assertTrue(runtime_state["valid"])
            self.assertTrue(count_sources)
            self.assertTrue(all(not isinstance(value, _Layer) for value in count_sources))
        finally:
            workflow_executor._load_operations = original_load
            workflow_executor._call_executor = original_call
            FAKE_ARCPY.MakeFeatureLayer_management = original_make_layer
            FAKE_ARCPY.mapping.AddLayer = original_add_layer
            FAKE_ARCPY.Describe = original_describe
            if original_fields is None:
                delattr(FAKE_ARCPY, "ListFields")
            else:
                FAKE_ARCPY.ListFields = original_fields
            if original_count is None:
                delattr(FAKE_ARCPY, "GetCount_management")
            else:
                FAKE_ARCPY.GetCount_management = original_count

            FAKE_ARCPY.SelectLayerByAttribute_management = original_select
            if original_compile is None:
                delattr(selection_ops.condition_utils, "compile_where")
            else:
                selection_ops.condition_utils.compile_where = original_compile
            if original_copy is None:
                delattr(FAKE_ARCPY, "CopyFeatures_management")
            else:
                FAKE_ARCPY.CopyFeatures_management = original_copy
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists
            shutil.rmtree(output_folder)

    def test_observation_merges_fields_from_explicit_distinct_input_sources(self):
        output = _Layer(r"D:\out\roads_identity.shp")
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        original_describe = FAKE_ARCPY.Describe
        original_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_count = getattr(FAKE_ARCPY, "GetCount_management", None)
        try:
            FAKE_ARCPY.Exists = lambda value: value is output
            FAKE_ARCPY.Describe = lambda value: type(
                "Description", (object,), {
                    "dataType": "FeatureClass",
                    "shapeType": "Polyline",
                    "spatialReference": type("SR", (object,), {"name": "EPSG:3857"})(),
                    "FIDSet": "",
                },
            )()
            FAKE_ARCPY.ListFields = lambda value: [
                type("Field", (object,), {"name": name, "type": "Integer"})()
                for name in ("RID", "ZID")
            ]
            FAKE_ARCPY.GetCount_management = lambda value: type(
                "Count", (object,), {"getOutput": lambda self, index: "1"},
            )()
            operation = {"capability_contract": {
                "outputs": {
                    "kind": "feature_class",
                    "geometry": {"rule": "fixed", "value": "polyline"},
                    "fields": {
                        "effect": "merge_inputs",
                        "sources": ["input_layer", "identity_layer"],
                        "static_fields": [],
                        "parameter_field": "not_applicable",
                    },
                    "cardinality": {"rule": "fixed", "value": "one"},
                },
                "postconditions": [{
                    "expectation": {
                        "fields": {"ref": "outputs.fields"},
                        "cardinality": {"ref": "outputs.cardinality"},
                    },
                }],
            }}
            snapshot = {"inputs": {
                "input_layer": {"fields": ["RID"]},
                "identity_layer": {"fields": ["ZID"]},
            }}

            observed = artifact_observation.observe_and_verify(
                operation, {}, {"output": output}, {}, {}, "none", snapshot,
            )

            self.assertEqual("passed", observed["contract"]["verdict"])
        finally:
            FAKE_ARCPY.Describe = original_describe
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists
            if original_fields is None:
                delattr(FAKE_ARCPY, "ListFields")
            else:
                FAKE_ARCPY.ListFields = original_fields
            if original_count is None:
                delattr(FAKE_ARCPY, "GetCount_management")
            else:
                FAKE_ARCPY.GetCount_management = original_count

    def test_execute_observes_each_layer_in_a_many_input_operation(self):
        """Public execution seam: a real multi-layer argument remains executable and observable."""
        left = _Layer(u"D:\\data\\suspect_projects.shp")
        right = _Layer(u"D:\\data\\protected.shp")
        FAKE_ARCPY.mapping.layers = [left, right]
        original_load = workflow_executor._load_operations
        original_call = workflow_executor._call_executor
        original_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_count = getattr(FAKE_ARCPY, "GetCount_management", None)
        try:
            FAKE_ARCPY.ListFields = lambda layer: []
            FAKE_ARCPY.GetCount_management = lambda layer: type(
                "CountResult", (object,), {"getOutput": lambda self, index: "3"}
            )()
            workflow_executor._load_operations = lambda: {
                "analysis.intersect": {
                    "executor": "intersect",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {
                            "input_layers": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 2,
                            }
                        },
                    },
                    "side_effects": "writes_data",
                    "output_policy": {"type": "feature_class", "add_to_map": False},
                    "capability_contract": {
                        "inputs": [{"parameter": "input_layers", "cardinality": "many"}],
                        "postconditions": [],
                    },
                }
            }
            workflow_executor._call_executor = lambda executor, context, arguments, outputs: {"ok": True}
            context = {
                "layers": [
                    {"layer_ref": "layer:0", "name": left.name, "longName": left.longName, "dataSource": left.dataSource},
                    {"layer_ref": "layer:1", "name": right.name, "longName": right.longName, "dataSource": right.dataSource},
                ],
                "is_saved": True,
            }
            row = {
                "context_hash": context_reader.context_hash(context),
                "workflow": {
                    "summary": "intersect two layers",
                    "steps": [{
                        "id": "intersect",
                        "operation": "analysis.intersect",
                        "arguments": {"input_layers": ["layer:0", "layer:1"]},
                    }],
                },
            }

            outcome = workflow_executor.execute(row, context)

            observed = outcome.result["steps"][0]["result"]["input_snapshot"]["inputs"]["input_layers"]
            self.assertEqual(2, len(observed))
            self.assertEqual([left.dataSource, right.dataSource], [item["path"] for item in observed])
        finally:
            workflow_executor._load_operations = original_load
            workflow_executor._call_executor = original_call
            if original_fields is None:
                delattr(FAKE_ARCPY, "ListFields")
            else:
                FAKE_ARCPY.ListFields = original_fields
            if original_count is None:
                delattr(FAKE_ARCPY, "GetCount_management")
            else:
                FAKE_ARCPY.GetCount_management = original_count

    def test_execute_verifies_many_input_contract_against_all_observed_layers(self):
        """Public execution seam: multi-input postconditions use every declared input."""
        left = _Layer(u"D:\\data\\suspect_projects.shp")
        right = _Layer(u"D:\\data\\protected.shp")
        output = u"D:\\out\\protected_conflicts.shp"
        FAKE_ARCPY.mapping.layers = [left, right]
        metadata = {
            left.dataSource: {"geometry": "Polygon", "fields": ["PROJECT_ID"], "count": 3},
            right.dataSource: {"geometry": "Polyline", "fields": ["ROAD_ID"], "count": 2},
            output: {"geometry": "Polyline", "fields": ["PROJECT_ID", "ROAD_ID"], "count": 1},
        }
        original_load = workflow_executor._load_operations
        original_call = workflow_executor._call_executor
        original_describe = FAKE_ARCPY.Describe
        original_exists = getattr(FAKE_ARCPY, "Exists", None)
        original_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_count = getattr(FAKE_ARCPY, "GetCount_management", None)
        try:
            def path_of(value):
                return getattr(value, "dataSource", value)
            def describe(value):
                path = path_of(value)
                item = metadata[path]
                return type("Description", (object,), {
                    "FIDSet": "",
                    "OIDFieldName": "OBJECTID",
                    "catalogPath": path,
                    "dataType": "FeatureClass",
                    "shapeType": item["geometry"],
                    "spatialReference": type("SpatialReference", (object,), {"name": "EPSG:32650"})(),
                })()
            FAKE_ARCPY.Describe = describe
            FAKE_ARCPY.Exists = lambda value: path_of(value) in metadata
            FAKE_ARCPY.ListFields = lambda value: [
                type("Field", (object,), {"name": name, "type": "Integer"})()
                for name in metadata[path_of(value)]["fields"]
            ]
            FAKE_ARCPY.GetCount_management = lambda value: type(
                "CountResult", (object,), {
                    "getOutput": lambda self, index: str(metadata[path_of(value)]["count"])
                }
            )()
            workflow_executor._load_operations = lambda: {
                "analysis.intersect": {
                    "executor": "intersect",
                    "parameters_schema": {
                        "type": "object",
                        "properties": {
                            "input_layers": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                            "output_name": {"type": "string"},
                        },
                    },
                    "side_effects": "writes_data",
                    "output_policy": {"type": "feature_class", "add_to_map": False},
                    "capability_contract": {
                        "inputs": [{"parameter": "input_layers", "cardinality": "many"}],
                        "outputs": {
                            "kind": "feature_class",
                            "geometry": {"rule": "lowest_dimension", "value": "input_layers"},
                            "fields": {
                                "effect": "merge_inputs",
                                "sources": ["input_layers"],
                                "static_fields": [],
                                "parameter_field": "not_applicable",
                            },
                            "spatial_reference": {"rule": "inherit", "input": "input_layers"},
                            "cardinality": {
                                "rule": "fixed",
                                "value": "one_or_more_per_input_feature",
                            },
                        },
                        "postconditions": [{
                            "kind": "feature_class_created",
                            "target": "output_name",
                            "expectation": {
                                "geometry": {"ref": "outputs.geometry"},
                                "fields": {"ref": "outputs.fields"},
                                "spatial_reference": {"ref": "outputs.spatial_reference"},
                                "cardinality": {"ref": "outputs.cardinality"},
                            },
                        }],
                    },
                }
            }
            workflow_executor._call_executor = lambda executor, context, arguments, outputs: {"output": output}
            context = {
                "layers": [
                    {"layer_ref": "layer:0", "name": left.name, "longName": left.longName, "dataSource": left.dataSource},
                    {"layer_ref": "layer:1", "name": right.name, "longName": right.longName, "dataSource": right.dataSource},
                ],
                "is_saved": True,
            }
            row = {
                "context_hash": context_reader.context_hash(context),
                "execution_contract": {
                    "schema": "geopilot-execution-contract/v1",
                    "workflow_hash": "workflow-hash",
                    "context_hash": "context-hash",
                    "capability_hash": "capability-hash",
                    "contract_hash": "contract-hash",
                    "cardinality_proofs": [{
                        "proof_id": "execution:intersect:analysis.intersect:outputs.cardinality",
                        "proof_kind": "validated_capability_output",
                        "step_id": "intersect",
                        "capability_id": "analysis.intersect",
                        "contract_path": "outputs.cardinality",
                        "expected": "one_or_more_per_input_feature",
                    }],
                },
                "workflow": {
                    "summary": "intersect polygon and polyline layers",
                    "steps": [{
                        "id": "intersect",
                        "operation": "analysis.intersect",
                        "arguments": {"input_layers": ["layer:0", "layer:1"], "output_name": "protected_conflicts"},
                    }],
                },
            }

            outcome = workflow_executor.execute(row, context)

            checks = dict(
                (item["name"], item)
                for item in outcome.result["steps"][0]["result"]["observation"]["contract"]["checks"]
            )
            self.assertEqual("passed", checks["geometry"]["verdict"])
            self.assertEqual("passed", checks["spatial_reference"]["verdict"])
            self.assertEqual("passed", checks["fields"]["verdict"])
            self.assertEqual(["PROJECT_ID", "ROAD_ID"], checks["fields"]["actual"]["required"])
            self.assertEqual("symbolically_verified", checks["cardinality"]["verdict"])
            self.assertEqual("one_or_more_per_input_feature", checks["cardinality"]["proof"]["expected"])

            metadata[output]["geometry"] = "Polygon"
            with self.assertRaises(workflow_executor.WorkflowExecutionError) as caught:
                workflow_executor.execute(row, context)
            self.assertIn(u"expectation.geometry", unicode(caught.exception))
            metadata[output]["geometry"] = "Polyline"

            row["execution_contract"]["cardinality_proofs"][0]["expected"] = "one"
            with self.assertRaises(workflow_executor.WorkflowExecutionError) as caught:
                workflow_executor.execute(row, context)
            self.assertIn(u"expectation.cardinality", unicode(caught.exception))
        finally:
            workflow_executor._load_operations = original_load
            workflow_executor._call_executor = original_call
            FAKE_ARCPY.Describe = original_describe
            if original_exists is None:
                delattr(FAKE_ARCPY, "Exists")
            else:
                FAKE_ARCPY.Exists = original_exists
            if original_fields is None:
                delattr(FAKE_ARCPY, "ListFields")
            else:
                FAKE_ARCPY.ListFields = original_fields
            if original_count is None:
                delattr(FAKE_ARCPY, "GetCount_management")
            else:
                FAKE_ARCPY.GetCount_management = original_count

    def test_select_by_attribute_compiles_same_layer_field_comparison(self):
        layer = _Layer(u"D:\\data\\parcels.shp")
        FAKE_ARCPY.mapping.layers = [layer]
        original_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_describe = FAKE_ARCPY.Describe
        original_select = FAKE_ARCPY.SelectLayerByAttribute_management
        clauses = []
        try:
            FAKE_ARCPY.ListFields = lambda value: [
                type("Field", (object,), {"name": "PLAN_USE", "type": "String"})(),
                type("Field", (object,), {"name": "ACT_USE", "type": "String"})(),
            ]
            FAKE_ARCPY.Describe = lambda value: type(
                "Description",
                (object,),
                {
                    "FIDSet": "",
                    "OIDFieldName": "OBJECTID",
                    "catalogPath": layer.catalogPath,
                    "path": u"D:\\data",
                },
            )()
            FAKE_ARCPY.SelectLayerByAttribute_management = lambda value, selection_type, where: clauses.append(
                (value, selection_type, where)
            )
            context = {
                "layers": [
                    {
                        "layer_ref": "layer:parcels",
                        "name": "parcels",
                        "longName": "parcels",
                        "dataSource": layer.dataSource,
                    }
                ],
                "is_saved": True,
            }

            result = selection_ops.select_by_attribute(
                context,
                {
                    "layer": "layer:parcels",
                    "where": {
                        "field": "PLAN_USE",
                        "op": "ne",
                        "value_field": "ACT_USE",
                    },
                },
                {},
            )

            self.assertEqual(result["selection_type"], "NEW_SELECTION")
            self.assertEqual(clauses[0][2], "PLAN_USE <> ACT_USE")
        finally:
            if original_fields is None:
                delattr(FAKE_ARCPY, "ListFields")
            else:
                FAKE_ARCPY.ListFields = original_fields
            FAKE_ARCPY.Describe = original_describe
            FAKE_ARCPY.SelectLayerByAttribute_management = original_select

    def test_execute_resolves_raster_from_step_with_raster_layer_handle(self):
        calls = []
        original_load = workflow_executor._load_operations
        original_call = workflow_executor._call_executor
        original_feature = FAKE_ARCPY.MakeFeatureLayer_management
        original_raster = FAKE_ARCPY.MakeRasterLayer_management
        original_layer = FAKE_ARCPY.mapping.Layer
        try:
            FAKE_ARCPY.MakeFeatureLayer_management = lambda *args: (_ for _ in ()).throw(RuntimeError("wrong maker"))
            FAKE_ARCPY.MakeRasterLayer_management = lambda *args: (_ for _ in ()).throw(RuntimeError("wrong maker"))
            FAKE_ARCPY.mapping.Layer = lambda path: (calls.append(path) or _Layer(path))
            workflow_executor._load_operations = lambda: {
                "make.raster": {"executor": "make", "parameters_schema": {}, "side_effects": "writes_data", "output_policy": {"type": "raster", "add_to_map": True}},
                "use.raster": {"executor": "use", "parameters_schema": {}, "side_effects": "read_only", "output_policy": {}},
            }
            def call(executor, context, arguments, outputs):
                if executor == "make": return {"output": r"D:\\out\\surface.tif"}
                layer = runtime_common.find_layer(context, arguments["layer"], outputs)
                self.assertEqual(layer.dataSource, r"D:\\out\\surface.tif")
                return {"ok": True}
            workflow_executor._call_executor = call
            context = {"layers": [], "is_saved": True}
            row = {"context_hash": context_reader.context_hash(context), "workflow": {"summary": "raster chain", "steps": [
                {"id": "make", "operation": "make.raster", "arguments": {}},
                {"id": "use", "operation": "use.raster", "arguments": {"layer": "from_step:make"}},
            ]}}
            self.assertTrue(workflow_executor.execute(row, context).result["ok"])
            self.assertEqual(calls, [r"D:\\out\\surface.tif"])
        finally:
            workflow_executor._load_operations = original_load
            workflow_executor._call_executor = original_call
            FAKE_ARCPY.MakeFeatureLayer_management = original_feature
            FAKE_ARCPY.MakeRasterLayer_management = original_raster
            FAKE_ARCPY.mapping.Layer = original_layer

    def test_exception_text_preserves_unicode_utf8_bytes_and_selection_cause(self):
        class _Unprintable(object):
            def __unicode__(self):
                raise UnicodeEncodeError("ascii", u"学校", 0, 1, "boom")

            def __str__(self):
                raise UnicodeEncodeError("ascii", u"学校", 0, 1, "boom")

        rendered = exception_text.exception_text(
            RuntimeError(u"选择集失败", u"学校".encode("utf-8"), _Unprintable()))
        self.assertIn(u"RuntimeError:", rendered)
        self.assertIn(u"选择集失败", rendered)
        self.assertIn(u"学校", rendered)
        self.assertIn(u"<unprintable _Unprintable>", rendered)
        cause = RuntimeError(u"ArcMap Desktop feature layer does not expose FIDSet.")
        wrapped = workflow_executor.WorkflowExecutionError(
            u"步骤 export_final_sites_csv 执行失败：%s" % exception_text.exception_text(cause))
        self.assertIn(u"FIDSet", workflow_executor._exception_text(wrapped))

    def test_from_step_detached_layer_exports_csv_from_registered_unicode_path(self):
        output_folder = tempfile.mkdtemp(prefix="arcmap_csv_repro_")
        temp_layers = {}
        make_sources = []
        field_sources = []
        cursor_sources = []
        detached_layer = None
        original_describe = FAKE_ARCPY.Describe
        original_list_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_make_layer = getattr(FAKE_ARCPY, "MakeFeatureLayer_management", None)
        original_select = FAKE_ARCPY.SelectLayerByAttribute_management
        original_delete = getattr(FAKE_ARCPY, "Delete_management", None)
        original_da = getattr(FAKE_ARCPY, "da", None)

        class _Cursor(object):
            def __init__(self, source, fields):
                cursor_sources.append(source)
                self.rows = [(u"实验学校",)]

            def __enter__(self):
                return iter(self.rows)

            def __exit__(self, exc_type, exc, tb):
                return False

        def describe(value):
            if isinstance(value, basestring) and value in temp_layers:
                source = temp_layers[value]
                return type("TemporaryDescription", (object,), {
                    "FIDSet": None if source is detached_layer else "",
                    "OIDFieldName": "OBJECTID",
                    "catalogPath": source if isinstance(source, unicode) else source.dataSource,
                })()
            return original_describe(value)

        def make_feature_layer(source, name, where_clause=None):
            temp_layers[name] = source
            make_sources.append(source)
            return type("FeatureLayerResult", (object,), {
                "getOutput": lambda self, index: _Layer(source)
            })()

        def list_fields(layer):
            field_sources.append(layer)
            if not (isinstance(layer, basestring) and layer in temp_layers):
                raise IOError(u"“final_sites”不存在")
            return [type("Field", (object,), {"name": u"学校名称", "type": "String"})()]

        def select(layer, selection_type, where_clause=None):
            source_layer = temp_layers.get(layer, layer)
            if isinstance(source_layer, basestring):
                return None
            return original_select(source_layer, selection_type, where_clause)

        try:
            FAKE_ARCPY.Describe = describe
            FAKE_ARCPY.ListFields = list_fields
            FAKE_ARCPY.MakeFeatureLayer_management = make_feature_layer
            FAKE_ARCPY.SelectLayerByAttribute_management = select
            FAKE_ARCPY.Delete_management = lambda layer: temp_layers.pop(layer, None)
            FAKE_ARCPY.da = type("DataAccess", (object,), {"SearchCursor": _Cursor})()
            source = u"D:\\成果\\final_sites.shp"
            live_layer = _Layer(source)
            runtime_common.export_table_to_csv(live_layer, os.path.join(output_folder, "live.csv"), False)
            with execution_session.ExecutionSession() as session:
                session.register_output("export_final_sites", source)
                detached_layer = session.layer_for_output("export_final_sites", source)
                result = export_ops.export_table_csv(
                    {},
                    {"layer": "from_step:export_final_sites", "output_name": "final_sites", "output_folder": output_folder},
                    {"export_final_sites": {"output": source}})
            self.assertTrue(os.path.exists(result["output"]))
            self.assertIs(make_sources[0], live_layer)
            self.assertEqual(make_sources[1], source)
            self.assertTrue(isinstance(make_sources[1], unicode))
            self.assertEqual(field_sources, cursor_sources)
            self.assertEqual(len(field_sources), 2)
        finally:
            FAKE_ARCPY.Describe = original_describe
            if original_list_fields is None:
                delattr(FAKE_ARCPY, "ListFields")
            else:
                FAKE_ARCPY.ListFields = original_list_fields
            if original_make_layer is None:
                delattr(FAKE_ARCPY, "MakeFeatureLayer_management")
            else:
                FAKE_ARCPY.MakeFeatureLayer_management = original_make_layer
            FAKE_ARCPY.SelectLayerByAttribute_management = original_select
            if original_delete is None:
                delattr(FAKE_ARCPY, "Delete_management")
            else:
                FAKE_ARCPY.Delete_management = original_delete
            if original_da is None:
                delattr(FAKE_ARCPY, "da")
            else:
                FAKE_ARCPY.da = original_da
            shutil.rmtree(output_folder)

    def test_csv_cursor_failure_leaves_no_final_or_temporary_file(self):
        output_folder = tempfile.mkdtemp(prefix="arcmap_csv_atomic_")
        target = os.path.join(output_folder, "final.csv")
        temporary_layers = {}
        original_describe = FAKE_ARCPY.Describe
        original_list_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_make_layer = getattr(FAKE_ARCPY, "MakeFeatureLayer_management", None)
        original_select = FAKE_ARCPY.SelectLayerByAttribute_management
        original_delete = getattr(FAKE_ARCPY, "Delete_management", None)
        original_da = getattr(FAKE_ARCPY, "da", None)

        class _FailingCursor(object):
            def __init__(self, source, fields):
                self.rows = iter([(u"first",)])

            def __enter__(self):
                return self

            def __iter__(self):
                return self

            def next(self):
                try:
                    return self.rows.next()
                except StopIteration:
                    raise RuntimeError("cursor failed")

            __next__ = next

            def __exit__(self, *args):
                return False

        try:
            FAKE_ARCPY.MakeFeatureLayer_management = lambda source, name, where=None: temporary_layers.update({name: source})
            FAKE_ARCPY.Describe = lambda value: type("Description", (object,), {"FIDSet": "", "OIDFieldName": "OBJECTID", "catalogPath": u"D:\\data.shp"})()
            FAKE_ARCPY.ListFields = lambda value: [type("Field", (object,), {"name": u"名称", "type": "String"})()]
            FAKE_ARCPY.SelectLayerByAttribute_management = lambda *args: None
            FAKE_ARCPY.Delete_management = lambda value: temporary_layers.pop(value, None)
            FAKE_ARCPY.da = type("DataAccess", (object,), {"SearchCursor": _FailingCursor})()
            self.assertRaises(RuntimeError, runtime_common.export_table_to_csv, _Layer(u"D:\\data.shp"), target, False)
            self.assertFalse(os.path.exists(target))
            self.assertEqual(os.listdir(output_folder), [])
        finally:
            FAKE_ARCPY.Describe = original_describe
            if original_list_fields is None:
                delattr(FAKE_ARCPY, "ListFields")
            else:
                FAKE_ARCPY.ListFields = original_list_fields
            if original_make_layer is None:
                delattr(FAKE_ARCPY, "MakeFeatureLayer_management")
            else:
                FAKE_ARCPY.MakeFeatureLayer_management = original_make_layer
            FAKE_ARCPY.SelectLayerByAttribute_management = original_select
            if original_delete is None:
                delattr(FAKE_ARCPY, "Delete_management")
            else:
                FAKE_ARCPY.Delete_management = original_delete
            if original_da is None:
                delattr(FAKE_ARCPY, "da")
            else:
                FAKE_ARCPY.da = original_da
            shutil.rmtree(output_folder)


if __name__ == "__main__":
    unittest.main()
