"""ModelRuntime: the sole owner of model calls (§6.4, §13.1, §14).

One ``invoke()`` entry point. It renders the canonical prompt, checks the
exact-result cache, performs single-flight reservation, calls the adapter,
validates the response and commits the call ledger. Caching, quota-stop and
uncertain-state handling are structural, not afterthoughts.

Cache key (§6.4) is SHA256 over 14 components:
  tenant_id + security_scope_hash + provider + model + role + prompt_version
  + system_prompt_hash + input_hash + tool_contract_hash + tools_hash
  + capability_hash + context_projection_hash + domain_rule_hash
  + generation_parameter_hash

Only ``succeeded + schema_validated`` records are reused. Quota errors,
network/protocol errors, contract-validation failures and uncertain calls are
never cached.

Streaming (§14): ``invoke`` accepts an optional ``on_token`` callback. When
the adapter supports streaming, tokens are pushed via ``on_token``; non-
streaming adapters skip the callback silently.

Response validation (§13.1): ``invoke`` accepts an optional ``response_model``
(a Pydantic ``BaseModel`` subclass). When provided, the adapter's raw response
is validated via ``response_model.model_validate()``; a ``ValidationError``
classifies the call as ``failed`` (not ``uncertain``), so a schema-mismatch
triggers the repair loop, not a silent retry.
"""
from __future__ import annotations

import hashlib
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, Type, runtime_checkable

from pydantic import BaseModel, ValidationError

from ..kernel.contracts import canonical_json, digest
from ..kernel.store import JournalStore
from ..llm_providers import StructuredOutputContract, ProviderError, ProviderProtocolError


# --- adapter protocol ------------------------------------------------------

@runtime_checkable
class ModelAdapter(Protocol):
    """A single provider's structured-output call (§6.4).

    MiniMax-M3 is the only production adapter; tests use FakeModelAdapter.
    The adapter never sees cache, ledger or prompt layout -- it receives the
    final messages and contract and returns a raw provider result.

    Adapters that support streaming implement ``chat_structured_stream``,
    yielding tokens before the final result dict.
    """
    provider: str
    model: str

    def chat_structured(self, messages: List[Dict[str, str]],
                        contract: StructuredOutputContract,
                        ) -> Dict[str, Any]: ...


# --- request / outcome (Pydantic v2, §13.1) --------------------------------

class ModelRequest(BaseModel):
    """Everything ModelRuntime needs to render a cacheable, ledger-bound call.

    The stable-prefix components (system_prompt, tool_contract, capability,
    domain_rule, prompt_version) are separated from the dynamic components
    (context_projection, user_input, diagnostics) so the renderer can place
    stable text first for provider prefix-cache hits (§6.4 Prompt layout).
    """
    model_config = {"frozen": True, "extra": "forbid", "validate_assignment": True}

    tenant_id: str
    security_scope_hash: str
    provider: str
    model: str
    role: str
    prompt_version: str
    system_prompt: str
    user_input: str
    tool_contract: Dict[str, Any] = {}
    tools: List[Dict[str, Any]] = []
    capability_hash: str
    context_projection: Dict[str, Any]
    domain_rule_hash: str
    generation_params: Dict[str, Any] = {}
    run_id: str = ""

    @property
    def call_key(self) -> str:
        """SHA256 over the cache-key components (§6.4).

        ``run_id`` is deliberately excluded: the same pure computation must
        produce the same key across runs and sessions. Run binding is recorded
        in the ledger, not in the cache key.
        """
        components = [
            self.tenant_id,
            self.security_scope_hash,
            self.provider,
            self.model,
            self.role,
            self.prompt_version,
            digest(self.system_prompt),
            digest(self.user_input),
            digest(self.tool_contract),
            digest(self.tools),
            self.capability_hash,
            digest(self.context_projection),
            self.domain_rule_hash,
            digest(self.generation_params),
        ]
        return hashlib.sha256(
            canonical_json(components).encode("utf-8")
        ).hexdigest()


class ModelResult(BaseModel):
    """The persisted, reusable result of one model call."""
    model_config = {"frozen": True, "extra": "forbid"}

    call_key: str
    status: str  # succeeded | failed | quota_stopped | uncertain
    response: Optional[Dict[str, Any]] = None
    usage: Dict[str, Any] = {}
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    @property
    def cacheable(self) -> bool:
        """Only succeeded + schema_validated calls may be reused (§6.4)."""
        return self.status == "succeeded" and self.response is not None

    @property
    def recoverable(self) -> bool:
        return self.status == "uncertain"


# --- prompt rendering (§6.4) -----------------------------------------------

def render_messages(request: ModelRequest,
                    contract: StructuredOutputContract) -> List[Dict[str, str]]:
    """Render the canonical prompt layout (§6.4).

    Stable platform contract -> stable role instruction -> stable capability
    index / tool contract -> versioned domain rules -> dynamic context
    projection -> dynamic user request -> diagnostics.

    Stable prefix never contains timestamps, run_id, session_id, random paths
    or unordered JSON. All JSON is canonical (sorted keys, tight separators).
    """
    system_parts = [
        request.system_prompt,
    ]
    if request.tool_contract:
        system_parts.append(canonical_json(request.tool_contract))
    system_parts.append(_capability_block(request.capability_hash, request.domain_rule_hash,
                      request.prompt_version))
    context_block = canonical_json(request.context_projection)
    user_parts = [request.user_input]
    predicate_catalog = request.generation_params.get("predicate_catalog")
    if predicate_catalog:
        user_parts.append("## 任务谓词目录\n" + predicate_catalog)
    if request.generation_params.get("diagnostics"):
        user_parts.append(canonical_json({"diagnostics": request.generation_params["diagnostics"]}))
    return [
        {"role": "system", "content": "\n\n".join(system_parts)},
        {"role": "user", "content": "\n\n".join([
            "## Context\n" + context_block,
            "## Request\n" + "\n".join(user_parts),
        ])},
    ]


def _capability_block(capability_hash: str, domain_rule_hash: str,
                      prompt_version: str) -> str:
    return canonical_json({
        "capability_hash": capability_hash,
        "domain_rule_hash": domain_rule_hash,
        "prompt_version": prompt_version,
    })


# --- ModelRuntime ----------------------------------------------------------

class ModelRuntime:
    """Sole owner of model calls (§6.4).

    Holds the adapter, the JournalStore (for the call ledger / cache), and an
    in-process single-flight lock so concurrent identical calls share one
    adapter invocation. Quota-stop is terminal: no retry, no provider switch.
    """

    def __init__(self, adapter: ModelAdapter, store: JournalStore):
        self.adapter = adapter
        self.store = store
        self._inflight: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def invoke(self, request: ModelRequest,
               contract: StructuredOutputContract,
               *,
               response_model: Optional[Type[BaseModel]] = None,
               on_token: Optional[Callable[[str], None]] = None,
               ) -> ModelResult:
        """One entry point for every model call (§6.4, §14).

        1. Check cache for a succeeded + schema_validated record.
        2. Reserve single-flight; if another caller is in-flight, wait.
        3. Call the adapter; classify the result.
        4. If ``response_model`` is provided, validate via Pydantic (§13.1).
        5. Commit the ledger; succeeded calls become cacheable.

        Streaming (§14): if the adapter supports ``chat_structured_stream``
        and ``on_token`` is provided, tokens are pushed via the callback
        before the final result. Non-streaming adapters skip silently.
        """
        cached = self._lookup_cache(request.call_key)
        if cached is not None:
            return cached

        if not self._reserve(request):
            return self._wait_for_inflight(request, contract)

        try:
            result = self._call_adapter(request, contract,
                                        response_model=response_model,
                                        on_token=on_token)
        except Exception as exc:
            result = self._classify_exception(request, exc)
        finally:
            self._commit(request, result)
            self._release(request.call_key)

        return result

    # -- cache lookup -------------------------------------------------------

    def _lookup_cache(self, call_key: str) -> Optional[ModelResult]:
        record = self.store.get_model_call(call_key)
        if record is None:
            return None
        if record["status"] != "succeeded" or record["response"] is None:
            return None
        return ModelResult(
            call_key=call_key, status="succeeded",
            response=record["response"],
            usage=record["ledger"].get("usage", {}),
        )

    # -- single-flight ------------------------------------------------------

    def _reserve(self, request: ModelRequest) -> bool:
        """Reserve the adapter call. Returns False if another caller holds it."""
        with self._lock:
            if request.call_key in self._inflight:
                return False
            self._inflight[request.call_key] = threading.Event()
        won = self.store.reserve_model_call(
            call_key=request.call_key, run_id=request.run_id,
            provider=request.provider, model=request.model,
            request_hash=request.call_key,
            ledger={"status": "reserved", "tenant_id": request.tenant_id},
        )
        if not won:
            with self._lock:
                event = self._inflight.pop(request.call_key, None)
            if event:
                event.set()
            return False
        return True

    def _wait_for_inflight(self, request: ModelRequest,
                           contract: StructuredOutputContract) -> ModelResult:
        with self._lock:
            event = self._inflight.get(request.call_key)
        if event is not None:
            event.wait(timeout=300.0)
        cached = self._lookup_cache(request.call_key)
        if cached is not None:
            return cached
        return ModelResult(
            call_key=request.call_key, status="uncertain",
            error="in-flight call did not produce a cacheable result",
        )

    def _release(self, call_key: str) -> None:
        with self._lock:
            event = self._inflight.pop(call_key, None)
        if event:
            event.set()

    # -- adapter call + classification --------------------------------------

    def _call_adapter(self, request: ModelRequest,
                      contract: StructuredOutputContract,
                      *,
                      response_model: Optional[Type[BaseModel]] = None,
                      on_token: Optional[Callable[[str], None]] = None,
                      ) -> ModelResult:
        messages = render_messages(request, contract)
        try:
            if request.tools:
                raw = self.adapter.chat_with_tools(messages, request.tools)
            elif on_token is not None and hasattr(self.adapter, "chat_structured_stream"):
                raw = self.adapter.chat_structured_stream(messages, contract, on_token)
            else:
                raw = self.adapter.chat_structured(messages, contract)
        except ProviderError as exc:
            return self._classify_provider_error(request, exc)
        response = self._extract_response(raw)
        usage = raw.get("_usage") or raw.get("usage") or {}
        if response_model is not None:
            try:
                validated = response_model.model_validate(response)
                response = validated.model_dump(mode="json")
            except ValidationError as exc:
                return ModelResult(
                    call_key=request.call_key, status="failed",
                    error="schema validation failed: %s" % exc,
                )
        return ModelResult(
            call_key=request.call_key, status="succeeded",
            response=response, usage=usage,
        )

    def _extract_response(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Keep only the schema-validated payload; drop provider wire noise.

        The ``_provider_response`` and ``_usage`` keys are metadata, not model
        output; they must not enter the cache (§6.4: cached responses must not
        contain session identity or transient output paths).
        """
        return {
            key: value for key, value in raw.items()
            if key not in ("_provider_response", "_usage")
        }

    def _classify_exception(self, request: ModelRequest, exc: Exception) -> ModelResult:
        if isinstance(exc, ProviderError):
            return self._classify_provider_error(request, exc)
        return ModelResult(
            call_key=request.call_key, status="uncertain",
            error="%s: %s" % (type(exc).__name__, str(exc)),
        )

    def _classify_provider_error(self, request: ModelRequest, exc: ProviderError) -> ModelResult:
        message = str(exc)
        lowered = message.lower()
        if "quota" in lowered or "余额" in message or "额度" in message:
            return ModelResult(
                call_key=request.call_key, status="quota_stopped", error=message,
            )
        if isinstance(exc, ProviderProtocolError):
            return ModelResult(
                call_key=request.call_key, status="failed", error=message,
            )
        return ModelResult(
            call_key=request.call_key, status="uncertain", error=message,
        )

    # -- ledger commit ------------------------------------------------------

    def _commit(self, request: ModelRequest, result: ModelResult) -> None:
        ledger = {
            "tenant_id": request.tenant_id,
            "security_scope_hash": request.security_scope_hash,
            "role": request.role,
            "usage": result.usage,
            "committed_at": time.time(),
        }
        response = result.response if result.cacheable else None
        self.store.commit_model_call(
            call_key=request.call_key, status=result.status,
            response=response, ledger=ledger,
        )

