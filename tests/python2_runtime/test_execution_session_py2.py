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
        self.layers.append(_Layer(layer.dataSource))


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


def _field_delimiters(data_source, field):
    if not isinstance(data_source, basestring) or not data_source:
        raise RuntimeError("data source must be a non-empty string")
    FAKE_ARCPY.delimiter_calls.append((data_source, field))
    return field


FAKE_ARCPY.AddFieldDelimiters = _field_delimiters
FAKE_ARCPY.SelectLayerByAttribute_management = _select
PY2 = sys.version_info[0] == 2
if PY2:
    sys.modules["arcpy"] = FAKE_ARCPY
    from arcmap_runtime_py2 import execution_session
    from arcmap_runtime_py2 import exception_text
    from arcmap_runtime_py2 import output_publisher
    from arcmap_runtime_py2 import workflow_executor
    from arcmap_runtime_py2 import arcmap_desktop_selection
    from arcmap_runtime_py2.operations import common as runtime_common

    _OPERATIONS = types.ModuleType("operations")
    _OPERATIONS.common = runtime_common
    _OPERATIONS.condition_utils = types.ModuleType("condition_utils")
    sys.modules["operations"] = _OPERATIONS
    sys.modules["operations.common"] = runtime_common
    sys.modules["operations.condition_utils"] = _OPERATIONS.condition_utils
    from arcmap_runtime_py2.operations import export_ops


@unittest.skipUnless(PY2, "ArcMap Python 2.7 runtime test")
class ExecutionSessionPython27Tests(unittest.TestCase):
    def setUp(self):
        FAKE_ARCPY.mapping.layers = []
        FAKE_ARCPY.env.addOutputsToMap = True
        FAKE_ARCPY.delimiter_calls = []
        FAKE_ARCPY.selection_calls = []
        FAKE_ARCPY.SelectLayerByAttribute_management = _select

    def test_nonempty_spaced_fid_set_is_captured_and_restored_on_live_layer(self):
        with execution_session.ExecutionSession() as session:
            session.register_output("s1", r"D:\out\intermediate.shp")
            session.register_output("s2", r"D:\out\final.shp")
            layer = session.layer_for_output("s1", r"D:\out\intermediate.shp")
            layer.visible = False
            layer._selection = set([3, 1])
            paths = session.publication_plan().paths
            records = session.publication_plan().records
            self.assertEqual(FAKE_ARCPY.mapping.layers, [])
            self.assertFalse(FAKE_ARCPY.env.addOutputsToMap)

        self.assertEqual(paths, [r"D:\out\intermediate.shp", r"D:\out\final.shp"])
        self.assertTrue(FAKE_ARCPY.env.addOutputsToMap)
        output_publisher.publish(execution_session.PublicationPlan.from_records(records))
        self.assertEqual([item.dataSource for item in FAKE_ARCPY.mapping.layers], paths)
        self.assertFalse(FAKE_ARCPY.mapping.layers[0].visible)
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
        detached_layer = None
        original_describe = FAKE_ARCPY.Describe
        original_list_fields = getattr(FAKE_ARCPY, "ListFields", None)
        original_make_layer = getattr(FAKE_ARCPY, "MakeFeatureLayer_management", None)
        original_select = FAKE_ARCPY.SelectLayerByAttribute_management
        original_delete = getattr(FAKE_ARCPY, "Delete_management", None)
        original_da = getattr(FAKE_ARCPY, "da", None)

        class _Cursor(object):
            def __init__(self, source, fields):
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

        def select(layer, selection_type, where_clause=None):
            source_layer = temp_layers.get(layer, layer)
            if isinstance(source_layer, basestring):
                return None
            return original_select(source_layer, selection_type, where_clause)

        try:
            FAKE_ARCPY.Describe = describe
            FAKE_ARCPY.ListFields = lambda layer: [
                type("Field", (object,), {"name": u"学校名称", "type": "String"})()]
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
