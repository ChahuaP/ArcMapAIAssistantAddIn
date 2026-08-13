"""WorkflowEngine: plan, verify and seal a VerifiedPlan (§6.3, §13.2).

Fixed flow is a LangGraph ``StateGraph``: draft -> deterministic validation ->
bounded repair -> G3 audit -> necessary revision -> final validation -> seal.
G3 audit only points out specific, verifiable problems; it never rewrites the
user goal, adds unasked outputs, relaxes validation, or declares success. The
deterministic verifier always makes the final ruling.

LangGraph integration (§13.2):
- The graph is compiled with a ``SqliteSaver`` checkpointer; ``thread_id``
  equals the kernel ``run_id``. ``plan()`` first checks the checkpoint for an
  already-sealed plan and reuses it, so a crash-restarted run never repeats
  model calls.
- ``stream_mode="updates"`` is the sole production planning path; its node
  updates are persisted by the graph checkpoint.
- Repair/audit loops are conditional edges with explicit budget counters in
  the state; exceeding the budget terminates with ``ContractFailed``. No
  LangGraph auto-retry is used: model calls are classified by ModelRuntime
  (quota_stopped / failed / uncertain) and surface as terminal outcomes.

State is JSON-serializable (dicts/strings/ints only) so the checkpoint stays
portable; Pydantic contracts are re-validated inside nodes.
"""
from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from ..kernel import contracts
from ..kernel.contracts import (
    CapabilitySnapshot, ContextSnapshot, IntentSpec, Outcome, context_verifier_view,
    VerifiedPlan, WorkflowStep, outcome_succeeded, outcome_failed,
    SUCCEEDED, CONTRACT_FAILED, CAPABILITY_FAILED, INFRASTRUCTURE_FAILED,
    QUOTA_STOPPED, MODEL_CALL_UNCERTAIN,
)
from ..model_runtime import ModelRuntime, ModelRequest
from .structured_outputs import PlannerDraftModel, RepairDraftModel, AuditResultModel
from ..structured_contracts import (
    workflow_tools_for_capabilities,
    workflow_capability_index,
    operation_id_from_tool,
)
from ..task_contract import task_contract_model_view
from ..workflow_protocol import workflow_protocol
from ..workflow_verifier import WorkflowVerifier
from ..plan_revision import PlanRevision, PlanRevisionError, MonotonicPlanValidator, revision_scope
from ..audit_contract import AUDIT_CONTRACT, audit_contract_for_scope
from ..validators import ValidationError, prepare_workflow
from pydantic import ValidationError as PydanticValidationError
from .dpapi_serde import DpapiCheckpointSerializer

PROMPT_VERSION = "workflow-engine-v2"
# Stable tenant for the model-call cache key (§6.4). The cache is content-
# addressed: identical pure planning inputs must hit the cache across runs and
# sessions. Binding the key to session_id would void the cache whenever the
# user clears history, so the tenant is the stable local operator, not session.
PLANNER_SYSTEM = (
    "你是 GeoPilot 工作流规划器。根据任务合同和当前地图上下文，选择合适的工具构建工作流。\n"
    "每个工具代表一个 GIS 操作，其参数 schema 已定义了必填字段和类型。\n"
    "按执行顺序调用工具，每个 tool_call 代表工作流中的一步。"
)
REPAIR_SYSTEM = (
    "你是 GeoPilot 工作流修复器。根据验证报告修复工作流草案，不要改变任务目标。"
)
AUDITOR_SYSTEM = (
    "你是 GeoPilot G3 审计器。审查计划草案，只指出具体、可验证的问题。\n"
    "不能改写用户目标、增加未要求成果、放宽验证或自行判定正确。"
)

MAX_VALIDATION_REVISIONS = 3
MAX_AUDIT_REVISIONS = 3


class _ModelOutcome(Exception):
    """Carries a money-sensitive terminal Outcome kind out of a model call.

    Raised when ModelRuntime returns quota_stopped / uncertain so the planning
    nodes route them to the matching terminal outcome instead of flattening
    them into ContractFailed / InfrastructureFailed (which would lose the
    no-retry / adjudication semantics).
    """
    def __init__(self, kind: str, stage: str, code: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.stage = stage
        self.code = code
        self.message = message


class _ModelPlanContractError(ValueError):
    """The checkpoint binding is incompatible with this sealed task."""


class _CheckpointContractError(ValueError):
    """The persisted planning state cannot be safely read or validated."""


class PlanState(TypedDict, total=False):
    """JSON-serializable state for the planning graph (§13.2).

    Pydantic contracts travel as ``model_dump(mode='json')`` dicts so the
    SqliteSaver checkpoint stays portable; nodes re-validate on entry.
    """
    run_id: str
    intent: Dict[str, Any]
    context: Dict[str, Any]
    capabilities: Dict[str, Any]
    task_contract: Dict[str, Any]
    verifier_context: Dict[str, Any]
    draft: Dict[str, Any]
    workflow: Optional[Dict[str, Any]]
    report: Dict[str, Any]
    validation_revisions: int
    audit_revisions: int
    auditor_enabled: bool
    audit_forced: bool
    audit_decision: Optional[str]
    audit_result: Dict[str, Any]
    audit_scope: Dict[str, Tuple[str, ...]]
    audit_options: Dict[str, Tuple[str, ...]]
    audit_baseline_workflow: Optional[Dict[str, Any]]
    audit_baseline_report: Dict[str, Any]
    plan: Optional[Dict[str, Any]]
    failure: Optional[Dict[str, Any]]
    done: bool
    decision: Optional[Dict[str, Any]]
    authorization_result: Optional[str]
    node_attempts: Dict[str, int]
    sealed_baseline: Optional[Dict[str, Any]]
    sealed_baseline_plan: Optional[Dict[str, Any]]
    model_plan_digest: str


class WorkflowEngine:
    """§6.3 WorkflowEngine: plan + verify + audit + seal (LangGraph).

    Holds a catalog for deterministic validation, a ModelRuntime for draft /
    repair / audit model calls, and a ``SqliteSaver`` checkpointer bound to the
    JournalStore database file. The kernel calls ``plan(run_id, ...)``; the
    engine never touches the store directly (§6.1: kernel owns persistence).
    """

    def __init__(self, catalog, model_runtime: ModelRuntime,
                 checkpoint_path: Optional[Path] = None,
                 journal=None, checkpointer=None):
        self.catalog = catalog
        self.model_runtime = model_runtime
        self.verifier = WorkflowVerifier(catalog)
        self.protocol = workflow_protocol()
        self._checkpoint_path = checkpoint_path
        self._journal = journal
        if checkpointer is not None and checkpoint_path is not None:
            raise ValueError("inject either checkpointer or checkpoint_path, never both")
        self._graph = self._build_graph()
        self._checkpointer = checkpointer
        self._app = None

    @property
    def runtime_identity(self) -> Dict[str, Dict[str, Any]]:
        return self.model_runtime.runtime_identity

    # -- LangGraph construction (§13.2) ------------------------------------

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(PlanState)
        graph.add_node("draft", self._journaled("draft", self._node_draft))
        graph.add_node("validate", self._journaled("validate", self._node_validate))
        graph.add_node("repair", self._journaled("repair", self._node_repair))
        graph.add_node("audit", self._journaled("audit", self._node_audit))
        graph.add_node("revise", self._journaled("revise", self._node_revise))
        graph.add_node("seal", self._journaled("seal", self._node_seal))
        graph.add_node("authorization_required", self._journaled("authorization_required", self._node_authorization_required))
        graph.add_node("authorization_auto", self._journaled("authorization_auto", self._node_authorization_auto))
        graph.add_node("fail", self._journaled("fail", self._node_fail))
        graph.add_edge(START, "draft")
        graph.add_edge("draft", "validate")
        graph.add_conditional_edges(
            "validate", self._route_validate,
            {"audit": "audit", "seal": "seal", "repair": "repair", "fail": "fail"},
        )
        graph.add_edge("repair", "validate")
        graph.add_conditional_edges(
            "audit", self._route_audit,
            {"seal": "seal", "revise": "revise", "fail": "fail"},
        )
        graph.add_edge("revise", "audit")
        graph.add_conditional_edges("seal", self._route_authorization,
                                    {"authorization_required": "authorization_required",
                                     "authorization_auto": "authorization_auto"})
        graph.add_edge("authorization_required", END)
        graph.add_edge("authorization_auto", END)
        graph.add_edge("fail", END)
        return graph

    def _compiled(self):
        if self._app is None:
            if self._checkpointer is not None:
                pass
            elif self._checkpoint_path is None:
                # In-memory checkpointer: run completes within one call, no
                # cross-call recovery needed (tests use this path).
                conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._checkpointer = SqliteSaver(conn, serde=DpapiCheckpointSerializer())
            else:
                conn = sqlite3.connect(
                    str(self._checkpoint_path), check_same_thread=False
                )
                self._checkpointer = SqliteSaver(conn, serde=DpapiCheckpointSerializer())
            self._app = self._graph.compile(checkpointer=self._checkpointer,
                                            interrupt_before=["authorization_required"])
        return self._app

    def _config(self, run_id: str) -> Dict[str, Any]:
        lineage = self._journal.current_planning_lineage(run_id) \
            if self._journal is not None else run_id
        return {"configurable": {"thread_id": lineage}}

    # -- public API ---------------------------------------------------------

    def plan(self, run_id: str, intent: IntentSpec, context: ContextSnapshot,
             capabilities: CapabilitySnapshot) -> Outcome:
        """Plan, verify, audit and seal (§6.3). Reuses a sealed checkpoint.

        Returns a succeeded Outcome carrying the VerifiedPlan, or a terminal
        Outcome (ContractFailed / CapabilityFailed / quota / uncertain).
        """
        return self.plan_ablation(run_id, intent, context, capabilities, auditor_enabled=True)

    def plan_ablation(self, run_id: str, intent: IntentSpec, context: ContextSnapshot,
                      capabilities: CapabilitySnapshot, auditor_enabled: bool,
                      sealed_baseline: Optional[VerifiedPlan] = None,
                      force_audit: bool = False) -> Outcome:
        """Plan an ablation arm on the production graph.

        The graph and all planner parameters remain fixed.  G2 only bypasses
        the auditor decision; it does not receive a different planner,
        validator, revision budget, cache scope, or workflow topology.
        """
        task_contract = intent.derived_facts.get("task_contract")
        if not isinstance(task_contract, dict):
            return outcome_failed(
                CONTRACT_FAILED, "plan", "missing_task_contract",
                "IntentSpec 没有携带 task_contract，无法规划。",
            )
        # Reuse an already-sealed plan from the checkpoint (§13.2: a
        # crash-restarted run must not repeat sealed model calls).
        expected_model_plan_digest = _intent_model_plan_digest(intent)
        try:
            existing = self._sealed_plan(run_id, expected_model_plan_digest)
        except _ModelPlanContractError as exc:
            return outcome_failed(CONTRACT_FAILED, "plan", "model_plan_drift", str(exc))
        except _CheckpointContractError as exc:
            return outcome_failed(CONTRACT_FAILED, "plan", "checkpoint_contract_failure", str(exc))
        if existing is not None:
            return outcome_succeeded("plan", "计划已封存，复用检查点。",
                                     details={"plan": existing,
                                              "topology_signature": self.ablation_topology_signature()})

        state: PlanState = {
            "run_id": run_id,
            "intent": intent.model_dump(mode="json"),
            "model_plan_digest": expected_model_plan_digest,
            "context": context.model_dump(mode="json"),
            "capabilities": capabilities.model_dump(mode="json"),
            "task_contract": task_contract,
            "verifier_context": context_verifier_view(context),
            "draft": None,
            "workflow": None,
            "report": {},
            "validation_revisions": 0,
            "audit_revisions": 0,
            "auditor_enabled": bool(auditor_enabled),
            "audit_forced": bool(force_audit or sealed_baseline is not None),
            "audit_decision": None,
            "audit_result": {},
            "audit_scope": {},
            "audit_options": {},
            "audit_baseline_workflow": None,
            "audit_baseline_report": {},
            "plan": None,
            "failure": None,
            "done": False,
            "decision": None,
            "authorization_result": None,
            "node_attempts": {},
            "sealed_baseline": _baseline_draft(sealed_baseline) if sealed_baseline is not None else None,
            "sealed_baseline_plan": sealed_baseline.model_dump(mode="json") if sealed_baseline is not None else None,
        }
        # The production path is the graph stream.  Its checkpoint is the
        # source of the final state, so a process interruption cannot leave a
        # hand-written planner state separate from LangGraph.
        app = self._compiled()
        prior = app.get_state(self._config(run_id))
        resume = (prior is not None and isinstance(prior.values, dict)
                  and "model_plan_digest" in prior.values)
        if resume and prior.values.get("model_plan_digest") != expected_model_plan_digest:
            return outcome_failed(CONTRACT_FAILED, "plan", "model_plan_drift",
                                  "规划检查点的模型绑定与当前任务封存绑定不一致。")
        stream_input = None if resume else state
        for _chunk in app.stream(stream_input, config=self._config(run_id), stream_mode="updates"):
            pass
        snapshot = app.get_state(self._config(run_id))
        final_state = snapshot.values if snapshot is not None else None
        if not isinstance(final_state, dict):
            return outcome_failed(CONTRACT_FAILED, "plan", "checkpoint_missing",
                                  "规划图未保存最终检查点。")
        outcome = self._outcome_from_state(final_state)
        if outcome.succeeded:
            return outcome.model_copy(update={"details": dict(outcome.details,
                                      topology_signature=self.ablation_topology_signature())})
        return outcome

    def ablation_topology_signature(self) -> str:
        """Hash the compiled graph after normalizing the sole varied auditor node."""
        nodes = set(self._graph.nodes.keys())
        edges = {(str(left), str(right)) for left, right in self._graph.edges}
        nodes.discard("audit")
        edges = {(left, right) for left, right in edges if left != "audit" and right != "audit"}
        document = {
            "nodes": sorted(nodes), "edges": sorted(edges),
            "validation_revision_budget": MAX_VALIDATION_REVISIONS,
            "audit_revision_budget": MAX_AUDIT_REVISIONS,
            "model_plan": self.model_runtime.runtime_identity,
            "cache_scope": "content_addressed",
            "protocol": self.protocol,
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def decide_authorization(self, run_id: str, approved: bool) -> str:
        """Resume the same checkpoint that interrupted before authorization."""
        app = self._compiled()
        config = self._config(run_id)
        snapshot = app.get_state(config)
        if snapshot is None or not isinstance(snapshot.values, dict):
            raise ValueError("authorization checkpoint is missing")
        state = snapshot.values
        if not isinstance(state.get("plan"), dict):
            raise ValueError("authorization checkpoint has no sealed plan")
        app.update_state(config, {"decision": {"approved": bool(approved)}})
        # This is the only authorization resume path: the graph advances from
        # the interrupt checkpoint, not from a kernel-side boolean state.
        for _chunk in app.stream(None, config=config, stream_mode="updates"):
            pass
        result = app.get_state(config).values.get("authorization_result")
        if result not in ("authorized", "denied"):
            raise ValueError("authorization graph did not produce a result")
        return result

    def _journaled(self, node: str, handler):
        """Persist node facts in the node boundary, never from stream yields."""
        def wrapped(state: PlanState):
            attempts = dict(state.get("node_attempts") or {})
            attempt = int(attempts.get(node, 0)) + 1
            if self._journal is not None and isinstance(state.get("run_id"), str):
                recorded = self._journal.get_planning_node_update(state["run_id"], node, attempt)
                if recorded is not None:
                    update = dict(recorded["update"])
                    attempts[node] = attempt
                    update["node_attempts"] = attempts
                    if recorded.get("exact") is False:
                        self._journal.record_planning_node_update(
                            state["run_id"], node, attempt, "replayed", update,
                            replayed_from_lineage=recorded["replayed_from_lineage"])
                    return update
            update = handler(state)
            update = dict(update or {})
            attempts[node] = attempt
            update["node_attempts"] = attempts
            if self._journal is not None and isinstance(state.get("run_id"), str):
                status = "failed" if isinstance(update, dict) and update.get("failure") else "succeeded"
                self._journal.record_planning_node_update(state["run_id"], node, attempt, status, update)
            return update
        return wrapped

    # -- graph nodes --------------------------------------------------------

    def _node_draft(self, state: PlanState) -> Dict[str, Any]:
        sealed_baseline = state.get("sealed_baseline")
        if sealed_baseline is not None:
            # G3 starts from the exact G2-sealed workflow. Only its audit node
            # can request a deterministic, bounded revision afterwards.
            return {"draft": sealed_baseline}
        intent = IntentSpec.model_validate(state["intent"])
        context = ContextSnapshot.model_validate(state["context"])
        capabilities = CapabilitySnapshot.model_validate(state["capabilities"])
        try:
            draft = self._generate_draft(intent, context, capabilities,
                                         state["task_contract"],
                                         state.get("run_id", ""))
        except _ModelOutcome as exc:
            return {"failure": _failure_document(outcome_failed(
                exc.kind, exc.stage, exc.code, exc.message))}
        except ValidationError as exc:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "draft_invalid",
                "工作流草案结构无效：%s" % str(exc)))}
        except Exception as exc:
            return {"failure": _failure_document(outcome_failed(
                INFRASTRUCTURE_FAILED, "plan", "draft_infrastructure",
                "%s: %s" % (type(exc).__name__, str(exc))))}
        return {"draft": draft}

    def _node_validate(self, state: PlanState) -> Dict[str, Any]:
        if state.get("failure"):
            return {}
        draft = state.get("draft") or state.get("workflow")
        if draft is None:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "missing_draft",
                "缺少工作流草案，无法验证。"))}
        workflow, report = self._validate(draft, state["verifier_context"],
                                          state["task_contract"])
        return {"workflow": workflow, "report": report}

    def _node_repair(self, state: PlanState) -> Dict[str, Any]:
        if state.get("failure"):
            return {}
        intent = IntentSpec.model_validate(state["intent"])
        context = ContextSnapshot.model_validate(state["context"])
        capabilities = CapabilitySnapshot.model_validate(state["capabilities"])
        draft = state.get("draft") or state.get("workflow")
        if draft is None:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "missing_draft",
                "缺少工作流草案，无法修复。"))}
        try:
            repaired = self._request_repair(intent, context, capabilities,
                                            state["task_contract"], draft,
                                            state["report"], state.get("run_id", ""))
        except _ModelOutcome as exc:
            return {"failure": _failure_document(outcome_failed(
                exc.kind, exc.stage, exc.code, exc.message))}
        if repaired is None:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "repair_failed",
                "修复模型调用失败。" ))}
        return {"draft": repaired,
                "validation_revisions": state.get("validation_revisions", 0) + 1}

    def _node_audit(self, state: PlanState) -> Dict[str, Any]:
        if state.get("failure"):
            return {}
        intent = IntentSpec.model_validate(state["intent"])
        context = ContextSnapshot.model_validate(state["context"])
        capabilities = CapabilitySnapshot.model_validate(state["capabilities"])
        workflow = state.get("workflow")
        if workflow is None:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "missing_workflow",
                "缺少已验证工作流，无法审计。"))}
        scope = revision_scope(workflow, state["report"], self.catalog)
        # The server generates every unresolved clarification node from the
        # real task contract; each node binds one-to-one to the in-scope proof
        # whose contract path equals the node's sealed target path.  No string
        # token guessing, no prefix search, no fail-open fallback.
        from ..clarification import build_nodes
        task_contract = intent.derived_facts.get("task_contract") if isinstance(intent.derived_facts, dict) else {}
        if not isinstance(task_contract, dict):
            task_contract = {}
        proof_graph = state["report"].get("proof_graph", ()) or []
        nodes, _graph_digest = build_nodes(task_contract)
        option_proofs = {}
        for node in nodes:
            matches = []
            for graph_node in proof_graph:
                if not isinstance(graph_node, dict):
                    continue
                detail = graph_node.get("detail") if isinstance(graph_node.get("detail"), dict) else {}
                contract_path = detail.get("contract_path") if isinstance(detail.get("contract_path"), str) else ""
                if graph_node.get("proof_id") in scope and contract_path == node.target_path:
                    matches.append(graph_node["proof_id"])
            option_proofs[node.proof_id] = tuple(sorted(set(matches)))
        try:
            audit_result = self._request_audit(intent, context, capabilities,
                                               workflow, state["report"], scope, option_proofs,
                                               state.get("run_id", ""))
        except _ModelOutcome as exc:
            return {"failure": _failure_document(outcome_failed(
                exc.kind, exc.stage, exc.code, exc.message))}
        if audit_result is None:
            return {"failure": _failure_document(outcome_failed(
                INFRASTRUCTURE_FAILED, "plan", "audit_call_failed",
                "审计模型调用失败。" ))}
        try:
            AUDIT_CONTRACT.validate_shape(audit_result)
        except Exception as exc:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "audit_invalid_result", str(exc)))}
        decision = audit_result["decision"]
        if decision == "revise":
            proof_id = (audit_result.get("revision") or {}).get("proof_id")
            if proof_id not in scope:
                return {"failure": _failure_document(outcome_failed(
                    CONTRACT_FAILED, "plan", "audit_proof_out_of_scope",
                    "审计选择了不属于未解决证明义务的 proof_id。"))}
        if decision == "clarify":
            option_id = (audit_result.get("clarification") or {}).get("option_id")
            if option_id not in option_proofs:
                return {"failure": _failure_document(outcome_failed(
                    CONTRACT_FAILED, "plan", "audit_option_out_of_scope",
                    "审计选择了不属于未解决证明义务的 option_id。"))}
        return {"audit_decision": decision, "audit_result": audit_result,
                "audit_scope": scope,
                "audit_options": option_proofs,
                "audit_baseline_workflow": state.get("audit_baseline_workflow") or workflow,
                "audit_baseline_report": state.get("audit_baseline_report") or state["report"],
                "audit_revisions": state.get("audit_revisions", 0) + 1}

    def _node_revise(self, state: PlanState) -> Dict[str, Any]:
        result = state.get("audit_result") or {}
        revision_doc = result.get("revision") if isinstance(result, dict) else None
        try:
            revision = PlanRevision(**revision_doc)
            candidate = MonotonicPlanValidator.apply(state["workflow"], revision, state.get("audit_scope", {}))
            prepared, candidate_report = self._validate(candidate, state["verifier_context"], state["task_contract"])
            if prepared is None:
                raise PlanRevisionError("revision fails deterministic verification")
            MonotonicPlanValidator.validate(state["audit_baseline_workflow"], prepared,
                                            state["audit_baseline_report"], candidate_report,
                                            revision.proof_id, revision)
        except (PlanRevisionError, TypeError, ValidationError) as exc:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "audit_revision_rejected", str(exc)))}
        return {"workflow": prepared, "report": candidate_report}

    def _node_seal(self, state: PlanState) -> Dict[str, Any]:
        intent = IntentSpec.model_validate(state["intent"])
        context = ContextSnapshot.model_validate(state["context"])
        capabilities = CapabilitySnapshot.model_validate(state["capabilities"])
        workflow = state.get("workflow")
        if workflow is None:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "missing_workflow",
                "缺少已验证工作流，无法封存。"))}
        unresolved = [item for item in state.get("report", {}).get("proof_graph", ())
                      if isinstance(item, dict) and item.get("detail", {}).get("required") is True
                      and item.get("status") != "Proven"]
        if unresolved:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "required_proofs_unresolved",
                "封存计划前必须证明所有必需语义义务。",
                details={"proof_ids": [item.get("proof_id") for item in unresolved]}))}
        if state.get("sealed_baseline_plan") is not None and state.get("audit_decision") == "pass":
            baseline = VerifiedPlan.model_validate(state["sealed_baseline_plan"])
            if _workflow_identity(_baseline_draft(baseline)) != _workflow_identity(workflow):
                return {"failure": _failure_document(outcome_failed(
                    CONTRACT_FAILED, "plan", "audit_pass_changed_baseline",
                    "审计 pass 时基线工作流不得变化。"))}
            return {"plan": baseline.model_dump(mode="json"), "done": True}
        audit_opinion = state.get("audit_result")
        audit_opinions: Tuple[Dict[str, Any], ...] = ()
        if isinstance(audit_opinion, dict):
            audit_opinions = (dict(audit_opinion),)
        plan = self._seal(intent, context, capabilities, workflow,
                          state["report"], audit_opinions=audit_opinions)
        return {"plan": plan.model_dump(mode="json"), "done": True}

    def _node_authorization_required(self, state: PlanState) -> Dict[str, Any]:
        decision = state.get("decision")
        if not isinstance(decision, dict) or "approved" not in decision:
            raise ValueError("authorization decision is required after interrupt")
        return {"authorization_result": "authorized" if decision["approved"] else "denied"}

    @staticmethod
    def _node_authorization_auto(state: PlanState) -> Dict[str, Any]:
        return {"authorization_result": "authorized"}

    def _node_fail(self, state: PlanState) -> Dict[str, Any]:
        if state.get("failure") is None:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "plan_failed",
                "规划失败。" ))}
        return {"done": True}

    # -- conditional routing (§6.3) ----------------------------------------

    def _route_validate(self, state: PlanState) -> str:
        if state.get("failure"):
            return "fail"
        if state.get("workflow") is not None and self._should_audit(state):
            return "audit"
        if state.get("workflow") is not None:
            return "seal"
        if state.get("validation_revisions", 0) < MAX_VALIDATION_REVISIONS:
            return "repair"
        # Repair budget exhausted; produce a meaningful failure from the
        # last validation report so the user sees the real reason.
        report = state.get("report") or {}
        violations = report.get("hard_violations", [])
        if violations:
            detail = "; ".join(
                v.get("message", v.get("code", "")) for v in violations[:3]
            )
        else:
            detail = "工作流验证未通过。"
        state["failure"] = _failure_document(outcome_failed(
            CONTRACT_FAILED, "plan", "validation_exhausted", detail,
        ))
        return "fail"

    @staticmethod
    def _should_audit(state: PlanState) -> bool:
        """Deterministic G3 trigger policy; models and UI cannot override it.

        G2 disables auditing. G3 experiment runs force it. Production audits
        unresolved ProofGraph obligations, plans with risk/effects beyond
        read-only, and workflows whose multi-step or lineage topology makes a
        one-step proof insufficient.
        """
        if not state.get("auditor_enabled", True):
            return False
        if state.get("audit_forced", False):
            return True
        report = state.get("report") or {}
        graph = report.get("proof_graph", ())
        if any(isinstance(item, dict) and item.get("status") == "Unresolved" for item in graph):
            return True
        intent = state.get("intent") or {}
        if int(intent.get("acceptable_side_effects", 1)) >= 2:
            return True
        if any(effect != "read_only" for effect in report.get("side_effects", ())):
            return True
        workflow = state.get("workflow") or {}
        if len(workflow.get("steps", ())) > 1:
            return True
        return any(isinstance(result, dict) and len(result.get("proof", {}).get("lineage_steps", ())) > 1
                   for result in report.get("requirements", ()))

    def _route_audit(self, state: PlanState) -> str:
        if state.get("failure"):
            return "fail"
        decision = state.get("audit_decision", "pass")
        if decision == "pass":
            return "seal"
        if decision == "clarify":
            clarification = (state.get("audit_result") or {}).get("clarification", {})
            option_id = clarification.get("option_id", "")
            state["failure"] = _failure_document(contracts.outcome_paused(
                contracts.CLARIFICATION_REQUIRED, "plan", "audit_clarification_required",
                "需要用户澄清后才能证明工作流正确。",
                details={"clarifications": [{
                    "option_id": option_id,
                    "question": clarification.get("question", "请澄清此 GIS 语义。"),
                    "proof_ids": list((state.get("audit_options") or {}).get(option_id, ())),
                }]}))
            return "fail"
        if decision == "reject":
            return "fail"
        if state.get("audit_revisions", 0) < MAX_AUDIT_REVISIONS:
            return "revise"
        return "fail"

    @staticmethod
    def _route_authorization(state: PlanState) -> str:
        plan = state.get("plan")
        if not isinstance(plan, dict):
            return "authorization_auto"
        return "authorization_required" if int(plan.get("risk_level", 1)) >= 2 else "authorization_auto"

    # -- draft generation ---------------------------------------------------

    def _generate_draft(self, intent: IntentSpec, context: ContextSnapshot,
                        capabilities: CapabilitySnapshot,
                        task_contract: Dict[str, Any],
                        run_id: str = "") -> Dict[str, Any]:
        model_view = task_contract_model_view(task_contract)
        tools = workflow_tools_for_capabilities(
            list(capabilities.operation_cards_as_dicts())
        )
        model_request = self._build_planner_request(intent, context, capabilities,
                                                     model_view, tools, run_id)
        result = self.model_runtime.invoke(model_request, None,
                                           response_model=PlannerDraftModel, on_token=lambda _token: None)
        if result.status != "succeeded" or result.response is None:
            # Preserve money-sensitive terminal statuses (quota_stopped /
            # uncertain) instead of flattening them into a generic failure —
            # the caller must route them to the matching outcome, not retry.
            if result.status == "quota_stopped":
                raise _ModelOutcome(contracts.QUOTA_STOPPED, "plan",
                                    "draft_quota_stopped", result.error or "")
            if result.status == "uncertain":
                raise _ModelOutcome(contracts.MODEL_CALL_UNCERTAIN, "plan",
                                    "draft_uncertain", result.error or "")
            raise ValidationError("模型调用失败：%s" % (result.error or result.status))
        return self._parse_draft(result.response, capabilities, intent.business_goal)

    def _parse_draft(self, response: Dict[str, Any],
                     capabilities: CapabilitySnapshot,
                     summary: str = "") -> Dict[str, Any]:
        """Convert multi-tool-call response into the workflow-draft dict.

        The provider returns ``{"tool_calls": [{"name": ..., "arguments": ...}]}``.
        Each tool_call maps to one workflow step.  Step ``id`` is synthesized
        by index (the model doesn't supply it); ``reason`` is derived from the
        operation card's business semantic.  ``summary`` comes from the intent
        business goal (tool_calls have no summary field).
        """
        tool_calls = response.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            raise ValidationError("模型响应缺少 tool_calls。")
        operation_ids = set(capabilities.operation_ids())
        card_index = workflow_capability_index(
            list(capabilities.operation_cards_as_dicts())
        )
        parsed_steps: List[Dict[str, Any]] = []
        seen_ids = set()
        for index, call in enumerate(tool_calls):
            tool_name = call.get("name")
            arguments = call.get("arguments")
            operation = operation_id_from_tool(tool_name) if isinstance(tool_name, str) else ""
            if not isinstance(operation, str) or operation not in operation_ids:
                raise ValidationError("tool_call[%d] 不在能力闭包内：%s" % (index, tool_name))
            if not isinstance(arguments, dict):
                raise ValidationError("tool_call[%d] 参数必须是对象。" % index)
            step_id = "step_%d" % (index + 1)
            if step_id in seen_ids:
                raise ValidationError("step id 重复：%s" % step_id)
            seen_ids.add(step_id)
            card = card_index.get(tool_name, {})
            reason = card.get("summary", operation)
            parsed_steps.append({
                "id": step_id, "operation": operation,
                "arguments": arguments, "reason": reason,
            })
        return {"action": "execute", "summary": summary, "steps": parsed_steps}

    # -- deterministic validation ------------------------------------------

    def _validate(self, draft: Dict[str, Any], context: Dict[str, Any],
                  task_contract: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Run prepare_workflow + WorkflowVerifier. Returns (workflow, report).

        The deterministic verifier always makes the final ruling (§6.3).
        If it crashes, that is an infrastructure failure — it must not be
        silently swallowed into an empty-pass report.
        """
        try:
            prepared = prepare_workflow(draft, self.catalog, context)
        except ValidationError as exc:
            report = {
                "ok": False,
                "hard_violations": [_violation("workflow_structure", str(exc))],
                "review_obligations": [], "blocking_clarifications": [],
                "facts": [], "normalization_events": [],
                "prepared_workflow": None, "output_results": [],
                "requirements": [], "side_effects": [],
                "authorization_scopes": [], "task_contract": task_contract,
            }
            return None, report
        report = self.verifier.verify(prepared, context, task_contract)
        if report.get("hard_violations"):
            return None, report
        return prepared, report

    # -- model repair + audit ----------------------------------------------

    def _request_repair(self, intent: IntentSpec, context: ContextSnapshot,
                        capabilities: CapabilitySnapshot, task_contract: Dict[str, Any],
                        draft: Dict[str, Any], report: Dict[str, Any],
                        run_id: str = "",
                        ) -> Optional[Dict[str, Any]]:
        tools = workflow_tools_for_capabilities(
            list(capabilities.operation_cards_as_dicts())
        )
        diagnostics = _repair_diagnostics(report)
        model_request = self._build_repair_request(intent, context, capabilities,
                                                   draft, diagnostics, tools, run_id)
        result = self.model_runtime.invoke(model_request, None,
                                           response_model=RepairDraftModel, on_token=lambda _token: None)
        if result.status != "succeeded" or result.response is None:
            if result.status == "quota_stopped":
                raise _ModelOutcome(contracts.QUOTA_STOPPED, "plan",
                                    "repair_quota_stopped", result.error or "")
            if result.status == "uncertain":
                raise _ModelOutcome(contracts.MODEL_CALL_UNCERTAIN, "plan",
                                    "repair_uncertain", result.error or "")
            return None
        try:
            return self._parse_draft(result.response, capabilities, intent.business_goal)
        except ValidationError as exc:
            from gateway_py3.logs import write_event
            write_event("workflow.repair_parse_failed", {"error": str(exc)[:200]})
            return None

    def _request_audit(self, intent: IntentSpec, context: ContextSnapshot,
                       capabilities: CapabilitySnapshot,
                       workflow: Dict[str, Any], report: Dict[str, Any], scope, option_proofs,
                       run_id: str = "") -> Optional[Dict[str, Any]]:
        audit_contract = audit_contract_for_scope(scope, option_proofs)
        model_request = self._build_audit_request(intent, context, capabilities,
                                                   workflow, report, audit_contract, run_id)
        result = self.model_runtime.invoke(model_request, audit_contract,
                                           response_model=AuditResultModel, on_token=lambda _token: None)
        if result.status != "succeeded" or result.response is None:
            if result.status == "quota_stopped":
                raise _ModelOutcome(contracts.QUOTA_STOPPED, "plan",
                                    "audit_quota_stopped", result.error or "")
            if result.status == "uncertain":
                raise _ModelOutcome(contracts.MODEL_CALL_UNCERTAIN, "plan",
                                    "audit_uncertain", result.error or "")
            return None
        # AuditResultModel wraps the body in ``audit_result``; unwrap so the
        # consumer (``_node_audit``) sees the decision body directly.
        body = result.response.get("audit_result")
        return body if isinstance(body, dict) else result.response

    # -- sealing -----------------------------------------------------------

    def _seal(self, intent: IntentSpec, context: ContextSnapshot,
              capabilities: CapabilitySnapshot, workflow: Dict[str, Any],
              report: Dict[str, Any], audit_opinions: Tuple[Dict[str, Any], ...] = ()) -> VerifiedPlan:
        task_outputs = intent.derived_facts.get("declared_outputs", ())
        if not isinstance(task_outputs, (list, tuple)):
            raise ValueError("compiled intent missing declared outputs")
        steps = tuple(
            self._build_sealed_step(step, task_outputs)
            for step in workflow.get("steps", [])
        )
        input_identities = set(self._input_identities(steps, context))
        layer_sources = {
            layer.identity.layer_ref: layer.identity.data_source
            for layer in context.layers if layer.identity.data_source
        }
        for entity in intent.bound_inputs:
            if entity.path:
                input_identities.add(entity.path)
            elif entity.layer_ref in layer_sources:
                input_identities.add(layer_sources[entity.layer_ref])
        input_identities = tuple(sorted(input_identities))
        if intent.acceptable_side_effects >= 2 and not input_identities:
            raise ValueError("write plan has no resolvable sealed input identity")
        return VerifiedPlan(
            plan_id=str(uuid.uuid4()), version=1,
            intent_digest=intent.digest, context_digest=context.digest,
            capability_digest=capabilities.digest,
            workflow=steps,
            validation_report=report,
            audit_opinions=audit_opinions,
            revision_history=(),
            model_identity=self.model_runtime.model_identity("planner"),
            prompt_version=PROMPT_VERSION,
            risk_level=intent.acceptable_side_effects,
            required_permissions=tuple(report.get("authorization_scopes", [])),
            input_identities=input_identities,
        )

    def _input_identities(self, steps, context):
        layer_sources = dict((layer.identity.layer_ref, layer.identity.data_source)
                             for layer in context.layers if layer.identity.data_source)
        found = set()
        for step in steps:
            properties = self.catalog.get(step.operation)["parameters_schema"]["properties"]
            for name, schema in properties.items():
                if schema.get("x-geopilot-kind") != "layer" or name not in step.arguments:
                    continue
                values = step.arguments[name]
                if not isinstance(values, (list, tuple)):
                    values = (values,)
                for value in values:
                    if isinstance(value, str) and value in layer_sources:
                        found.add(layer_sources[value])
        return tuple(sorted(found))

    @staticmethod
    def _build_sealed_step(step: Dict[str, Any], task_outputs: Tuple[Dict[str, Any], ...]) -> WorkflowStep:
        """Build a WorkflowStep with declared_outputs fixed from the tool call.

        The model's tool_call arguments name the output (``output_name`` /
        ``output_path``). Seal fixes that into a structured ``DeclaredOutput``
        whose ``kind`` is inferred from the operation id and the output path
        extension (raster/table/layer_file/feature_class) — never hard-coded
        to feature_class regardless of the actual output type.
        """
        from ..kernel.contracts import DeclaredOutput
        arguments = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        output_name = arguments.get("output_name") or arguments.get("output_path")
        operation = step.get("operation", "")
        declared_outputs: Tuple[DeclaredOutput, ...] = ()
        if output_name:
            matching = [item for item in task_outputs if isinstance(item, dict)
                        and item.get("name") == output_name]
            if len(matching) != 1:
                raise ValueError("workflow output must match exactly one task-contract output")
            output = matching[0]
            declared_outputs = (DeclaredOutput(
                output_id=str(output["output_id"]), name=str(output_name),
                kind=str(output["kind"]), output_format=str(output["format"]),
                destination_policy=str(output["destination_policy"]),
                coordinate_system=str(output["spatial_reference"]),
                geometry_type=str(output["geometry"]),
                expected_fields=tuple(output.get("required_fields", ())),
            ),)
        return WorkflowStep(
            id=step["id"], operation=step["operation"],
            arguments=arguments, reason=step["reason"],
            declared_outputs=declared_outputs,
        )

    # -- checkpoint reuse ---------------------------------------------------

    def _sealed_plan(self, run_id: str, expected_model_plan_digest: str) -> Optional[VerifiedPlan]:
        """Return a previously sealed plan from the checkpoint, if any.

        §13.2: a sealed plan is committed fact; a crash-restarted run reuses
        it without repeating any model call.
        """
        try:
            snapshot = self._compiled().get_state(self._config(run_id))
        except Exception as exc:
            raise _CheckpointContractError(
                "planning checkpoint could not be read: %s" % type(exc).__name__
            ) from exc
        if snapshot is None:
            return None
        try:
            values = snapshot.values
        except Exception as exc:
            raise _CheckpointContractError(
                "planning checkpoint state could not be read: %s" % type(exc).__name__
            ) from exc
        if not isinstance(values, dict):
            raise _CheckpointContractError("planning checkpoint state is malformed")
        # LangGraph returns an empty values mapping before it has ever written
        # this thread. That is the sole representation of no checkpoint.
        if not values:
            return None
        actual_model_plan_digest = values.get("model_plan_digest")
        if not isinstance(actual_model_plan_digest, str) or not actual_model_plan_digest:
            raise _ModelPlanContractError("planning checkpoint is missing model binding digest")
        if actual_model_plan_digest != expected_model_plan_digest:
            raise _ModelPlanContractError("sealed planning checkpoint model binding differs from task binding")
        plan_doc = values.get("plan")
        if plan_doc is None:
            return None
        if not isinstance(plan_doc, dict):
            raise _CheckpointContractError("planning checkpoint plan is malformed")
        try:
            return VerifiedPlan.model_validate(plan_doc)
        except PydanticValidationError as exc:
            from gateway_py3.logs import write_event
            write_event("workflow.checkpoint_plan_invalid", {"run_id": run_id,
                        "error": str(exc)[:200]})
            raise _CheckpointContractError("planning checkpoint plan is invalid") from exc

    def _outcome_from_state(self, state: PlanState) -> Outcome:
        failure = state.get("failure")
        if failure is not None:
            return _outcome_from_document(failure)
        plan_doc = state.get("plan")
        if isinstance(plan_doc, dict):
            try:
                plan = VerifiedPlan.model_validate(plan_doc)
            except Exception as exc:
                return outcome_failed(
                    CONTRACT_FAILED, "plan", "seal_invalid",
                    "封存的计划无法反序列化：%s" % exc,
                )
            return outcome_succeeded(
                "plan", "计划验证通过并封存。", details={
                    "plan": plan,
                    "awaiting_authorization": plan.risk_level >= 2 and not state.get("authorization_result"),
                    "authorization_result": state.get("authorization_result"),
                },
            )
        return outcome_failed(
            CONTRACT_FAILED, "plan", "plan_failed",
            "规划未产生封存计划。",
        )

    # -- model request builders -------------------------------------------

    def _build_planner_request(self, intent: IntentSpec, context: ContextSnapshot,
                              capabilities: CapabilitySnapshot,
                              model_view: Dict[str, Any],
                              tools: List[Dict[str, Any]],
                              run_id: str = "") -> ModelRequest:
        return ModelRequest(
            tenant_id=_intent_security_scope(intent)["tenant_id"],
            security_scope_hash=_intent_scope_hash(intent),
            role="planner",
            prompt_version=PROMPT_VERSION,
            system_prompt=PLANNER_SYSTEM,
            user_input=intent.business_goal,
            tools=tools,
            capability_hash=capabilities.digest,
            context_projection=model_view,
            domain_rule_hash=capabilities.domain_rule_hash,
            model_plan=_intent_model_plan(intent),
            model_binding_summary=_intent_model_binding_summary(intent),
            generation_params={"protocol": self.protocol},
            run_id=run_id,
        )

    def _build_repair_request(self, intent: IntentSpec, context: ContextSnapshot,
                             capabilities: CapabilitySnapshot,
                             draft: Dict[str, Any], diagnostics: List[Dict[str, Any]],
                             tools: List[Dict[str, Any]],
                             run_id: str = "") -> ModelRequest:
        return ModelRequest(
            tenant_id=_intent_security_scope(intent)["tenant_id"],
            security_scope_hash=_intent_scope_hash(intent),
            role="repairer",
            prompt_version=PROMPT_VERSION,
            system_prompt=REPAIR_SYSTEM,
            user_input=intent.business_goal,
            tools=tools,
            capability_hash=capabilities.digest,
            context_projection={"draft": draft, "diagnostics": diagnostics},
            domain_rule_hash=capabilities.domain_rule_hash,
            model_plan=_intent_model_plan(intent),
            model_binding_summary=_intent_model_binding_summary(intent),
            generation_params={"protocol": self.protocol},
            run_id=run_id,
        )

    def _build_audit_request(self, intent: IntentSpec, context: ContextSnapshot,
                            capabilities: CapabilitySnapshot,
                            workflow: Dict[str, Any], report: Dict[str, Any],
                            audit_contract,
                            run_id: str = "") -> ModelRequest:
        return ModelRequest(
            tenant_id=_intent_security_scope(intent)["tenant_id"],
            security_scope_hash=_intent_scope_hash(intent),
            role="auditor",
            prompt_version=PROMPT_VERSION,
            system_prompt=AUDITOR_SYSTEM,
            user_input=intent.business_goal,
            tool_contract=audit_contract.schema,
            capability_hash=capabilities.digest,
            context_projection={"workflow": workflow, "report": report},
            domain_rule_hash=capabilities.domain_rule_hash,
            model_plan=_intent_model_plan(intent),
            model_binding_summary=_intent_model_binding_summary(intent),
            generation_params={"protocol": self.protocol},
            run_id=run_id,
        )


# --- helpers ----------------------------------------------------------------

def _baseline_draft(plan: VerifiedPlan) -> Dict[str, Any]:
    """Project one sealed G2 plan back to the immutable graph draft form.

    This is deliberately a lossless projection of the executable workflow,
    not a new model draft.  G3 may audit and, if justified, revise this exact
    baseline; it must never make a second independent planner call.
    """
    return {
        "action": "execute",
        "summary": "",
        "steps": [{"id": step.id, "operation": step.operation,
                   "arguments": dict(step.arguments), "reason": step.reason}
                  for step in plan.workflow],
    }


def _workflow_identity(workflow: Dict[str, Any]) -> Tuple[Tuple[str, str, str], ...]:
    """Executable identity only: pass cannot alter step ids, operations, or arguments."""
    return tuple((step["id"], step["operation"],
                  json.dumps(step.get("arguments", {}), sort_keys=True, separators=(",", ":")))
                 for step in workflow.get("steps", []))

def _intent_security_scope(intent: IntentSpec) -> Dict[str, Any]:
    """Recover the caller scope persisted with the compiled intent.

    Planning has no RequestEnvelope parameter by design; its cache key must
    nevertheless remain tenant/role/data-scope isolated.
    """
    value = intent.derived_facts.get("security_scope", {})
    if not isinstance(value, dict):
        raise ValueError("compiled intent is missing security scope")
    tenant_id = value.get("tenant_id")
    role = value.get("role")
    data_scope = value.get("data_scope")
    if not isinstance(tenant_id, str) or not tenant_id or not isinstance(role, str) or not role:
        raise ValueError("compiled intent has invalid security scope")
    if not isinstance(data_scope, (list, tuple)) or any(not isinstance(item, str) for item in data_scope):
        raise ValueError("compiled intent has invalid data scope")
    return {"tenant_id": tenant_id, "role": role, "data_scope": tuple(sorted(data_scope))}


def _intent_model_plan(intent: IntentSpec):
    """Load the immutable task plan carried by the compiler's sealed intent."""
    from ..model_runtime import AgentModelPlan
    value = intent.derived_facts.get("model_plan")
    if not isinstance(value, dict):
        raise ValueError("compiled intent is missing its model plan")
    return AgentModelPlan.model_validate(value)


def _intent_model_binding_summary(intent: IntentSpec) -> Dict[str, Any]:
    value = intent.derived_facts.get("model_binding_summary")
    if not isinstance(value, dict):
        raise ValueError("compiled intent is missing its model binding summary")
    return value


def _intent_model_plan_digest(intent: IntentSpec) -> str:
    plan = _intent_model_plan(intent)
    return contracts.digest(plan.model_dump(mode="json"))


def _intent_scope_hash(intent: IntentSpec) -> str:
    """Stable security-scope hash for the model-call cache key (§6.4).

    ``intent.digest`` includes session_id/request_id, which would void the
    cache across tasks. The cache is content-addressed: the scope hash must
    capture WHAT is being planned (business goal, semantics, bound inputs,
    constraints, expected outputs) without the transient identities. A truly
    identical planning request across runs/sessions produces the same hash.
    """
    from ..kernel.contracts import canonical_json, digest
    stable = {
        "security_scope": _intent_security_scope(intent),
        "business_goal": intent.business_goal,
        "spatial_semantic": intent.spatial_semantic,
        "constraints": list(intent.constraints),
        "expected_outputs": list(intent.expected_outputs),
        "quality_conditions": list(intent.quality_conditions),
        "bound_inputs": [
            {"name": e.name, "kind": e.kind, "path": e.path}
            for e in intent.bound_inputs
        ],
        "user_fields": list(intent.user_fields),
        "user_values": dict(intent.user_values),
        "acceptable_side_effects": intent.acceptable_side_effects,
    }
    return digest(canonical_json(stable))


def _violation(code: str, message: str) -> Dict[str, Any]:
    return {
        "violation_id": code, "code": code, "contract_path": "",
        "message": message,
    }


def _repair_diagnostics(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    diagnostics = []
    for violation in report.get("hard_violations", []):
        diagnostics.append({
            "code": violation.get("code", ""),
            "message": violation.get("message", ""),
            "step_id": violation.get("step_id"),
        })
    return diagnostics


def _failure_document(outcome: Outcome) -> Dict[str, Any]:
    return {
        "kind": outcome.kind, "code": outcome.code, "stage": outcome.stage,
        "message": outcome.message, "details": outcome.details,
    }


def _outcome_from_document(document: Dict[str, Any]) -> Outcome:
    return Outcome.model_validate(document)
