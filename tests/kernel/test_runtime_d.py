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
    RuntimeLease, SideEffectScope, VerifiedPlan, WorkflowStep, DeclaredOutput, ArtifactIdentity,
    EXECUTION_INDETERMINATE, POLICY_DENIED, ACCEPTANCE_FAILED, CONTRACT_FAILED,
    INFRASTRUCTURE_FAILED,
)
from gateway_py3.runtime.policy import PolicyGate
from gateway_py3.runtime.arcmap_runtime import ArcMapRuntime
from gateway_py3.runtime.acceptance_publisher import AcceptancePublisher
from gateway_py3.kernel.coordinator import GeoPilotKernel
from gateway_py3.kernel.coordinator import KernelPorts
from gateway_py3.kernel.store import JournalStore


def _caller(user="u1", tenant="t1", role="analyst"):
    return CallerIdentity(user_id=user, tenant_id=tenant, role=role)


def _envelope(session_id="00000000-0000-0000-0000-000000000001",
              request_id="00000000-0000-0000-0000-000000000002",
              text="buffer cities", execute=True, level=1):
    side = SideEffectScope(level=level) if execute else None
    return RequestEnvelope(
        session_id=session_id, request_id=request_id, text=text,
        caller=_caller(), execute=execute, side_effects=side,
        target_selector={"bridge_pid": 2001, "bridge_port": 8766,
                         "arcmap_pid": 2000, "hwnd": 3000,
                         "deployment_hash": "a" * 64} if execute else {},
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
                         arguments={"output_name": "out"}, reason="buffer",
                         declared_outputs=(DeclaredOutput(output_id="out-1", name="out",
                             kind="feature_class", destination="C:\\publish\\out.gdb\\out",
                             geometry_type="Polygon", expected_fields=("NAME",)),)),
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

    def sample_values(self, lease, layer_ref, fields, max_rows, max_samples):
        self.sample_calls.append((lease, layer_ref, fields))
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
                                       "outputs": ({"output_id": "out-1", "destination": "C:\\out\\out.shp"},)})
        self.assertTrue(outcome.succeeded)
        grant = outcome.details["grant"]
        self.assertIsInstance(grant, AuthorizationGrant)
        self.assertEqual(grant.run_id, lease.run_id)
        self.assertEqual(grant.plan_digest, plan.digest)
        self.assertEqual(grant.lease_id, lease.lease_id)
        self.assertEqual(grant.lease_epoch, lease.epoch)
        self.assertEqual(grant.allowed_side_effect_level, 2)
        self.assertIn("cities", grant.input_identities)
        self.assertIn(("out-1", "C:\\out\\out.shp"), grant.output_identities)
        self.assertGreater(grant.expires_at, 0)
        self.assertEqual(grant.actor.user_id, "u1")

    def test_effect_exceeding_plan_risk_denied(self):
        # Policy direction (§6.6): the requested effect level must cover the
        # plan's risk. level < plan.risk_level is denied; a high level against
        # a low-risk plan is fine.
        plan = _plan(risk_level=3)
        outcome = self.gate.authorize(_envelope(level=1), plan, _lease(plan=plan),
                                      {"level": 1})
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
        sampled = runtime.sample_values(self.lease, context, ["cities"], ["POP"])
        self.assertIsNotNone(sampled.layers[0].value_summary)
        self.assertEqual(len(bridge.sample_calls), 1)
        self.assertIs(bridge.sample_calls[0][0], self.lease)
        self.assertEqual(bridge.sample_calls[0][1:], ("cities", ["POP"]))

    def test_lazy_sampling_fails_closed_when_bridge_has_no_values(self):
        class MissingSamples(object):
            def sample_values(self, lease, layer_ref, fields, max_rows, max_samples):
                return {}
        runtime = ArcMapRuntime(MissingSamples(), "deploy-v1", 1000)
        context = runtime.capture(self.lease.run_id, self.lease, {"mxd": "a.mxd"}, [
            {"name": "cities", "layer_ref": "cities",
             "fields": [{"name": "POP", "dtype": "Integer"}]},
        ])
        with self.assertRaisesRegex(RuntimeError, "no authoritative lazy samples"):
            runtime.sample_values(self.lease, context, ["cities"], ["POP"])


class AcceptancePublisherTest(unittest.TestCase):
    """§6.8 + §11 验收发布: unaccepted artifacts never publish."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.staging = self.tmp / "staging"
        self.publish_dir = self.tmp / "publish"
        self.staging.mkdir()
        self.plan = _plan(risk_level=3)
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
        artifact = self.staging / "out.gdb"
        artifact.write_text("shp-content", encoding="utf-8")
        return artifact

    def _probe(self, artifact, digest=""):
        probe = {"output_id": "out-1", "kind": "feature_class", "canonical_path": str(artifact),
                 "exists": True, "geometry": "Polygon", "spatial_reference": "WGS84",
                 "fields": ["NAME"], "feature_count": 1,
                 "members": [{"relative_path": "out.gdb", "size": 11, "sha256": "a" * 64}]}
        from gateway_py3.runtime.acceptance_publisher import _canonical_json
        import hashlib
        probe["manifest_digest"] = digest or hashlib.sha256(
            _canonical_json(probe).encode("utf-8")).hexdigest()
        return probe

    def _unit_probe(self, source, members):
        probe = {"probe_type": "unit", "source_publish_unit_path": str(source),
                 "datasets": ["out"], "members": members}
        from gateway_py3.runtime.acceptance_publisher import _canonical_json
        import hashlib
        probe["manifest_digest"] = hashlib.sha256(_canonical_json(probe).encode("utf-8")).hexdigest()
        return probe

    def test_accept_passes_when_staged_output_present(self):
        source_gdb = self.staging / "staged.gdb"
        source_gdb.mkdir()
        (source_gdb / "a00000001.gdbtable").write_bytes(b"gdb-content")
        publisher = AcceptancePublisher()
        artifact = source_gdb / "out"
        identity = ArtifactIdentity(output_id="out-1", kind="feature_class",
            logical_dataset_path=str(artifact), source_publish_unit_path=str(source_gdb),
            destination_dataset_path="C:\\publish\\out.gdb\\out",
            destination_publish_unit_path="C:\\publish\\out.gdb")
        output_probe = self._probe(artifact)
        outcome = publisher.accept(self.intent, self.plan, [output_probe, self._unit_probe(source_gdb, output_probe["members"])], [identity])
        self.assertTrue(outcome.succeeded)

    def test_accept_fails_when_output_missing(self):
        publisher = AcceptancePublisher()
        outcome = publisher.accept(self.intent, self.plan, [], [])
        self.assertEqual(outcome.kind, ACCEPTANCE_FAILED)

    def _map_plan(self):
        output = DeclaredOutput(output_id="map-1", name="map", kind="map_state",
                                destination="not_applicable")
        step = WorkflowStep(id="map", operation="layer.set_visibility",
                            arguments={"layer": "layer:0", "visible": True},
                            reason="show layer", declared_outputs=(output,))
        return self.plan.model_copy(update={"workflow": (step,)})

    def _map_probe(self, plan, passed=True, digest=None):
        from gateway_py3.catalog_loader import OperationCatalog
        from gateway_py3.runtime.acceptance_publisher import _canonical_json
        condition = OperationCatalog().get("layer.set_visibility")["capability_contract"]["postconditions"][0]
        probe = {"probe_type": "map_state", "output_id": "map-1", "kind": "map_state",
                 "postcondition": condition, "arguments": plan.workflow[0].arguments,
                 "map_state": {"active_view": "Layers", "extent": {}, "layers": []},
                 "map_state_check": {"kind": condition["kind"], "verdict": "passed" if passed else "failed"},
                 "passed": passed}
        import hashlib
        probe["manifest_digest"] = digest or hashlib.sha256(_canonical_json(probe).encode("utf-8")).hexdigest()
        return probe

    def test_map_state_probe_accepts_live_postcondition(self):
        outcome = AcceptancePublisher().accept(self.intent, self._map_plan(),
                                               [self._map_probe(self._map_plan())], [])
        self.assertTrue(outcome.succeeded)

    def test_map_state_probe_rejects_failed_live_postcondition(self):
        plan = self._map_plan()
        outcome = AcceptancePublisher().accept(self.intent, plan, [self._map_probe(plan, passed=False)], [])
        self.assertEqual(outcome.kind, ACCEPTANCE_FAILED)

    def test_map_state_probe_rejects_tampered_digest(self):
        plan = self._map_plan()
        outcome = AcceptancePublisher().accept(self.intent, plan, [self._map_probe(plan, digest="0" * 64)], [])
        self.assertEqual(outcome.kind, ACCEPTANCE_FAILED)

    def test_publish_filegdb_logical_dataset_as_one_atomic_unit(self):
        source_gdb = self.staging / "staged.gdb"
        source_gdb.mkdir()
        (source_gdb / "a00000001.gdbtable").write_bytes(b"gdb-content")
        target_gdb = self.publish_dir / "published.gdb"
        output = self.plan.workflow[0].declared_outputs[0].model_copy(update={
            "kind": "feature_class", "destination": str(target_gdb / "roads"),
        })
        plan = self.plan.model_copy(update={
            "workflow": (self.plan.workflow[0].model_copy(update={"declared_outputs": (output,)}),),
        })
        logical_path = source_gdb / "roads"
        members = [{"relative_path": "a00000001.gdbtable", "size": 11,
                    "sha256": __import__("hashlib").sha256(b"gdb-content").hexdigest()}]
        probe = {"output_id": "out-1", "kind": "feature_class",
                 "canonical_path": str(logical_path), "exists": True,
                 "geometry": "Polygon", "spatial_reference": "WGS84",
                 "fields": ["NAME"], "feature_count": 1, "members": members}
        from gateway_py3.runtime.acceptance_publisher import _canonical_json
        probe["manifest_digest"] = __import__("hashlib").sha256(
            _canonical_json(probe).encode("utf-8")).hexdigest()
        artifact = ArtifactIdentity(output_id="out-1", kind="feature_class",
            logical_dataset_path=str(logical_path), source_publish_unit_path=str(source_gdb),
            destination_dataset_path=str(target_gdb / "roads"),
            destination_publish_unit_path=str(target_gdb))
        publisher = AcceptancePublisher()
        accepted = publisher.accept(self.intent, plan, [probe, self._unit_probe(source_gdb, members)], [artifact])
        self.assertTrue(accepted.succeeded)
        grant = PolicyGate().authorize(
            _envelope(level=3), plan, self.lease.model_copy(update={"plan_digest": plan.digest}),
            {"level": 3, "outputs": ({"output_id": "out-1", "destination": str(target_gdb / "roads")},)}
        ).details["grant"]
        prepared = publisher.prepare([artifact], accepted.details["report"], grant,
                                     "00000000-0000-0000-0000-0000000000cc")
        self.assertTrue(prepared.succeeded)
        self.assertTrue(publisher.materialize(prepared.details["publication"], [artifact]).succeeded)
        outcome = publisher.commit(prepared.details["publication"])
        self.assertTrue(outcome.succeeded)
        self.assertEqual((target_gdb / "a00000001.gdbtable").read_bytes(), b"gdb-content")


class AuthorizationOutputIdentityTest(unittest.TestCase):
    def test_approval_cannot_omit_or_redirect_sealed_output(self):
        plan = _plan(risk_level=3)
        exact = SideEffectScope(level=3, output_identities=(("out-1", "C:\\publish\\out.gdb\\out"),))
        GeoPilotKernel._validate_approved_outputs(plan, exact)
        with self.assertRaises(ValueError):
            GeoPilotKernel._validate_approved_outputs(plan, SideEffectScope(level=3))
        with self.assertRaises(ValueError):
            GeoPilotKernel._validate_approved_outputs(
                plan, SideEffectScope(level=3, output_identities=(("out-1", "C:\\other\\out.gdb\\out"),)))


class PublicationRecoveryGrantBindingTest(unittest.TestCase):
    def test_prepared_publication_recovers_after_grant_expiry_without_rechecking_policy(self):
        store = JournalStore(path=Path(tempfile.mkdtemp()) / "gp.sqlite")
        request = _envelope()
        store.create_session(request.session_id, request.caller.tenant_id)
        run_id = store.create_run(request)["run_id"]
        for kind, stage in (("context_leased", "context_leased"), ("context_frozen", "context_frozen"),
                            ("intent_compiled", "intent_compiled"), ("plan_verified", "plan_verified"),
                            ("authorization_required", "authorization_required"), ("authorization_approved", "authorized"),
                            ("runtime_acquired", "runtime_acquired"), ("execution_started", "executing"),
                            ("executed", "executed"), ("accepted", "accepted")):
            store.append_event(run_id, kind, stage, {})
        plan = _plan()
        lease = _lease(run_id=run_id, plan=plan)
        grant = PolicyGate().authorize(request, plan, lease, {"level": 1}).details["grant"].model_copy(
            update={"expires_at": 1.0})
        store.store_verified_plan(run_id, plan)
        store.store_runtime_lease(lease)
        store.store_authorization_grant(grant)
        prepared = {
            "publication_id": "00000000-0000-0000-0000-0000000000dd",
            "publication_kind": "state_change", "run_id": run_id,
            "grant_id": grant.grant_id, "artifacts": [],
        }
        store.prepare_publication(run_id, prepared)

        class _PolicyMustNotRun(object):
            def check_grant(self, *args):
                raise AssertionError("prepared recovery must use its frozen grant binding")

        class _RecoverOnly(object):
            def __init__(self):
                self.prepared = None
            def recover(self, frozen, staged, report, frozen_grant):
                self.prepared = (frozen, frozen_grant)
                return contracts.outcome_succeeded("publish", "recovered", details={"publication": frozen})

        publisher = _RecoverOnly()
        kernel = GeoPilotKernel(KernelPorts(store=store, policy=_PolicyMustNotRun(), acceptance=publisher))
        kernel._publish(run_id, store.get_run(run_id))

        self.assertEqual("published", store.get_run(run_id)["stage"])
        self.assertEqual(prepared, publisher.prepared[0])
        self.assertEqual(1.0, publisher.prepared[1].expires_at)


if __name__ == "__main__":
    unittest.main()
