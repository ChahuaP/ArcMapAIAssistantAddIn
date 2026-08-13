from __future__ import annotations

import tempfile
import unittest
import uuid
import json
from pathlib import Path
from types import SimpleNamespace

from gateway_py3.experiments.supervisor import ExperimentSupervisor
from gateway_py3.kernel.contracts import Outcome, TargetSelector, VerifiedPlan, WorkflowStep
from gateway_py3.model_runtime.contracts import AGENT_ROLES


def _identity(role: str) -> dict:
    return {
        "connection_id": "minimax-primary",
        "provider": "minimax",
        "model": "MiniMax-M3",
        "endpoint_fingerprint": "endpoint-digest",
        "deployment_fingerprint": "deployment-digest",
        "credential_ref": "credential:minimax",
        "role": role,
        "parameters": {"temperature": 0.0, "max_output_tokens": 1024},
        "token_plan": {"call_budget": 2, "context_token_limit": 1024,
                       "output_token_limit": 1024, "concurrency_limit": 1,
                       "requests_per_minute": 2, "tokens_per_minute": 2048,
                       "cost_limit_microusd": None},
        "adapter_type": "tests.local.NeverPersistThis",
    }


class _Port:
    @property
    def runtime_identity(self):
        return {role: _identity(role) for role in AGENT_ROLES}


class ExperimentSupervisorBindingEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.supervisor = object.__new__(ExperimentSupervisor)
        self.supervisor._port = _Port()

    def test_binding_summary_is_the_exact_nine_field_call_evidence(self):
        summary = self.supervisor._formal_binding_summary()
        required = {
            "connection_id", "provider", "model", "endpoint_fingerprint",
            "deployment_fingerprint", "credential_ref", "role", "parameters",
            "token_plan",
        }
        self.assertEqual(set(AGENT_ROLES), set(summary))
        for role, evidence in summary.items():
            self.assertEqual(required, set(evidence))
            self.assertEqual(role, evidence["role"])
            self.assertNotIn("adapter_type", evidence)

    def test_runtime_identity_property_does_not_leak_adapter_type(self):
        identity = self.supervisor.runtime_identity
        self.assertTrue(all("adapter_type" not in value for value in identity.values()))

    def test_export_seals_the_canonical_summary_without_adapter_type(self):
        class Port(_Port):
            def export_run_journal(self, run_id):
                return {
                    "request_envelope": {"experiment": {"pair_id": "pair"}},
                    "model_calls": [],
                }

        with tempfile.TemporaryDirectory() as temporary:
            supervisor = object.__new__(ExperimentSupervisor)
            supervisor._port = Port()
            supervisor.campaign_root = Path(temporary)
            supervisor._campaign = "campaign"
            (supervisor.campaign_root / supervisor._campaign).mkdir()
            exported = json.loads(supervisor.export_evidence("run").read_text(encoding="utf-8"))

        self.assertEqual(supervisor._formal_binding_evidence(), exported["runtime_identity"])
        self.assertTrue(all("adapter_type" not in value
                            for value in exported["runtime_identity"].values()))

    def test_run_pair_seals_canonical_nine_field_summary_into_both_requests(self):
        pair_id = str(uuid.uuid4())
        plan = VerifiedPlan(
            plan_id=str(uuid.uuid4()), version=1, intent_digest="intent", context_digest="context",
            capability_digest="capability", workflow=(WorkflowStep(
                id="inspect", operation="context.list_layers", arguments={}, reason="test"),),
            validation_report={"valid": True}, model_identity="minimax", prompt_version="test",
        )
        succeeded = Outcome(kind="Succeeded", code="ok", stage="done", message="ok")

        class Port(_Port):
            def __init__(self): self.requests = []
            def submit(self, request):
                self.requests.append(request)
                return SimpleNamespace(run_id="run-" + request.experiment.arm)
            def await_progress(self, run_id, events, timeout):
                arm = run_id.rsplit("-", 1)[-1]
                return SimpleNamespace(
                    run_id=run_id, stage="succeeded", plan=plan, outcome=succeeded,
                    events=[{"kind": "experiment_baseline_frozen", "payload": {}}] if arm == "g2" else
                           [{"kind": "plan_verified", "payload": {
                               "pair_id": pair_id, "topology_signature": "topology",
                               "auditor_enabled": True, "provider": "minimax", "model": "MiniMax-M3",
                               "baseline_digest": "baseline", "task_contract_digest": "task"}}],
                )
            def inspect(self, run_id):
                arm = run_id.rsplit("-", 1)[-1]
                return SimpleNamespace(
                    run_id=run_id, stage="succeeded", plan=plan, outcome=succeeded,
                    events=[{"kind": "experiment_baseline_frozen", "payload": {}}] if arm == "g2" else
                           [{"kind": "plan_verified", "payload": {
                               "pair_id": pair_id, "topology_signature": "topology",
                               "auditor_enabled": True, "provider": "minimax", "model": "MiniMax-M3",
                               "baseline_digest": "baseline", "task_contract_digest": "task"}}],
                )
            def export_run_journal(self, run_id):
                return {"request_envelope": {"experiment": {"pair_id": pair_id}},
                        "model_calls": [], "experiment_baseline": {
                            "intent_digest": "intent", "context_digest": "context",
                            "capability_digest": "capability", "baseline_digest": "baseline",
                            "task_contract": {}}}

        port = Port()
        with tempfile.TemporaryDirectory() as temporary:
            supervisor = object.__new__(ExperimentSupervisor)
            supervisor._port = port
            supervisor.campaign_root = Path(temporary)
            supervisor._campaign = "campaign"
            (supervisor.campaign_root / supervisor._campaign).mkdir()
            selector = TargetSelector(bridge_pid=1, bridge_port=2, arcmap_pid=3, hwnd=4,
                                      deployment_hash="d" * 64)
            result = supervisor.run_pair(
                "task", 1, target_selector=selector, provider="minimax", model="MiniMax-M3",
                prepare_arm=lambda arm: None, pair_id=pair_id,
                arm_artifact_roots={"g2": str(Path(temporary) / "g2-root"),
                                    "g3": str(Path(temporary) / "g3-root")},
            )
        self.assertFalse(result["pair_valid"])
        for request in port.requests:
            self.assertEqual(set(AGENT_ROLES), set(request.model_binding_summary))
            self.assertTrue(all(set(value) == {
                "connection_id", "provider", "model", "endpoint_fingerprint",
                "deployment_fingerprint", "credential_ref", "role", "parameters", "token_plan",
            } for value in request.model_binding_summary.values()))


if __name__ == "__main__":
    unittest.main()
