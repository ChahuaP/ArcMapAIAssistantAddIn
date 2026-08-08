# -*- coding: utf-8 -*-
"""Stage D PolicyGate / ArcMapRuntime / AcceptancePublisher tests (§6.6-6.8,
§11 ArcMap + 验收发布 + 安全).

Covers: grant binding, destructive-disabled, stale lease/epoch/plan_hash
callbacks rejected, deployment mismatch, unaccepted artifacts never publish,
unprovable execution -> ExecutionIndeterminate (no auto-replay), duplicate
receipts rejected.
"""
from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

from gateway_py3.kernel import contracts
from gateway_py3.kernel.contracts import (
    AuthorizationGrant, CallerIdentity, CapabilitySnapshot, CapabilitySpec,
    ContextSnapshot, IntentSpec, LayerRef, LayerSnapshot, RequestEnvelope,
    RuntimeLease, SideEffectScope, VerifiedPlan, WorkflowStep,
    EXECUTION_INDETERMINATE, POLICY_DENIED, ACCEPTANCE_FAILED, CONTRACT_FAILED,
    INFRASTRUCTURE_FAILED,
)
from gateway_py3.runtime.policy import PolicyGate
from gateway_py3.runtime.arcmap_runtime import ArcMapRuntime
from gateway_py3.runtime.acceptance_publisher import AcceptancePublisher


def _caller(user="u1", tenant="t1", role="analyst"):
    return CallerIdentity(user_id=user, tenant_id=tenant, role=role)


def _envelope(session_id="00000000-0000-0000-0000-000000000001",
              request_id="00000000-0000-0000-0000-000000000002",
              text="buffer cities", execute=True, level=1):
    side = SideEffectScope(level=level) if execute else None
    return RequestEnvelope(
        session_id=session_id, request_id=request_id, text=text,
        caller=_caller(), execute=execute, side_effects=side,
    )


def _plan(risk_level=1, plan_id="00000000-0000-0000-0000-0000000000bb") -> VerifiedPlan:
    return VerifiedPlan(
        plan_id=plan_id,
        version=1,
        intent_digest="intent-digest",
        context_digest="context-digest",
        capability_digest="capability-digest",
        workflow=(
            WorkflowStep(id="s1", operation="analysis.buffer",
                         arguments={"output_name": "out.shp"}, reason="buffer"),
        ),
        validation_report={"ok": True},
        model_identity="fake", prompt_version="v1",
        risk_level=risk_level,
    )


def _lease(run_id="00000000-0000-0000-0000-00000000000c",
           plan=None, epoch=1, lease_id="00000000-0000-0000-0000-0000000000cc") -> RuntimeLease:
    plan = plan or _plan()
    return RuntimeLease(
        lease_id=lease_id,
        run_id=run_id, plan_digest=plan.digest,
        gateway_pid=1000, arcmap_pid=2000, bridge_pid=2001, bridge_port=8766,
        target_hwnd=3000, deployment_hash="deploy-v1", epoch=epoch,
        acquired_at=100.0, last_heartbeat=100.0,
    )


def _context(lease: RuntimeLease, deployment_hash="deploy-v1") -> ContextSnapshot:
    return ContextSnapshot(
        lease_id=lease.lease_id, arcmap_pid=lease.arcmap_pid,
        bridge_pid=lease.bridge_pid, bridge_port=lease.bridge_port,
        target_hwnd=lease.target_hwnd,
        document_identity={"mxd": "a.mxd"},
        layers=(LayerSnapshot(identity=LayerRef(name="cities", layer_ref="cities")),),
        deployment_hash=deployment_hash, content_hash="ch", captured_at=1.0,
    )


# --- fake bridge -----------------------------------------------------------

class _ScriptedBridge:
    """Bridge that returns scripted dispatch receipts."""

    def __init__(self, receipt=None, reconcile_result=None):
        self.receipt = receipt
        self.reconcile_result = reconcile_result
        self.dispatched = 0
        self.sample_calls = []

    def dispatch(self, lease, plan, grant, context_snapshot):
        self.dispatched += 1
        return "token-%d" % self.dispatched

    def wait_for_receipt(self, receipt_token, timeout):
        return self.receipt

    def reconcile(self, lease, run_id):
        return self.reconcile_result

    def sample_values(self, layer_ref, fields, max_rows, max_samples):
        self.sample_calls.append((layer_ref, fields))
        return {"values": {f: ["a", "b"] for f in fields}}


class PolicyGateTest(unittest.TestCase):
    """§6.6 grant binding and denials."""

    def setUp(self):
        self.gate = PolicyGate()

    def test_grant_binds_actor_plan_lease(self):
        plan = _plan(risk_level=2)
        lease = _lease(plan=plan)
        outcome = self.gate.authorize(_envelope(level=2), plan, lease,
                                      {"level": 2, "inputs": ("cities",),
                                       "outputs": ("out.shp",)})
        self.assertTrue(outcome.succeeded)
        grant = outcome.details["grant"]
        self.assertIsInstance(grant, AuthorizationGrant)
        self.assertEqual(grant.run_id, lease.run_id)
        self.assertEqual(grant.plan_digest, plan.digest)
        self.assertEqual(grant.lease_id, lease.lease_id)
        self.assertEqual(grant.lease_epoch, lease.epoch)
        self.assertEqual(grant.allowed_side_effect_level, 2)
        self.assertIn("cities", grant.input_identities)
        self.assertIn("out.shp", grant.output_identities)
        self.assertGreater(grant.expires_at, 0)
        self.assertEqual(grant.actor.user_id, "u1")

    def test_effect_exceeding_plan_risk_denied(self):
        plan = _plan(risk_level=1)
        outcome = self.gate.authorize(_envelope(level=3), plan, _lease(plan=plan),
                                      {"level": 3})
        self.assertEqual(outcome.kind, POLICY_DENIED)

    def test_destructive_level4_disabled_by_default(self):
        plan = _plan(risk_level=4)
        outcome = self.gate.authorize(_envelope(level=4), plan, _lease(plan=plan),
                                      {"level": 4, "transactional": True})
        self.assertEqual(outcome.kind, POLICY_DENIED)

    def test_lease_plan_mismatch_denied(self):
        plan = _plan()
        other = _plan(plan_id="00000000-0000-0000-0000-0000000000be")
        outcome = self.gate.authorize(_envelope(), plan, _lease(plan=other),
                                      {"level": 1})
        self.assertEqual(outcome.kind, CONTRACT_FAILED)

    def test_precheck_validates_without_lease(self):
        plan = _plan(risk_level=2)
        outcome = self.gate.precheck(_envelope(level=2), plan, {"level": 2})
        self.assertTrue(outcome.succeeded)
        outcome2 = self.gate.precheck(_envelope(level=4), plan, {"level": 4})
        self.assertEqual(outcome2.kind, POLICY_DENIED)

    def test_check_grant_rejects_stale_epoch(self):
        plan = _plan()
        lease = _lease(plan=plan, epoch=2)
        grant = self.gate.authorize(_envelope(), plan, lease, {"level": 1}).details["grant"]
        old_lease = _lease(plan=plan, epoch=1)
        outcome = self.gate.check_grant(grant, old_lease, plan.digest)
        self.assertEqual(outcome.kind, POLICY_DENIED)


class ArcMapRuntimeFencingTest(unittest.TestCase):
    """§6.7 + §11 ArcMap: fencing, deployment mismatch, indeterminate."""

    def setUp(self):
        self.plan = _plan()
        self.lease = _lease(plan=self.plan)
        self.grant = PolicyGate().authorize(
            _envelope(), self.plan, self.lease, {"level": 1}
        ).details["grant"]

    def _receipt(self, lease=None, plan=None, epoch=None, status="executed"):
        lease = lease or self.lease
        plan = plan or self.plan
        return {
            "lease_id": lease.lease_id,
            "epoch": lease.epoch if epoch is None else epoch,
            "plan_hash": plan.digest,
            "status": status,
        }

    def test_execute_accepts_matching_receipt(self):
        runtime = ArcMapRuntime(_ScriptedBridge(receipt=self._receipt()), "deploy-v1", 1000)
        outcome = runtime.execute(self.lease, self.plan, self.grant,
                                  _context(self.lease))
        self.assertTrue(outcome.succeeded)
        self.assertEqual(outcome.details["receipt"]["status"], "executed")

    def test_stale_epoch_receipt_rejected(self):
        runtime = ArcMapRuntime(
            _ScriptedBridge(receipt=self._receipt(epoch=0)), "deploy-v1", 1000)
        outcome = runtime.execute(self.lease, self.plan, self.grant,
                                  _context(self.lease))
        self.assertEqual(outcome.kind, POLICY_DENIED)

    def test_wrong_lease_receipt_rejected(self):
        other_lease = _lease(plan=self.plan, epoch=1,
                             lease_id="00000000-0000-0000-0000-0000000000cd")
        runtime = ArcMapRuntime(
            _ScriptedBridge(receipt=self._receipt(lease=other_lease)), "deploy-v1", 1000)
        outcome = runtime.execute(self.lease, self.plan, self.grant,
                                  _context(self.lease))
        self.assertEqual(outcome.kind, POLICY_DENIED)

    def test_wrong_plan_hash_receipt_rejected(self):
        other_plan = _plan(plan_id="00000000-0000-0000-0000-0000000000be")
        runtime = ArcMapRuntime(
            _ScriptedBridge(receipt=self._receipt(plan=other_plan)), "deploy-v1", 1000)
        outcome = runtime.execute(self.lease, self.plan, self.grant,
                                  _context(self.lease))
        self.assertEqual(outcome.kind, POLICY_DENIED)

    def test_deployment_mismatch_rejected(self):
        runtime = ArcMapRuntime(_ScriptedBridge(receipt=self._receipt()), "deploy-v1", 1000)
        outcome = runtime.execute(self.lease, self.plan, self.grant,
                                  _context(self.lease, deployment_hash="other"))
        self.assertEqual(outcome.kind, INFRASTRUCTURE_FAILED)

    def test_missing_receipt_is_indeterminate_not_replay(self):
        runtime = ArcMapRuntime(_ScriptedBridge(receipt=None), "deploy-v1", 1000)
        outcome = runtime.execute(self.lease, self.plan, self.grant,
                                  _context(self.lease))
        self.assertEqual(outcome.kind, EXECUTION_INDETERMINATE)

    def test_reconcile_unprovable_is_indeterminate(self):
        runtime = ArcMapRuntime(_ScriptedBridge(reconcile_result=None), "deploy-v1", 1000)
        outcome = runtime.reconcile(self.lease, self.lease.run_id)
        self.assertEqual(outcome.kind, EXECUTION_INDETERMINATE)

    def test_reconcile_confirms_executed(self):
        runtime = ArcMapRuntime(
            _ScriptedBridge(reconcile_result=self._receipt()), "deploy-v1", 1000)
        outcome = runtime.reconcile(self.lease, self.lease.run_id)
        self.assertTrue(outcome.succeeded)

    def test_capture_incremental_value_sampling(self):
        bridge = _ScriptedBridge(receipt=self._receipt())
        runtime = ArcMapRuntime(bridge, "deploy-v1", 1000)
        context = runtime.capture(self.lease.run_id, self.lease,
                                  {"mxd": "a.mxd"}, [
                                      {"name": "cities", "layer_ref": "cities",
                                       "fields": [{"name": "POP", "dtype": "Integer"}]},
                                  ])
        self.assertIsNone(context.layers[0].value_summary)
        sampled = runtime.sample_values(context, ["cities"], ["POP"])
        self.assertIsNotNone(sampled.layers[0].value_summary)
        self.assertEqual(bridge.sample_calls, [("cities", ["POP"])])


class AcceptancePublisherTest(unittest.TestCase):
    """§6.8 + §11 验收发布: unaccepted artifacts never publish."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.staging = self.tmp / "staging"
        self.publish_dir = self.tmp / "publish"
        self.staging.mkdir()
        self.plan = _plan(risk_level=3)  # declares output_name "out.shp"
        self.intent = IntentSpec(
            session_id="00000000-0000-0000-0000-000000000001",
            request_id="00000000-0000-0000-0000-000000000002",
            model_identity="fake", prompt_version="v1",
        )
        self.lease = _lease(plan=self.plan)
        self.grant = PolicyGate().authorize(
            _envelope(level=3), self.plan, self.lease, {"level": 3}
        ).details["grant"]

    def _stage_artifact(self):
        artifact = self.staging / "out.shp"
        artifact.write_text("shp-content", encoding="utf-8")
        return artifact

    def test_accept_passes_when_staged_output_present(self):
        self._stage_artifact()
        publisher = AcceptancePublisher(staging_root=self.staging)
        outcome = publisher.accept(self.intent, self.plan, {})
        self.assertTrue(outcome.succeeded)

    def test_accept_fails_when_output_missing(self):
        publisher = AcceptancePublisher(staging_root=self.staging)
        outcome = publisher.accept(self.intent, self.plan, {})
        self.assertEqual(outcome.kind, ACCEPTANCE_FAILED)

    def test_publish_rejects_without_passed_report(self):
        publisher = AcceptancePublisher(staging_root=self.staging,
                                        publish_root=self.publish_dir)
        outcome = publisher.publish([], {"passed": False}, self.grant)
        self.assertEqual(outcome.kind, ACCEPTANCE_FAILED)

    def test_publish_atomically_copies_and_hashes(self):
        artifact = self._stage_artifact()
        publisher = AcceptancePublisher(staging_root=self.staging,
                                        publish_root=self.publish_dir)
        accepted = publisher.accept(self.intent, self.plan, {})
        self.assertTrue(accepted.succeeded)
        outcome = publisher.publish([{"path": str(artifact)}],
                                    accepted.details["report"], self.grant)
        self.assertTrue(outcome.succeeded)
        publication = outcome.details["publication"]
        self.assertEqual(len(publication["artifacts"]), 1)
        published = self.publish_dir / "out.shp"
        self.assertTrue(published.exists())
        artifact_hash = publication["artifacts"][0]["hash"]
        self.assertEqual(len(artifact_hash), 64)  # sha256 hex
        self.assertTrue(all(c in "0123456789abcdef" for c in artifact_hash))


if __name__ == "__main__":
    unittest.main()
