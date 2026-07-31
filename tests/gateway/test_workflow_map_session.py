import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch


sys.modules.setdefault("arcpy", types.SimpleNamespace(ExecuteError=RuntimeError))

from arcmap_runtime_py2 import workflow_executor
from arcmap_runtime_py2 import execution_session
from arcmap_runtime_py2 import map_exporter
from arcmap_runtime_py2 import output_publisher
from arcmap_runtime_py2.operations import common


class _Mapping:
    def __init__(self):
        self.layers = []
        self.added = []

    def MapDocument(self, value):
        return "mxd"

    def ListDataFrames(self, mxd):
        return ["df"]

    def ListLayers(self, mxd, wildcard, data_frame):
        return list(self.layers)

    def Layer(self, path):
        return SimpleNamespace(
            name=path,
            longName=path,
            dataSource=path,
            supports=lambda capability: capability == "DATASOURCE",
            visible=True,
            isFeatureLayer=True,
            getSelectionSet=lambda: set(),
            setSelectionSet=lambda method, values: None,
        )

    def AddLayer(self, data_frame, layer, position):
        self.layers.append(layer)
        self.added.append((data_frame, layer.dataSource, position))


class ExecutionSessionTests(unittest.TestCase):
    def setUp(self):
        self.mapping = _Mapping()
        self.arcpy = SimpleNamespace(
            ExecuteError=RuntimeError,
            env=SimpleNamespace(addOutputsToMap=True),
            mapping=self.mapping,
        )
        self.arcpy_patch = patch.object(common, "arcpy", self.arcpy)
        self.arcpy_patch.start()
        self.session_arcpy_patch = patch.object(execution_session, "arcpy", self.arcpy)
        self.session_arcpy_patch.start()
        self.publisher_arcpy_patch = patch.object(output_publisher, "arcpy", self.arcpy)
        self.publisher_arcpy_patch.start()
        self.exporter_arcpy_patch = patch.object(map_exporter, "arcpy", self.arcpy)
        self.exporter_arcpy_patch.start()

    def tearDown(self):
        self.exporter_arcpy_patch.stop()
        self.publisher_arcpy_patch.stop()
        self.session_arcpy_patch.stop()
        self.arcpy_patch.stop()

    def test_outputs_are_resolved_without_map_publication_and_published_after_execution(self):
        output = r"D:\out\result.shp"
        step_outputs = {"s1": {"output": output}}

        with execution_session.ExecutionSession() as session:
            session.register_output("s1", output)
            first = common.find_layer({}, "from_step:s1", step_outputs)
            second = common.find_layer({}, "from_step:s1", step_outputs)
            self.assertEqual(self.mapping.added, [])

        self.assertIs(first, second)
        self.assertEqual(self.mapping.added, [])
        output_publisher.publish(session.publication_plan())
        self.assertIs(self.mapping.layers[0], first)
        self.assertEqual(self.mapping.added, [("df", output, "TOP")])

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
            layer.getSelectionSet = lambda: (_ for _ in ()).throw(RuntimeError("selection unavailable"))
            with self.assertRaisesRegex(RuntimeError, "selection unavailable"):
                session.publication_plan()

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

    def register_output(self, step_id, path):
        self.events.append("stage:" + step_id + ":" + path)

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
