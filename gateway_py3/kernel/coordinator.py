"""GeoPilotKernel: the sole deep module callers may reach (§6.1).

Drives the Run state machine (§6.1) and returns a unified ``RunView``. The
kernel itself understands no GIS operation, builds no model prompt, scans no
ArcMap port and publishes no file. Each deep collaborator is injected through
a ``Protocol`` so Stage A can wire Fake adapters end-to-end and later stages
swap in real implementations without touching the kernel.
"""
from __future__ import annotations

import time
import uuid
import ntpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from . import contracts
from .contracts import (
    AUTHORIZATION_REQUIRED,
    AUTHORIZED,
    CONTEXT_LEASED,
    CONTEXT_FROZEN,
    INTENT_COMPILED,
    PLAN_VERIFIED,
    RECEIVED,
    RUNTIME_ACQUIRED,
    EXECUTING,
    EXECUTED,
    ACCEPTED,
    PUBLISHED,
    SUCCEEDED_STAGE,
    Outcome,
    RunView,
    RequestEnvelope,
    ContextSnapshot,
    CapabilitySnapshot,
    IntentSpec,
    VerifiedPlan,
    AuthorizationGrant,
    RuntimeLease,
    FieldColumn,
    EvidenceRef,
    SUCCEEDED,
    outcome_succeeded,
    outcome_paused,
    outcome_failed,
)
from .store import JournalStore


# --- deep-module ports (§6) ------------------------------------------------

@runtime_checkable
class ContextProvider(Protocol):
    """§6.7 ArcMapRuntime.capture: freeze a context snapshot for one run."""
    def capture(self, run_id: str, lease: contracts.RuntimeLease) -> contracts.ContextSnapshot: ...


@runtime_checkable
class CapabilityProvider(Protocol):
    """§6.5 CapabilityRegistry: freeze the capability set for one run."""
    def snapshot(self, run_id: str) -> contracts.CapabilitySnapshot: ...


@runtime_checkable
class IntentCompiler(Protocol):
    """§6.2 TaskCompiler: compile a request + context + capabilities into intent."""
    def compile(self, request: RequestEnvelope, context: ContextSnapshot,
                capabilities: CapabilitySnapshot) -> contracts.Outcome: ...


@runtime_checkable
class WorkflowPlanner(Protocol):
    """§6.3 WorkflowEngine: plan, verify and seal a VerifiedPlan.

    ``run_id`` binds the LangGraph thread for checkpointing (§13.2:
    thread_id = run_id); checkpoint persistence happens inside the engine.
    """
    def plan(self, run_id: str, intent: IntentSpec, context: ContextSnapshot,
             capabilities: CapabilitySnapshot) -> contracts.Outcome: ...
    def plan_ablation(self, run_id: str, intent: IntentSpec, context: ContextSnapshot,
                      capabilities: CapabilitySnapshot, auditor_enabled: bool,
                      sealed_baseline: Optional[VerifiedPlan] = None) -> contracts.Outcome: ...
    def decide_authorization(self, run_id: str, approved: bool) -> str: ...


@runtime_checkable
class PolicyGate(Protocol):
    """§6.6 PolicyGate: authorize a sealed plan + requested effects."""
    def authorize(self, actor: RequestEnvelope, plan: VerifiedPlan,
                  requested_effects: Any) -> contracts.Outcome: ...


@runtime_checkable
class ArcMapExecutor(Protocol):
    """§6.7 ArcMapRuntime.execute under a lease."""
    def acquire_lease(self, run_id: str, target_selector: contracts.TargetSelector) -> contracts.RuntimeLease: ...
    def execute(self, lease: RuntimeLease, plan: VerifiedPlan,
                grant: AuthorizationGrant) -> contracts.Outcome: ...
    def reconcile(self, lease: RuntimeLease, run_id: str) -> contracts.Outcome: ...


@runtime_checkable
class AcceptancePublisher(Protocol):
    """§6.8 AcceptancePublisher: accept staged artifacts and publish."""
    def accept(self, intent: IntentSpec, plan: VerifiedPlan,
               probe_documents: Any, staged_artifacts: Any) -> contracts.Outcome: ...
    def prepare(self, staged_artifacts: Any, acceptance_report: Any,
                grant: AuthorizationGrant, publication_id: str) -> contracts.Outcome: ...
    def commit(self, prepared: Dict[str, Any]) -> contracts.Outcome: ...
    def materialize(self, prepared: Dict[str, Any], staged_artifacts: Any) -> contracts.Outcome: ...
    def recover(self, prepared: Dict[str, Any], staged_artifacts: Any,
                acceptance_report: Any, grant: AuthorizationGrant) -> contracts.Outcome: ...


@dataclass(frozen=True)
class KernelPorts:
    """The deep collaborators the kernel drives (§6). Any None port means the
    stage is not yet wired; the kernel will refuse to cross it."""
    store: JournalStore
    context: Optional[ContextProvider] = None
    capabilities: Optional[CapabilityProvider] = None
    compiler: Optional[IntentCompiler] = None
    planner: Optional[WorkflowPlanner] = None
    policy: Optional[PolicyGate] = None
    executor: Optional[ArcMapExecutor] = None
    acceptance: Optional[AcceptancePublisher] = None
    model: Optional[Any] = None
    bridge: Optional[Any] = None


class GeoPilotKernel:
    """The sole public deep module (§3, §6.1).

    Adapters (HTTP, Web, external agent, future ExperimentSupervisor) call
    only ``submit`` / ``inspect`` / ``decide`` / ``resume``. The kernel drives
    the state machine forward until it pauses for a decision, reaches a
    terminal outcome, or finishes.
    """

    def __init__(self, ports: KernelPorts):
        self.ports = ports
        import threading
        import weakref
        self._run_locks = weakref.WeakValueDictionary()
        self._run_locks_guard = threading.Lock()

    def _run_lock(self, run_id: str):
        with self._run_locks_guard:
            lock = self._run_locks.get(run_id)
            if lock is None:
                lock = __import__('threading').RLock()
                self._run_locks[run_id] = lock
            return lock

    # -- public API (§3) ----------------------------------------------------

    def submit(self, request: RequestEnvelope) -> RunView:
        """Create a run and return immediately at ``received`` stage.

        The run advances in a background thread so the HTTP response returns
        instantly and SSE events drive the front-end progress display.
        """
        import threading
        self.ports.store.assert_active_session(request.session_id, request.caller.tenant_id)
        self.ports.store.create_session(request.session_id, request.caller.tenant_id)
        run = self.ports.store.create_run(request)
        self.ports.store.append_event(
            run["run_id"], "user_text", RECEIVED, {"text": request.text}
        )
        # Drive the run forward in a background thread.
        run_id = run["run_id"]
        worker = threading.Thread(
            target=self._drive_lifecycle_safely,
            args=(run_id,),
            name="geopilot-run-" + run_id,
            daemon=True,
        )
        worker.start()
        return self._view(run_id)

    def _drive_lifecycle_safely(self, run_id: str) -> None:
        """Run the outer deterministic lifecycle driver in a worker."""
        from gateway_py3.logs import write_event
        import traceback
        try:
            self._drive_lifecycle(run_id)
        except Exception as exc:
            # ``clear_active_session`` may commit the cancellation between a
            # lifecycle guard and the next append.  That is a successful
            # cancellation race, never an infrastructure failure.
            row = self.ports.store.get_run(run_id)
            if row is not None and row.get("outcome_kind") == contracts.CANCELLED:
                return
            tb = traceback.format_exc()[:500]
            write_event("run.advancer_error", {"run_id": run_id,
                        "error": str(exc)[:200], "type": type(exc).__name__,
                        "traceback": tb})
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "advancer",
                       "advance_failed", "%s: %s" % (type(exc).__name__, str(exc)[:200]))

    def inspect(self, run_id: str) -> RunView:
        return self._view(run_id)

    def experiment_port(self) -> "ExperimentKernelPort":
        """Return the narrow orchestration port used only by formal gates."""
        return ExperimentKernelPort(self)

    def list_runs(self, session_id: str) -> List[RunView]:
        """List recent runs for one session (§5: sessions are isolation boundaries).

        An empty session_id returns an empty list rather than every run — the
        HTTP adapter must require an explicit session.
        """
        if not session_id:
            return []
        return [self._view(run["run_id"])
                for run in self.ports.store.list_recent_runs(session_id=session_id, limit=50)]

    def recover_interrupted_runs(self) -> None:
        """Reconcile runs left active by a prior crash (§7).

        Called once at startup. Stages before the runtime is acquired are pure
        Py3 computation and can be safely re-driven. Runs that reached the
        runtime/execution layer must be reconciled against the Bridge: if the
        Bridge cannot prove execution, the run remains
        ``execution_indeterminate`` and is never replayed or rewritten as an
        infrastructure failure. Orphan ``reserved`` model calls are quarantined as ``uncertain``
        so they are never retried automatically (the call may have been billed).
        """
        from gateway_py3.logs import write_event
        recovered = 0
        for run in self.ports.store.iter_active_runs():
            run_id = run["run_id"]
            stage = run["stage"]
            try:
                if stage == EXECUTING:
                    self._reconcile_runtime_run(run_id)
                else:
                    # Pure Py3 stages (received … plan_verified, authorization,
                    # executed, accepted, published): acceptance and publish
                    # read persisted receipts/artifacts/reports — they do not
                    # need the Bridge. Safe to re-drive the state machine.
                    self._drive_lifecycle(run_id)
                recovered += 1
            except Exception as exc:
                write_event("run.recover_failed", {"run_id": run_id,
                            "stage": stage, "error": str(exc)[:200]})
                self._fail(run_id, contracts.INFRASTRUCTURE_FAILED,
                           "reconcile", "recover_failed",
                           "%s: %s" % (type(exc).__name__, str(exc)[:200]))
        orphaned = self.ports.store.quarantine_reserved_model_calls()
        if recovered or orphaned:
            write_event("run.recovery_summary",
                        {"recovered": recovered, "orphan_model_calls": orphaned})

    def _reconcile_runtime_run(self, run_id: str) -> None:
        """Reconcile a run whose dispatch was recorded but lacked a receipt (§7).

        ``executor.reconcile`` returns one of:
        * paused ``ExecutionIndeterminate`` (recoverable, not terminal) — the
          Bridge cannot prove whether execution happened; fail closed.
        * succeeded with ``details["receipt"]`` — execution was proven; persist
          the receipt and advance to EXECUTED so acceptance runs.
        * terminal failure — the Bridge proved execution failed.
        """
        lease = self.ports.store.get_runtime_lease(run_id)
        if lease is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED,
                       "reconcile", "lease_missing",
                       "重启时找不到运行租约。")
            return
        outcome = self.ports.executor.reconcile(lease, run_id)
        if outcome.is_recoverable:
            # ExecutionIndeterminate: cannot prove whether execution happened.
            # Keep the run paused at execution_indeterminate so a human can
            # adjudicate — do NOT rewrite it as InfrastructureFailed (that
            # would lose the recoverable semantics and the audit trail). The
            # run never auto-replays (§7).
            self.ports.store.append_event(
                run_id, "execution_indeterminate", "execution_indeterminate",
                {"reason": outcome.message}, outcome=outcome,
            )
            return
        if not outcome.succeeded:
            self._fail(run_id, outcome.kind, "reconcile",
                       outcome.code or "runtime_failed", outcome.message)
            return
        # Proven executed: persist the receipt, then advance into acceptance.
        receipt_doc = outcome.details.get("receipt") if outcome.details else None
        if not isinstance(receipt_doc, dict):
            paused = outcome_paused(contracts.EXECUTION_INDETERMINATE, "reconcile", "receipt_missing",
                                    "重启恢复未获得权威执行回执。")
            self.ports.store.append_event(run_id, "execution_indeterminate", "execution_indeterminate",
                                          {"reason": paused.message}, outcome=paused)
            return
        self._ingest_execution_receipt(run_id, lease, receipt_doc)
        plan = self._load_plan(run_id)
        self.ports.store.append_event(
            run_id, "executed", EXECUTED,
            {"summary": outcome.message, "reconciled": True},
            clear_outcome=True,
        )
        self._drive_lifecycle(run_id)

    # -- lease callbacks (§3.2 fencing protocol) ---------------------------
    #
    # The HTTP adapter forwards Bridge callbacks here so it never touches the
    # store directly. Each method enforces the lease fencing triple
    # (lease_id + epoch + plan_hash) before mutating state.

    def receive_receipt_callback(self, run_id: str, payload: Dict[str, Any]) -> None:
        """Bridge posts an execution receipt (lease protocol §3.2)."""
        self.ports.store.assert_run_in_active_session(run_id)
        lease = self._fence_lease(run_id, payload)
        if payload.get("deployment_hash") != lease.deployment_hash:
            raise ValueError("execution receipt deployment hash mismatch")
        receipt = dict(payload)
        result = payload.get("result")
        if isinstance(result, dict):
            receipt.setdefault("message", result.get("summary", ""))
        if receipt.get("plan_hash") != lease.plan_digest:
            raise ValueError("plan hash mismatch")
        accepted = self.ports.bridge.receive_receipt(run_id, receipt) \
            if self.ports.bridge is not None else False
        if not accepted:
            raise ValueError("no waiter for receipt")

    def receive_sample_callback(self, run_id: str, payload: Dict[str, Any]) -> None:
        self.ports.store.assert_run_in_active_session(run_id)
        lease = self._fence_lease(run_id, payload)
        if payload.get("deployment_hash") != lease.deployment_hash:
            raise ValueError("sample deployment hash mismatch")
        layer_ref = payload.get("layer_ref")
        values = payload.get("values")
        if not isinstance(layer_ref, str) or not layer_ref or not isinstance(values, dict):
            raise ValueError("sample callback is malformed")
        accepted = self.ports.bridge.receive_sample(run_id, layer_ref, values) \
            if self.ports.bridge is not None else False
        if not accepted:
            raise ValueError("no waiter for sample")

    def receive_acceptance_probe_callback(self, run_id: str, payload: Dict[str, Any]) -> None:
        """Accept only a fully fenced ArcPy probe callback."""
        self.ports.store.assert_run_in_active_session(run_id)
        lease = self._fence_lease(run_id, payload)
        if payload.get("deployment_hash") != lease.deployment_hash:
            raise ValueError("acceptance probe deployment hash mismatch")
        document = payload.get("document")
        if not isinstance(document, dict):
            raise ValueError("acceptance probe document is required")
        probe_type = document.get("probe_type")
        if probe_type == "unit":
            required = {"probe_type", "source_publish_unit_path", "datasets", "members", "manifest_digest"}
            if set(document) != required or not isinstance(document["datasets"], list) or not isinstance(document["members"], list):
                raise ValueError("unit probe document has an invalid contract")
            units = {item.source_publish_unit_path for item in self.ports.store.list_staged_artifacts(run_id)}
            if len(units) != 1 or document["source_publish_unit_path"] != next(iter(units)):
                raise ValueError("unit probe source publish unit mismatch")
            import hashlib, json
            body = dict(document)
            actual = body.pop("manifest_digest")
            encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if actual != hashlib.sha256(encoded).hexdigest():
                raise ValueError("unit probe manifest digest mismatch")
        elif probe_type is None:
            required = {"output_id", "kind", "canonical_path", "exists", "geometry", "spatial_reference", "fields", "feature_count", "members", "manifest_digest"}
            if set(document) != required or not document.get("output_id"):
                raise ValueError("output probe document has an invalid contract")
        elif probe_type == "map_state":
            required = {"probe_type", "output_id", "kind", "postcondition", "arguments", "map_state", "map_state_check", "passed", "manifest_digest"}
            if set(document) != required or document.get("kind") != "map_state" or not document.get("output_id"):
                raise ValueError("map-state probe document has an invalid contract")
            import hashlib, json
            body = dict(document)
            actual = body.pop("manifest_digest")
            encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if actual != hashlib.sha256(encoded).hexdigest():
                raise ValueError("map-state probe manifest digest mismatch")
        else:
            raise ValueError("unknown acceptance probe type")
        accepted = self.ports.bridge.receive_probe(run_id, document) if self.ports.bridge is not None else False
        if not accepted:
            raise ValueError("no waiter for acceptance probe")

    def heartbeat_callback(self, run_id: str, payload: Dict[str, Any]) -> None:
        """Bridge heartbeat: refresh the lease last_heartbeat."""
        self.ports.store.assert_run_in_active_session(run_id)
        lease = self._fence_lease(run_id, payload)
        updated = lease.model_copy(update={"last_heartbeat": time.time()})
        self.ports.store.store_runtime_lease(updated)

    def context_callback(self, run_id: str, payload: Dict[str, Any]) -> None:
        """Bridge posts the before-planning context snapshot under a lease.

        The payload carries the lease triple (lease_id, epoch, plan_hash) plus
        a ``context`` field with raw context (layers, mxd_path, pids, hwnd,
        content_hash). The lease triple is fenced against the run's context
        lease before the snapshot is accepted.
        """
        self.ports.store.assert_run_in_active_session(run_id)
        if not isinstance(payload, dict) or not payload:
            raise ValueError("empty context payload")
        # Fence the lease triple before trusting the context.
        self._fence_lease(run_id, payload)
        lease = self.ports.store.get_runtime_lease(run_id)
        if lease is None:
            raise ValueError("no context lease bound to run; cannot accept context")
        if payload.get("deployment_hash") != lease.deployment_hash:
            raise ValueError("context deployment hash mismatch")
        # The target identity in the callback must match the leased target —
        # reject a context posted against a different ArcMap window.
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        for field, lease_val in (("arcmap_pid", lease.arcmap_pid),
                                 ("bridge_pid", lease.bridge_pid),
                                 ("bridge_port", lease.bridge_port),
                                 ("hwnd", lease.target_hwnd)):
            cb_val = target.get(field)
            if cb_val is None:
                continue
            try:
                if int(cb_val) != lease_val:
                    raise ValueError("context target %s mismatch: callback=%s lease=%s"
                                     % (field, cb_val, lease_val))
            except (TypeError, ValueError):
                raise ValueError("context target %s is invalid: %s" % (field, cb_val))
        snapshot = _build_context_snapshot_from_payload(payload, lease)
        if snapshot is None:
            raise ValueError("context payload missing required identity fields")
        self.ports.store.store_planning_context_snapshot(run_id, snapshot)
        request = self._reconstruct_request(self.ports.store.get_run(run_id))
        if request.experiment is not None:
            self.ports.store.store_captured_context_snapshot(run_id, snapshot)

    def lease_ack_callback(self, run_id: str,
                           payload: Dict[str, Any]) -> Dict[str, Any]:
        """Bridge acknowledges the lease and fetches the workflow to execute."""
        self.ports.store.assert_run_in_active_session(run_id)
        lease = self._fence_lease(run_id, payload)
        plan = self.ports.store.get_verified_plan(run_id)
        if plan is None:
            raise ValueError("no sealed plan for run")
        context_snapshot = self.ports.store.get_planning_context_snapshot(run_id)
        from ..paths import localappdata_dir
        staging_root = str(localappdata_dir() / "staging" / run_id)
        return {
            "lease": lease.model_dump(mode="json"),
            "run_id": run_id,
            "staging_root": staging_root,
            "workflow": {
                "action": "execute",
                "summary": "",
                "steps": [_runtime_step_document(step) for step in plan.workflow],
            },
            "content_hash": context_snapshot.content_hash if context_snapshot else "",
        }

    def _fence_lease(self, run_id: str, payload: Dict[str, Any]) -> RuntimeLease:
        """Load the lease and verify the full fencing triple from the callback.

        All three fields (lease_id, epoch, plan_hash) are required — no field
        may be missing. A partial triple is rejected (§6.7 fencing).
        """
        lease = self.ports.store.get_runtime_lease(run_id)
        if lease is None:
            raise ValueError("no lease bound to run")
        if payload.get("lease_id") != lease.lease_id:
            raise ValueError("lease fencing mismatch")
        if int(payload.get("epoch", 0)) != lease.epoch:
            raise ValueError("stale epoch")
        plan_hash = payload.get("plan_hash")
        if not plan_hash:
            raise ValueError("plan_hash is required for lease fencing")
        if plan_hash != lease.plan_digest:
            raise ValueError("plan hash mismatch")
        return lease

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Return the raw run row (for adapter session-ownership checks)."""
        return self.ports.store.get_run(run_id)

    def get_plan(self, run_id: str) -> Optional[VerifiedPlan]:
        """Return the sealed VerifiedPlan for a run (experiment pairing check)."""
        return self._load_plan(run_id)

    def export_run_journal(self, run_id: str) -> Dict[str, Any]:
        """Export the complete journal for one run (evidence snapshot).

        Returns a dict with run events, model calls, execution receipt and
        acceptance report. The export is a read-only snapshot — it does not
        mutate the live journal. Adapters (e.g. ExperimentSupervisor) use this
        instead of reaching into the store.
        """
        store = self.ports.store
        row = store.get_run(run_id)
        request = self._reconstruct_request(row)
        context = self._load_context(run_id)
        capabilities = self._load_capabilities(run_id)
        intent = self._load_intent(run_id)
        plan = self._load_plan(run_id)
        experiment = request.experiment
        baseline = store.get_experiment_baseline(experiment.pair_id) if experiment else None
        return {
            "run_id": run_id,
            "request_envelope": request.model_dump(mode="json"),
            "captured_context_snapshot": (store.get_captured_context_snapshot(run_id).model_dump(mode="json")
                                          if store.get_captured_context_snapshot(run_id) else None),
            "planning_context_snapshot": context.model_dump(mode="json") if context else None,
            "capability_snapshot": capabilities.model_dump(mode="json") if capabilities else None,
            "intent_spec": intent.model_dump(mode="json") if intent else None,
            "task_contract": intent.derived_facts.get("task_contract") if intent else None,
            "verified_plan": plan.model_dump(mode="json") if plan else None,
            "authorization_grant": store.get_authorization_grant(run_id).model_dump(mode="json") if store.get_authorization_grant(run_id) else None,
            "runtime_lease": store.get_runtime_lease(run_id).model_dump(mode="json") if store.get_runtime_lease(run_id) else None,
            "artifacts": store.list_published_artifacts(run_id),
            "staging_artifacts": store.list_artifacts(run_id),
            "prepared_publication": store.get_prepared_publication(run_id),
            "publication_receipt": store.get_publication_receipt(run_id),
            "experiment_baseline": {key: value for key, value in (baseline or {}).items()
                                    if key not in ("context", "intent", "capabilities")},
            "digests": {"context": context.digest if context else None,
                        "capabilities": capabilities.digest if capabilities else None,
                        "intent": intent.digest if intent else None,
                        "plan": plan.digest if plan else None,
                        "baseline": baseline.get("baseline_digest") if baseline else None},
            "run_events": store.run_events(run_id),
            "model_calls": store.list_model_calls_for_run(run_id),
            "execution_receipt": store.get_execution_outcome(run_id),
            "acceptance_report": store.get_acceptance_report(run_id),
        }

    @property
    def runtime_identity(self) -> Dict[str, Any]:
        """Read-only identity of the wired production model runtime."""
        identity = getattr(self.ports.planner, "runtime_identity", None)
        return dict(identity) if isinstance(identity, dict) else {}

    def _freeze_experiment_baseline(self, run_id: str, request: RequestEnvelope,
                                    context: ContextSnapshot, capabilities: CapabilitySnapshot,
                                    intent: IntentSpec, plan: VerifiedPlan) -> None:
        spec = request.experiment
        if spec is None:
            return
        binding = {"text": request.text, "inputs": list(request.inputs), "seed": spec.seed,
                   "target_selector": request.target_selector.model_dump(mode="json")}
        self.ports.store.freeze_experiment_baseline(spec.pair_id, run_id, context, capabilities,
                                                    intent, plan, spec.provider, spec.model, binding)
        self.ports.store.append_event(run_id, "experiment_baseline_frozen", INTENT_COMPILED,
                                      {"pair_id": spec.pair_id, "arm": spec.arm,
                                       "provider": spec.provider, "model": spec.model,
                                       "experiment_input_hash": contracts.experiment_input_hash(context),
                                       "binding": binding})

    def decide(self, run_id: str, decision: contracts.AuthorizationDecision) -> RunView:
        with self._run_lock(run_id):
            self.ports.store.assert_run_in_active_session(run_id)
            return self._decide_locked(run_id, decision)

    def _decide_locked(self, run_id: str, decision: contracts.AuthorizationDecision) -> RunView:
        row = self.ports.store.get_run(run_id)
        if row["stage"] != AUTHORIZATION_REQUIRED:
            raise ValueError("run is not awaiting a decision: %s" % row["stage"])
        if decision.run_id != run_id:
            raise ValueError("decision.run_id does not match the run.")
        plan = self._load_plan(run_id)
        if plan is not None and decision.plan_digest != plan.digest:
            raise ValueError("decision.plan_digest does not match the sealed plan; re-approve the current plan.")
        if not decision.approved:
            outcome = outcome_failed(
                contracts.POLICY_DENIED, "authorization", "user_denied",
                "用户拒绝了该操作的授权。",
            )
            result = self.ports.planner.decide_authorization(run_id, False)
            if result != "denied":
                raise ValueError("authorization graph did not resume to denial")
            self.ports.store.append_event(run_id, "authorization_denied", AUTHORIZATION_REQUIRED,
                                          {"decision": "denied"}, outcome=outcome)
            return self._view(run_id)
        if plan is None:
            raise ValueError("sealed plan is required for approval")
        self._validate_approved_outputs(plan, decision.approved_scope)
        scope_doc = decision.approved_scope.model_dump(mode="json") if decision.approved_scope else {}
        result = self.ports.planner.decide_authorization(run_id, True)
        if result != "authorized":
            raise ValueError("authorization graph did not resume to approval")
        self.ports.store.append_event(run_id, "authorization_approved", AUTHORIZED,
                                      {"decision": "approved", "approved_scope": scope_doc})
        return self._drive_lifecycle(run_id)

    def resume(self, run_id: str) -> RunView:
        """Drive a paused run forward from its current stage (§7).

        For a run paused at ``execution_indeterminate`` (dispatch happened but
        execution could not be proven), resume re-runs reconcile: an operator
        who has adjudicated the situation explicitly asks the Bridge again
        whether execution happened. Indeterminate stays paused; proven-executed
        advances; proven-failed terminates.
        """
        with self._run_lock(run_id):
            self.ports.store.assert_run_in_active_session(run_id)
            row = self.ports.store.get_run(run_id)
            if row is not None and row.get("outcome_kind") == contracts.CLARIFICATION_REQUIRED:
                raise ValueError("clarification_required runs must use answer_clarification")
            if row is not None and row["stage"] == "execution_indeterminate":
                self._reconcile_runtime_run(run_id)
                return self._view(run_id)
            if row is not None and row["stage"] == "publication_indeterminate":
                self.ports.store.append_event(run_id, "publication_recovery_started", "publication_indeterminate",
                                              {}, clear_outcome=True)
                self._publish(run_id, self.ports.store.get_run(run_id))
                return self._view(run_id)
            if row is not None and (row.get("outcome_kind") is not None or row.get("stage") == SUCCEEDED_STAGE):
                return self._view(run_id)
            return self._drive_lifecycle_locked(run_id)

    def answer_clarification(self, run_id: str,
                             answer: contracts.ClarificationAnswer) -> RunView:
        """Validate and deterministically apply one sealed typed patch."""
        from ..clarification import apply_patch, validate_answer
        with self._run_lock(run_id):
            self.ports.store.assert_run_in_active_session(run_id)
            if answer.run_id != run_id:
                raise ValueError("clarification answer run_id does not match the run")
            row = self.ports.store.get_run(run_id)
            if row is None or row["stage"] != "clarification_required" or \
                    row.get("outcome_kind") != contracts.CLARIFICATION_REQUIRED:
                raise ValueError("run is not awaiting a clarification answer")
            request = self._reconstruct_request(row)
            if answer.session_id != request.session_id or answer.caller != request.caller:
                raise ValueError("clarification answer caller does not own the run")
            pending = self._pending_clarifications(run_id)
            item = next((value for value in pending
                         if value.get("clarification_id") == answer.clarification_id), None)
            if item is None:
                raise ValueError("clarification_id is not pending for this run")
            context = self._load_context(run_id)
            if context is None or item.get("context_digest") != context.digest:
                raise ValueError("clarification context has drifted; submit a new task")
            request_digest = contracts.digest(request.model_dump(mode="json"))
            if item.get("request_digest") != request_digest:
                raise ValueError("clarification request binding is invalid")
            sealed = contracts.ClarificationRequest.model_validate(item)
            plan = self._load_plan(run_id)
            if sealed.plan_digest is not None and (plan is None or plan.digest != sealed.plan_digest):
                raise ValueError("clarification plan has drifted")
            pending_event = next(event for event in reversed(self.ports.store.run_events(run_id))
                                 if event["kind"] == "clarification_required")
            old_contract = pending_event.get("payload", {}).get("task_contract_draft")
            if not isinstance(old_contract, dict):
                intent = self._load_intent(run_id)
                old_contract = intent.derived_facts.get("task_contract") if intent is not None else None
            if not isinstance(old_contract, dict) or contracts.digest(old_contract) != sealed.task_contract_digest:
                raise ValueError("clarification task contract has drifted")
            # Rebuild the clarification nodes from the CURRENT contract and
            # verify the sealed node still exists with the same descriptor and
            # graph binding.  A resolved/stale/irrelevant answer is rejected
            # outright — no scanner at answer time, no fail-open rebinding.
            from ..clarification import build_nodes, node_for
            current_nodes, current_graph_digest = build_nodes(old_contract)
            current_node = node_for(current_nodes, sealed.patch.target_path)
            if current_node is None:
                raise ValueError("clarification answer targets a resolved or absent location")
            if current_node.node_digest != sealed.node_digest:
                raise ValueError("clarification node descriptor has drifted")
            if sealed.graph_digest and current_graph_digest != sealed.graph_digest:
                raise ValueError("clarification proof graph has drifted")
            if current_node.proof_id not in sealed.proof_ids:
                raise ValueError("clarification answer is not bound to the sealed node")
            validate_answer(sealed.patch.value_schema, answer.answer)
            new_contract = apply_patch(old_contract, sealed.patch.target_path, answer.answer)
            capabilities = self._load_capabilities(run_id)
            if capabilities is None:
                raise ValueError("clarification capability snapshot is missing")
            outcome = self.ports.compiler.resume_with_patch(request, context, capabilities, new_contract)
            if not outcome.succeeded:
                raise ValueError(outcome.message)
            intent = outcome.details.get("intent")
            if not isinstance(intent, IntentSpec):
                raise ValueError("clarification patch did not produce an IntentSpec")
            self.ports.store.store_intent_spec(run_id, intent)
            self.ports.store.start_clarification_lineage(run_id)
            self.ports.store.append_event(
                run_id, "clarification_answered", INTENT_COMPILED,
                {"clarification_id": answer.clarification_id, "option_id": sealed.option_id,
                 "answer": answer.answer, "patch": sealed.patch.model_dump(mode="json"),
                 "old_task_contract_digest": sealed.task_contract_digest,
                 "new_task_contract_digest": contracts.digest(new_contract),
                 "proof_ids": list(sealed.proof_ids), "request_digest": request_digest,
                 "context_digest": context.digest, "plan_digest": sealed.plan_digest},
                clear_outcome=True,
            )
            return self._drive_lifecycle_locked(run_id)

    def _pending_clarifications(self, run_id: str) -> List[Dict[str, Any]]:
        events = self.ports.store.run_events(run_id)
        for event in reversed(events):
            if event["kind"] != "clarification_required":
                continue
            values = event.get("payload", {}).get("clarifications", [])
            if isinstance(values, list):
                return values
        return []

    def resume_quota(self, run_id: str) -> RunView:
        """Resume exactly the unfinished model node after operator restart."""
        with self._run_lock(run_id):
            self.ports.store.assert_run_in_active_session(run_id)
            row = self.ports.store.get_run(run_id)
            if row.get("outcome_kind") == contracts.QUOTA_STOPPED:
                self.ports.store.reopen_quota_stopped_run(run_id)
            elif not self.ports.store.has_active_quota_resume(run_id):
                raise ValueError("only a quota-stopped run can be resumed")
            return self._drive_lifecycle_locked(run_id)

    # -- state machine driver ----------------------------------------------

    def _drive_lifecycle(self, run_id: str) -> RunView:
        """Advance the run through stages until it pauses or terminates."""
        with self._run_lock(run_id):
            return self._drive_lifecycle_locked(run_id)

    def _drive_lifecycle_locked(self, run_id: str) -> RunView:
        while True:
            row = self.ports.store.get_run(run_id)
            # A clear rotates the sole active session and records a terminal
            # cancellation in the same transaction.  A detached background
            # worker must never advance, retry, or invoke a model after that
            # durable boundary.
            if not self.ports.store.is_run_in_active_session(run_id):
                return self._view(run_id)
            stage = row["stage"]
            if stage == SUCCEEDED_STAGE or row["outcome_kind"] is not None:
                return self._view(run_id)
            if stage == RECEIVED:
                self._acquire_context_lease(run_id)
                continue
            if stage == CONTEXT_LEASED:
                self._freeze_context(run_id)
                continue
            if stage == CONTEXT_FROZEN:
                self._compile_intent(run_id, row)
                continue
            if stage == INTENT_COMPILED:
                if self._load_intent(run_id) is None:
                    self._compile_intent(run_id, row)
                    continue
                self._verify_plan(run_id, row)
                continue
            if stage == PLAN_VERIFIED:
                # dry_run / plan-only requests stop here: execute=False means
                # the caller asked for a plan, not a side-effecting run. Pause
                # for human review instead of crossing into authorization.
                if not row.get("execute"):
                    self.ports.store.append_event(
                        run_id, "clarification_required",
                        contracts.CLARIFICATION_REQUIRED,
                        {"reason": "plan_only"},
                        outcome=contracts.outcome_paused(
                            contracts.CLARIFICATION_REQUIRED, "planning",
                            "plan_only", "计划已完成；未请求执行（execute=False）。"),
                    )
                    return self._view(run_id)
                self._request_authorization(run_id, row)
                continue
            if stage == AUTHORIZATION_REQUIRED:
                return self._view(run_id)
            if stage == AUTHORIZED:
                self._acquire_runtime(run_id, row)
                continue
            if stage == RUNTIME_ACQUIRED:
                self._execute(run_id, row)
                continue
            if stage == EXECUTING:
                return self._view(run_id)
            if stage == EXECUTED:
                self._accept(run_id, row)
                continue
            if stage == ACCEPTED:
                self._publish(run_id, row)
                continue
            if stage == PUBLISHED:
                self._complete(run_id)
                continue
            return self._view(run_id)

    # -- stage steps --------------------------------------------------------

    def _acquire_context_lease(self, run_id: str) -> None:
        """Acquire a context lease binding the ArcMap target before capture (§6.7).

        The lease is taken before planning so the captured ContextSnapshot
        carries a real lease_id and the target identity is fenced from the
        start. plan_digest is empty at this stage; it is bound when the
        execution lease is re-signed (or enforced via AuthorizationGrant).
        """
        if self.ports.executor is None:
            raise ValueError("arcmap executor is not wired; cannot acquire context lease.")
        try:
            request = self._reconstruct_request(self.ports.store.get_run(run_id))
            lease = self.ports.executor.acquire_lease(run_id, request.target_selector)
        except Exception as exc:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "context",
                       "lease_acquire_error", str(exc))
            return
        self.ports.store.store_runtime_lease(lease)
        self.ports.store.append_event(
            run_id, "context_leased", CONTEXT_LEASED,
            {"lease_id": lease.lease_id},
        )

    def _freeze_context(self, run_id: str) -> None:
        if self.ports.context is None:
            raise ValueError("context provider is not wired; cannot freeze context.")
        lease = self.ports.store.get_runtime_lease(run_id)
        if lease is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "context",
                       "lease_missing", "context lease not acquired before capture")
            return
        try:
            snapshot = self.ports.context.capture(run_id, lease)
        except Exception as exc:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "context",
                       "context_capture_error", str(exc))
            return
        self.ports.store.store_planning_context_snapshot(run_id, snapshot)
        request = self._reconstruct_request(self.ports.store.get_run(run_id))
        if request.experiment is not None:
            self.ports.store.store_captured_context_snapshot(run_id, snapshot)
        self.ports.store.append_event(
            run_id, "context_frozen", CONTEXT_FROZEN,
            {"context_digest": snapshot.digest, "lease_id": snapshot.lease_id},
        )

    def _compile_intent(self, run_id: str, row: Dict[str, Any]) -> None:
        if self.ports.compiler is None:
            raise ValueError("intent compiler is not wired; cannot compile intent.")
        base_request = self._reconstruct_request(row)
        request = self._reconstruct_request(row)
        context = self._load_context(run_id)
        if request.experiment is not None and request.experiment.arm == "g3":
            baseline = self.ports.store.get_experiment_baseline(request.experiment.pair_id)
            if baseline is None:
                self._fail(run_id, contracts.CONTRACT_FAILED, "intent", "baseline_missing",
                           "G3 requires a completed G2 baseline.")
                return
            binding = {"text": request.text, "inputs": list(request.inputs), "seed": request.experiment.seed,
                       "target_selector": request.target_selector.model_dump(mode="json")}
            if binding != baseline["binding"]:
                self._fail(run_id, contracts.CONTRACT_FAILED, "intent", "baseline_binding_mismatch",
                           "G3 request differs from the frozen G2 experiment binding.")
                return
            if baseline["planning_context_hash"] != contracts.experiment_input_hash(context):
                self._fail(run_id, contracts.CONTRACT_FAILED, "intent", "context_changed",
                           "G3 context content_hash differs from the G2 baseline.")
                return
            intent = baseline["intent"]
            capabilities = baseline["capabilities"]
            baseline_context = baseline["context"]
            self.ports.store.append_event(run_id, "experiment_context_fenced", CONTEXT_FROZEN,
                                          {"pair_id": request.experiment.pair_id,
                                           "captured_context_digest": context.digest,
                                           "baseline_context_digest": baseline_context.digest,
                                           "experiment_input_hash": contracts.experiment_input_hash(context)})
            self.ports.store.store_planning_context_snapshot(run_id, baseline_context)
            self.ports.store.store_intent_spec(run_id, intent)
            self.ports.store.store_capability_snapshot(run_id, capabilities)
            self.ports.store.append_event(run_id, "experiment_baseline_reused", INTENT_COMPILED,
                                          {"pair_id": request.experiment.pair_id,
                                           "baseline_run_id": baseline["run_id"],
                                           "baseline_digest": baseline["baseline_digest"],
                                           "experiment_input_hash": contracts.experiment_input_hash(context)})
            return
        capabilities = self._freeze_capabilities(run_id)
        if context is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "intent",
                       "context_missing", "context snapshot missing for intent compilation")
            return
        outcome = self.ports.compiler.compile(request, context, capabilities, run_id)
        if outcome.kind == contracts.CLARIFICATION_REQUIRED:
            clarifications = self._bind_clarifications(run_id, base_request, context,
                                                        outcome.details.get("clarifications", []),
                                                        outcome.details.get("task_contract_draft"))
            self.ports.store.append_event(
                run_id, "clarification_required", INTENT_COMPILED,
                {"message": outcome.message, "clarifications": clarifications,
                 "task_contract_draft": outcome.details.get("task_contract_draft")}, outcome=outcome,
            )
            return
        if not outcome.succeeded:
            self.ports.store.append_event(
                run_id, "intent_failed", INTENT_COMPILED,
                {"reason": outcome.message}, outcome=outcome,
            )
            return
        intent = outcome.details.get("intent")
        if not isinstance(intent, IntentSpec):
            self._fail(run_id, contracts.CONTRACT_FAILED, "intent",
                       "compiler_returned_no_intent", "compiler did not return an IntentSpec")
            return
        self.ports.store.store_intent_spec(run_id, intent)
        self.ports.store.append_event(
            run_id, "intent_compiled", INTENT_COMPILED,
            {"intent_digest": intent.digest},
        )

    @staticmethod
    def _bind_clarifications(run_id: str, request: RequestEnvelope,
                             context: ContextSnapshot, values: Any,
                             task_contract: Dict[str, Any],
                             plan_digest: Optional[str] = None) -> List[Dict[str, Any]]:
        from ..clarification import build_nodes, ClarificationError
        if not isinstance(task_contract, dict):
            raise ValueError("clarification outcome lacks its task contract")
        request_digest = contracts.digest(request.model_dump(mode="json"))
        task_contract_digest = contracts.digest(task_contract)
        # The server is the sole authority: it generates one sealed proof node
        # per genuinely unresolved location.  The model only supplies questions
        # per kind; it cannot name a path.  Multiple locations of the same kind
        # become distinct, separately-addressable instances.
        try:
            nodes, graph_digest = build_nodes(task_contract)
        except ClarificationError as exc:
            raise ValueError("clarification nodes are not sealable: %s" % exc)
        questions = {}
        for value in (values or []):
            if isinstance(value, dict) and isinstance(value.get("option_id"), str) \
                    and isinstance(value.get("question"), str) and value["question"]:
                questions.setdefault(value["option_id"], value["question"])
        bound = []
        for node in nodes:
            question = questions.get(node.option_id) or (
                "请澄清未决的 %s 字段。" % node.option_id)
            clarification_id = "clarification:" + contracts.digest({
                "run_id": run_id, "proof_id": node.proof_id,
                "target_path": node.target_path, "node_digest": node.node_digest,
                "task_contract": task_contract_digest,
            })[:24]
            sealed = contracts.ClarificationRequest(
                clarification_id=clarification_id, option_id=node.option_id,
                question=question,
                patch=contracts.ClarificationPatch(
                    option_id=node.option_id, kind=node.kind.value,
                    target_path=node.target_path, value_schema=node.value_schema),
                proof_ids=(node.proof_id,), request_digest=request_digest,
                context_digest=context.digest, plan_digest=plan_digest,
                task_contract_digest=task_contract_digest,
                graph_digest=graph_digest, node_digest=node.node_digest,
            )
            bound.append(sealed.model_dump(mode="json"))
        if not bound:
            raise ValueError("clarification outcome produced no sealed unresolved node")
        return bound

    def _verify_plan(self, run_id: str, row: Dict[str, Any]) -> None:
        if self.ports.planner is None:
            raise ValueError("workflow planner is not wired; cannot verify plan.")
        intent = self._load_intent(run_id)
        context = self._load_context(run_id)
        capabilities = self._load_capabilities(run_id)
        if intent is None or context is None or capabilities is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "plan",
                       "snapshot_missing", "a sealed snapshot required for planning is missing")
            return
        request = self._reconstruct_request(row)
        baseline = None
        if request.experiment is not None:
            if request.experiment.arm == "g3":
                baseline = self.ports.store.get_experiment_baseline(request.experiment.pair_id)
                if baseline is None:
                    self._fail(run_id, contracts.CONTRACT_FAILED, "plan", "baseline_missing",
                               "G3 requires a sealed G2 baseline plan.")
                    return
            outcome = self.ports.planner.plan_ablation(
                run_id, intent, context, capabilities, request.experiment.arm == "g3",
                baseline["plan"] if baseline is not None else None,
                force_audit=request.experiment.arm == "g3")
        else:
            outcome = self.ports.planner.plan(run_id, intent, context, capabilities)
        if outcome.kind == contracts.CLARIFICATION_REQUIRED:
            clarifications = self._bind_clarifications(run_id, request, context,
                                                        outcome.details.get("clarifications", []),
                                                        intent.derived_facts.get("task_contract"),
                                                        baseline["plan"].digest if baseline is not None else None)
            self.ports.store.append_event(
                run_id, "clarification_required", PLAN_VERIFIED,
                {"message": outcome.message, "clarifications": clarifications,
                 "task_contract_draft": intent.derived_facts.get("task_contract")}, outcome=outcome,
            )
            return
        if not outcome.succeeded:
            self.ports.store.append_event(
                run_id, "plan_failed", PLAN_VERIFIED,
                {"reason": outcome.message}, outcome=outcome,
            )
            return
        plan = outcome.details.get("plan")
        if not isinstance(plan, VerifiedPlan):
            self._fail(run_id, contracts.CONTRACT_FAILED, "plan",
                       "planner_returned_no_plan", "planner did not return a VerifiedPlan")
            return
        from ..acceptance_contract import derive as derive_acceptance_contract, AcceptanceContractError
        try:
            # This is an execution gate, not a late acceptance convenience:
            # all independent inputs must be sealable before authorization.
            derive_acceptance_contract(intent.derived_facts.get("task_contract"),
                                       plan, intent.bound_inputs, context,
                                       catalog=getattr(self.ports.planner, "catalog", None))
        except AcceptanceContractError as exc:
            self._fail(run_id, contracts.CONTRACT_FAILED, "plan",
                       "acceptance_contract_unsealable", str(exc))
            return
        if request.experiment is not None and request.experiment.arm == "g2":
            self._freeze_experiment_baseline(run_id, request, context, capabilities, intent, plan)
        if request.experiment is not None:
            artifact_root = request.experiment.artifact_root
        else:
            from ..paths import data_dir
            artifact_root = str((data_dir() / "runs" / run_id / "artifacts").resolve())
        plan = _bind_server_destinations(plan, artifact_root)
        self.ports.store.store_verified_plan(run_id, plan)
        facts = {"plan_id": plan.plan_id, "plan_digest": plan.digest}
        if request.experiment is not None:
            facts.update({"pair_id": request.experiment.pair_id, "arm": request.experiment.arm,
                          "provider": request.experiment.provider, "model": request.experiment.model,
                          "auditor_enabled": request.experiment.arm == "g3",
                          "task_contract_digest": contracts.digest(intent.derived_facts.get("task_contract")),
                          "baseline_digest": (self.ports.store.get_experiment_baseline(request.experiment.pair_id) or {}).get("baseline_digest"),
                          "topology_signature": outcome.details.get("topology_signature")})
        self.ports.store.append_event(run_id, "plan_verified", PLAN_VERIFIED, facts)

    def _request_authorization(self, run_id: str, row: Dict[str, Any]) -> None:
        plan = self._load_plan(run_id)
        if plan is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "authorization",
                       "plan_missing", "sealed plan missing for authorization")
            return
        request = self._reconstruct_request(row)
        requested_level = _requested_effect_level(request)
        # PolicyGate pre-checks the plan's required risk against the level the
        # caller authorized (§6.6). Insufficient authorization is denied.
        if self.ports.policy is not None:
            precheck = self.ports.policy.precheck(
                request, plan, {"level": requested_level},
            )
            if precheck.is_terminal and not precheck.succeeded:
                self._fail(run_id, contracts.POLICY_DENIED, "authorization",
                           "policy_denied", precheck.message)
                return
        # Read-only plans (risk level 1) auto-authorize. Plans that touch map
        # state or write data (risk >= 2) pause for an explicit human decision
        # (§6.6): the front-end shows the plan and the user calls decide().
        if plan.risk_level >= 2:
            self.ports.store.append_event(
                run_id, "authorization_required", AUTHORIZATION_REQUIRED,
                {"plan_digest": plan.digest, "risk_level": plan.risk_level},
            )
            return
        self.ports.store.append_event(
            run_id, "authorization_auto", AUTHORIZED,
            {"plan_digest": plan.digest},
        )

    def _acquire_runtime(self, run_id: str, row: Dict[str, Any]) -> None:
        if self.ports.executor is None:
            raise ValueError("arcmap executor is not wired; cannot acquire runtime.")
        plan = self._load_plan(run_id)
        if plan is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "runtime",
                       "plan_missing", "sealed plan missing for runtime acquire")
            return
        # Re-sign the context lease with the sealed plan: same target identity,
        # plan_digest bound, epoch incremented. No re-discovery — the target was
        # fenced at CONTEXT_LEASED and must not change.
        context_lease = self.ports.store.get_runtime_lease(run_id)
        if context_lease is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "runtime",
                       "context_lease_missing",
                       "context lease not found; cannot bind plan")
            return
        import time as _time
        lease = context_lease.model_copy(update={
            "plan_digest": plan.digest,
            "epoch": context_lease.epoch + 1,
            "last_heartbeat": _time.time(),
        })
        self.ports.store.store_runtime_lease(lease)
        # Issue the real grant bound to this lease (§6.6). The kernel never
        # authorizes itself (§2.3); PolicyGate binds actor + plan + lease.
        if self.ports.policy is not None:
            request = self._reconstruct_request(row)
            if plan.risk_level >= 2:
                # The run paused at AUTHORIZATION_REQUIRED and the user's
                # AuthorizationDecision carries the exact approved_scope. It is
                # REQUIRED — proceeding on the pre-decision scope would bypass
                # what the user actually authorized.
                approved_scope = self._load_approved_scope(run_id)
                if approved_scope is None:
                    self._fail(run_id, contracts.POLICY_DENIED, "runtime",
                               "approved_scope_missing",
                               "已暂停授权的运行缺少用户批准的授权范围。")
                    return
                effects = {
                    "level": approved_scope.level,
                    "inputs": plan.input_identities,
                    "outputs": tuple(
                        {"output_id": output_id, "destination": destination}
                        for output_id, destination in approved_scope.output_identities
                    ),
                }
            else:
                # Read-only (risk 1): auto-authorized, use the request's level.
                effects = {"level": _requested_effect_level(request)}
            granted = self.ports.policy.authorize(
                request, plan, lease, effects,
            )
            if granted.is_terminal and not granted.succeeded:
                self.ports.store.append_event(
                    run_id, "authorization_failed", AUTHORIZED,
                    {"reason": granted.message}, outcome=granted,
                )
                return
            grant = granted.details.get("grant")
            if isinstance(grant, contracts.AuthorizationGrant):
                self.ports.store.store_authorization_grant(grant)
        self.ports.store.append_event(
            run_id, "runtime_acquired", RUNTIME_ACQUIRED,
            {"lease_id": lease.lease_id, "epoch": lease.epoch},
        )

    def _load_approved_scope(self, run_id: str):
        """Return the user-approved SideEffectScope from the authorization event.

        None when the run was auto-authorized (risk level 1) — in that case the
        request's original level is used. The scope is stored in the
        ``authorization_approved`` event payload by ``decide()``.
        """
        events = self.ports.store.run_events(run_id)
        approved = next((e for e in events if e.get("kind") == "authorization_approved"), None)
        if approved is None:
            return None
        scope_doc = (approved.get("payload") or {}).get("approved_scope")
        if not isinstance(scope_doc, dict):
            return None
        try:
            return contracts.SideEffectScope(
                level=int(scope_doc.get("level", 1)),
                input_identities=tuple(tuple(item) for item in (scope_doc.get("input_identities") or ())),
                output_identities=tuple(tuple(item) for item in (scope_doc.get("output_identities") or ())),
            )
        except Exception:
            return None

    @staticmethod
    def _plan_output_identities(plan: VerifiedPlan):
        return tuple(sorted(
            (output.output_id, output.destination_path)
            for step in plan.workflow for output in step.declared_outputs
            if output.kind != "map_state"
        ))

    @staticmethod
    def _validate_approved_outputs(plan: VerifiedPlan, scope: contracts.SideEffectScope) -> None:
        if tuple(sorted(identity for _input_id, identity in scope.input_identities)) != plan.input_identities:
            raise ValueError("approved_scope.inputs must exactly match sealed plan input identities")
        if tuple(sorted(scope.output_identities)) != GeoPilotKernel._plan_output_identities(plan):
            raise ValueError("approved_scope.outputs must exactly match sealed plan output identities")

    def _execute(self, run_id: str, row: Dict[str, Any]) -> None:
        if self.ports.executor is None:
            raise ValueError("arcmap executor is not wired; cannot execute.")
        plan = self._load_plan(run_id)
        lease = self._load_lease(run_id)
        grant = self.ports.store.get_authorization_grant(run_id)
        if plan is None or lease is None or grant is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "execution",
                       "binding_missing", "lease, plan or grant missing for execution")
            return
        # Fencing: the persisted grant must match the current lease and plan.
        if grant.lease_id != lease.lease_id or grant.lease_epoch != lease.epoch:
            self._fail(run_id, contracts.POLICY_DENIED, "execution",
                       "grant_lease_mismatch", "授权与当前租约不匹配。")
            return
        if grant.plan_digest != plan.digest:
            self._fail(run_id, contracts.POLICY_DENIED, "execution",
                       "grant_plan_mismatch", "授权绑定的计划哈希已变化。")
            return
        # Grant expiry (§6.6): a persisted grant may have expired between
        # authorization and execution. Re-check before dispatching.
        if self.ports.policy is not None:
            check = self.ports.policy.check_grant(grant, lease, plan.digest)
            if not check.succeeded:
                self._fail(run_id, contracts.POLICY_DENIED, "execution",
                           check.code or "grant_invalid", check.message)
                return
        self.ports.store.append_event(
            run_id, "execution_started", EXECUTING,
            {"lease_id": lease.lease_id, "plan_digest": plan.digest},
        )
        outcome = self.ports.executor.execute(lease, plan, grant)
        receipt_doc = outcome.details.get("receipt") if outcome.details else None
        if outcome.is_terminal and not outcome.succeeded:
            self.ports.store.append_event(
                run_id, "execution_failed", EXECUTING,
                {"reason": outcome.message}, outcome=outcome,
            )
            return
        if not outcome.succeeded:
            # Paused (ExecutionIndeterminate): dispatch happened but execution
            # cannot be proven. Record as a recoverable paused state — never
            # auto-write ``executed`` (that would mark an uncertain result as
            # success). Resume is driven by explicit reconcile (§7).
            self.ports.store.append_event(
                run_id, "execution_indeterminate", EXECUTING,
                {"reason": outcome.message}, outcome=outcome,
            )
            return
        if not isinstance(receipt_doc, dict):
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "execution", "receipt_missing",
                       "执行成功缺少权威回执。")
            return
        self._ingest_execution_receipt(run_id, lease, receipt_doc)
        self.ports.store.append_event(
            run_id, "executed", EXECUTED,
            {"summary": outcome.message},
        )

    def _ingest_execution_receipt(self, run_id: str, lease: RuntimeLease,
                                  receipt_doc: Dict[str, Any]) -> None:
        """Single fenced receipt ingestion path for normal and recovered runs."""
        plan = self._load_plan(run_id)
        import hashlib
        import json
        result = receipt_doc.get("result")
        computed_result_hash = hashlib.sha256(json.dumps(
            result, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("ascii")).hexdigest() if isinstance(result, dict) else None
        if plan is None or receipt_doc.get("lease_id") != lease.lease_id or \
                int(receipt_doc.get("epoch", 0)) != lease.epoch or \
                receipt_doc.get("plan_hash") != plan.digest or \
                receipt_doc.get("deployment_hash") != lease.deployment_hash or \
                computed_result_hash is None or receipt_doc.get("result_hash") != computed_result_hash:
            raise ValueError("execution receipt is not authoritative for this run")
        self.ports.store.store_execution_receipt(
            receipt_doc.get("receipt_id", receipt_doc["lease_id"]), run_id, lease.lease_id,
            plan.digest, receipt_doc.get("status", "executed"), receipt_doc["result_hash"], receipt_doc)
        self._stage_artifacts_from_receipt(run_id, receipt_doc)

    def _stage_artifacts_from_receipt(self, run_id: str, receipt_doc: Dict[str, Any]) -> None:
        """Extract staged artifacts from the execution receipt (§6.8).

        The Py2 runtime records each step's ``observation`` (path, kind) under
        ``receipt.result.steps[].result.observation``. Only paths inside the
        run's staging directory are accepted — an arbitrary path from the
        receipt must never be registered (it could point outside staging and
        enable an out-of-bounds publish).
        """
        plan = self._load_plan(run_id)
        if plan is None:
            raise ValueError("cannot stage artifacts without sealed plan")
        outputs_by_step = {
            step.id: step.declared_outputs for step in plan.workflow
        }
        from ..paths import localappdata_dir
        staging_root = Path(localappdata_dir() / "staging" / run_id).resolve()
        result = receipt_doc.get("result") if isinstance(receipt_doc.get("result"), dict) else {}
        steps = result.get("steps") if isinstance(result.get("steps"), list) else []
        for step in steps:
            if not isinstance(step, dict):
                continue
            step_id = step.get("step_id") or step.get("id") or ""
            declared = outputs_by_step.get(step_id, ())
            payload = step.get("result") if isinstance(step.get("result"), dict) else {}
            obs = payload.get("observation") if isinstance(payload.get("observation"), dict) else {}
            path = obs.get("path")
            if not isinstance(path, str) or not path:
                continue
            if len(declared) != 1 or declared[0].kind == "map_state":
                # Keep the evidence registered rather than dropping it: the
                # acceptance boundary compares the staged set to sealed
                # output_ids and deterministically rejects this side effect.
                output = None
            else:
                output = declared[0]
            # Boundary check: the path must resolve under this run's staging
            # directory. Reject anything escaping staging (../, absolute paths
            # elsewhere, symlinks pointing out).
            try:
                resolved = Path(path).resolve()
                resolved.relative_to(staging_root)
            except (ValueError, OSError):
                from gateway_py3.logs import write_event
                write_event("artifact.staging_escape_rejected",
                            {"run_id": run_id, "path": path[:200]})
                continue
            if output is None:
                raise ValueError("receipt contains undeclared or ambiguous staged output")
            destination = output.destination_path
            if output.destination_policy != "physical" or not destination:
                raise ValueError("sealed output lacks a server-derived physical destination")
            if output.output_format == "gdb":
                source_gdb = _file_gdb_unit(str(resolved))
                destination_gdb = _file_gdb_unit(destination)
                publication_kind = "file_gdb"
                if source_gdb is None or destination_gdb is None:
                    raise ValueError("gdb output must be contained in a FileGDB publish unit")
            else:
                publication_kind = "file"
            identity = contracts.ArtifactIdentity(
                output_id=output.output_id, kind=output.kind,
                output_format=output.output_format,
                logical_dataset_path=str(resolved), source_publish_unit_path=str(staging_root),
                destination_dataset_path=destination,
                destination_publish_unit_path=ntpath.dirname(
                    _file_gdb_unit(destination) if publication_kind == "file_gdb" else ntpath.dirname(destination)),
                publication_kind=publication_kind,
            )
            self.ports.store.store_artifact(run_id, identity, staged=True)

    def _accept(self, run_id: str, row: Dict[str, Any]) -> None:
        if (self.ports.store.get_run(run_id) or {}).get("outcome_kind") is not None:
            return
        if self.ports.acceptance is None:
            raise ValueError("acceptance publisher is not wired; cannot accept.")
        plan = self._load_plan(run_id)
        intent = self._load_intent(run_id)
        context = self._load_context(run_id)
        if plan is None or intent is None or context is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "acceptance",
                       "binding_missing", "plan or intent missing for acceptance")
            return
        from ..acceptance_contract import derive as derive_acceptance_contract, AcceptanceContractError
        try:
            acceptance_contract = derive_acceptance_contract(
                intent.derived_facts.get("task_contract"), plan, intent.bound_inputs, context,
                catalog=getattr(self.ports.planner, "catalog", None))
        except AcceptanceContractError as exc:
            self._fail(run_id, contracts.ACCEPTANCE_FAILED, "acceptance", "contract_invalid", str(exc))
            return
        staged = self.ports.store.list_staged_artifacts(run_id)
        lease = self._load_lease(run_id)
        declared = [output for step in plan.workflow for output in step.declared_outputs
                    if output.kind != "map_state"]
        map_outputs = [(step, output) for step in plan.workflow for output in step.declared_outputs
                       if output.kind == "map_state"]
        probes = []
        if declared:
            if lease is None or self.ports.bridge is None:
                self._fail(run_id, contracts.ACCEPTANCE_FAILED, "acceptance", "probe_unavailable",
                           "缺少独立 ArcPy 验收探针。")
                return
            staged_by_output = {item.output_id: item for item in staged
                                if isinstance(item, contracts.ArtifactIdentity)}
            gdb_artifacts = [item for item in staged_by_output.values()
                             if item.publication_kind == "file_gdb"]
            units = {_file_gdb_unit(item.logical_dataset_path) for item in gdb_artifacts}
            if len(units) > 1:
                self._fail(run_id, contracts.ACCEPTANCE_FAILED, "acceptance", "multiple_source_units",
                           "所有 FileGDB 成果必须来自同一个 staging FileGDB。")
                return
            if units:
                unit = self.ports.bridge.probe_unit(lease, plan, next(iter(units)))
                expected = sorted(_relative_dataset_path(item.logical_dataset_path, _file_gdb_unit(item.logical_dataset_path))
                                  for item in gdb_artifacts)
                actual = sorted(unit.get("datasets", ())) if isinstance(unit, dict) else []
                if expected != actual:
                    self._fail(run_id, contracts.ACCEPTANCE_FAILED, "acceptance", "unit_dataset_mismatch",
                               "staging FileGDB 含有未声明、缺失或路径不一致的数据集。")
                    return
                probes.append(unit)
            for output in declared:
                artifact = staged_by_output.get(output.output_id)
                if artifact is None:
                    self._fail(run_id, contracts.ACCEPTANCE_FAILED, "acceptance", "staged_artifact_missing",
                               "封存成果未登记 staging artifact。")
                    return
                probe = self.ports.bridge.probe_output(
                    lease, plan, output.output_id, output.kind, output.output_format,
                    artifact.logical_dataset_path, acceptance_contract)
                if not isinstance(probe, dict):
                    self._fail(run_id, contracts.ACCEPTANCE_FAILED, "acceptance", "probe_missing",
                               "独立 ArcPy 验收探针未返回证据。")
                    return
                probes.append(probe)
        if map_outputs:
            if lease is None or self.ports.bridge is None:
                self._fail(run_id, contracts.ACCEPTANCE_FAILED, "acceptance", "probe_unavailable",
                           "缺少独立 ArcPy 地图状态验收探针。")
                return
            for step, output in map_outputs:
                try:
                    conditions = (self.ports.planner.catalog.get(step.operation).get("capability_contract") or {}).get("postconditions") or []
                    supported = [item for item in conditions if isinstance(item, dict) and item.get("kind")]
                except (AttributeError, KeyError):
                    supported = []
                if len(supported) != 1:
                    self._fail(run_id, contracts.ACCEPTANCE_FAILED, "acceptance", "map_postcondition_unverifiable",
                               "封存地图状态成果必须声明一个可独立验收的后置条件。")
                    return
                probe = self.ports.bridge.probe_map_state(lease, plan, output.output_id,
                                                          supported[0], step.arguments,
                                                          acceptance_contract)
                if not isinstance(probe, dict):
                    self._fail(run_id, contracts.ACCEPTANCE_FAILED, "acceptance", "map_probe_missing",
                               "独立 ArcPy 地图状态验收探针未返回证据。")
                    return
                probes.append(probe)
        outcome = self.ports.acceptance.accept(intent, plan, probes, staged, acceptance_contract)
        if not outcome.succeeded:
            self.ports.store.append_event(
                run_id, "acceptance_failed", ACCEPTED,
                {"reason": outcome.message}, outcome=outcome,
            )
            return
        # Persist the acceptance report so publish can verify it passed.
        report = outcome.details.get("report", {})
        if isinstance(report, dict):
            report_id = str(uuid.uuid4())
            self.ports.store.store_acceptance_report(
                report_id, run_id, plan.digest,
                bool(report.get("passed", True)), report,
            )
        self.ports.store.append_event(
            run_id, "accepted", ACCEPTED, {"report": outcome.message},
        )

    def _publish(self, run_id: str, row: Dict[str, Any]) -> None:
        if (self.ports.store.get_run(run_id) or {}).get("outcome_kind") is not None:
            return
        if self.ports.acceptance is None:
            raise ValueError("acceptance publisher is not wired; cannot publish.")
        plan = self._load_plan(run_id)
        grant = self.ports.store.get_authorization_grant(run_id)
        if plan is None or grant is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "publish",
                       "plan_or_grant_missing", "plan or grant missing for publication")
            return
        lease = self._load_lease(run_id)
        if lease is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "publish",
                       "lease_missing", "publication requires the bound runtime lease")
            return
        prepared = self.ports.store.get_prepared_publication(run_id)
        if prepared is None and self.ports.policy is not None:
            valid = self.ports.policy.check_grant(grant, lease, plan.digest)
            if not valid.succeeded:
                self.ports.store.append_event(run_id, "publish_failed", PUBLISHED,
                                              {"reason": valid.message}, outcome=valid)
                return
        staged = self.ports.store.list_staged_artifacts(run_id)
        report = self.ports.store.get_acceptance_report(run_id)
        if prepared is None:
            outcome = self.ports.acceptance.prepare(staged, report, grant, str(uuid.uuid4()))
            if not outcome.succeeded:
                self.ports.store.append_event(run_id, "publish_failed", PUBLISHED,
                                              {"reason": outcome.message}, outcome=outcome)
                return
            prepared = outcome.details["publication"]
            self.ports.store.prepare_publication(run_id, prepared)
            outcome = self.ports.acceptance.materialize(prepared, staged)
            if not outcome.succeeded:
                self.ports.store.append_event(run_id, "publish_failed", PUBLISHED,
                                              {"reason": outcome.message}, outcome=outcome)
                return
            outcome = self.ports.acceptance.commit(prepared)
        else:
            outcome = self.ports.acceptance.recover(prepared, staged, report, grant)
        if not outcome.succeeded:
            if outcome.is_recoverable:
                self.ports.store.append_event(
                    run_id, "publication_indeterminate", "publication_indeterminate",
                    {"reason": outcome.message}, outcome=outcome,
                )
            else:
                self.ports.store.append_event(
                    run_id, "publish_failed", PUBLISHED,
                    {"reason": outcome.message}, outcome=outcome,
                )
            return
        publication = outcome.details.get("publication")
        if not isinstance(publication, dict) or not publication.get("publication_id"):
            raise ValueError("publication commit did not return a sealed receipt")
        self.ports.store.finalize_publication(run_id, grant.grant_id, publication)

    def _complete(self, run_id: str) -> None:
        outcome = outcome_succeeded("succeeded", "任务完成。")
        self.ports.store.append_event(
            run_id, "succeeded", SUCCEEDED_STAGE, {}, outcome=outcome,
        )

    # -- helpers ------------------------------------------------------------

    def _fail(self, run_id: str, kind: str, stage: str, code: str, message: str) -> None:
        outcome = outcome_failed(kind, stage, code, message)
        self.ports.store.append_event(
            run_id, "%s_failed" % stage, stage, {"reason": message}, outcome=outcome,
        )

    def _load_context(self, run_id: str) -> Optional[ContextSnapshot]:
        return self.ports.store.get_planning_context_snapshot(run_id)

    def _load_capabilities(self, run_id: str) -> Optional[CapabilitySnapshot]:
        return self.ports.store.get_capability_snapshot(run_id)

    def _load_intent(self, run_id: str) -> Optional[IntentSpec]:
        return self.ports.store.get_intent_spec(run_id)

    def _load_plan(self, run_id: str) -> Optional[VerifiedPlan]:
        return self.ports.store.get_verified_plan(run_id)

    def _load_lease(self, run_id: str) -> Optional[RuntimeLease]:
        return self.ports.store.get_runtime_lease(run_id)

    def _freeze_capabilities(self, run_id: str) -> Optional[CapabilitySnapshot]:
        if self.ports.capabilities is None:
            raise ValueError("capability provider is not wired; cannot freeze capabilities.")
        snapshot = self.ports.capabilities.snapshot(run_id)
        self.ports.store.store_capability_snapshot(run_id, snapshot)
        return snapshot

    def _reconstruct_request(self, row: Dict[str, Any]) -> RequestEnvelope:
        """Rebuild the envelope from the persisted run row for stage steps.

        Stage A stores the caller and side-effects in the first journal event;
        later stages will persist a full envelope snapshot. This keeps the
        kernel self-sufficient without re-reading adapter state.
        """
        events = self.ports.store.run_events(row["run_id"])
        received = next((e for e in events if e["kind"] == "run_received"), None)
        if received is None:
            raise ValueError("run lost its received event: %s" % row["run_id"])
        payload = received["payload"]
        caller_doc = payload["caller"]
        side_effects_doc = payload.get("side_effects")
        side_effects = None
        if side_effects_doc is not None:
            side_effects = contracts.SideEffectScope(
                level=side_effects_doc["level"],
                input_identities=tuple(tuple(item) for item in side_effects_doc.get("input_identities", ())),
                output_identities=tuple(tuple(item) for item in side_effects_doc.get("output_identities", ())),
            )
        return RequestEnvelope(
            session_id=row["session_id"],
            request_id=row["request_id"],
            text=row["text"],
            caller=contracts.CallerIdentity(
                user_id=caller_doc["user_id"], tenant_id=caller_doc["tenant_id"],
                role=caller_doc["role"], data_scope=tuple(caller_doc.get("data_scope", ())),
                client_kind=caller_doc.get("client_kind", "web"),
            ),
            execute=bool(row["execute"]),
            side_effects=side_effects,
            inputs=tuple(payload.get("inputs") or ()),
            target_selector=payload["target_selector"],
            model_plan=payload["model_plan"],
            model_binding_summary=payload["model_binding_summary"],
            experiment=contracts.ExperimentSpec.model_validate(payload["experiment"])
            if payload.get("experiment") is not None else None,
        )

    def _view(self, run_id: str) -> RunView:
        row = self.ports.store.get_run(run_id)
        events = self.ports.store.run_events(run_id)
        outcome = None
        if row["outcome_kind"]:
            outcome = _load_outcome(row["outcome"])
        return RunView(
            run_id=row["run_id"], session_id=row["session_id"],
            stage=row["stage"], outcome=outcome,
            events=tuple(events),
            plan=self._load_plan(run_id),
            intent=self._load_intent(run_id),
        )


# --- helpers ----------------------------------------------------------------

def _file_gdb_unit(path: str) -> Optional[str]:
    """Return the containing FileGDB without accepting a path outside one."""
    candidate = Path(path)
    for item in (candidate, *candidate.parents):
        if item.name.lower().endswith(".gdb"):
            return str(item)
    return None


def _runtime_step_document(step: contracts.WorkflowStep) -> Dict[str, Any]:
    return {
        "id": step.id,
        "operation": step.operation,
        "arguments": dict(step.arguments),
        "reason": step.reason,
    }


def _bind_server_destinations(plan: contracts.VerifiedPlan,
                              artifact_root: str) -> contracts.VerifiedPlan:
    """Derive physical destinations from one server-owned output root.

    The LLM never sees or selects this path.  Every non-map output is rebound
    to the server-owned FileGDB while output ids, topology, operations and all
    analytical arguments remain unchanged.
    """
    import ntpath
    normalized = ntpath.normpath(artifact_root)
    if (not ntpath.isabs(normalized) or normalized != artifact_root
            or ntpath.splitext(normalized)[1]):
        raise ValueError("server artifact root must be a normalized absolute directory path")
    document = plan.model_dump(mode="json")
    for step in document["workflow"]:
        file_outputs = [item for item in step.get("declared_outputs", ())
                        if item.get("kind") != "map_state"]
        if not file_outputs:
            continue
        step["arguments"] = dict(step.get("arguments") or {})
        for output in file_outputs:
            if output.get("destination_policy") != "server_derived" or output.get("destination_path") is not None:
                raise ValueError("logical plan contains a physical or unknown destination")
            name = output["name"]
            if ntpath.basename(name) != name or name in (".", ".."):
                raise ValueError("logical output name is not a safe path segment")
            fmt = output["output_format"]
            if fmt == "gdb":
                if "." in name:
                    raise ValueError("FileGDB dataset output name cannot contain an extension")
                destination = ntpath.join(normalized, "published.gdb", name)
            elif fmt in ("csv", "png"):
                extension = "." + fmt
                leaf = name if name.lower().endswith(extension) else name + extension
                destination = ntpath.join(normalized, "files", leaf)
            else:
                raise ValueError("persisted experiment output has an unsupported format")
            output["destination_policy"] = "physical"
            output["destination_path"] = ntpath.normpath(destination)
    return contracts.VerifiedPlan.model_validate(document)


def _relative_dataset_path(dataset_path: str, unit_path: str) -> str:
    import ntpath
    relative = ntpath.relpath(dataset_path, unit_path)
    if relative == ".." or relative.startswith(".." + ntpath.sep):
        raise ValueError("dataset path escapes FileGDB unit")
    return relative.replace("\\", "/")

def _requested_effect_level(request: RequestEnvelope) -> int:
    """Side-effect level requested by the envelope (side_effects.level)."""
    if request.side_effects is not None:
        return request.side_effects.level
    return 1


def _load_outcome(document: Dict[str, Any]) -> Outcome:
    return Outcome.model_validate(document)


class ExperimentKernelPort:
    """Complete, narrow experiment lifecycle owned by the production Kernel."""
    def __init__(self, kernel: GeoPilotKernel):
        self._kernel = kernel

    @property
    def runtime_identity(self) -> Dict[str, Any]:
        return self._kernel.runtime_identity

    def submit(self, request: RequestEnvelope) -> RunView:
        return self._kernel.submit(request)

    def inspect(self, run_id: str) -> RunView:
        return self._kernel.inspect(run_id)

    def decide(self, decision: AuthorizationDecision) -> RunView:
        return self._kernel.decide(decision.run_id, decision)

    def resume_quota(self, run_id: str) -> RunView:
        return self._kernel.resume_quota(run_id)

    def export_run_journal(self, run_id: str) -> Dict[str, Any]:
        return self._kernel.export_run_journal(run_id)

    def await_progress(self, run_id: str, event_kinds=(),
                       timeout: float = 30.0) -> RunView:
        self._kernel.ports.store.wait_for_run_event(run_id, event_kinds, timeout)
        return self._kernel.inspect(run_id)


def _build_context_snapshot_from_payload(payload: Dict[str, Any], lease):
    """Build a ContextSnapshot from a raw Bridge context callback payload.

    The Py2 callback puts ArcMap identity in the top-level ``target`` and keeps
    the map contents (layers, mxd_path, content_hash) under ``context``. This
    function assembles a ContextSnapshot by combining both: identity comes from
    ``target`` (+ ``deployment_hash`` from the lease), map contents from
    ``context``. Returns None if a required field is missing so the caller can
    reject the callback (§0: fail fast, no defaulting to ``1`` / ``"unknown"``).
    """
    from .contracts import ContextSnapshot, LayerSnapshot, LayerRef
    target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
    context_data = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    try:
        arcmap_pid = int(target.get("arcmap_pid", 0))
        bridge_pid = int(target.get("bridge_pid", 0))
        bridge_port = int(target.get("bridge_port", 0))
        hwnd = int(target.get("hwnd", 0))
        deployment_hash = payload["deployment_hash"]
        content_hash = context_data["content_hash"]
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    if arcmap_pid <= 0 or bridge_pid <= 0 or bridge_port <= 0 or hwnd <= 0:
        return None
    if not isinstance(deployment_hash, str) or not deployment_hash.strip():
        return None
    required = ("layers", "mxd_path", "data_frame", "is_saved", "content_hash",
                "edit_session_state", "active_view", "extent")
    if any(name not in context_data for name in required):
        return None
    layers_data = context_data["layers"]
    if not isinstance(layers_data, list):
        return None
    mxd = context_data["mxd_path"]
    data_frame = context_data["data_frame"]
    is_saved = bool(context_data["is_saved"])
    return ContextSnapshot(
        lease_id=lease.lease_id,
        arcmap_pid=arcmap_pid, bridge_pid=bridge_pid,
        bridge_port=bridge_port, target_hwnd=hwnd,
        document_identity={"mxd": mxd, "active_data_frame": data_frame},
        layers=tuple(_layer_from_context_payload(layer) for layer in layers_data),
        active_data_frame=data_frame,
        edit_session_state=context_data["edit_session_state"],
        is_saved=is_saved,
        view_state={"active_view": context_data["active_view"], "extent": context_data["extent"]},
        captured_at=time.time(),
        deployment_hash=deployment_hash,
        content_hash=str(content_hash),
    )


def _layer_from_context_payload(layer: Dict[str, Any]):
    from .contracts import LayerSnapshot, LayerRef, FieldColumn
    required = ("name", "layer_ref", "long_name", "visible", "selection_hash", "data_source", "layer_type", "fields",
                "geometry_type", "spatial_reference", "selected_count")
    if not isinstance(layer, dict) or any(name not in layer for name in required):
        raise ValueError("context layer is incomplete")
    fields = layer["fields"]
    if not isinstance(fields, list) or any(not isinstance(field, dict) or
                                           "name" not in field or "type" not in field
                                           for field in fields):
        raise ValueError("context fields are incomplete")
    return LayerSnapshot(
        identity=LayerRef(name=layer["name"], layer_ref=layer["layer_ref"],
                          data_source=layer["data_source"], layer_type=layer["layer_type"]),
        fields=tuple(FieldColumn(
            name=field["name"], dtype=field.get("type"),
            nullable=bool(field.get("nullable", True)),
            precision=field.get("precision"), scale=field.get("scale"),
            length=field.get("length"), domain=tuple(field.get("domain") or ()),
        ) for field in fields),
        geometry_type=layer["geometry_type"], coordinate_system=layer["spatial_reference"],
        selection_count=int(layer["selected_count"]),
        long_name=layer["long_name"], visible=bool(layer["visible"]),
        selection_hash=layer["selection_hash"],
        identity_fields=tuple(layer.get("identity_fields") or ()),
        source_content_digest=layer.get("source_content_digest"),
        feature_manifest_digest=layer.get("feature_manifest_digest"),
        raster_content_digest=layer.get("raster_content_digest"),
        crs_type=layer.get("crs_type"),
        meters_per_unit=layer.get("meters_per_unit"),
    )
