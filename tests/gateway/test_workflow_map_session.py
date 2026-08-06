import sys
import types
import unittest
import re
from types import SimpleNamespace
from unittest.mock import patch


sys.modules.setdefault("arcpy", types.SimpleNamespace(ExecuteError=RuntimeError))

from arcmap_runtime_py2 import workflow_executor
from arcmap_runtime_py2 import execution_session
from arcmap_runtime_py2 import map_exporter
from arcmap_runtime_py2 import output_publisher
from arcmap_runtime_py2 import arcmap_desktop_selection
from arcmap_runtime_py2 import context_reader
from arcmap_runtime_py2.operations import common
from arcmap_runtime_py2.operations import selection_ops


class _Layer:
    def __init__(self, path):
        self.name = path
        self.longName = path
        self.dataSource = path
        self.visible = True
        self.isFeatureLayer = True
        self.selection = set()
        self.catalogPath = path

    def supports(self, capability):
        return capability == "DATASOURCE"


class _Mapping:
    def __init__(self):
        self.layers = []
        self.added = []
        self.added_layer_names = []

    def MapDocument(self, value):
        return "mxd"

    def ListDataFrames(self, mxd):
        return ["df"]

    def ListLayers(self, mxd, wildcard, data_frame):
        return list(self.layers)

    def Layer(self, path):
        return _Layer(path)

    def AddLayer(self, data_frame, layer, position):
        added = _Layer(layer.dataSource)
        added.name = layer.name
        added.longName = layer.longName
        added.visible = layer.visible
        added.isFeatureLayer = layer.isFeatureLayer
        added.selection = set(layer.selection)
        self.layers.append(added)
        self.added.append((data_frame, layer.dataSource, position))
        self.added_layer_names.append(layer.name)

    def RemoveLayer(self, data_frame, layer):
        self.layers.remove(layer)


class ExecutionSessionTests(unittest.TestCase):
    def setUp(self):
        self.mapping = _Mapping()
        self.arcpy = SimpleNamespace(
            ExecuteError=RuntimeError,
            env=SimpleNamespace(addOutputsToMap=True),
            mapping=self.mapping,
            Describe=self._describe,
            AddFieldDelimiters=self._field_delimiters,
            SelectLayerByAttribute_management=self._select,
            ListFields=lambda layer: [],
        )
        self.arcpy.MakeFeatureLayer_management = self._make_feature_layer
        self.arcpy.Delete_management = lambda name: None
        self.delimiter_calls = []
        self.arcpy_patch = patch.object(common, "arcpy", self.arcpy)
        self.arcpy_patch.start()
        self.session_arcpy_patch = patch.object(execution_session, "arcpy", self.arcpy)
        self.session_arcpy_patch.start()
        self.publisher_arcpy_patch = patch.object(output_publisher, "arcpy", self.arcpy)
        self.publisher_arcpy_patch.start()
        self.selection_arcpy_patch = patch.object(arcmap_desktop_selection, "arcpy", self.arcpy)
        self.selection_arcpy_patch.start()
        self.context_arcpy_patch = patch.object(context_reader, "arcpy", self.arcpy)
        self.context_arcpy_patch.start()
        self.exporter_arcpy_patch = patch.object(map_exporter, "arcpy", self.arcpy)
        self.exporter_arcpy_patch.start()

    def tearDown(self):
        self.exporter_arcpy_patch.stop()
        self.publisher_arcpy_patch.stop()
        self.context_arcpy_patch.stop()
        self.selection_arcpy_patch.stop()
        self.session_arcpy_patch.stop()
        self.arcpy_patch.stop()

    @staticmethod
    def _describe(layer):
        return SimpleNamespace(
            FIDSet="; ".join(str(oid) for oid in sorted(layer.selection)),
            OIDFieldName="OBJECTID",
            catalogPath=layer.catalogPath,
        )

    @staticmethod
    def _make_feature_layer(path, name):
        layer = _Layer(path)
        layer.name = name
        layer.longName = name
        return SimpleNamespace(getOutput=lambda index: layer)

    def _field_delimiters(self, data_source, field):
        if not isinstance(data_source, str) or not data_source:
            raise RuntimeError("data source must be a non-empty string")
        self.delimiter_calls.append((data_source, field))
        return field

    @staticmethod
    def _select(layer, selection_type, where_clause=None):
        if selection_type == "CLEAR_SELECTION":
            layer.selection = set()
            return
        values = set(int(value) for value in re.search(r"\((.*)\)", where_clause).group(1).split(","))
        if selection_type == "NEW_SELECTION":
            layer.selection = values
        elif selection_type == "ADD_TO_SELECTION":
            layer.selection.update(values)
        else:
            raise RuntimeError("unexpected selection type: %s" % selection_type)

    def test_outputs_are_resolved_without_map_publication_and_published_after_execution(self):
        output = r"D:\out\result.shp"
        step_outputs = {"s1": {"output": output}}

        with execution_session.ExecutionSession() as session:
            session.register_output("s1", output)
            first = common.find_layer({}, "from_step:s1", step_outputs)
            second = common.find_layer({}, "from_step:s1", step_outputs)
            publication_plan = session.publication_plan()
            self.assertEqual(self.mapping.added[-1][2], "BOTTOM")

        self.assertIs(first, second)
        self.assertEqual(self.mapping.layers, [])
        output_publisher.publish(publication_plan)
        self.assertIsNot(self.mapping.layers[0], first)
        self.assertEqual(self.mapping.added[-1], ("df", output, "TOP"))
        self.assertEqual(self.mapping.added_layer_names[-1], output)

    def test_failed_workflow_does_not_publish_partial_outputs(self):
        with self.assertRaises(ValueError):
            with execution_session.ExecutionSession() as session:
                session.register_output("s1", r"D:\out\partial.shp")
                raise ValueError("failed")

        self.assertEqual(self.mapping.added, [])
        self.assertIsNone(execution_session.current())

    def test_from_step_resolves_an_explicitly_added_live_layer(self):
        layer = self.mapping.Layer(r"D:\data\roads.shp")
        self.mapping.layers.append(layer)

        resolved = common.find_layer(
            {}, "from_step:add", {"add": {"layer_path": r"D:\data\roads.shp"}}
        )

        self.assertIs(resolved, layer)

    def test_publication_plan_keeps_all_outputs_for_later_business_rounds(self):
        with execution_session.ExecutionSession() as session:
            session.register_output("s1", r"D:\out\one.shp")
            session.register_output("s2", r"D:\out\two.shp")
            session.register_output("s3", r"D:\out\final.shp")
            paths = session.publication_plan().paths

        self.assertEqual(paths, [r"D:\out\one.shp", r"D:\out\two.shp", r"D:\out\final.shp"])

    def test_selection_capture_failure_aborts_publication_plan(self):
        with execution_session.ExecutionSession() as session:
            session.register_output("s1", r"D:\out\result.shp")
            layer = session.layer_for_output("s1", r"D:\out\result.shp")
            self.arcpy.Describe = lambda value: (_ for _ in ()).throw(RuntimeError("selection unavailable"))
            with self.assertRaisesRegex(RuntimeError, "selection unavailable"):
                session.publication_plan()

    def test_empty_selection_is_cleared_and_verified(self):
        layer = self.mapping.Layer(r"D:\out\empty.shp")
        layer.selection = set([7])
        output_publisher._apply_state(
            layer, execution_session.PublicationItem(layer.dataSource, visible=True, selection_oids=[]))
        self.assertEqual(layer.selection, set())

    def test_spaced_fid_set_is_captured_strictly(self):
        layer = self.mapping.Layer(r"D:\out\spaced.shp")
        layer.selection = set([1, 2, 3])
        item = execution_session.PublicationItem.capture(layer.dataSource, layer)
        self.assertEqual(item.selection_oids, [1, 2, 3])

    def test_fid_set_rejects_empty_noncanonical_and_duplicate_tokens(self):
        for fid_set in ("1; ;3", "1;01", "1;-2", "1;1", "1;invalid"):
            with self.subTest(fid_set=fid_set):
                with self.assertRaises(RuntimeError):
                    arcmap_desktop_selection._parse_fid_set(fid_set)

    def test_restore_passes_catalog_path_string_to_add_field_delimiters(self):
        layer = self.mapping.Layer(r"D:\out\source.shp")
        output_publisher._apply_state(
            layer, execution_session.PublicationItem(layer.dataSource, selection_oids=[1]))
        self.assertEqual(self.delimiter_calls, [(r"D:\out\source.shp", "OBJECTID")])

    def test_selection_restore_is_batched_and_exact(self):
        layer = self.mapping.Layer(r"D:\out\many.shp")
        expected = list(range(arcmap_desktop_selection.OID_BATCH_SIZE + 1))
        calls = []
        original_select = self.arcpy.SelectLayerByAttribute_management

        def recording_select(*args):
            calls.append(args[1])
            return original_select(*args)

        self.arcpy.SelectLayerByAttribute_management = recording_select
        output_publisher._apply_state(
            layer, execution_session.PublicationItem(layer.dataSource, selection_oids=expected))
        self.assertEqual(calls, ["NEW_SELECTION", "ADD_TO_SELECTION"])
        self.assertEqual(layer.selection, set(expected))

    def test_selection_restore_verification_failure_aborts(self):
        layer = self.mapping.Layer(r"D:\out\incorrect.shp")
        self.arcpy.SelectLayerByAttribute_management = lambda *args: None
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            output_publisher._apply_state(
                layer, execution_session.PublicationItem(layer.dataSource, selection_oids=[1]))

    def test_context_selection_read_failure_is_not_downgraded_to_warning(self):
        layer = self.mapping.Layer(r"D:\out\context.shp")
        self.arcpy.Describe = lambda value: (_ for _ in ()).throw(RuntimeError("FIDSet unavailable"))
        with self.assertRaisesRegex(RuntimeError, "FIDSet unavailable"):
            context_reader._layer_info(layer, 0)

    def test_context_uses_one_description_for_geometry_and_selection(self):
        layer = self.mapping.Layer(r"D:\out\one_describe.shp")
        calls = []

        def describe(value):
            calls.append(value)
            return SimpleNamespace(
                FIDSet="1; 2",
                OIDFieldName="OBJECTID",
                catalogPath=layer.catalogPath,
                shapeType="Polygon",
                spatialReference=SimpleNamespace(name="WGS_1984_UTM_Zone_50N"),
            )

        self.arcpy.Describe = describe
        info = context_reader._layer_info(layer, 0)
        self.assertEqual(calls, [layer])
        self.assertEqual(info["geometry_type"], "Polygon")
        self.assertEqual(info["spatial_reference"], "WGS_1984_UTM_Zone_50N")
        self.assertEqual(info["selected_count"], 2)

    def test_context_hash_changes_when_a_layer_spatial_reference_changes(self):
        first = {"layers": [{"layer_ref": "layer:0", "name": "roads", "spatial_reference": "EPSG:3857"}]}
        second = {"layers": [{"layer_ref": "layer:0", "name": "roads", "spatial_reference": "EPSG:32650"}]}
        self.assertNotEqual(context_reader.context_hash(first), context_reader.context_hash(second))

    def test_context_extent_removes_binary_noise_but_preserves_meaningful_changes(self):
        first = SimpleNamespace(extent=SimpleNamespace(
            XMin=664127.6836158192, YMin=3536999.9999999995,
            XMax=695872.3163841808, YMax=3558179.661016949,
        ))
        binary_noise = SimpleNamespace(extent=SimpleNamespace(
            XMin=664127.6836158193, YMin=3537000.0,
            XMax=695872.3163841807, YMax=3558179.6610169495,
        ))
        meaningful_change = SimpleNamespace(extent=SimpleNamespace(
            XMin=664127.6846158192, YMin=3536999.9999999995,
            XMax=695872.3163841808, YMax=3558179.661016949,
        ))

        self.assertEqual(context_reader._extent(first), context_reader._extent(binary_noise))
        self.assertNotEqual(context_reader._extent(first), context_reader._extent(meaningful_change))

    def test_clear_selection_failure_is_not_swallowed(self):
        layer = self.mapping.Layer(r"D:\out\clear.shp")
        self.arcpy.SelectLayerByAttribute_management = lambda *args: (_ for _ in ()).throw(RuntimeError("clear failed"))
        with self.assertRaisesRegex(RuntimeError, "clear failed"):
            common.clear_layer_selection(layer)

    def test_clear_selection_operation_returns_success_only_after_verified_clear(self):
        layer = self.mapping.Layer(r"D:\out\operation_clear.shp")
        layer.selection = set([9])
        with patch.object(selection_ops.common, "find_layer", return_value=layer), \
                patch.object(selection_ops.common, "clear_layer_selection", side_effect=common.clear_layer_selection):
            result = selection_ops.clear_selection({}, {"layer": "roads"}, {})
        self.assertEqual(result, {"layer": layer.name, "cleared": True, "selected_count": 0})
        self.assertEqual(layer.selection, set())

    def test_clear_selection_operation_does_not_claim_success_after_failure(self):
        layer = self.mapping.Layer(r"D:\out\operation_clear_failure.shp")
        with patch.object(selection_ops.common, "find_layer", return_value=layer), \
                patch.object(selection_ops.common, "clear_layer_selection", side_effect=RuntimeError("clear failed")):
            with self.assertRaisesRegex(RuntimeError, "clear failed"):
                selection_ops.clear_selection({}, {"layer": "roads"}, {})

    def test_new_layer_state_is_applied_to_added_live_layer(self):
        detached = self.mapping.Layer(r"D:\out\live.shp")
        detached.visible = False
        detached.selection = set([2])
        plan = execution_session.PublicationPlan([
            execution_session.PublicationItem.capture(detached.dataSource, detached)])
        output_publisher.publish(plan)
        self.assertEqual(self.mapping.layers[0].selection, set([2]))
        self.assertFalse(self.mapping.layers[0].visible)

    def test_publication_replay_is_idempotent(self):
        item = execution_session.PublicationItem(r"D:\out\replay.shp", visible=False, selection_oids=[2])
        plan = execution_session.PublicationPlan([item])
        self.assertEqual(output_publisher.publish(plan)["published"], 1)
        self.assertEqual(output_publisher.publish(plan), {"published": 0, "already_visible": 1})
        self.assertEqual(len(self.mapping.layers), 1)
        self.assertEqual(self.mapping.layers[0].selection, set([2]))

    def test_map_export_uses_isolated_mxd_with_same_run_outputs(self):
        events = []
        current_mxd = SimpleNamespace(saveACopy=lambda path: events.append(("copy", path)))

        def map_document(path):
            return current_mxd if path == "CURRENT" else "render-mxd"

        self.mapping.MapDocument = map_document
        self.mapping.ExportToPNG = lambda mxd, output, **kwargs: events.append(("export", mxd, output))
        with patch.object(map_exporter, "cleanup_stale"), \
                patch.object(map_exporter.path_utils, "exists", return_value=True), \
                patch.object(map_exporter.path_utils, "remove", side_effect=lambda path: events.append(("remove", path))):
            with execution_session.ExecutionSession() as session:
                session.register_output("s1", r"D:\out\result.shp")
                map_exporter.export_png(r"D:\out\map.png", resolution=300)

        self.assertEqual(self.mapping.added, [("df", r"D:\out\result.shp", "TOP")])
        self.assertEqual(events[0][0], "copy")
        self.assertEqual(events[1], ("export", "render-mxd", r"D:\out\map.png"))
        self.assertEqual(events[2][0], "remove")


class _Session:
    def __init__(self, events):
        self.events = events

    def __enter__(self):
        self.events.append("enter")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.events.append("commit" if exc_type is None else "abort")
        return False

    def register_output(self, step_id, path, output_type):
        self.events.append("stage:" + step_id + ":" + path)

    def canonicalize_runtime_references(self, value):
        return value

    def publication_plan(self):
        self.events.append("plan")
        return execution_session.PublicationPlan.from_records([
            {"path": "fake.one.shp", "visible": None, "selection_oids": None},
            {"path": "fake.two.shp", "visible": None, "selection_oids": None},
        ])


class WorkflowExecutorPublicationTests(unittest.TestCase):
    def test_execute_stages_every_output_and_commits_after_the_last_step(self):
        events = []
        operations = {
            "make.one": {
                "executor": "fake.one",
                "parameters_schema": {},
                "side_effects": "writes_data",
                "output_policy": {"type": "feature_class", "add_to_map": True},
            },
            "make.two": {
                "executor": "fake.two",
                "parameters_schema": {},
                "side_effects": "writes_data",
                "output_policy": {"type": "feature_class", "add_to_map": True},
            },
        }
        row = {
            "context_hash": "hash",
            "workflow": {
                "summary": "two outputs",
                "steps": [
                    {"id": "s1", "operation": "make.one", "arguments": {}},
                    {"id": "s2", "operation": "make.two", "arguments": {}},
                ],
            },
        }

        def execute_operation(executor, context, arguments, step_outputs):
            events.append("execute:" + executor)
            return {"output": executor + ".shp"}

        with patch.object(workflow_executor.context_reader, "context_hash", return_value="hash"), \
                patch.object(workflow_executor, "_load_operations", return_value=operations), \
                patch.object(workflow_executor.execution_session, "ExecutionSession", return_value=_Session(events)), \
                patch.object(workflow_executor, "_validate_arguments"), \
                patch.object(workflow_executor, "_validate_write_policy"), \
                patch.object(workflow_executor, "_prepare_runtime_arguments", side_effect=lambda operation, context, arguments, outputs: arguments), \
                patch.object(workflow_executor, "_confirm_edit_if_needed"), \
                patch.object(workflow_executor, "_call_executor", side_effect=execute_operation), \
                patch.object(workflow_executor, "_finalize_runtime_result", side_effect=lambda operation, context, arguments, result: result):
            outcome = workflow_executor.execute(row, {})

        self.assertTrue(outcome.result["ok"])
        self.assertEqual(outcome.publication_plan.paths, ["fake.one.shp", "fake.two.shp"])
        self.assertEqual(events, [
            "enter",
            "execute:fake.one",
            "stage:s1:fake.one.shp",
            "execute:fake.two",
            "stage:s2:fake.two.shp",
            "plan",
            "commit",
        ])


if __name__ == "__main__":
    unittest.main()
