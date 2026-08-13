"""TaskCompiler: compile a request into an IntentSpec (§6.2).

The sole entry point is ``compile(request, context, capabilities) -> Outcome``.
It calls ModelRuntime to get a task-contract draft, then the server binds
entities, derives facts and validates the contract. The model never decides
output format, layer type, selection derivatives or default workspace; those
are server-derived.

Stage C wraps the existing ``task_contract`` validation logic (task_contract,
semantic_domain, condition_contract) behind the new deep-module boundary. The
full validated task contract is retained in ``IntentSpec.derived_facts`` so
WorkflowEngine can consume it without a second semantic model call (§6.3).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from ..kernel import contracts
from ..kernel.contracts import (
    CapabilitySnapshot, ContextSnapshot, IntentSpec, EntityBinding, context_verifier_view,
    Outcome, RequestEnvelope, outcome_succeeded, outcome_paused, outcome_failed,
)
from ..model_runtime import AgentModelPlan, ModelRuntime, ModelRequest, StructuredOutputContract
from ..task_contract import (
    TaskContractError,
    bind_model_task_contract,
    parse_task_contract,
    task_contract_for_context,
    task_contract_model_view,
)
from ..semantic_domain import task_predicate_catalog


PROMPT_VERSION = "task-compiler-v2"
SYSTEM_PROMPT = (
    "你是 GeoPilot 任务合同编译器。根据用户请求和 ArcMap 上下文，提交一个封闭的任务合同。\n"
    "只表达用户业务目标，不猜测实现细节。输出格式、图层类型、选择派生由服务器绑定。\n"
    "每个解释必须能追溯到用户请求原文或上下文证据。\n"
    "predicate_json 的 kind 必须从上下文中提供的任务谓词目录的 variants 里选择，不得自行发明。"
)

# Side-effect mapping from task_contract allowed_side_effects to PolicyGate levels.
_EFFECT_TO_LEVEL = {
    "read_only": 1,
    "changes_map": 2,
    "writes_data": 3,
    "edits_data": 4,
}


class TaskCompiler:
    """§6.2 TaskCompiler: compile request + context + capabilities -> IntentSpec.

    Holds a ModelRuntime for the semantic model call. The capability snapshot
    provides the domain_rule_hash for cache isolation.
    """

    def __init__(self, model_runtime: ModelRuntime):
        self.model_runtime = model_runtime
        self._catalog_text = json.dumps(
            task_predicate_catalog(), ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        )

    def compile(self, request: RequestEnvelope, context: ContextSnapshot,
                capabilities: CapabilitySnapshot, run_id: str = "") -> Outcome:
        verifier_context = context_verifier_view(context)
        contract = task_contract_for_context(verifier_context, request.text)
        model_request = self._build_model_request(request, context, capabilities,
                                                   verifier_context, contract, run_id)
        result = self.model_runtime.invoke(model_request, contract, on_token=lambda _token: None)
        if result.status == "quota_stopped":
            return outcome_failed(
                contracts.QUOTA_STOPPED, "intent", "model_quota_stopped",
                "当前角色的模型预算已耗尽。不重试、不切换供应商。",
            )
        if result.status == "uncertain":
            return outcome_failed(
                contracts.MODEL_CALL_UNCERTAIN, "intent", "model_call_uncertain",
                "模型调用结果不确定，无法证明已持久化。",
            )
        if result.status != "succeeded" or result.response is None:
            return outcome_failed(
                contracts.CONTRACT_FAILED, "intent", "model_call_failed",
                result.error or "模型调用失败。",
            )
        # Check clarifications BEFORE strict contract validation — the model
        # may legitimately ask for more info without filling every field.
        raw_contract = result.response.get("task_contract") or result.response
        if isinstance(raw_contract, dict) and raw_contract.get("clarifications"):
            clarifications = raw_contract["clarifications"]
            if isinstance(clarifications, list) and clarifications:
                try:
                    draft = bind_model_task_contract(raw_contract, request.text, verifier_context)
                except TaskContractError as exc:
                    return outcome_failed(contracts.CONTRACT_FAILED, "intent", "clarification_contract_invalid", str(exc))
                question = clarifications[0].get("question", "需要用户澄清。") if isinstance(clarifications[0], dict) else str(clarifications[0])
                return outcome_paused(
                    contracts.CLARIFICATION_REQUIRED, "intent", "clarification_required",
                    question,
                    details={"clarifications": clarifications, "task_contract_draft": draft},
                )
        try:
            task_contract = self._bind_and_parse(
                result.response, request.text, verifier_context,
            )
        except TaskContractError as exc:
            return outcome_failed(
                contracts.CONTRACT_FAILED, "intent", "task_contract_invalid",
                str(exc),
            )
        if task_contract.get("clarifications"):
            return outcome_paused(
                contracts.CLARIFICATION_REQUIRED, "intent", "clarification_required",
                task_contract["clarifications"][0].get("question", "需要用户澄清。"),
                details={"clarifications": task_contract["clarifications"]},
            )
        intent = self._build_intent(request, task_contract, context, capabilities)
        return outcome_succeeded(
            "intent", "意图编译完成。",
            details={"intent": intent, "task_contract": task_contract},
        )

    # -- model request construction ----------------------------------------

    def _build_model_request(self, request: RequestEnvelope, context: ContextSnapshot,
                            capabilities: CapabilitySnapshot,
                            verifier_context: Dict[str, Any],
                            contract: StructuredOutputContract,
                            run_id: str = "") -> ModelRequest:
        context_projection = {
            "layers": [
                {
                    "layer_ref": layer.identity.layer_ref,
                    "name": layer.identity.name,
                    "data_source": layer.identity.data_source,
                    "geometry_type": layer.geometry_type,
                    "fields": [{"name": f.name, "type": f.dtype} for f in layer.fields],
                    "selected_count": layer.selection_count,
                }
                for layer in context.layers
            ],
            "is_saved": bool(verifier_context.get("is_saved", False)),
            "active_data_frame": context.active_data_frame,
        }
        tool_contract = contract.schema
        return ModelRequest(
            tenant_id=request.caller.tenant_id,
            security_scope_hash=_security_scope_hash(request),
            role="compiler",
            prompt_version=PROMPT_VERSION,
            system_prompt=SYSTEM_PROMPT,
            user_input=request.text,
            tool_contract=tool_contract,
            capability_hash=capabilities.digest,
            context_projection=context_projection,
            domain_rule_hash=capabilities.domain_rule_hash,
            model_plan=request.model_plan,
            model_binding_summary=request.model_binding_summary,
            generation_params={
                "predicate_catalog": self._catalog_text,
            },
            run_id=run_id,
        )

    # -- server-side binding + validation ---------------------------------

    def _bind_and_parse(self, response: Dict[str, Any], request_text: str,
                        verifier_context: Dict[str, Any]) -> Dict[str, Any]:
        """Bind model response to the canonical task_contract format.

        The server is the authority: the model never decides output format,
        layer type, or selection derivatives.  TaskContractError propagates
        to the caller as a CONTRACT_FAILED outcome.
        """
        inner = response.get("task_contract") if isinstance(response.get("task_contract"), dict) else response
        bound = bind_model_task_contract(inner, request_text, verifier_context)
        return parse_task_contract(bound, request_text, verifier_context)

    def resume_with_patch(self, request: RequestEnvelope, context: ContextSnapshot,
                          capabilities: CapabilitySnapshot,
                          task_contract: Dict[str, Any]) -> Outcome:
        """Build IntentSpec from one server-patched contract without a model call."""
        try:
            parsed = parse_task_contract(task_contract, request.text, context_verifier_view(context))
        except TaskContractError as exc:
            return outcome_failed(contracts.CONTRACT_FAILED, "intent", "clarification_patch_invalid", str(exc))
        if parsed.get("clarifications"):
            return outcome_failed(contracts.CONTRACT_FAILED, "intent", "clarification_patch_incomplete",
                                  "typed clarification patch did not clear the pending request")
        intent = self._build_intent(request, parsed, context, capabilities)
        return outcome_succeeded("intent", "澄清答案已确定性应用。",
                                 details={"intent": intent, "task_contract": parsed})

    # -- IntentSpec construction -------------------------------------------

    def _build_intent(self, request: RequestEnvelope, task_contract: Dict[str, Any],
                      context: ContextSnapshot,
                      capabilities: CapabilitySnapshot) -> IntentSpec:
        live_layers = {}
        for layer in context.layers:
            live_layers[layer.identity.layer_ref] = layer.identity.layer_ref
            live_layers[layer.identity.name] = layer.identity.layer_ref
        bound_inputs = tuple(
            EntityBinding(
                name=entity.get("entity_id", ""),
                kind=entity.get("kind") or "input",
                path=(entity.get("reference")
                      if entity.get("reference") not in live_layers else None),
                layer_ref=live_layers.get(entity.get("reference")),
            )
            for entity in task_contract.get("input_entities", [])
        )
        constraints = tuple(
            req.get("requirement_id", "")
            for req in task_contract.get("requirements", [])
        )
        expected_outputs = tuple(
            output.get("output_id", "")
            for output in task_contract.get("outputs", [])
        )
        effects = task_contract.get("allowed_side_effects", [])
        level = max((_EFFECT_TO_LEVEL.get(e, 1) for e in effects), default=1)
        explanations: Tuple[Tuple[str, str], ...] = tuple(
            (entity.get("entity_id", ""), entity.get("evidence", ""))
            for entity in task_contract.get("input_entities", [])
            if entity.get("evidence")
        )
        return IntentSpec(
            session_id=request.session_id,
            request_id=request.request_id,
            bound_inputs=bound_inputs,
            business_goal=request.text,
            constraints=constraints,
            expected_outputs=expected_outputs,
            acceptable_side_effects=level,
            explanations=explanations,
            derived_facts={
                "task_contract": task_contract,
                "context_digest": context.digest,
                "capability_digest": capabilities.digest,
                "security_scope": {
                    "tenant_id": request.caller.tenant_id,
                    "role": request.caller.role,
                    "data_scope": tuple(request.caller.data_scope),
                },
                "publication_targets": tuple(request.outputs),
                "declared_outputs": tuple(task_contract.get("outputs", [])),
                "model_plan": request.model_plan,
                "model_binding_summary": request.model_binding_summary,
            },
            model_identity=self.model_runtime.model_identity_for(
                AgentModelPlan.model_validate(request.model_plan), "compiler"),
            prompt_version=PROMPT_VERSION,
        )


def _security_scope_hash(request: RequestEnvelope) -> str:
    return contracts.digest({
        "tenant": request.caller.tenant_id,
        "role": request.caller.role,
        "data_scope": list(request.caller.data_scope),
    })
