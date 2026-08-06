import copy
import tempfile
import unittest
from pathlib import Path

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.dominance_gate import DominanceGate
from gateway_py3.planning_engine import PlanningEngine, workflow_draft_model_view
from gateway_py3.run_store import RunStore
from gateway_py3.validators import context_hash
from tests.gateway.test_experiments import CONTEXT, TASK_CONTRACT, WORKFLOW, plan_bound
from tests.gateway.planner_test_utils import model_wire_response


class Client:
    provider_id = "test-provider"
    model_id = "test-model"
    def __init__(self, replies): self.replies = list(replies); self.calls = []
    def chat_structured(self, messages, contract):
        self.calls.append(messages)
        return model_wire_response(self.replies.pop(0), messages)


class ProofDrivenAuditor:
    provider_id = "test-provider"
    model_id = "test-model"

    def __init__(self):
        self.calls = []

    def chat_structured(self, messages, contract):
        self.calls.append(messages)
        payload = __import__("json").loads(messages[1]["content"])
        report = payload["plan_artifact"]["baseline_verifier_report"]
        proof = next((
            item for item in report["review_obligations"]
            if item["code"] == "requirement.semantic_unresolved"
            and item.get("requirement_id") == "merge_exclusion"
        ), None)
        if proof is None:
            return {"audit_result": {"decision": "pass", "claims": []}}
        return {"audit_result": {"decision": "revise", "claims": [{
            "kind": "revision",
            "proof_id": proof["obligation_id"],
            "change_target": "workflow",
            "required_change": "Make the merge step directly produce site_exclusion.",
        }]}}


class PlanningStateMachinePublicTests(unittest.TestCase):
    def engine(self, factory):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return PlanningEngine(OperationCatalog(), RunStore(Path(temp.name) / "runs.sqlite"), factory)

    def test_g2_review_obligation_is_planned_not_clarified(self):
        # Existing fixture is fully proved; this verifies the public G2 path reaches planned.
        client = Client([{"task_contract": TASK_CONTRACT}, {"workflow_draft": copy.deepcopy(WORKFLOW)}])
        row = plan_bound(self.engine(lambda p, m: client), "x", CONTEXT, "g2_constrained")
        self.assertEqual("planned", row["status"])

    def test_g2_freezes_the_validated_artifact_after_a_planner_repair(self):
        invalid = copy.deepcopy(WORKFLOW)
        invalid["steps"][0]["arguments"]["layer"] = "missing_layer"
        client = Client([
            {"task_contract": TASK_CONTRACT},
            {"workflow_draft": invalid},
            {"workflow_draft": copy.deepcopy(WORKFLOW)},
        ])

        row = plan_bound(self.engine(lambda p, m: client), "x", CONTEXT, "g2_constrained")

        trace = row["agent_trace"][0]["run"]
        self.assertEqual("planned", row["status"])
        self.assertEqual(trace["plan_artifact"]["artifact_hash"], trace["plan_artifact_hash"])
        self.assertEqual(WORKFLOW, trace["plan_artifact"]["baseline_workflow"])

    def test_g3_audits_the_immutable_baseline_artifact(self):
        planner = Client([{"task_contract": TASK_CONTRACT}, {"workflow_draft": copy.deepcopy(WORKFLOW)}])
        auditor = Client([{"audit_result": {"decision": "pass", "claims": []}}])
        clients = iter((planner, auditor))
        row = plan_bound(self.engine(lambda p, m: next(clients)), "x", CONTEXT, "g3_audited")
        trace = row["agent_trace"][0]["run"]
        self.assertEqual("planned", row["status"])
        self.assertEqual(trace["plan_artifact_hash"], trace["audits"][0]["baseline_artifact_hash"])
        self.assertEqual(trace["plan_artifact_hash"], __import__("json").loads(auditor.calls[0][1]["content"])["plan_artifact_hash"])

    def test_dominance_rejects_new_side_effect_or_lost_output(self):
        base = {"baseline_verifier_report": {"hard_violations": [], "review_obligations": [
            {"obligation_id": "o1"}], "blocking_clarifications": [], "output_results": [{"output_id": "x", "satisfied": True}],
            "requirements": [{"requirement_id": "r", "satisfied": True}], "side_effects": ["read_only"]}}
        candidate = {"hard_violations": [], "review_obligations": [], "blocking_clarifications": [],
                     "output_results": [], "requirements": [{"requirement_id": "r", "satisfied": True}], "side_effects": ["writes_data"]}
        result = DominanceGate().admit(base, candidate, [{"claim": {"proof_id": "o1"}}])
        self.assertFalse(result["accepted"])
        self.assertIn("lost_output", result["reasons"])
        self.assertIn("expanded_side_effects", result["reasons"])

    def test_dominance_accepts_only_a_proven_removed_obligation(self):
        proof = {"obligation_id": "o1"}
        base = {"baseline_verifier_report": {"hard_violations": [], "review_obligations": [proof],
            "blocking_clarifications": [], "output_results": [{"output_id": "x", "satisfied": True}],
            "requirements": [{"requirement_id": "r", "satisfied": True}], "side_effects": ["read_only"]}}
        candidate = {"hard_violations": [], "review_obligations": [], "blocking_clarifications": [],
                     "output_results": [{"output_id": "x", "satisfied": True}], "requirements": [{"requirement_id": "r", "satisfied": True}], "side_effects": ["read_only"]}
        self.assertTrue(DominanceGate().admit(base, candidate, [{"claim": {"proof_id": "o1"}}])["accepted"])

    def test_zero_problem_baseline_cannot_start_a_revision(self):
        planner = Client([{"task_contract": TASK_CONTRACT}, {"workflow_draft": copy.deepcopy(WORKFLOW)}])
        bad_claim = {"kind": "revision", "proof_id": "invented", "change_target": "workflow",
                     "required_change": "change it"}
        auditor = Client([{"audit_result": {"decision": "revise", "claims": [bad_claim]}}])
        clients = iter((planner, auditor))
        with self.assertRaisesRegex(ValueError, "unknown baseline proof"):
            plan_bound(self.engine(lambda p, m: next(clients)), "x", CONTEXT, "g3_audited")

    def test_g3_routes_request_alignment_workflow_repairs_without_rewriting_task_contract(self):
        baseline_client = Client([
            {"task_contract": TASK_CONTRACT}, {"workflow_draft": copy.deepcopy(WORKFLOW)},
        ])
        baseline = self.engine(lambda p, m: baseline_client)
        source = plan_bound(baseline, "x", CONTEXT, "g2_constrained")
        artifact = source["agent_trace"][0]["run"]["plan_artifact"]
        proof_id = next(
            item["obligation_id"]
            for item in artifact["baseline_verifier_report"]["review_obligations"]
            if item["code"] == "request_alignment.unresolved"
        )
        revised_workflow = copy.deepcopy(artifact["baseline_workflow"])
        revised_workflow["steps"][0]["reason"] = "Apply the audited request-alignment correction."
        planner = Client([{"workflow_draft": revised_workflow}])
        auditor = Client([
            {"audit_result": {"decision": "revise", "claims": [{
                "kind": "revision", "proof_id": proof_id, "change_target": "workflow",
                "required_change": "Repair the workflow while preserving the correct TaskContract.",
            }]}},
            {"audit_result": {"decision": "pass", "claims": []}},
        ])
        clients = iter((planner, auditor))
        replay = PlanningEngine(OperationCatalog(), baseline.store, lambda p, m: next(clients))
        target = baseline.store.create_run("x", "g3_audited")
        baseline.store.bind_context(target["id"], {
            "context": CONTEXT, "context_hash": context_hash(CONTEXT),
            "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4},
            "captured_at": 2,
        })

        result = replay.plan_with_artifact(
            target["id"], "x", CONTEXT, "g3_audited", artifact,
            provider="test-provider", model="test-model",
        )

        trace = result["agent_trace"][0]["run"]
        self.assertEqual("planned", result["status"])
        self.assertEqual(revised_workflow, result["workflow"])
        self.assertEqual(artifact["task_contract"], trace["task_contract"])
        self.assertEqual(1, len(planner.calls))
        self.assertEqual(2, len(auditor.calls))
        self.assertEqual(1, trace["counts"]["audit_revisions"])
        self.assertTrue(trace["dominance_reports"][-1]["accepted"])
        self.assertTrue(trace["dominance_reports"][-1]["semantic_confirmation_audit"])

    def test_audit_origin_survives_deterministic_repairs_until_dominance_gate(self):
        command = "copy roads without modifying roads"
        context = {"is_saved": True, "layers": [
            {"layer_ref": "layer:roads", "name": "roads", "geometry_type": "Polyline", "fields": [{"name": "RID"}]},
            {"layer_ref": "layer:zones", "name": "zones", "geometry_type": "Polygon", "fields": [{"name": "ZID"}]},
        ]}
        task = {
            "input_entities": [{
                "entity_id": "roads", "role": "source", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": "roads",
            }, {
                "entity_id": "zones", "role": "join", "kind": "feature_layer",
                "reference": "layer:zones", "evidence": "roads",
            }],
            "outputs": [{
                "output_id": "grouped", "kind": "feature_class", "name": "grouped",
                "format": "gdb", "geometry": "polyline",
                "required_fields": ["RID", "Join_Count"], "spatial_reference": "EPSG:3857",
                "destination": "default", "evidence": "copy roads",
            }],
            "requirements": [{
                "requirement_id": "preserve", "predicate": {"kind": "source_preserved", "subject": "roads"},
                "evidence": "without modifying roads",
            }],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }
        baseline_workflow = {"action": "execute", "summary": command, "steps": [{
            "id": "join", "operation": "analysis.spatial_join",
            "arguments": {"target_layer": "roads", "join_layer": "zones", "output_name": "grouped"},
            "reason": command,
        }]}
        wrong_revision = {"action": "execute", "summary": command, "steps": [
            {"id": "project", "operation": "analysis.project",
             "arguments": {"input_layer": "roads", "spatial_reference": "EPSG:4326", "output_name": "roads_projected"},
             "reason": command},
            {"id": "join", "operation": "analysis.spatial_join",
             "arguments": {"target_layer": "from_step:project", "join_layer": "zones", "output_name": "grouped"},
             "reason": command},
        ]}
        correct_revision = copy.deepcopy(wrong_revision)
        correct_revision["steps"][0]["arguments"]["spatial_reference"] = "EPSG:3857"

        baseline_client = Client([
            {"task_contract": task},
            {"workflow_draft": baseline_workflow},
        ])
        baseline = self.engine(lambda p, m: baseline_client)
        source = plan_bound(baseline, command, context, "g2_constrained")
        artifact = source["agent_trace"][0]["run"]["plan_artifact"]
        proof_id = next(
            item["obligation_id"] for item in artifact["baseline_verifier_report"]["review_obligations"]
            if item["code"] == "output.spatial_reference_unresolved"
        )
        planner = Client([
            {"workflow_draft": wrong_revision},
            {"workflow_draft": correct_revision},
        ])
        auditor = Client([{"audit_result": {"decision": "revise", "claims": [{
            "kind": "revision", "proof_id": proof_id, "change_target": "workflow",
            "required_change": "Project the output to the required EPSG:3857 spatial reference.",
        }]}}])
        clients = iter((planner, auditor))
        replay = PlanningEngine(OperationCatalog(), baseline.store, lambda p, m: next(clients))
        target = baseline.store.create_run(command, "g3_audited")
        baseline.store.bind_context(target["id"], {
            "context": context, "context_hash": context_hash(context),
            "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4},
            "captured_at": 2,
        })

        result = replay.plan_with_artifact(
            target["id"], command, context, "g3_audited", artifact,
            provider="test-provider", model="test-model",
        )

        trace = result["agent_trace"][0]["run"]
        self.assertEqual("planned", result["status"])
        self.assertEqual(1, len(trace["audits"]))
        self.assertTrue(trace["dominance_reports"][-1]["accepted"])
        self.assertEqual(2, len(planner.calls))
        self.assertTrue(all(
            "GeoPilot workflow_repair role" in messages[0]["content"]
            for messages in planner.calls
        ))
        second_repair = __import__("json").loads(planner.calls[1][1]["content"])
        self.assertEqual(workflow_draft_model_view(wrong_revision), second_repair["workflow_draft"])
        self.assertEqual("workflow_verifier", second_repair["diagnostic"]["kind"])

    def test_g3_repairs_a_proven_request_to_task_semantic_mismatch_and_reaudits(self):
        command = "select roads where CLASS A and export roads_a"
        context = {"is_saved": True, "layers": [{
            "layer_ref": "layer:roads", "name": "roads", "geometry_type": "Polyline",
            "spatial_reference": "EPSG:3857",
            "fields": [{"name": "RID", "type": "Integer"}, {"name": "CLASS", "type": "String"}],
        }]}
        wrong_task = {
            "input_entities": [{
                "entity_id": "roads", "role": "source", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": "roads",
            }],
            "outputs": [{
                "output_id": "roads_a", "kind": "feature_class", "name": "roads_a",
                "format": "gdb", "geometry": "polyline",
                "required_fields": ["RID", "CLASS"], "spatial_reference": "EPSG:3857",
                "destination": "default", "evidence": "roads_a",
            }],
            "requirements": [{
                "requirement_id": "filter", "evidence": "CLASS A",
                "predicate": {"kind": "attribute_filter", "subject": "roads", "target": "roads", "where": {"field": "CLASS", "op": "eq", "value": "B"}, "selection_type": "new_selection"},
            }, {
                "requirement_id": "export", "evidence": "export roads_a",
                "predicate": {"kind": "artifact_export", "subject": "roads_a", "target": "roads", "action": "export_selected_features", "selected_only": True, "output_format": "gdb"},
            }],
            "allowed_side_effects": ["changes_map", "writes_data"], "clarifications": [],
        }
        corrected_task = copy.deepcopy(wrong_task)
        corrected_task["requirements"][0]["predicate"]["where"]["value"] = "A"
        wrong_workflow = {"action": "execute", "summary": command, "steps": [
            {"id": "select", "operation": "selection.select_by_attribute", "arguments": {"layer": "roads", "where": {"field": "CLASS", "op": "eq", "value": "B"}, "selection_type": "NEW_SELECTION"}, "reason": command},
            {"id": "export", "operation": "selection.export_selected_features", "arguments": {"layer": "roads", "output_name": "roads_a", "output_format": "gdb"}, "reason": command},
        ]}
        corrected_workflow = copy.deepcopy(wrong_workflow)
        corrected_workflow["steps"][0]["arguments"]["where"]["value"] = "A"
        baseline_client = Client([
            {"task_contract": wrong_task}, {"workflow_draft": wrong_workflow},
        ])
        baseline = self.engine(lambda p, m: baseline_client)
        source = plan_bound(baseline, command, context, "g2_constrained")
        artifact = source["agent_trace"][0]["run"]["plan_artifact"]
        proof_id = next(
            item["obligation_id"] for item in artifact["baseline_verifier_report"]["review_obligations"]
            if item["code"] == "request_alignment.unresolved" and item["requirement_id"] == "filter"
        )
        planner = Client([
            {"task_contract": corrected_task}, {"workflow_draft": corrected_workflow},
        ])
        auditor = Client([
            {"audit_result": {"decision": "revise", "claims": [{
                "kind": "revision", "proof_id": proof_id, "change_target": "task_contract",
                "required_change": "Bind the CLASS filter value to A as stated by the request.",
            }]}},
            {"audit_result": {"decision": "pass", "claims": []}},
        ])
        clients = iter((planner, auditor))
        replay = PlanningEngine(OperationCatalog(), baseline.store, lambda p, m: next(clients))
        target = baseline.store.create_run(command, "g3_audited")
        baseline.store.bind_context(target["id"], {
            "context": context, "context_hash": context_hash(context),
            "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4},
            "captured_at": 2,
        })

        result = replay.plan_with_artifact(
            target["id"], command, context, "g3_audited", artifact,
            provider="test-provider", model="test-model",
        )

        trace = result["agent_trace"][0]["run"]
        self.assertEqual("planned", result["status"])
        self.assertEqual(2, len(trace["audits"]))
        self.assertEqual("A", trace["task_contract"]["requirements"][0]["predicate"]["where"]["value"])
        self.assertEqual("A", result["workflow"]["steps"][0]["arguments"]["where"]["value"])
        self.assertTrue(trace["dominance_reports"][-1]["accepted"])
        self.assertTrue(trace["dominance_reports"][-1]["semantic_confirmation_audit"])

    def test_g3_revalidates_an_unchanged_workflow_after_contract_only_semantic_repair(self):
        command = "select roads where CLASS A and export roads_a"
        context = {"is_saved": True, "layers": [{
            "layer_ref": "layer:roads", "name": "roads", "geometry_type": "Polyline",
            "spatial_reference": "EPSG:3857",
            "fields": [{"name": "RID", "type": "Integer"}, {"name": "CLASS", "type": "String"}],
        }]}
        workflow = {"action": "execute", "summary": command, "steps": [
            {"id": "select", "operation": "selection.select_by_attribute", "arguments": {"layer": "roads", "where": {"field": "CLASS", "op": "eq", "value": "A"}, "selection_type": "NEW_SELECTION"}, "reason": command},
            {"id": "export", "operation": "selection.export_selected_features", "arguments": {"layer": "roads", "output_name": "roads_a", "output_format": "gdb"}, "reason": command},
        ]}
        wrong_task = {
            "input_entities": [{
                "entity_id": "roads", "role": "source", "kind": "feature_layer",
                "reference": "layer:roads", "evidence": "roads",
            }],
            "outputs": [{
                "output_id": "roads_a", "kind": "feature_class", "name": "roads_a",
                "format": "gdb", "geometry": "polyline",
                "required_fields": ["RID", "CLASS"], "spatial_reference": "EPSG:3857",
                "destination": "default", "evidence": "roads_a",
            }],
            "requirements": [{
                "requirement_id": "filter", "evidence": "CLASS A",
                "predicate": {"kind": "attribute_filter", "subject": "roads", "target": "roads", "where": {"field": "CLASS", "op": "eq", "value": "A"}, "selection_type": "new_selection"},
            }, {
                "requirement_id": "export", "evidence": "export roads_a",
                "predicate": {"kind": "artifact_export", "subject": "roads_a", "target": "roads", "action": "export_selected_features", "selected_only": True, "output_format": "gdb"},
            }],
            "allowed_side_effects": ["changes_map", "writes_data"], "clarifications": [],
        }
        corrected_task = copy.deepcopy(wrong_task)
        corrected_task["input_entities"][0]["role"] = "target"
        baseline_client = Client([
            {"task_contract": wrong_task}, {"workflow_draft": workflow},
        ])
        baseline = self.engine(lambda p, m: baseline_client)
        source = plan_bound(baseline, command, context, "g2_constrained")
        artifact = source["agent_trace"][0]["run"]["plan_artifact"]
        frozen_workflow = artifact["baseline_workflow"]
        proof_id = next(
            item["obligation_id"] for item in artifact["baseline_verifier_report"]["review_obligations"]
            if item["code"] == "request_alignment.unresolved" and item["requirement_id"] == "filter"
        )
        planner = Client([
            {"task_contract": corrected_task},
            {"workflow_draft": copy.deepcopy(frozen_workflow)},
        ])
        auditor = Client([
            {"audit_result": {"decision": "revise", "claims": [{
                "kind": "revision", "proof_id": proof_id, "change_target": "task_contract",
                "required_change": "Bind the requirement to the complete selection clause from the request.",
            }]}},
            {"audit_result": {"decision": "pass", "claims": []}},
        ])
        clients = iter((planner, auditor))
        replay = PlanningEngine(OperationCatalog(), baseline.store, lambda p, m: next(clients))
        target = baseline.store.create_run(command, "g3_audited")
        baseline.store.bind_context(target["id"], {
            "context": context, "context_hash": context_hash(context),
            "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4},
            "captured_at": 2,
        })

        result = replay.plan_with_artifact(
            target["id"], command, context, "g3_audited", artifact,
            provider="test-provider", model="test-model",
        )

        trace = result["agent_trace"][0]["run"]
        self.assertEqual("planned", result["status"])
        self.assertEqual(frozen_workflow, result["workflow"])
        self.assertEqual(1, len(planner.calls))
        self.assertEqual(2, len(trace["audits"]))
        self.assertEqual(0, trace["counts"]["audit_revisions"])
        self.assertEqual("target", trace["task_contract"]["input_entities"][0]["role"])
        self.assertEqual(command, trace["task_contract"]["requirements"][0]["evidence"])
        self.assertTrue(trace["dominance_reports"][-1]["accepted"])
        self.assertTrue(trace["dominance_reports"][-1]["semantic_confirmation_audit"])

    def test_g3_accepts_a_merge_requirement_proved_by_an_intermediate_in_the_final_output_lineage(self):
        command = "merge exclusion inputs, dissolve them into site_exclusion"
        context = {"is_saved": True, "layers": [
            {"layer_ref": "layer:a", "name": "a", "geometry_type": "Polygon",
             "spatial_reference": "EPSG:32650", "fields": []},
            {"layer_ref": "layer:b", "name": "b", "geometry_type": "Polygon",
             "spatial_reference": "EPSG:32650", "fields": []},
        ]}
        task = {
            "input_entities": [
                {"entity_id": "input:a", "role": "source", "kind": "feature_layer",
                 "reference": "layer:a", "evidence": command},
                {"entity_id": "input:b", "role": "source", "kind": "feature_layer",
                 "reference": "layer:b", "evidence": command},
            ],
            "outputs": [{
                "output_id": "output:site_exclusion", "kind": "feature_class",
                "name": "site_exclusion", "format": "shp", "geometry": "polygon",
                "required_fields": [], "spatial_reference": "EPSG:32650",
                "destination": "default", "evidence": "site_exclusion",
            }],
            "requirements": [{
                "requirement_id": "merge_exclusion", "evidence": command,
                "predicate": {
                    "kind": "merge", "subject": "output:site_exclusion",
                    "sources": ["input:a", "input:b"],
                },
            }],
            "allowed_side_effects": ["writes_data", "changes_map"],
            "clarifications": [],
        }
        workflow = {"action": "execute", "summary": command, "steps": [
            {
                "id": "merge", "operation": "analysis.merge",
                "arguments": {
                    "input_layers": ["layer:a", "layer:b"],
                    "output_name": "site_exclusion_merge", "output_format": "shp",
                },
                "reason": command,
            },
            {
                "id": "dissolve", "operation": "analysis.dissolve",
                "arguments": {
                    "input_layer": "from_step:merge", "dissolve_fields": [],
                    "output_name": "site_exclusion", "output_format": "shp",
                },
                "reason": command,
            },
        ]}
        baseline_client = Client([
            {"task_contract": task}, {"workflow_draft": copy.deepcopy(workflow)},
        ])
        baseline = self.engine(lambda p, m: baseline_client)
        source = plan_bound(baseline, command, context, "g2_constrained")
        artifact = source["agent_trace"][0]["run"]["plan_artifact"]
        repair = Client([
            {"workflow_draft": copy.deepcopy(workflow)},
            {"workflow_draft": copy.deepcopy(workflow)},
        ])
        auditor = ProofDrivenAuditor()
        clients = iter((repair, auditor))
        replay = PlanningEngine(OperationCatalog(), baseline.store, lambda p, m: next(clients))
        target = baseline.store.create_run(command, "g3_audited")
        baseline.store.bind_context(target["id"], {
            "context": context, "context_hash": context_hash(context),
            "bridge": {"bridge_pid": 1, "bridge_port": 2, "arcmap_pid": 3, "hwnd": 4},
            "captured_at": 2,
        })

        result = replay.plan_with_artifact(
            target["id"], command, context, "g3_audited", artifact,
            provider="test-provider", model="test-model",
        )

        trace = result["agent_trace"][0]["run"]
        self.assertEqual("planned", result["status"])
        requirement = next(
            item for item in trace["plan_artifact"]["baseline_verifier_report"]["requirements"]
            if item["requirement_id"] == "merge_exclusion"
        )
        self.assertTrue(requirement["satisfied"])
        self.assertEqual("merge", requirement["proof"]["semantic_fact"]["kind"])
        self.assertEqual(["dissolve"], requirement["proof"]["lineage_steps"])
        self.assertFalse(any(
            key.startswith("_")
            for key in requirement["proof"]["semantic_fact"]
        ))
        self.assertEqual(1, len(auditor.calls))
        self.assertEqual(0, len(repair.calls))


if __name__ == "__main__": unittest.main()
