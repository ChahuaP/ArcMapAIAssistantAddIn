import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.experiments import ExperimentRunner
from gateway_py3.run_store import RunStore
from gateway_py3.validators import ValidationError, context_hash, prepare_workflow


SEMANTICS = {
    "goal": "refresh",
    "inputs": [],
    "constraints": [],
    "success_criteria": [],
}

WORKFLOW = {
    "action": "execute",
    "summary": "refresh",
    "steps": [
        {
            "id": "s1",
            "operation": "view.refresh_view",
            "arguments": {},
            "reason": "refresh",
        }
    ],
}

INVALID = {
    "action": "execute",
    "summary": "bad",
    "steps": [
        {
            "id": "s1",
            "operation": "selection.select_by_attribute",
            "arguments": {
                "layer": "roads",
                "where": {
                    "field": "TYPE",
                    "op": "in",
                    "value": ["A"],
                },
            },
            "reason": "bad",
        }
    ],
}


class Client:
    def __init__(self, provider, model, replies, events):
        self.provider_id = provider
        self.model_id = model
        self.replies = list(replies)
        self.events = events

    def chat_json(self, messages):
        role = messages[0]["content"].split("GeoPilot ")[1].split(" role")[0]
        self.events.append((role, json.loads(messages[1]["content"])))
        return self.replies.pop(0)


class Catalog:
    def __init__(self):
        self.operations = {
            "make.vector": self._writer("feature_class"),
            "make.raster": self._writer("raster"),
            "make.file": self._writer("file"),
            "make.collection": self._writer("file_collection"),
            "use.layer": self._reader(),
            "layer.add_layer": self._add(),
        }

    def _writer(self, output_type):
        return {
            "id": "make." + output_type,
            "parameters_schema": {
                "type": "object",
                "required": ["output_name"],
                "properties": {
                    "output_name": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "context_requirements": {},
            "side_effects": "writes_data",
            "output_policy": {
                "type": output_type,
                "workspace": "mxd_default",
                "add_to_map": output_type in ("feature_class", "raster"),
                "extension": ".csv",
                "formats": ["shp"],
            },
        }

    @staticmethod
    def _reader():
        return {
            "id": "use.layer",
            "parameters_schema": {
                "type": "object",
                "required": ["layer"],
                "properties": {
                    "layer": {
                        "type": "string",
                        "x-geopilot-kind": "layer",
                    },
                },
                "additionalProperties": False,
            },
            "context_requirements": {},
            "side_effects": "read_only",
            "output_policy": {},
        }

    @staticmethod
    def _add():
        return {
            "id": "layer.add_layer",
            "parameters_schema": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                },
                "additionalProperties": False,
            },
            "context_requirements": {},
            "side_effects": "changes_map",
            "output_policy": {},
        }

    def get(self, operation_id):
        return self.operations[operation_id]

    def all_operations(self):
        return self.operations.values()


class G3ValidationFirstTests(unittest.TestCase):
    @staticmethod
    def _context():
        return {
            "is_saved": True,
            "layers": [
                {
                    "layer_ref": "layer:roads",
                    "name": "roads",
                    "longName": "roads",
                    "fields": [
                        {
                            "name": "TYPE",
                            "type": "String",
                        }
                    ],
                }
            ],
        }

    @staticmethod
    def _workflow(operation, output_name="final_sites"):
        return {
            "action": "execute",
            "summary": "x",
            "steps": [
                {
                    "id": "s1",
                    "operation": operation,
                    "arguments": {"output_name": output_name},
                    "reason": "x",
                },
                {
                    "id": "s2",
                    "operation": "use.layer",
                    "arguments": {"layer": "from_step:s1"},
                    "reason": "x",
                },
            ],
        }

    def _plan_g3(self, primary, reviewer):
        configuration = {
            "primary_provider": "primary",
            "primary_model": "planner",
            "reviewer_provider": "reviewer",
            "reviewer_model": "auditor",
        }
        context = self._context()

        def create_client(provider, model):
            if provider == "primary":
                return primary
            return reviewer

        with tempfile.TemporaryDirectory() as temp:
            with patch(
                "gateway_py3.llm_providers.load_config",
                return_value=configuration,
            ):
                store = RunStore(Path(temp) / "runs.sqlite")
                runner = ExperimentRunner(
                    OperationCatalog(),
                    store,
                    create_client,
                )
                run = store.create_run("refresh", "multi_agent")
                store.bind_context(
                    run["id"],
                    {
                        "context": context,
                        "context_hash": context_hash(context),
                        "bridge": {
                            "bridge_pid": 1,
                            "bridge_port": 1,
                            "arcmap_pid": 1,
                            "hwnd": 1,
                        },
                        "captured_at": 1,
                    },
                )
                return runner.plan(
                    run["id"],
                    "refresh",
                    context,
                    "multi_agent",
                )

    def test_output_type_matrix_and_redundant_add(self):
        for kind, allowed in (
            ("vector", True),
            ("raster", True),
            ("file", False),
            ("collection", False),
        ):
            with self.subTest(kind=kind):
                workflow = self._workflow("make." + kind)
                if allowed:
                    prepare_workflow(workflow, Catalog(), self._context())
                else:
                    with self.assertRaisesRegex(ValidationError, "file 输出"):
                        prepare_workflow(workflow, Catalog(), self._context())

        redundant = {
            "action": "execute",
            "summary": "x",
            "steps": [
                {
                    "id": "s1",
                    "operation": "make.vector",
                    "arguments": {"output_name": "final_sites"},
                    "reason": "x",
                },
                {
                    "id": "s2",
                    "operation": "layer.add_layer",
                    "arguments": {"path": "D:/out/final_sites.shp"},
                    "reason": "x",
                },
            ],
        }
        with self.assertRaisesRegex(ValidationError, "不得重复添加"):
            prepare_workflow(redundant, Catalog(), self._context())

    def test_flood_path_and_final_sites_file_reference(self):
        catalog = Catalog()
        workflow = self._workflow("make.vector", output_name="flood_high")
        workflow["steps"][1]["arguments"]["layer"] = "D:/out/flood_high.shp"

        with self.assertRaisesRegex(ValidationError, "from_step:s1"):
            prepare_workflow(workflow, catalog, self._context())

        workflow["steps"][1]["arguments"]["layer"] = "from_step:s1"
        prepare_workflow(workflow, catalog, self._context())

        workflow["steps"][0]["operation"] = "make.file"
        with self.assertRaisesRegex(ValidationError, "file 输出"):
            prepare_workflow(workflow, catalog, self._context())

    def test_in_value_is_repaired_before_audit(self):
        events = []
        primary = Client(
            "primary",
            "planner",
            [
                {"task_semantics": SEMANTICS},
                {"workflow_draft": INVALID},
                {"workflow_draft": WORKFLOW},
            ],
            events,
        )
        reviewer = Client(
            "reviewer",
            "auditor",
            [
                {
                    "audit_result": {
                        "decision": "pass",
                        "issues": [],
                        "revision_requirements": [],
                    }
                }
            ],
            events,
        )

        row = self._plan_g3(primary, reviewer)

        self.assertEqual(
            [role for role, _ in events],
            ["semantic", "planner", "planner", "auditor"],
        )
        self.assertEqual(
            row["agent_trace"][0]["run"]["counts"]["validation_revisions"],
            1,
        )

    def test_identical_invalid_revision_stalls_without_audit(self):
        events = []
        primary = Client(
            "primary",
            "planner",
            [
                {"task_semantics": SEMANTICS},
                {"workflow_draft": INVALID},
                {"workflow_draft": INVALID},
            ],
            events,
        )
        reviewer = Client("reviewer", "auditor", [], events)

        row = self._plan_g3(primary, reviewer)
        trace = row["agent_trace"][0]["run"]

        self.assertEqual(row["status"], "failed")
        self.assertEqual(trace["terminal"]["detail"]["kind"], "stalled_revision")
        self.assertEqual(trace["counts"]["validation_revisions"], 1)
        self.assertEqual(trace["counts"]["stalls"], 1)
        self.assertNotIn("auditor", [role for role, _ in events])

    def test_audit_a_b_a_cycle_stops_before_third_audit(self):
        workflow_b = dict(WORKFLOW, summary="workflow b")
        revise = {
            "decision": "revise",
            "issues": ["fix"],
            "revision_requirements": ["fix"],
        }
        events = []
        primary = Client(
            "primary",
            "planner",
            [
                {"task_semantics": SEMANTICS},
                {"workflow_draft": WORKFLOW},
                {"workflow_draft": workflow_b},
                {"workflow_draft": WORKFLOW},
            ],
            events,
        )
        reviewer = Client(
            "reviewer",
            "auditor",
            [
                {"audit_result": revise},
                {"audit_result": revise},
            ],
            events,
        )

        row = self._plan_g3(primary, reviewer)
        trace = row["agent_trace"][0]["run"]

        self.assertEqual(row["status"], "failed")
        self.assertEqual(trace["terminal"]["detail"]["kind"], "cyclic_revision")
        self.assertEqual(trace["terminal"]["detail"]["source"], "audit")
        self.assertEqual(trace["counts"]["audit_revisions"], 2)
        self.assertEqual(trace["counts"]["validation_revisions"], 0)
        self.assertEqual(trace["counts"]["contract_revisions"], 0)
        self.assertEqual(trace["counts"]["cycles"], 1)
        self.assertEqual(
            [role for role, _ in events],
            ["semantic", "planner", "auditor", "planner", "auditor", "planner"],
        )

    def test_audit_revision_invalid_draft_is_repaired_before_next_audit(self):
        revise = {
            "decision": "revise",
            "issues": ["fix"],
            "revision_requirements": ["fix"],
        }
        passed = {
            "decision": "pass",
            "issues": [],
            "revision_requirements": [],
        }
        repaired = dict(WORKFLOW, summary="repaired workflow")
        events = []
        primary = Client(
            "primary",
            "planner",
            [
                {"task_semantics": SEMANTICS},
                {"workflow_draft": WORKFLOW},
                {"workflow_draft": INVALID},
                {"workflow_draft": repaired},
            ],
            events,
        )
        reviewer = Client(
            "reviewer",
            "auditor",
            [
                {"audit_result": revise},
                {"audit_result": passed},
            ],
            events,
        )

        row = self._plan_g3(primary, reviewer)
        trace = row["agent_trace"][0]["run"]

        self.assertEqual(row["status"], "planned")
        self.assertEqual(trace["counts"]["validation_revisions"], 1)
        self.assertEqual(trace["counts"]["audit_revisions"], 1)
        self.assertEqual(trace["counts"]["contract_revisions"], 0)
        self.assertEqual(
            [role for role, _ in events],
            ["semantic", "planner", "auditor", "planner", "planner", "auditor"],
        )

    def test_invalid_a_b_a_cycle_stops_without_audit(self):
        workflow_b = {
            "action": "execute",
            "summary": "other bad workflow",
            "steps": [
                {
                    "id": "s1",
                    "operation": "missing.operation",
                    "arguments": {},
                    "reason": "bad",
                }
            ],
        }
        events = []
        primary = Client(
            "primary",
            "planner",
            [
                {"task_semantics": SEMANTICS},
                {"workflow_draft": INVALID},
                {"workflow_draft": workflow_b},
                {"workflow_draft": INVALID},
            ],
            events,
        )
        reviewer = Client("reviewer", "auditor", [], events)

        row = self._plan_g3(primary, reviewer)
        trace = row["agent_trace"][0]["run"]

        self.assertEqual(row["status"], "failed")
        self.assertEqual(trace["terminal"]["detail"]["kind"], "cyclic_revision")
        self.assertEqual(trace["counts"]["validation_revisions"], 2)
        self.assertEqual(trace["counts"]["cycles"], 1)
        self.assertEqual(trace["counts"]["stalls"], 0)
        self.assertNotIn("auditor", [role for role, _ in events])


if __name__ == "__main__":
    unittest.main()
