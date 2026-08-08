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
- ``stream_mode="updates"`` produces one event per completed node for the SSE
  channel (§14); ``WorkflowEngine.stream_plan`` exposes it.
- Repair/audit loops are conditional edges with explicit budget counters in
  the state; exceeding the budget terminates with ``ContractFailed``. No
  LangGraph auto-retry is used: model calls are classified by ModelRuntime
  (quota_stopped / failed / uncertain) and surface as terminal outcomes.

State is JSON-serializable (dicts/strings/ints only) so the checkpoint stays
portable; Pydantic contracts are re-validated inside nodes.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from ..kernel import contracts
from ..kernel.contracts import (
    CapabilitySnapshot, ContextSnapshot, IntentSpec, Outcome,
    VerifiedPlan, WorkflowStep, outcome_succeeded, outcome_failed,
    SUCCEEDED, CONTRACT_FAILED, CAPABILITY_FAILED, INFRASTRUCTURE_FAILED,
)
from .model_runtime import ModelRuntime, ModelRequest
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

PROMPT_VERSION = "workflow-engine-v2"
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
    legacy_context: Dict[str, Any]
    draft: Dict[str, Any]
    workflow: Optional[Dict[str, Any]]
    report: Dict[str, Any]
    validation_revisions: int
    audit_revisions: int
    audit_decision: Optional[str]
    audit_report: Dict[str, Any]
    plan: Optional[Dict[str, Any]]
    failure: Optional[Dict[str, Any]]
    done: bool


class WorkflowEngine:
    """§6.3 WorkflowEngine: plan + verify + audit + seal (LangGraph).

    Holds a catalog for deterministic validation, a ModelRuntime for draft /
    repair / audit model calls, and a ``SqliteSaver`` checkpointer bound to the
    JournalStore database file. The kernel calls ``plan(run_id, ...)``; the
    engine never touches the store directly (§6.1: kernel owns persistence).
    """

    def __init__(self, catalog, model_runtime: ModelRuntime,
                 auditor_runtime: Optional[ModelRuntime] = None,
                 checkpoint_path: Optional[Path] = None):
        self.catalog = catalog
        self.model_runtime = model_runtime
        self.auditor_runtime = auditor_runtime
        self.verifier = WorkflowVerifier(catalog)
        self.protocol = workflow_protocol()
        self._checkpoint_path = checkpoint_path
        self._graph = self._build_graph()
        self._checkpointer = None
        self._app = None

    # -- LangGraph construction (§13.2) ------------------------------------

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(PlanState)
        graph.add_node("draft", self._node_draft)
        graph.add_node("validate", self._node_validate)
        graph.add_node("repair", self._node_repair)
        graph.add_node("audit", self._node_audit)
        graph.add_node("seal", self._node_seal)
        graph.add_node("fail", self._node_fail)
        graph.add_edge(START, "draft")
        graph.add_edge("draft", "validate")
        graph.add_conditional_edges(
            "validate", self._route_validate,
            {"audit": "audit", "repair": "repair", "fail": "fail"},
        )
        graph.add_edge("repair", "validate")
        graph.add_conditional_edges(
            "audit", self._route_audit,
            {"seal": "seal", "repair": "repair", "fail": "fail"},
        )
        graph.add_edge("seal", END)
        graph.add_edge("fail", END)
        return graph

    def _compiled(self):
        if self._app is None:
            if self._checkpoint_path is None:
                # In-memory checkpointer: run completes within one call, no
                # cross-call recovery needed (tests use this path).
                conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._checkpointer = SqliteSaver(conn)
            else:
                conn = sqlite3.connect(
                    str(self._checkpoint_path), check_same_thread=False
                )
                self._checkpointer = SqliteSaver(conn)
            self._app = self._graph.compile(checkpointer=self._checkpointer)
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
            return outcome_succeeded(
                "plan", "计划已封存，复用检查点。", details={"plan": existing},
            )

        state: PlanState = {
            "run_id": run_id,
            "intent": intent.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "capabilities": capabilities.model_dump(mode="json"),
            "task_contract": task_contract,
            "legacy_context": _project_context(context),
            "draft": None,
            "workflow": None,
            "report": {},
            "validation_revisions": 0,
            "audit_revisions": 0,
            "audit_decision": None,
            "audit_report": {},
            "plan": None,
            "failure": None,
            "done": False,
        }
        final_state = self._compiled().invoke(state, config=self._config(run_id))
        return self._outcome_from_state(final_state)

    def stream_plan(self, run_id: str, intent: IntentSpec, context: ContextSnapshot,
                    capabilities: CapabilitySnapshot):
        """Yield (node_name, state_update) per completed node (§14).

        The SSE channel consumes these as ``planning.node_update`` events.
        """
        task_contract = intent.derived_facts.get("task_contract")
        if not isinstance(task_contract, dict):
            yield "fail", {"failure": _failure_document(
                outcome_failed(CONTRACT_FAILED, "plan", "missing_task_contract",
                               "IntentSpec 没有携带 task_contract，无法规划。"))}
            return
        state: PlanState = {
            "run_id": run_id,
            "intent": intent.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
            "capabilities": capabilities.model_dump(mode="json"),
            "task_contract": task_contract,
            "legacy_context": _project_context(context),
            "draft": None,
            "workflow": None,
            "report": {},
            "validation_revisions": 0,
            "audit_revisions": 0,
            "audit_decision": None,
            "audit_report": {},
            "plan": None,
            "failure": None,
            "done": False,
        }
        for chunk in self._compiled().stream(
            state, config=self._config(run_id), stream_mode="updates"
        ):
            for node, update in chunk.items():
                yield node, update

    # -- graph nodes --------------------------------------------------------

    def _node_draft(self, state: PlanState) -> Dict[str, Any]:
        intent = IntentSpec.model_validate(state["intent"])
        context = ContextSnapshot.model_validate(state["context"])
        capabilities = CapabilitySnapshot.model_validate(state["capabilities"])
        try:
            draft = self._generate_draft(intent, context, capabilities,
                                         state["task_contract"])
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
        workflow, report = self._validate(draft, state["legacy_context"],
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
        repaired = self._request_repair(intent, context, capabilities,
                                        state["task_contract"], draft,
                                        state["report"])
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
        if self.auditor_runtime is None:
            # No auditor wired: skip audit (not all deployments need G3).
            return {"audit_decision": "pass", "audit_report": {},
                    "audit_revisions": state.get("audit_revisions", 0)}
        audit_result = self._request_audit(intent, context, capabilities,
                                           workflow, state["report"])
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
        plan = self._seal(intent, context, capabilities, workflow, state["report"])
        return {"plan": plan.model_dump(mode="json"), "done": True}

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
        if state.get("workflow") is not None:
            return "audit"
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

    # -- draft generation ---------------------------------------------------

    def _generate_draft(self, intent: IntentSpec, context: ContextSnapshot,
                        capabilities: CapabilitySnapshot,
                        task_contract: Dict[str, Any]) -> Dict[str, Any]:
        model_view = task_contract_model_view(task_contract)
        tools = workflow_tools_for_capabilities(
            list(capabilities.operation_cards_as_dicts())
        )
        model_request = self._build_planner_request(intent, context, capabilities,
                                                     model_view, tools)
        result = self.model_runtime.invoke(model_request, None)
        if result.status != "succeeded" or result.response is None:
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
                        draft: Dict[str, Any], report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        tools = workflow_tools_for_capabilities(
            list(capabilities.operation_cards_as_dicts())
        )
        diagnostics = _repair_diagnostics(report)
        model_request = self._build_repair_request(intent, context, capabilities,
                                                   draft, diagnostics, tools)
        result = self.model_runtime.invoke(model_request, None)
        if result.status != "succeeded" or result.response is None:
            return None
        try:
            return self._parse_draft(result.response, capabilities, intent.business_goal)
        except ValidationError as exc:
            from gateway_py3.logs import write_event
            write_event("workflow.repair_parse_failed", {"error": str(exc)[:200]})
            return None

    def _request_audit(self, intent: IntentSpec, context: ContextSnapshot,
                       capabilities: CapabilitySnapshot,
                       workflow: Dict[str, Any], report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from ..audit_contract import audit_contract_for_report
        audit_contract = audit_contract_for_report(report)
        model_request = self._build_audit_request(intent, context, capabilities,
                                                   workflow, report, audit_contract)
        result = self.auditor_runtime.invoke(model_request, audit_contract)
        if result.status != "succeeded" or result.response is None:
            return None
        return result.response

    # -- sealing -----------------------------------------------------------

    def _seal(self, intent: IntentSpec, context: ContextSnapshot,
              capabilities: CapabilitySnapshot, workflow: Dict[str, Any],
              report: Dict[str, Any]) -> VerifiedPlan:
        steps = tuple(
            WorkflowStep(
                id=step["id"], operation=step["operation"],
                arguments=step["arguments"], reason=step["reason"],
            )
            for step in workflow.get("steps", [])
        )
        return VerifiedPlan(
            plan_id=str(uuid.uuid4()), version=1,
            intent_digest=intent.digest, context_digest=context.digest,
            capability_digest=capabilities.digest,
            workflow=steps,
            validation_report=report,
            audit_opinions=(),
            revision_history=(),
            model_identity=self.model_runtime.adapter.model,
            prompt_version=PROMPT_VERSION,
            risk_level=intent.acceptable_side_effects,
            required_permissions=tuple(report.get("authorization_scopes", [])),
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
                "plan", "计划验证通过并封存。", details={"plan": plan},
            )
        return outcome_failed(
            CONTRACT_FAILED, "plan", "plan_failed",
            "规划未产生封存计划。",
        )

    # -- model request builders -------------------------------------------

    def _build_planner_request(self, intent: IntentSpec, context: ContextSnapshot,
                              capabilities: CapabilitySnapshot,
                              model_view: Dict[str, Any],
                              tools: List[Dict[str, Any]]) -> ModelRequest:
        return ModelRequest(
            tenant_id=intent.session_id,
            security_scope_hash=intent.digest,
            provider=self.model_runtime.adapter.provider,
            model=self.model_runtime.adapter.model,
            role="planner",
            prompt_version=PROMPT_VERSION,
            system_prompt=PLANNER_SYSTEM,
            user_input=intent.business_goal,
            tools=tools,
            capability_hash=capabilities.digest,
            context_projection=model_view,
            domain_rule_hash=capabilities.domain_rule_hash,
            generation_params={"protocol": self.protocol},
        )

    def _build_repair_request(self, intent: IntentSpec, context: ContextSnapshot,
                             capabilities: CapabilitySnapshot,
                             draft: Dict[str, Any], diagnostics: List[Dict[str, Any]],
                             tools: List[Dict[str, Any]]) -> ModelRequest:
        return ModelRequest(
            tenant_id=intent.session_id,
            security_scope_hash=intent.digest,
            provider=self.model_runtime.adapter.provider,
            model=self.model_runtime.adapter.model,
            role="workflow_repair",
            prompt_version=PROMPT_VERSION,
            system_prompt=REPAIR_SYSTEM,
            user_input=intent.business_goal,
            tools=tools,
            capability_hash=capabilities.digest,
            context_projection={"draft": draft, "diagnostics": diagnostics},
            domain_rule_hash=capabilities.domain_rule_hash,
            generation_params={"protocol": self.protocol},
        )

    def _build_audit_request(self, intent: IntentSpec, context: ContextSnapshot,
                            capabilities: CapabilitySnapshot,
                            workflow: Dict[str, Any], report: Dict[str, Any],
                            audit_contract) -> ModelRequest:
        return ModelRequest(
            tenant_id=intent.session_id,
            security_scope_hash=intent.digest,
            provider=self.auditor_runtime.adapter.provider,
            model=self.auditor_runtime.adapter.model,
            role="auditor",
            prompt_version=PROMPT_VERSION,
            system_prompt=AUDITOR_SYSTEM,
            user_input=intent.business_goal,
            tool_contract=audit_contract.schema,
            capability_hash=capabilities.digest,
            context_projection={"workflow": workflow, "report": report},
            domain_rule_hash=capabilities.domain_rule_hash,
            generation_params={"protocol": self.protocol},
        )


# --- helpers ----------------------------------------------------------------

def _project_context(context: ContextSnapshot) -> Dict[str, Any]:
    """Delegate to ContextSnapshot.to_legacy_dict (single source of truth)."""
    return context.to_legacy_dict()


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
    return outcome_failed(
        document.get("kind", CONTRACT_FAILED),
        document.get("stage", "plan"),
        document.get("code", "plan_failed"),
        document.get("message", "规划失败。"),
        details=document.get("details", {}),
    )
