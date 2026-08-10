"""Fake deep-module adapters for Stage A end-to-end kernel testing.

These implement the kernel's Protocol ports with deterministic, in-process
behaviour. They are the only model/ArcMap substitutes allowed during Stage A
(§2.7, §A.4). Real MiniMax-M3 and ArcMap adapters arrive in later stages and
replace these fakes at the injection point, never inside the kernel.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, List, Optional

from gateway_py3.kernel import contracts
from gateway_py3.kernel.contracts import (
    CapabilitySnapshot, CapabilitySpec, ContextSnapshot, IntentSpec,
    LayerRef, LayerSnapshot, FieldColumn,
    Outcome, RequestEnvelope, RuntimeLease, VerifiedPlan, WorkflowStep,
    EntityBinding, outcome_succeeded, outcome_paused, outcome_failed,
)
from gateway_py3.model_runtime.adapter import ProviderError
from gateway_py3.model_runtime.contracts import (
    AgentModelPlan, ModelBinding, ProviderConnection, ProviderInvocation,
    ProviderResponse, StructuredOutputContract, TokenPlan,
)
from gateway_py3.model_runtime.registry import ProviderRegistry

FAKE_LEASE_ID = "00000000-0000-0000-0000-0000000000aa"
FAKE_PLAN_ID = "00000000-0000-0000-0000-0000000000bb"
FAKE_DEPLOYMENT_HASH = "fake-deployment-v1"
FAKE_MODEL_IDENTITY = "fake-model"
FAKE_PROMPT_VERSION = "fake-prompt-v1"
FAKE_DOMAIN_RULE_HASH = "fake-rules-v1"
FAKE_REGISTRY_VERSION = "fake-registry-v1"
FAKE_CONNECTION_ID = "fake-connection"


def fake_provider_connection(provider_type: str = "fake",
                             model_id: str = "Fake",
                             connection_id: str = FAKE_CONNECTION_ID,
                             endpoint: str = "http://fake.invalid/v1") -> ProviderConnection:
    return ProviderConnection(
        connection_id=connection_id,
        provider_type=provider_type,
        endpoint=endpoint,
        credential_ref=None,
        enabled_models=(model_id,),
        deployment_fingerprint="fake-deployment",
    )


def fake_agent_model_plan(connection_id: str = FAKE_CONNECTION_ID,
                          model_id: str = "Fake") -> AgentModelPlan:
    budget = TokenPlan(
        call_budget=100, context_token_limit=100_000,
        output_token_limit=10_000, concurrency_limit=8,
        requests_per_minute=1_000, tokens_per_minute=10_000_000,
        cost_limit_microusd=None,
    )
    def binding(role: str) -> ModelBinding:
        return ModelBinding(
            connection_id=connection_id, model_id=model_id, role=role,
            temperature=0.0, max_output_tokens=2_000, budget_policy=budget,
        )
    return AgentModelPlan(
        compiler=binding("compiler"), planner=binding("planner"),
        auditor=binding("auditor"), repairer=binding("repairer"),
    )


def build_test_model_runtime(adapter, store, *,
                             connection: Optional[ProviderConnection] = None,
                             plan: Optional[AgentModelPlan] = None):
    from gateway_py3.model_runtime import ModelRuntime
    connection = connection or fake_provider_connection(
        provider_type=adapter.provider_type,
        connection_id=adapter.connection_id,
    )
    registry = ProviderRegistry()
    registry.register(connection, adapter)
    return ModelRuntime(
        registry,
        plan or fake_agent_model_plan(connection.connection_id,
                                      connection.enabled_models[0]),
        store,
    )


def _fake_context_snapshot(run_id: str, lease_id: str = FAKE_LEASE_ID) -> ContextSnapshot:
    return ContextSnapshot(
        lease_id=lease_id,
        arcmap_pid=2000, bridge_pid=2001, bridge_port=8766, target_hwnd=3000,
        document_identity={"mxd": "Untitled.mxd", "data_frame": "Layers"},
        layers=(
            LayerSnapshot(
                identity=LayerRef(name="cities", layer_ref="cities",
                                 data_source="C:/data/cities.shp", layer_type="Feature Layer"),
                fields=(FieldColumn(name="NAME"), FieldColumn(name="POP"),),
                geometry_type="Point", coordinate_system="WGS84",
                selection_count=0,
            ),
        ),
        active_data_frame="Layers",
        edit_session_state="none",
        captured_at=1.0,
        deployment_hash=FAKE_DEPLOYMENT_HASH,
        content_hash="fake-content-hash",
    )

def _fake_capability_snapshot(run_id: str) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        operation_cards=(
            CapabilitySpec(
                operation_id="select_layer",
                business_semantic="选择地图图层",
                parameters_schema={"type": "object", "properties": {}},
                risk_level=1, idempotent=True,
            ),
        ),
        domain_rule_hash=FAKE_DOMAIN_RULE_HASH,
        registry_version=FAKE_REGISTRY_VERSION,
    )


def _fake_intent(request: RequestEnvelope, context: ContextSnapshot,
                 capabilities: CapabilitySnapshot) -> IntentSpec:
    bound = tuple(
        EntityBinding(name=layer.identity.name, kind="feature_layer",
                      layer_ref=layer.identity.layer_ref,
                      path=layer.identity.data_source)
        for layer in context.layers
    )
    return IntentSpec(
        session_id=request.session_id, request_id=request.request_id,
        bound_inputs=bound,
        business_goal=request.text,
        spatial_semantic="query",
        expected_outputs=("selection",),
        acceptable_side_effects=1,
        derived_facts={"task_contract": {"outputs": [], "requirements": []}},
        model_identity=FAKE_MODEL_IDENTITY,
        prompt_version=FAKE_PROMPT_VERSION,
    )


def _fake_plan(intent: IntentSpec, context: ContextSnapshot,
               capabilities: CapabilitySnapshot, risk_level: int = 1) -> VerifiedPlan:
    return VerifiedPlan(
        plan_id=FAKE_PLAN_ID, version=1,
        intent_digest=intent.digest, context_digest=context.digest,
        capability_digest=capabilities.digest,
        workflow=(
            WorkflowStep(
                id="step_1", operation="select_layer",
                arguments={"layer": "cities"}, reason="select the cities layer",
            ),
        ),
        validation_report={"valid": True, "errors": []},
        model_identity=FAKE_MODEL_IDENTITY,
        prompt_version=FAKE_PROMPT_VERSION,
        risk_level=risk_level,
        required_permissions=("read",),
    )


class FakeModelAdapter:
    """``ModelAdapter`` implementation returning a deterministic structured
    response for the ``submit_task_contract`` / ``submit_workflow`` contracts.

    Streaming (§14) is exercised through the same strict ``invoke`` interface.
    """

    provider_type = "fake"
    connection_id = FAKE_CONNECTION_ID

    def __init__(self, intent_payload: Optional[Dict[str, Any]] = None,
                 workflow_payload: Optional[Dict[str, Any]] = None,
                 require_clarification: bool = False):
        self.intent_payload = intent_payload
        self.workflow_payload = workflow_payload
        self.require_clarification = require_clarification
        self.call_count = 0

    def _response(self, contract: StructuredOutputContract) -> Dict[str, Any]:
        self.call_count += 1
        if self.require_clarification:
            return {"task_contract": {"clarifications": [{"clarification_id": "c1", "question": "请指定要操作的图层。"}]}}
        if contract.name == "submit_task_contract":
            payload = self.intent_payload or {"task_contract": {
                "input_entities": [], "outputs": [], "requirements": [],
                "allowed_side_effects": ["read_only"], "clarifications": [],
            }}
            return payload
        payload = self.workflow_payload or {"tool_calls": [
            {"name": "select_layer", "arguments": {"layer": "cities"}},
        ]}
        return payload

    def invoke(self, call: ProviderInvocation, on_token=None) -> ProviderResponse:
        if on_token is not None:
            for token in ("plan", ":", "select", "cities"):
                on_token(token)
        if call.tools:
            response = self._response_for_messages(call.messages)
        else:
            response = self._response(call.structured_contract)
        return ProviderResponse(response=response, usage={"provider": "fake", "total_tokens": 10})

    def _response_for_messages(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        self.call_count += 1
        system = messages[0]["content"] if messages else ""
        if "任务合同编译器" in system:
            return self.intent_payload or {"task_contract": {
                "input_entities": [], "outputs": [], "requirements": [],
                "allowed_side_effects": ["read_only"], "clarifications": [],
            }}
        return self.workflow_payload or {"tool_calls": [
            {"name": "select_layer", "arguments": {"layer": "cities"}},
        ]}

class FakeContextProvider:
    """§6.7 capture: returns a fixed snapshot per run."""
    def capture(self, run_id: str, lease) -> ContextSnapshot:
        return _fake_context_snapshot(run_id, lease.lease_id)


class FakeCapabilityProvider:
    """§6.5 snapshot: returns a fixed capability set per run."""
    def snapshot(self, run_id: str) -> CapabilitySnapshot:
        return _fake_capability_snapshot(run_id)


class FakeIntentCompiler:
    """§6.2 compile: calls the model through ModelRuntime, then binds the
    response into a deterministic IntentSpec (Stage B wiring)."""
    def __init__(self, model_runtime=None):
        self.model_runtime = model_runtime

    def compile(self, request: RequestEnvelope, context: ContextSnapshot,
                capabilities: CapabilitySnapshot, run_id: str = "") -> Outcome:
        if self.model_runtime is None:
            intent = _fake_intent(request, context, capabilities)
            return outcome_succeeded(
                "intent", "意图编译完成。",
                details={"intent": intent},
            )
        from gateway_py3.model_runtime.contracts import StructuredOutputContract
        contract = StructuredOutputContract(
            name="submit_task_contract", description="submit task contract",
            schema={"type": "object"},
        )
        # Cache-safe projection: user text + structural context only (§6.4).
        model_request = _build_model_request(
            request, "compiler", "请分析以下 GIS 请求并输出任务契约。",
            {"request": request.text, "context": _context_projection(context)},
        )
        result = self.model_runtime.invoke(model_request, contract)
        if result.status == "quota_stopped":
            return outcome_failed(
                contracts.QUOTA_STOPPED, "intent", "quota", result.error or "额度不足")
        if not result.succeeded:
            return outcome_failed(
                contracts.CONTRACT_FAILED, "intent", "model_call_failed",
                result.error or "模型调用失败")
        clarifications = (result.response or {}).get("task_contract", {}).get("clarifications")
        if clarifications:
            return outcome_paused(
                contracts.CLARIFICATION_REQUIRED, "intent", "need_layer",
                clarifications[0].get("question", "请补充信息。"),
            )
        intent = _fake_intent(request, context, capabilities)
        return outcome_succeeded(
            "intent", "意图编译完成。",
            details={"intent": intent},
        )


class FakeWorkflowPlanner:
    """§6.3 plan: calls the model through ModelRuntime, then binds the
    response into a deterministic VerifiedPlan (Stage B wiring).

    ``risk_level`` lets a test request a high-risk plan so the kernel pauses
    at ``authorization_required`` for an explicit decision (read-only plans
    at risk level 1 auto-authorize).
    """
    def __init__(self, model_runtime=None, risk_level: int = 1):
        self.model_runtime = model_runtime
        self.risk_level = risk_level

    def plan(self, run_id: str, intent: IntentSpec, context: ContextSnapshot,
             capabilities: CapabilitySnapshot) -> Outcome:
        return self.plan_ablation(run_id, intent, context, capabilities, auditor_enabled=True)

    def plan_ablation(self, run_id: str, intent: IntentSpec, context: ContextSnapshot,
                      capabilities: CapabilitySnapshot, auditor_enabled: bool) -> Outcome:
        if self.model_runtime is None:
            plan = _fake_plan(intent, context, capabilities, self.risk_level)
            return outcome_succeeded(
                "plan", "计划验证通过。",
                details={"plan": plan},
            )
        from gateway_py3.model_runtime.contracts import StructuredOutputContract
        contract = StructuredOutputContract(
            name="submit_workflow", description="submit workflow draft",
            schema={"type": "object"},
        )
        # Cache-safe projection: business fields only, no session/run identity
        # or timestamps (§6.4 cache-key invariant).
        intent_projection = {
            "business_goal": intent.business_goal,
            "spatial_semantic": intent.spatial_semantic,
            "constraints": list(intent.constraints),
            "expected_outputs": list(intent.expected_outputs),
            "bound_inputs": [
                {"name": e.name, "kind": e.kind}
                for e in intent.bound_inputs
            ],
        }
        model_request = _build_model_request(
            intent, "planner", "根据任务契约选择操作并生成工作流草案。",
            {"intent": intent_projection,
             "context": _context_projection(context),
             "capabilities": capabilities.operation_cards_as_dicts()},
        )
        result = self.model_runtime.invoke(model_request, contract)
        if result.status == "quota_stopped":
            return outcome_failed(
                contracts.QUOTA_STOPPED, "plan", "quota", result.error or "额度不足")
        if not result.succeeded:
            return outcome_failed(
                contracts.CONTRACT_FAILED, "plan", "model_call_failed",
                result.error or "模型调用失败")
        plan = _fake_plan(intent, context, capabilities, self.risk_level)
        return outcome_succeeded(
            "plan", "计划验证通过。",
            details={"plan": plan},
        )

    def decide_authorization(self, run_id, approved):
        return "authorized" if approved else "denied"


class FakeArcMapExecutor:
    """§6.7 acquire_lease + acquire + execute + reconcile: returns a lease and
    a succeeded runtime outcome."""
    def __init__(self):
        self.reconcile_calls = []

    def acquire_lease(self, run_id: str, target_selector) -> RuntimeLease:
        required = {"bridge_pid", "bridge_port", "arcmap_pid", "hwnd", "deployment_hash"}
        target_selector = target_selector.model_dump(mode="json")
        return RuntimeLease(
            lease_id=str(uuid.uuid4()), run_id=run_id, plan_digest="",
            gateway_pid=1000, arcmap_pid=target_selector["arcmap_pid"],
            bridge_pid=target_selector["bridge_pid"], bridge_port=target_selector["bridge_port"],
            target_hwnd=target_selector["hwnd"], deployment_hash=FAKE_DEPLOYMENT_HASH,
            epoch=1, acquired_at=1.0, last_heartbeat=1.0,
        )

    def execute(self, lease: RuntimeLease, plan: VerifiedPlan,
                grant: contracts.AuthorizationGrant) -> Outcome:
        result = {"selected": ["cities"]}
        receipt = {"receipt_id": str(uuid.uuid4()), "lease_id": lease.lease_id,
                   "epoch": lease.epoch, "plan_hash": plan.digest,
                   "deployment_hash": lease.deployment_hash, "status": "executed",
                   "result_hash": hashlib.sha256(json.dumps(
                       result, ensure_ascii=True, sort_keys=True, separators=(",", ":")
                   ).encode("ascii")).hexdigest(), "result": result}
        return outcome_succeeded(
            "execution", "ArcMap 执行完成。",
            details={"receipt": receipt},
        )

    def reconcile(self, lease: RuntimeLease, run_id: str) -> Outcome:
        """§6.7 reconcile probe: confirm the lease's run executed cleanly."""
        self.reconcile_calls.append((lease, run_id))
        return outcome_succeeded(
            "execution", "ArcMap 执行完成（reconcile）。",
        )


class FakeAcceptancePublisher:
    """§6.8 accept + publish: always passes."""
    def accept(self, intent: IntentSpec, plan: VerifiedPlan,
               runtime_outcome: Any, staged_artifacts: Any = None) -> Outcome:
        return outcome_succeeded(
            "acceptance", "成果验收通过。",
            details={"artifacts": [], "report": {"passed": True}},
        )

    def prepare(self, staged_artifacts: Any, acceptance_report: Any,
                grant: contracts.AuthorizationGrant, publication_id: str) -> Outcome:
        return outcome_succeeded("publish", "成果已准备发布。", details={"publication": {
            "publication_id": publication_id, "run_id": grant.run_id,
            "grant_id": grant.grant_id, "target_unit_path": "C:\\fake.gdb",
            "temporary_unit_path": "C:\\fake.prepared", "expected_manifest": [], "artifacts": []}})

    def commit(self, prepared: Dict[str, Any]) -> Outcome:
        return outcome_succeeded("publish", "成果已发布。", details={"publication": prepared})

    def materialize(self, prepared: Dict[str, Any], staged_artifacts: Any) -> Outcome:
        return outcome_succeeded("publish", "prepared 临时发布单元已验真。")

    def recover(self, prepared: Dict[str, Any], staged_artifacts: Any,
                acceptance_report: Any, grant: contracts.AuthorizationGrant) -> Outcome:
        return self.commit(prepared)


def _context_projection(context: ContextSnapshot) -> Dict[str, Any]:
    """Cache-safe structural projection of a ContextSnapshot (§4.2).

    Excludes lease identity, timestamps, deployment/content hashes and value
    summaries: those vary per capture and would defeat the exact-result cache
    (§6.4). The model sees stable structural facts only.
    """
    return {
        "layers": [
            {
                "name": layer.identity.name,
                "layer_ref": layer.identity.layer_ref,
                "layer_type": layer.identity.layer_type,
                "geometry_type": layer.geometry_type,
                "coordinate_system": layer.coordinate_system,
                "selection_count": layer.selection_count,
            }
            for layer in context.layers
        ],
        "active_data_frame": context.active_data_frame,
        "edit_session_state": context.edit_session_state,
    }


def _build_model_request(owner, role: str, system_prompt: str,
                         payload: Dict[str, Any]):
    """Build a ModelRequest from a run/session owner for ModelRuntime."""
    from gateway_py3.kernel.contracts import canonical_json
    from gateway_py3.model_runtime import ModelRequest
    return ModelRequest(
        tenant_id="t1", security_scope_hash="ssh1",
        role=role, prompt_version="v1",
        system_prompt=system_prompt,
        user_input=payload.get("request") or payload.get("text") or canonical_json(payload),
        tool_contract={"type": "object"},
        capability_hash="ch", context_projection=payload,
        domain_rule_hash="drh", generation_params={},
        run_id=getattr(owner, "request_id", ""),
    )


def wait_for_terminal(kernel, run_id: str, timeout: float = 5.0,
                      poll_interval: float = 0.005):
    """Poll ``inspect`` until the run reaches a terminal/paused stage.

    ``submit`` drives the state machine in a background thread and returns
    immediately at ``received``; tests that need the final state poll here.
    Returns the final RunView. Raises AssertionError on timeout.
    """
    import time
    from gateway_py3.kernel.contracts import (
        TERMINAL_STAGES, PAUSED_STAGES, RECEIVED,
    )
    deadline = time.time() + timeout
    last = kernel.inspect(run_id)
    while time.time() < deadline:
        if last.stage in TERMINAL_STAGES or last.stage in PAUSED_STAGES:
            return last
        if last.stage != RECEIVED and last.outcome is not None:
            return last
        time.sleep(poll_interval)
        last = kernel.inspect(run_id)
    raise AssertionError(
        "run %s did not settle within %.1fs (last stage=%s)"
        % (run_id, timeout, last.stage))


def build_fake_ports(store, adapter: Optional[FakeModelAdapter] = None,
                     policy: Optional[Any] = None, risk_level: int = 1,
                     bridge: Optional[Any] = None):
    """Wire all fake adapters into KernelPorts for a Stage A-D kernel.

    ``adapter`` lets a test inject a custom FakeModelAdapter (e.g. to force
    clarification or quota). ``policy`` defaults to the real PolicyGate so
    execute-path runs exercise real grant issuance (§6.6). ``risk_level``
    fixes the verified plan's risk level; use >= 2 to make execute-path runs
    pause at ``authorization_required`` instead of auto-authorizing.
    ``bridge`` wires a bridge client for lease-protocol callbacks.
    """
    from gateway_py3.kernel.coordinator import KernelPorts
    from gateway_py3.runtime.policy import PolicyGate
    model_runtime = build_test_model_runtime(adapter or FakeModelAdapter(), store)
    return KernelPorts(
        store=store,
        context=FakeContextProvider(),
        capabilities=FakeCapabilityProvider(),
        compiler=FakeIntentCompiler(model_runtime=model_runtime),
        planner=FakeWorkflowPlanner(model_runtime=model_runtime,
                                    risk_level=risk_level),
        policy=policy if policy is not None else PolicyGate(),
        executor=FakeArcMapExecutor(),
        acceptance=FakeAcceptancePublisher(),
        model=model_runtime,
        bridge=bridge,
    )
