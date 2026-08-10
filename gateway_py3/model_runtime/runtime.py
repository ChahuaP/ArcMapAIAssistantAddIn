"""Deep provider-neutral model execution module.

Callers submit a role-bound ``ModelRequest``.  This module alone resolves the
``AgentModelPlan``, chooses the exact registered connection, renders prompts,
enforces cache identity and budgets, invokes the provider adapter, validates
the response, and persists complete call evidence.  There is no default
provider lookup, provider switching, or retry path.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, ValidationError

from ..kernel.contracts import canonical_json
from ..kernel.store import JournalStore, QuotaStoppedError, UncertainCallError
from .adapter import ProviderError
from .contracts import (
    AgentModelPlan,
    ModelRequest,
    ModelResult,
    ProviderInvocation,
    ProviderResponse,
    ResolvedModelCall,
    StructuredOutputContract,
    model_binding_evidence,
)
from .registry import ProviderRegistry, ProviderRoute


def render_messages(request: ModelRequest,
                    contract: Optional[StructuredOutputContract]) -> List[Dict[str, str]]:
    """Render stable platform facts before dynamic context and user text."""
    system_parts = [request.system_prompt]
    if request.tool_contract:
        system_parts.append(canonical_json(request.tool_contract))
    system_parts.append(canonical_json({
        "capability_hash": request.capability_hash,
        "domain_rule_hash": request.domain_rule_hash,
        "prompt_version": request.prompt_version,
    }))
    user_parts = [request.user_input]
    predicate_catalog = request.generation_params.get("predicate_catalog")
    if predicate_catalog:
        user_parts.append("## 任务谓词目录\n" + predicate_catalog)
    diagnostics = request.generation_params.get("diagnostics")
    if diagnostics:
        user_parts.append(canonical_json({"diagnostics": diagnostics}))
    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": "\n\n".join([
            "## Context\n" + canonical_json(request.context_projection),
            "## Request\n" + "\n".join(user_parts),
        ])},
    ]


class ModelRuntime:
    """Sole model-call interface for every Agent and Workflow."""

    def __init__(self, registry: ProviderRegistry,
                 model_plan: AgentModelPlan,
                 store: JournalStore):
        self.registry = registry
        self.model_plan = model_plan
        self.store = store
        self._inflight: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._concurrency = {
            self._binding_key(binding): threading.BoundedSemaphore(
                binding.budget_policy.concurrency_limit
            )
            for binding in model_plan.bindings()
        }
        self._validate_plan()

    def _validate_plan(self) -> None:
        for binding in self.model_plan.bindings():
            self.registry.resolve(binding)

    @staticmethod
    def _binding_key(binding) -> Tuple[str, str, str]:
        return binding.connection_id, binding.model_id, binding.role

    @property
    def runtime_identity(self) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for binding in self.model_plan.bindings():
            route = self.registry.resolve(binding)
            identity = model_binding_evidence(route.connection, binding)
            identity["adapter_type"] = "%s.%s" % (
                type(route.adapter).__module__, type(route.adapter).__name__
            )
            result[binding.role] = identity
        return result

    def identity_for(self, role: str) -> Dict[str, Any]:
        try:
            return dict(self.runtime_identity[role])
        except KeyError:
            raise KeyError("unbound agent role: %s" % role)

    def model_identity(self, role: str) -> str:
        identity = self.identity_for(role)
        return "%s/%s@%s" % (
            identity["provider"], identity["model"],
            identity["endpoint_fingerprint"],
        )

    def invoke(self, request: ModelRequest,
               contract: Optional[StructuredOutputContract],
               *,
               response_model: Optional[Type[BaseModel]] = None,
               on_token: Optional[Callable[[str], None]] = None,
               ) -> ModelResult:
        route, resolved = self._resolve(request)
        cached = self._lookup_cache(resolved)
        if cached is not None:
            if request.run_id:
                self.store.record_cache_hit(resolved.call_key, request.run_id)
            return cached

        budget_error = self._budget_error(resolved)
        if budget_error is not None:
            return ModelResult(call_key=resolved.call_key,
                               status="quota_stopped", error=budget_error)

        try:
            reserved = self._reserve(resolved)
        except QuotaStoppedError:
            return ModelResult(call_key=resolved.call_key, status="quota_stopped",
                               error="prior call hit quota stop; not retried")
        except UncertainCallError:
            return ModelResult(call_key=resolved.call_key, status="uncertain",
                               error="prior call finished uncertain; needs adjudication")
        if not reserved:
            return self._wait_for_inflight(resolved)

        started_at = time.monotonic()
        self._emit(resolved, "model.call_started", {
            "call_key": resolved.call_key, **resolved.evidence(),
        })
        result = ModelResult(call_key=resolved.call_key, status="uncertain",
                             error="provider call did not complete")
        try:
            result = self._call_adapter(
                route, resolved, contract,
                response_model=response_model, on_token=on_token,
            )
        except Exception as exc:
            result = self._classify_exception(resolved, exc)
        finally:
            try:
                self._commit(resolved, result)
                self._emit(resolved, "model.call_finished", {
                    "call_key": resolved.call_key,
                    **resolved.evidence(),
                    "status": result.status,
                    "latency_ms": int((time.monotonic() - started_at) * 1000),
                })
            finally:
                self._release(resolved.call_key)
        return result

    def _resolve(self, request: ModelRequest) -> Tuple[ProviderRoute, ResolvedModelCall]:
        binding = self.model_plan.binding_for(request.role)
        route = self.registry.resolve(binding)
        return route, ResolvedModelCall(
            request=request, connection=route.connection, binding=binding,
        )

    def _lookup_cache(self, resolved: ResolvedModelCall) -> Optional[ModelResult]:
        record = self.store.get_model_call(resolved.call_key)
        if record is None or record["status"] != "succeeded" or record["response"] is None:
            return None
        ledger = record.get("ledger") or {}
        evidence = resolved.evidence()
        if record["provider"] != evidence["provider"] or record["model"] != evidence["model"]:
            raise RuntimeError("model cache identity differs from its call key.")
        for field in (
            "connection_id", "endpoint_fingerprint", "deployment_fingerprint",
            "credential_ref", "role", "parameters", "token_plan",
        ):
            if ledger.get(field) != evidence[field]:
                raise RuntimeError("model cache evidence differs for %s." % field)
        return ModelResult(
            call_key=resolved.call_key, status="succeeded",
            response=record["response"], usage=ledger.get("usage", {}),
        )

    def _budget_error(self, resolved: ResolvedModelCall) -> Optional[str]:
        request = resolved.request
        if not request.run_id:
            return None
        calls = [
            call for call in self.store.list_model_calls_for_run(request.run_id)
            if not call.get("cache_hit")
            and (call.get("ledger") or {}).get("role") == resolved.binding.role
        ]
        plan = resolved.binding.budget_policy
        if len(calls) >= plan.call_budget:
            return "agent role call budget exhausted"
        now = time.time()
        recent = [call for call in calls if now - float(call.get("reserved_at") or 0) < 60.0]
        if len(recent) >= plan.requests_per_minute:
            return "agent role request rate limit exhausted"
        tokens = sum(int(((call.get("ledger") or {}).get("usage") or {}).get("total_tokens") or 0)
                     for call in recent)
        if tokens >= plan.tokens_per_minute:
            return "agent role token rate limit exhausted"
        if plan.cost_limit_microusd is not None:
            cost = sum(int(((call.get("ledger") or {}).get("usage") or {}).get("cost_microusd") or 0)
                       for call in calls)
            if cost >= plan.cost_limit_microusd:
                return "agent role cost limit exhausted"
        return None

    def _reserve(self, resolved: ResolvedModelCall) -> bool:
        call_key = resolved.call_key
        with self._lock:
            if call_key in self._inflight:
                return False
            self._inflight[call_key] = threading.Event()
        evidence = resolved.evidence()
        try:
            won = self.store.reserve_model_call(
                call_key=call_key,
                run_id=resolved.request.run_id,
                provider=evidence["provider"],
                model=evidence["model"],
                request_hash=call_key,
                ledger={
                    "status": "reserved",
                    "tenant_id": resolved.request.tenant_id,
                    "security_scope_hash": resolved.request.security_scope_hash,
                    **evidence,
                },
            )
        except Exception:
            self._release(call_key)
            raise
        if not won:
            self._release(call_key)
        return won

    def _wait_for_inflight(self, resolved: ResolvedModelCall) -> ModelResult:
        with self._lock:
            event = self._inflight.get(resolved.call_key)
        if event is not None:
            event.wait(timeout=300.0)
        cached = self._lookup_cache(resolved)
        if cached is not None:
            if resolved.request.run_id:
                self.store.record_cache_hit(resolved.call_key, resolved.request.run_id)
            return cached
        return ModelResult(
            call_key=resolved.call_key, status="uncertain",
            error="in-flight call did not produce a cacheable result",
        )

    def _release(self, call_key: str) -> None:
        with self._lock:
            event = self._inflight.pop(call_key, None)
        if event:
            event.set()

    def _emit(self, resolved: ResolvedModelCall,
              kind: str, payload: Dict[str, Any]) -> None:
        run_id = resolved.request.run_id
        if not run_id:
            return
        try:
            row = self.store.get_run(run_id)
        except KeyError:
            return
        self.store.append_event(run_id, kind, row["stage"], payload)

    def _call_adapter(self, route: ProviderRoute,
                      resolved: ResolvedModelCall,
                      contract: Optional[StructuredOutputContract],
                      *, response_model: Optional[Type[BaseModel]],
                      on_token: Optional[Callable[[str], None]]) -> ModelResult:
        request = resolved.request
        if not request.tools and contract is None:
            raise ValueError("structured model calls require an output contract.")
        if request.tools and contract is not None:
            raise ValueError("a model call cannot combine a tool set with one forced output contract.")
        invocation = ProviderInvocation(
            model_id=resolved.binding.model_id,
            messages=render_messages(request, contract),
            structured_contract=contract,
            tools=request.tools,
            temperature=resolved.binding.temperature,
            max_output_tokens=resolved.binding.max_output_tokens,
            context_token_limit=resolved.binding.budget_policy.context_token_limit,
        )
        semaphore = self._concurrency[self._binding_key(resolved.binding)]
        with semaphore:
            provider_response = route.adapter.invoke(
                invocation,
                None if on_token is None else
                lambda token: self._emit_token(resolved, token, on_token),
            )
        if not isinstance(provider_response, ProviderResponse):
            raise ProviderError("protocol", "provider adapter returned an invalid response contract.")
        if not isinstance(provider_response.response, dict) or not isinstance(provider_response.usage, dict):
            raise ProviderError("protocol", "provider adapter response and usage must be objects.")
        response = provider_response.response
        if response_model is not None:
            try:
                response = response_model.model_validate(response).model_dump(mode="json")
            except ValidationError as exc:
                return ModelResult(
                    call_key=resolved.call_key, status="failed",
                    error="schema validation failed: %s" % exc,
                )
        return ModelResult(
            call_key=resolved.call_key, status="succeeded",
            response=response, usage=provider_response.usage,
        )

    def _emit_token(self, resolved: ResolvedModelCall, token: str,
                    callback: Callable[[str], None]) -> None:
        if not isinstance(token, str):
            raise TypeError("model stream token must be text")
        self._emit(resolved, "model.token", {
            "call_key": resolved.call_key,
            "role": resolved.binding.role,
            "token": token,
        })
        callback(token)

    @staticmethod
    def _classify_exception(resolved: ResolvedModelCall,
                            exc: Exception) -> ModelResult:
        if isinstance(exc, ProviderError):
            status = {
                "quota": "quota_stopped",
                "protocol": "failed",
                "transport": "uncertain",
                "uncertain": "uncertain",
            }[exc.kind]
            return ModelResult(call_key=resolved.call_key, status=status, error=str(exc))
        return ModelResult(
            call_key=resolved.call_key, status="uncertain",
            error="%s: %s" % (type(exc).__name__, str(exc)),
        )

    def _commit(self, resolved: ResolvedModelCall,
                result: ModelResult) -> None:
        ledger = {
            "status": result.status,
            "tenant_id": resolved.request.tenant_id,
            "security_scope_hash": resolved.request.security_scope_hash,
            **resolved.evidence(),
            "usage": result.usage,
            "committed_at": time.time(),
        }
        self.store.commit_model_call(
            call_key=resolved.call_key,
            status=result.status,
            response=result.response if result.cacheable else None,
            ledger=ledger,
        )
