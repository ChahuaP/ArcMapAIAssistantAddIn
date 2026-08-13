"""Closed G3 revision contract and monotonicity tests."""
from __future__ import annotations

import unittest

from gateway_py3.plan_revision import (
    MonotonicPlanValidator, PlanRevision, PlanRevisionError, revision_scope,
)
from gateway_py3.intelligence.workflow_engine import WorkflowEngine
from gateway_py3.kernel import contracts


class _Catalog:
    capabilities = {
        "analysis.buffer": {"parameters_schema": {"properties": {
            "distance": {}, "input_layer": {"x-geopilot-kind": "layer"},
            "output_name": {},
        }}},
    }


def _workflow(distance=10):
    return {"action": "execute", "summary": "buffer", "steps": [{
        "id": "step_1", "operation": "analysis.buffer",
        "arguments": {"distance": distance, "input_layer": "roads", "output_name": "buffer"},
        "reason": "required",
    }]}


def _report(status="Unresolved", proof_id="proof:distance", effects=()):
    return {"side_effects": list(effects), "proof_graph": [{
        "proof_id": proof_id, "status": status, "subject": "step_1",
        "detail": {"step_id": "step_1"},
    }]}


class PlanRevisionTest(unittest.TestCase):
    def setUp(self):
        self.workflow = _workflow()
        self.baseline = _report()
        self.scope = revision_scope(self.workflow, self.baseline, _Catalog())

    def test_scope_excludes_input_output_and_whole_dictionaries(self):
        self.assertEqual(self.scope, {"proof:distance": ("arguments.distance",)})
        with self.assertRaises(PlanRevisionError):
            PlanRevision("proof:distance", "step_1", "arguments", {"distance": 20})

    def test_rejects_illegal_proof_step_and_path(self):
        for revision in (
            PlanRevision("unknown", "step_1", "arguments.distance", 20),
            PlanRevision("proof:distance", "other", "arguments.distance", 20),
            PlanRevision("proof:distance", "step_1", "arguments.output_name", "other"),
        ):
            with self.assertRaises(PlanRevisionError):
                MonotonicPlanValidator.apply(self.workflow, revision, self.scope)

    def test_accepts_one_legal_scalar_revision(self):
        candidate = MonotonicPlanValidator.apply(
            self.workflow, PlanRevision("proof:distance", "step_1", "arguments.distance", 20), self.scope)
        self.assertEqual(candidate["steps"][0]["arguments"]["distance"], 20)
        self.assertEqual(self.workflow["steps"][0]["arguments"]["distance"], 10)

    def test_monotonic_guard_rejects_no_improvement_new_obligation_and_risk(self):
        candidate = _workflow(20)
        for report in (
            _report(),
            {"side_effects": [], "proof_graph": [
                {"proof_id": "proof:distance", "status": "Proven"},
                {"proof_id": "new", "status": "Unresolved"},
            ]},
            _report("Proven", effects=("writes_data",)),
        ):
            with self.assertRaises(PlanRevisionError):
                MonotonicPlanValidator.validate(self.workflow, candidate, self.baseline, report, "proof:distance",
                                                PlanRevision("proof:distance", "step_1", "arguments.distance", 20))

    def test_monotonic_guard_accepts_strictly_resolved_proof(self):
        candidate = _workflow(20)
        report = _report("Proven")
        MonotonicPlanValidator.validate(self.workflow, candidate, self.baseline, report, "proof:distance",
                                        PlanRevision("proof:distance", "step_1", "arguments.distance", 20))

    def test_clarify_routes_to_the_kernel_clarification_outcome(self):
        state = {"audit_decision": "clarify", "audit_result": {
            "clarification": {"option_id": "quantity.unit", "question": "缓冲距离单位是什么？"},
        }, "audit_options": {"quantity.unit": ("proof:distance",)}}
        self.assertEqual(WorkflowEngine._route_audit(None, state), "fail")
        self.assertEqual(state["failure"]["kind"], contracts.CLARIFICATION_REQUIRED)
        self.assertEqual(state["failure"]["details"]["clarifications"][0]["proof_ids"], ["proof:distance"])

    def test_audit_trigger_skips_simple_read_only_plan(self):
        state = {"auditor_enabled": True, "audit_forced": False,
                 "intent": {"acceptable_side_effects": 1},
                 "workflow": {"steps": [{"id": "step_1"}]},
                 "report": {"proof_graph": [{"status": "Proven"}],
                            "side_effects": ["read_only"], "requirements": []}}
        self.assertFalse(WorkflowEngine._should_audit(state))

    def test_audit_trigger_covers_unresolved_risk_effects_and_complex_lineage(self):
        base = {"auditor_enabled": True, "audit_forced": False,
                "intent": {"acceptable_side_effects": 1},
                "workflow": {"steps": [{"id": "step_1"}]},
                "report": {"proof_graph": [{"status": "Proven"}],
                           "side_effects": ["read_only"], "requirements": []}}
        cases = [
            {"report": {"proof_graph": [{"status": "Unresolved"}], "side_effects": ["read_only"], "requirements": []}},
            {"intent": {"acceptable_side_effects": 2}},
            {"report": {"proof_graph": [{"status": "Proven"}], "side_effects": ["changes_map"], "requirements": []}},
            {"workflow": {"steps": [{"id": "step_1"}, {"id": "step_2"}]}},
            {"report": {"proof_graph": [{"status": "Proven"}], "side_effects": ["read_only"],
                         "requirements": [{"proof": {"lineage_steps": ["step_1", "step_2"]}}]}},
        ]
        for patch in cases:
            state = dict(base)
            state.update(patch)
            self.assertTrue(WorkflowEngine._should_audit(state))

    def test_experiment_force_audit_overrides_low_risk_skip_but_g2_disables(self):
        state = {"auditor_enabled": True, "audit_forced": True,
                 "intent": {"acceptable_side_effects": 1}, "workflow": {"steps": []},
                 "report": {"proof_graph": [], "side_effects": [], "requirements": []}}
        self.assertTrue(WorkflowEngine._should_audit(state))
        state["auditor_enabled"] = False
        self.assertFalse(WorkflowEngine._should_audit(state))


if __name__ == "__main__":
    unittest.main()
