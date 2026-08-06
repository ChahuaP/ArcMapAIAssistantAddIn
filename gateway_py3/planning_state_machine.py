"""Finite-state controller for the sole G2/G3 planning path."""
from __future__ import annotations

import time

from .audit_contract import AuditContractExhausted
from .dominance_gate import DominanceGate
from .evidence_resolver import EvidenceResolver
from .plan_artifact import PlanArtifact


class PlanningCancelled(Exception):
    """Raised only when a persisted run was cancelled between planning stages."""


class PlanningFaultAfterCancellation(Exception):
    """Carries a real model fault that raced with a persisted cancellation."""

    def __init__(self, error):
        super().__init__(str(error))
        self.error = error


VALIDATING_BASELINE = "validating_baseline"
BASELINE_VALIDATED = "baseline_validated"
AUDITING = "auditing"
REVISING = "revising"
VALIDATING_REVISION = "validating_revision"
TERMINAL = "terminal"
ALLOWED_TRANSITIONS = {
    None: {VALIDATING_BASELINE, BASELINE_VALIDATED},
    VALIDATING_BASELINE: {BASELINE_VALIDATED, REVISING, TERMINAL},
    BASELINE_VALIDATED: {AUDITING, TERMINAL},
    AUDITING: {REVISING, VALIDATING_REVISION, TERMINAL},
    REVISING: {VALIDATING_REVISION, TERMINAL},
    VALIDATING_REVISION: {BASELINE_VALIDATED, AUDITING, REVISING, TERMINAL},
    TERMINAL: set(),
}


class PlanningStateMachine:
    """Owns states, budgets, histories and the one terminal transition for a run."""

    def __init__(self, owner, run_id, planner, planner_role, task_contract, context,
                 capability_cards, protocol, trace, draft, version_id, auditor, request,
                 frozen_artifact=None):
        self.owner = owner
        self.run_id = run_id
        self.planner = planner
        self.planner_role = planner_role
        self.task_contract = task_contract
        self.context = context
        self.capability_cards = capability_cards
        self.protocol = protocol
        self.trace = trace
        self.draft = draft
        self.version_id = version_id
        self.auditor = auditor
        self.request = request
        self.state = None
        self.revision_counts = {"validation": 0, "audit": 0}
        self.revision_origin = None
        self.seen_invalid = set()
        self.seen_audited = set()
        self.seen_repairs = set()
        self.evidence_resolver = None
        self.dominance_gate = DominanceGate()
        self.frozen_artifact = frozen_artifact
        self.semantic_confirmation_pending = False
        self.confirming_semantic_revision = False
        self.candidate_artifact = None
        self.candidate_report = None
        self.baseline_resolved_claims = []
        if frozen_artifact is None:
            self._transition(VALIDATING_BASELINE)
        else:
            self.prepared = draft
            self._transition(BASELINE_VALIDATED, prepared_hash=self.owner.digest(draft), artifact_source="supplied")

    def run(self):
        try:
            while self.state != TERMINAL:
                if self.state in (VALIDATING_BASELINE, VALIDATING_REVISION):
                    row = self._validate()
                elif self.state == BASELINE_VALIDATED:
                    row = self._after_baseline()
                elif self.state == AUDITING:
                    row = self._audit()
                elif self.state == REVISING:
                    row = self._revise()
                else:
                    raise RuntimeError("unknown planning state: %s" % self.state)
                if row is not None:
                    return row
        except PlanningCancelled:
            return self._terminal("cancelled", self.draft, {"outcome": "cancelled"})
        except AuditContractExhausted as exc:
            return self._terminal("failed", self.draft, {
                "kind": "audit_contract_exhausted",
                "message": str(exc),
            })
        raise RuntimeError("terminal state requires a persisted result")

    @staticmethod
    def record_cancelled(trace):
        transitions = trace.setdefault("transitions", [])
        if transitions and transitions[-1].get("state") == TERMINAL:
            return
        if not transitions:
            transitions.append({
                "state": TERMINAL, "at": time.time(), "version_id": None,
                "status": "cancelled",
            })
            return
        current = transitions[-1].get("state")
        if TERMINAL not in ALLOWED_TRANSITIONS.get(current, set()):
            raise RuntimeError("illegal planning cancellation transition: %s -> %s" % (current, TERMINAL))
        transitions.append({
            "state": TERMINAL, "at": time.time(),
            "version_id": transitions[-1].get("version_id"), "status": "cancelled",
        })

    def _transition(self, state, **details):
        if state not in ALLOWED_TRANSITIONS.get(self.state, set()):
            raise RuntimeError("illegal planning transition: %s -> %s" % (self.state, state))
        self.state = state
        record = {"state": state, "at": time.time(), "version_id": self.version_id}
        record.update(details)
        self.trace.setdefault("transitions", []).append(record)

    def _validate(self):
        validation = self.owner._validation(self.draft, self.context, self.trace, self.version_id)
        if not validation["ok"]:
            report = validation.get("report")
            if report and not report["hard_violations"] and report["blocking_clarifications"]:
                return self._terminal("clarify", self.draft, {
                    "kind": "workflow_verifier_clarification",
                    "blocking_clarifications": report["blocking_clarifications"],
                })
            workflow_hash = self.owner.digest(self.draft)
            if workflow_hash in self.seen_invalid:
                self.trace["counts"]["cycles"] += 1
                return self._terminal("failed", self.draft, {
                    "kind": "cyclic_revision", "source": "validation",
                    "message": "Planner returned a previously invalid workflow.",
                })
            self.seen_invalid.add(workflow_hash)
            origin = self.revision_origin if self.state == VALIDATING_REVISION else "validation"
            return self._begin_revision(
                "validation", validation["diagnostic"], self.draft, origin=origin,
            )
        self.prepared = validation["workflow"]
        closure = self.owner.capability_closure(self.task_contract, self.prepared)
        self.capability_cards = list(closure.cards)
        self.owner.record_capability_closure(
            self.trace, closure,
            "validated_%s" % ("baseline" if self.state == VALIDATING_BASELINE else "revision"),
        )
        if self.state == VALIDATING_REVISION and self.revision_origin == "audit":
            report = validation["report"]
            if self.semantic_confirmation_pending:
                report = self.owner.reproducible_baseline_report(
                    self.prepared, self.context, self.task_contract,
                )
                self.candidate_report = report
                self.candidate_artifact = PlanArtifact(
                    self.request, self.context, self.trace["execution_context_hash"],
                    self.capability_cards, self.task_contract, self.prepared, report,
                    self.owner.planning_policy_snapshot(self.protocol),
                )
                self.trace["candidate_plan_artifact"] = self.candidate_artifact.as_dict()
                self.trace["candidate_plan_artifact_hash"] = self.candidate_artifact.hash
                self.semantic_confirmation_pending = False
                self.confirming_semantic_revision = True
                self._transition(AUDITING, prepared_hash=self.owner.digest(self.prepared), audit_subject="semantic_candidate")
                return None
            dominance = self.dominance_gate.admit(
                self.trace["plan_artifact"], report, self.resolved_claims,
            )
            self.trace["dominance_reports"].append({
                "baseline_artifact_hash": self.trace["plan_artifact_hash"],
                "candidate_version_id": self.version_id,
                "candidate_workflow_hash": self.owner.digest(self.prepared),
                **dominance,
            })
            if not dominance["accepted"]:
                return self._terminal("failed", self.prepared, {
                    "kind": "non_dominating_audit_revision", "dominance": dominance,
                })
            return self._complete_planned(self.prepared)
        if self.state == VALIDATING_BASELINE or (
            self.state == VALIDATING_REVISION and self.revision_origin == "validation"
        ):
            report = validation.get("report")
            if report is None:
                raise RuntimeError("G2/G3 baseline must have a WorkflowVerifier report.")
            report = self.owner.reproducible_baseline_report(
                self.prepared, self.context, self.task_contract,
            )
            artifact = PlanArtifact(
                self.request,
                self.context,
                self.trace["execution_context_hash"],
                self.capability_cards,
                self.task_contract,
                self.prepared,
                report,
                self.owner.planning_policy_snapshot(self.protocol),
            )
            self.trace["plan_artifact"] = artifact.as_dict()
            self.trace["plan_artifact_hash"] = artifact.hash
            self._transition(BASELINE_VALIDATED, prepared_hash=self.owner.digest(self.prepared))
        return self._after_baseline()

    def _after_baseline(self):
        if self.auditor is None:
            return self._complete_planned(self.prepared)
        prepared_hash = self.owner.digest(self.prepared)
        if prepared_hash in self.seen_audited:
            self.trace["counts"]["cycles"] += 1
            return self._terminal("failed", self.prepared, {
                "kind": "cyclic_revision", "source": "audit",
                "message": "Planner returned a previously audited workflow.",
            })
        self.seen_audited.add(prepared_hash)
        self._transition(AUDITING, prepared_hash=prepared_hash)
        return None

    def _audit(self):
        self.owner._check_cancel(self.run_id)
        audited_artifact = (
            self.candidate_artifact.as_dict()
            if self.confirming_semantic_revision else self.trace["plan_artifact"]
        )
        response = self.owner._call(self.run_id, self.auditor, "auditor", {
            "plan_artifact": audited_artifact,
            "plan_artifact_hash": audited_artifact["artifact_hash"],
        }, self.trace, "audit", self._evaluate_audit_response)
        audit_selection = response["audit_result"]
        self.evidence_resolver = EvidenceResolver(audited_artifact)
        audit = dict(audit_selection)
        audit["claims"] = self.evidence_resolver.materialize(
            audit_selection["claims"], audited_artifact["baseline_verifier_report"],
        )
        resolution = self.evidence_resolver.resolve(
            audit["claims"], audited_artifact["baseline_verifier_report"],
        )
        self.trace["audits"].append({
            "version_id": self.version_id, "workflow_hash": self.owner.digest(self.prepared),
            "baseline_artifact_hash": self.trace["plan_artifact_hash"],
            "audited_artifact_hash": audited_artifact["artifact_hash"], "raw": audit_selection,
            **audit, **resolution,
        })
        if resolution["rejected_claims"]:
            return self._terminal("failed", self.prepared, {
                "kind": "unreproducible_audit_claim", "rejected_claims": resolution["rejected_claims"],
            })
        if audit["decision"] == "pass":
            if self.confirming_semantic_revision:
                dominance = self.dominance_gate.admit(
                    self.trace["plan_artifact"], self.candidate_report,
                    self.baseline_resolved_claims, confirmed_alignment=True,
                )
                self.trace["dominance_reports"].append({
                    "baseline_artifact_hash": self.trace["plan_artifact_hash"],
                    "candidate_version_id": self.version_id,
                    "candidate_workflow_hash": self.owner.digest(self.prepared),
                    "candidate_task_contract_hash": self.candidate_artifact.as_dict()["task_contract_hash"],
                    "semantic_confirmation_audit": True,
                    **dominance,
                })
                if not dominance["accepted"]:
                    return self._terminal("failed", self.prepared, {
                        "kind": "non_dominating_semantic_revision", "dominance": dominance,
                    })
                return self._complete_planned(self.prepared)
            return self._complete_planned(self.prepared)
        if audit["decision"] in ("clarify", "reject"):
            return self._terminal(audit["decision"], self.prepared, audit)
        self.resolved_claims = resolution["resolved_claims"]
        alignment_claims = [
            item for item in self.resolved_claims
            if item["proof"].get("code") == "request_alignment.unresolved"
        ]
        task_contract_claims = [
            item for item in self.resolved_claims
            if item["claim"]["change_target"] == "task_contract"
        ]
        workflow_claims = [
            item for item in self.resolved_claims
            if item["claim"]["change_target"] == "workflow"
        ]
        confirmation_required = bool(task_contract_claims or alignment_claims)
        self.trace.setdefault("audit_routes", []).append({
            "version_id": self.version_id,
            "task_contract_claims": len(task_contract_claims),
            "workflow_claims": len(workflow_claims),
            "semantic_confirmation_required": confirmation_required,
        })
        if confirmation_required:
            if not self.baseline_resolved_claims:
                self.baseline_resolved_claims = list(self.resolved_claims)
            self.semantic_confirmation_pending = True
            self.confirming_semantic_revision = False
        if task_contract_claims:
            task_contract_audit = dict(audit)
            task_contract_audit["claims"] = [item["claim"] for item in task_contract_claims]
            revised_task_contract = self.owner._request_task_contract_revision(
                self.run_id, self.planner, self.task_contract, self.context,
                self.protocol, self.trace,
                {"audit": task_contract_audit, "resolved_claims": task_contract_claims}, self.request,
            )
            if self.owner.digest(revised_task_contract) == self.owner.digest(self.task_contract):
                return self._terminal("failed", self.prepared, {
                    "kind": "stalled_semantic_revision",
                    "message": "Semantic planner returned the identical TaskContract.",
                })
            self.task_contract = revised_task_contract
            closure = self.owner.capability_closure(revised_task_contract, self.prepared)
            self.capability_cards = list(closure.cards)
            self.owner.record_capability_closure(
                self.trace, closure, "task_contract_revision",
            )
            self.trace["task_contract"] = revised_task_contract
            self.trace.setdefault("task_contract_versions", []).append({
                "source": "audit_revision", "task_contract": revised_task_contract,
                "hash": self.owner.digest(revised_task_contract),
            })
        if workflow_claims:
            workflow_audit = dict(audit)
            workflow_audit["claims"] = [item["claim"] for item in workflow_claims]
            return self._begin_revision("audit", workflow_audit, self.prepared)
        if task_contract_claims:
            self.draft = self.prepared
            self.revision_origin = "audit"
            self._transition(
                VALIDATING_REVISION,
                source="audit",
                origin="audit",
                semantic_contract_revision=True,
                revision_hash=self.owner.digest(self.draft),
            )
            return None
        raise RuntimeError("revise audit did not select a change target")

    def _evaluate_audit_response(self, response):
        return {
            "audit_result": self.owner.audit_contract.validate_shape(response["audit_result"])
        }

    def _begin_revision(self, source, diagnostic, draft, origin=None):
        origin = source if origin is None else origin
        if origin not in {"validation", "audit"}:
            raise RuntimeError("unknown revision origin: %s" % origin)
        key = (self.owner.digest(draft), self.owner.digest(diagnostic), source)
        if key in self.seen_repairs:
            self.trace["counts"]["stalls"] += 1
            return self._terminal("failed", draft, {
                "kind": "stalled_revision", "source": source,
                "message": "Repeated workflow and diagnostic.",
            })
        self.seen_repairs.add(key)
        limit = self.owner.validation_revision_limit if source == "validation" else self.owner.audit_revision_limit
        if self.revision_counts[source] >= limit:
            return self._terminal("failed", draft, {
                "kind": "revision_budget_exhausted", "source": source, "diagnostic": diagnostic,
            })
        self.revision_counts[source] += 1
        self.trace["counts"][source + "_revisions"] = self.revision_counts[source]
        self.draft = draft
        self.diagnostic = diagnostic
        self.revision_source = source
        self.revision_origin = origin
        self._transition(
            REVISING, source=source, origin=origin,
            revision=self.revision_counts[source],
        )
        return None

    def _revise(self):
        self.owner._check_cancel(self.run_id)
        revised = self.owner._request_revision(
            self.run_id, self.planner, self.task_contract, self.context,
            self.capability_cards, self.protocol, self.trace, self.draft, self.diagnostic,
            self.request,
        )
        if self.owner.digest(revised) == self.owner.digest(self.draft):
            self.trace["counts"]["stalls"] += 1
            return self._terminal("failed", revised, {
                "kind": "stalled_revision", "source": self.revision_source,
                "message": "Planner returned the identical workflow.",
            })
        self.draft = revised
        self.version_id = self.owner._record_workflow(self.trace, revised, self.planner_role)
        self._transition(
            VALIDATING_REVISION, source=self.revision_source, origin=self.revision_origin,
            revision_hash=self.owner.digest(revised),
        )
        return None

    def _complete_planned(self, workflow):
        return self._terminal_record("planned", workflow, {"outcome": "planned"})

    def _terminal(self, status, workflow, detail):
        return self._terminal_record(status, workflow, detail)

    def _terminal_record(self, status, workflow, detail):
        self._transition(TERMINAL, status=status, workflow_hash=self.owner.digest(workflow))
        self.trace["terminal"] = {"stage": "planning", "status": status, "detail": detail}
        if status == "planned":
            return self.owner._persist_planned_run(self.run_id, workflow, self.trace)
        self.owner.store.update_run(self.run_id, status, workflow=workflow, trace=self.trace,
                                    result={"terminal": detail})
        return self.owner.store.get(self.run_id)
