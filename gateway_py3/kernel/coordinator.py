"""GeoPilotKernel: the sole deep module callers may reach (§6.1).

Drives the Run state machine (§6.1) and returns a unified ``RunView``. The
kernel itself understands no GIS operation, builds no model prompt, scans no
ArcMap port and publishes no file. Each deep collaborator is injected through
a ``Protocol`` so Stage A can wire Fake adapters end-to-end and later stages
swap in real implementations without touching the kernel.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from . import contracts
from .contracts import (
    AUTHORIZATION_REQUIRED,
    AUTHORIZED,
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
    def capture(self, run_id: str) -> contracts.ContextSnapshot: ...


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


@runtime_checkable
class PolicyGate(Protocol):
    """§6.6 PolicyGate: authorize a sealed plan + requested effects."""
    def authorize(self, actor: RequestEnvelope, plan: VerifiedPlan,
                  requested_effects: Any) -> contracts.Outcome: ...


@runtime_checkable
class ArcMapExecutor(Protocol):
    """§6.7 ArcMapRuntime.execute under a lease."""
    def acquire(self, run_id: str, plan: VerifiedPlan) -> contracts.RuntimeLease: ...
    def execute(self, lease: RuntimeLease, plan: VerifiedPlan,
                grant: AuthorizationGrant) -> contracts.Outcome: ...


@runtime_checkable
class AcceptancePublisher(Protocol):
    """§6.8 AcceptancePublisher: accept staged artifacts and publish."""
    def accept(self, intent: IntentSpec, plan: VerifiedPlan,
               runtime_outcome: Any) -> contracts.Outcome: ...
    def publish(self, staged_artifacts: Any, acceptance_report: Any,
                grant: AuthorizationGrant) -> contracts.Outcome: ...


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


class GeoPilotKernel:
    """The sole public deep module (§3, §6.1).

    Adapters (HTTP, Web, external agent, future ExperimentSupervisor) call
    only ``submit`` / ``inspect`` / ``decide`` / ``resume``. The kernel drives
    the state machine forward until it pauses for a decision, reaches a
    terminal outcome, or finishes.
    """

    def __init__(self, ports: KernelPorts):
        self.ports = ports

    # -- public API (§3) ----------------------------------------------------

    def submit(self, request: RequestEnvelope) -> RunView:
        """Create a run and return immediately at ``received`` stage.

        The run advances in a background thread so the HTTP response returns
        instantly and SSE events drive the front-end progress display.
        """
        import threading
        self.ports.store.create_session(request.session_id, request.caller.tenant_id)
        run = self.ports.store.create_run(request)
        self.ports.store.append_event(
            run["run_id"], "user_text", RECEIVED, {"text": request.text}
        )
        # Drive the run forward in a background thread.
        run_id = run["run_id"]
        worker = threading.Thread(
            target=self._advance_safely,
            args=(run_id,),
            name="geopilot-run-" + run_id,
            daemon=True,
        )
        worker.start()
        return self._view(run_id)

    def _advance_safely(self, run_id: str) -> None:
        """Run _advance in a background thread, logging failures."""
        from gateway_py3.logs import write_event
        import traceback
        try:
            self._advance(run_id)
        except Exception as exc:
            tb = traceback.format_exc()[:500]
            write_event("run.advancer_error", {"run_id": run_id,
                        "error": str(exc)[:200], "type": type(exc).__name__,
                        "traceback": tb})
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "advancer",
                       "advance_failed", "%s: %s" % (type(exc).__name__, str(exc)[:200]))

    def inspect(self, run_id: str) -> RunView:
        return self._view(run_id)

    def decide(self, run_id: str, approved: bool) -> RunView:
        row = self.ports.store.get_run(run_id)
        if row["stage"] != AUTHORIZATION_REQUIRED:
            raise ValueError("run is not awaiting a decision: %s" % row["stage"])
        if not approved:
            outcome = outcome_failed(
                contracts.POLICY_DENIED, "authorization", "user_denied",
                "用户拒绝了该操作的授权。",
            )
            self.ports.store.append_event(
                run_id, "authorization_denied", AUTHORIZATION_REQUIRED,
                {"decision": "denied"}, outcome=outcome,
            )
            return self._view(run_id)
        self.ports.store.append_event(
            run_id, "authorization_approved", AUTHORIZED, {"decision": "approved"}
        )
        return self._advance(run_id)

    def resume(self, run_id: str) -> RunView:
        """Drive a paused run forward from its current stage (§7)."""
        return self._advance(run_id)

    # -- state machine driver ----------------------------------------------

    def _advance(self, run_id: str) -> RunView:
        """Advance the run through stages until it pauses or terminates."""
        while True:
            row = self.ports.store.get_run(run_id)
            stage = row["stage"]
            if stage == SUCCEEDED_STAGE or row["outcome_kind"] is not None:
                return self._view(run_id)
            if stage == RECEIVED:
                self._freeze_context(run_id)
                continue
            if stage == CONTEXT_FROZEN:
                self._compile_intent(run_id, row)
                continue
            if stage == INTENT_COMPILED:
                self._verify_plan(run_id, row)
                continue
            if stage == PLAN_VERIFIED:
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

    def _freeze_context(self, run_id: str) -> None:
        if self.ports.context is None:
            raise ValueError("context provider is not wired; cannot freeze context.")
        try:
            snapshot = self.ports.context.capture(run_id)
        except Exception as exc:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "context",
                       "context_capture_error", str(exc))
            return
        self.ports.store.store_context_snapshot(run_id, snapshot)
        self.ports.store.append_event(
            run_id, "context_frozen", CONTEXT_FROZEN,
            {"context_digest": snapshot.digest, "lease_id": snapshot.lease_id},
        )

    def _compile_intent(self, run_id: str, row: Dict[str, Any]) -> None:
        if self.ports.compiler is None:
            raise ValueError("intent compiler is not wired; cannot compile intent.")
        request = self._reconstruct_request(row)
        context = self._load_context(run_id)
        capabilities = self._freeze_capabilities(run_id)
        if context is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "intent",
                       "context_missing", "context snapshot missing for intent compilation")
            return
        outcome = self.ports.compiler.compile(request, context, capabilities)
        if outcome.kind == contracts.CLARIFICATION_REQUIRED:
            self.ports.store.append_event(
                run_id, "clarification_required", INTENT_COMPILED,
                {"message": outcome.message}, outcome=outcome,
            )
            return
        if outcome.is_terminal and not outcome.succeeded:
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
        outcome = self.ports.planner.plan(run_id, intent, context, capabilities)
        if outcome.is_terminal and not outcome.succeeded:
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
        self.ports.store.store_verified_plan(run_id, plan)
        self.ports.store.append_event(
            run_id, "plan_verified", PLAN_VERIFIED,
            {"plan_id": plan.plan_id, "plan_digest": plan.digest},
        )

    def _request_authorization(self, run_id: str, row: Dict[str, Any]) -> None:
        plan = self._load_plan(run_id)
        if plan is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "authorization",
                       "plan_missing", "sealed plan missing for authorization")
            return
        # Full trust mode: PolicyGate pre-checks the plan's risk level against
        # the requested effects. The grant itself is bound to the lease in
        # _acquire_runtime (the grant carries lease_id + epoch).
        if self.ports.policy is not None:
            request = self._reconstruct_request(row)
            precheck = self.ports.policy.precheck(
                request, plan, {"level": _requested_effect_level(request)},
            )
            if precheck.is_terminal and not precheck.succeeded:
                self._fail(run_id, contracts.POLICY_DENIED, "authorization",
                           "policy_denied", precheck.message)
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
        try:
            lease = self.ports.executor.acquire(run_id, plan)
        except Exception as exc:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "runtime",
                       "acquire_failed", str(exc))
            return
        self.ports.store.store_runtime_lease(lease)
        # Issue the real grant bound to this lease (§6.6). The kernel never
        # authorizes itself (§2.3); PolicyGate binds actor + plan + lease.
        if self.ports.policy is not None:
            request = self._reconstruct_request(row)
            granted = self.ports.policy.authorize(
                request, plan, lease,
                {"level": _requested_effect_level(request)},
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
        # Persist the execution receipt so acceptance can inspect it.
        if isinstance(receipt_doc, dict):
            self.ports.store.store_execution_receipt(
                receipt_doc.get("lease_id", lease.lease_id),
                run_id, lease.lease_id, plan.digest,
                receipt_doc.get("status", "executed"),
                receipt_doc.get("result_hash", ""),
                receipt_doc,
            )
        self.ports.store.append_event(
            run_id, "executed", EXECUTED,
            {"summary": outcome.message},
        )

    def _accept(self, run_id: str, row: Dict[str, Any]) -> None:
        if self.ports.acceptance is None:
            raise ValueError("acceptance publisher is not wired; cannot accept.")
        plan = self._load_plan(run_id)
        intent = self._load_intent(run_id)
        runtime_outcome = self.ports.store.get_execution_outcome(run_id)
        if plan is None or intent is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "acceptance",
                       "binding_missing", "plan or intent missing for acceptance")
            return
        outcome = self.ports.acceptance.accept(intent, plan, runtime_outcome)
        if outcome.is_terminal and not outcome.succeeded:
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
        if self.ports.acceptance is None:
            raise ValueError("acceptance publisher is not wired; cannot publish.")
        plan = self._load_plan(run_id)
        grant = self.ports.store.get_authorization_grant(run_id)
        if plan is None or grant is None:
            self._fail(run_id, contracts.INFRASTRUCTURE_FAILED, "publish",
                       "plan_or_grant_missing", "plan or grant missing for publication")
            return
        staged = self.ports.store.list_staged_artifacts(run_id)
        report = self.ports.store.get_acceptance_report(run_id)
        outcome = self.ports.acceptance.publish(staged, report, grant)
        if outcome.is_terminal and not outcome.succeeded:
            self.ports.store.append_event(
                run_id, "publish_failed", PUBLISHED,
                {"reason": outcome.message}, outcome=outcome,
            )
            return
        publication = outcome.details.get("publication")
        if isinstance(publication, dict):
            self.ports.store.store_publication_receipt(
                publication.get("publication_id", str(uuid.uuid4())), run_id,
                grant.grant_id, publication,
            )
        self.ports.store.append_event(
            run_id, "published", PUBLISHED, {},
        )

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
        return self.ports.store.get_context_snapshot(run_id)

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
                paths=tuple(side_effects_doc.get("paths", ())),
                datasets=tuple(side_effects_doc.get("datasets", ())),
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
        )


# --- helpers ----------------------------------------------------------------

def _requested_effect_level(request: RequestEnvelope) -> int:
    """Side-effect level requested by the envelope (side_effects.level)."""
    if request.side_effects is not None:
        return request.side_effects.level
    return 1


def _load_outcome(document: Dict[str, Any]) -> Outcome:
    return Outcome.model_validate(document)

