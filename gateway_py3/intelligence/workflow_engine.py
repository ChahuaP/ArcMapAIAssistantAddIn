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
    audit_decision: Optional[str]
    audit_report: Dict[str, Any]
    plan: Optional[Dict[str, Any]]
    failure: Optional[Dict[str, Any]]
    done: bool
    decision: Optional[Dict[str, Any]]
    authorization_result: Optional[str]
    node_attempts: Dict[str, int]


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
            {"seal": "seal", "repair": "repair", "fail": "fail"},
        )
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
        return {"configurable": {"thread_id": run_id}}

    # -- public API ---------------------------------------------------------

    def plan(self, run_id: str, intent: IntentSpec, context: ContextSnapshot,
             capabilities: CapabilitySnapshot) -> Outcome:
        """Plan, verify, audit and seal (§6.3). Reuses a sealed checkpoint.

        Returns a succeeded Outcome carrying the VerifiedPlan, or a terminal
        Outcome (ContractFailed / CapabilityFailed / quota / uncertain).
        """
        return self.plan_ablation(run_id, intent, context, capabilities, auditor_enabled=True)

    def plan_ablation(self, run_id: str, intent: IntentSpec, context: ContextSnapshot,
                      capabilities: CapabilitySnapshot, auditor_enabled: bool) -> Outcome:
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
        existing = self._sealed_plan(run_id)
        if existing is not None:
            return outcome_succeeded("plan", "计划已封存，复用检查点。",
                                     details={"plan": existing,
                                              "topology_signature": self.ablation_topology_signature()})

        state: PlanState = {
            "run_id": run_id,
            "intent": intent.model_dump(mode="json"),
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
            "audit_decision": None,
            "audit_report": {},
            "plan": None,
            "failure": None,
            "done": False,
            "decision": None,
            "authorization_result": None,
            "node_attempts": {},
        }
        # The production path is the graph stream.  Its checkpoint is the
        # source of the final state, so a process interruption cannot leave a
        # hand-written planner state separate from LangGraph.
        app = self._compiled()
        prior = app.get_state(self._config(run_id))
        resume = prior is not None and isinstance(prior.values, dict) and bool(prior.values)
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
                                            state["report"],
                                            state.get("run_id", ""),
                                            state.get("audit_report"))
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
        try:
            audit_result = self._request_audit(intent, context, capabilities,
                                               workflow, state["report"],
                                               state.get("run_id", ""))
        except _ModelOutcome as exc:
            return {"failure": _failure_document(outcome_failed(
                exc.kind, exc.stage, exc.code, exc.message))}
        if audit_result is None:
            return {"failure": _failure_document(outcome_failed(
                INFRASTRUCTURE_FAILED, "plan", "audit_call_failed",
                "审计模型调用失败。" ))}
        decision = audit_result.get("decision")
        if decision not in ("pass", "revise", "clarify", "reject"):
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "audit_invalid_decision",
                "审计返回了无效的决策：%s" % decision))}
        return {"audit_decision": decision, "audit_report": audit_result,
                "audit_revisions": state.get("audit_revisions", 0) + 1}

    def _node_seal(self, state: PlanState) -> Dict[str, Any]:
        intent = IntentSpec.model_validate(state["intent"])
        context = ContextSnapshot.model_validate(state["context"])
        capabilities = CapabilitySnapshot.model_validate(state["capabilities"])
        workflow = state.get("workflow")
        if workflow is None:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "missing_workflow",
                "缺少已验证工作流，无法封存。"))}
        audit_report = state.get("audit_report")
        audit_opinions: Tuple[Dict[str, Any], ...] = ()
        if isinstance(audit_report, dict):
            audit_opinions = (dict(audit_report),)
        plan = self._seal(intent, context, capabilities, workflow,
                          state["report"], audit_opinions=audit_opinions)
        units = {_gdb_publish_unit(output.destination)
                 for step in plan.workflow for output in step.declared_outputs
                 if output.kind != "map_state"}
        if None in units or len(units) > 1:
            return {"failure": _failure_document(outcome_failed(
                CONTRACT_FAILED, "plan", "multiple_publish_units",
                "一个运行的正式数据输出必须属于同一个目标 FileGDB；请拆分任务。"))}
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
        if state.get("workflow") is not None and state.get("auditor_enabled", True):
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

    def _route_audit(self, state: PlanState) -> str:
        if state.get("failure"):
            return "fail"
        decision = state.get("audit_decision", "pass")
        if decision == "pass":
            return "seal"
        if decision in ("clarify", "reject"):
            return "fail"
        if state.get("audit_revisions", 0) < MAX_AUDIT_REVISIONS:
            return "repair"
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
                        audit_report: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        tools = workflow_tools_for_capabilities(
            list(capabilities.operation_cards_as_dicts())
        )
        diagnostics = _repair_diagnostics(report)
        # When the repair is triggered by a G3 audit revision, fold the audit
        # claims into the diagnostics so the model sees what the auditor flagged
        # (not just the deterministic verifier's hard violations).
        if isinstance(audit_report, dict):
            claims = audit_report.get("claims")
            if isinstance(claims, list):
                for claim in claims:
                    if isinstance(claim, dict):
                        diagnostics.append({
                            "code": claim.get("proof_id", "audit_claim"),
                            "message": claim.get("required_change", ""),
                            "step_id": None,
                            "source": "audit",
                        })
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
                       workflow: Dict[str, Any], report: Dict[str, Any],
                       run_id: str = "") -> Optional[Dict[str, Any]]:
        from ..audit_contract import audit_contract_for_report
        audit_contract = audit_contract_for_report(report)
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
        # consumer (``_node_audit``) sees ``{decision, claims}`` directly.
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
                kind=str(output["kind"]), destination=str(output["destination"]),
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

    def _sealed_plan(self, run_id: str) -> Optional[VerifiedPlan]:
        """Return a previously sealed plan from the checkpoint, if any.

        §13.2: a sealed plan is committed fact; a crash-restarted run reuses
        it without repeating any model call.
        """
        try:
            snapshot = self._compiled().get_state(self._config(run_id))
        except (KeyError, ValueError, RuntimeError):
            return None
        values = snapshot.values if snapshot is not None else None
        plan_doc = (values or {}).get("plan") if isinstance(values, dict) else None
        if not isinstance(plan_doc, dict):
            return None
        try:
            return VerifiedPlan.model_validate(plan_doc)
        except PydanticValidationError as exc:
            from gateway_py3.logs import write_event
            write_event("workflow.checkpoint_plan_invalid", {"run_id": run_id,
                        "error": str(exc)[:200]})
            return None

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
            generation_params={"protocol": self.protocol},
            run_id=run_id,
        )


# --- helpers ----------------------------------------------------------------

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


def _gdb_publish_unit(destination: str) -> Optional[str]:
    normalized = str(destination).replace("/", "\\")
    parts = normalized.split("\\")
    for index, part in enumerate(parts):
        if part.lower().endswith(".gdb"):
            return "\\".join(parts[:index + 1]).casefold()
    return None


def _failure_document(outcome: Outcome) -> Dict[str, Any]:
    return {
        "kind": outcome.kind, "code": outcome.code, "stage": outcome.stage,
        "message": outcome.message, "details": outcome.details,
    }


def _outcome_from_document(document: Dict[str, Any]) -> Outcome:
    return outcome_failed(
        document.get("kind", CONTRACT_FAILED),
        document.get("stage", "plan"),
        document.get("code", "plan_failed"),
        document.get("message", "规划失败。"),
        details=document.get("details", {}),
    )
