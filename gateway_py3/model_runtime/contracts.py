"""Immutable contracts for provider-neutral model execution."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, model_validator

from ..kernel.contracts import canonical_json, digest


AGENT_ROLES = ("compiler", "planner", "auditor", "repairer")


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s must be non-empty text." % label)
    return value.strip()


def normalize_endpoint(endpoint: str) -> str:
    value = _required_text(endpoint, "provider_connection.endpoint")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("provider_connection.endpoint must be an absolute HTTP(S) URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("provider_connection.endpoint cannot contain credentials, query, or fragment.")
    host = parsed.hostname.lower()
    port = (":%d" % parsed.port) if parsed.port is not None else ""
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), host + port, path, "", ""))


class _FrozenModel(BaseModel):
    model_config = {"frozen": True, "extra": "forbid", "validate_assignment": True}


@dataclass(frozen=True)
class StructuredOutputContract:
    name: str
    description: str
    schema: Dict[str, Any]


class TokenPlan(_FrozenModel):
    """Hard per-binding budget limits; zero and implicit unlimited values are forbidden."""

    call_budget: int = Field(gt=0)
    context_token_limit: int = Field(gt=0)
    output_token_limit: int = Field(gt=0)
    concurrency_limit: int = Field(gt=0)
    requests_per_minute: int = Field(gt=0)
    tokens_per_minute: int = Field(gt=0)
    cost_limit_microusd: Optional[int] = Field(default=None, gt=0)


class ProviderConnection(_FrozenModel):
    connection_id: str
    provider_type: str
    endpoint: str
    credential_ref: Optional[str] = None
    enabled_models: Tuple[str, ...]
    deployment_fingerprint: str

    @model_validator(mode="after")
    def _validate(self) -> "ProviderConnection":
        object.__setattr__(self, "connection_id", _required_text(self.connection_id, "connection_id"))
        object.__setattr__(self, "provider_type", _required_text(self.provider_type, "provider_type"))
        object.__setattr__(self, "endpoint", normalize_endpoint(self.endpoint))
        if self.credential_ref is not None:
            object.__setattr__(self, "credential_ref", _required_text(self.credential_ref, "credential_ref"))
        models = tuple(_required_text(item, "enabled_models item") for item in self.enabled_models)
        if not models or len(set(models)) != len(models):
            raise ValueError("enabled_models must be non-empty and unique.")
        object.__setattr__(self, "enabled_models", models)
        object.__setattr__(self, "deployment_fingerprint",
                           _required_text(self.deployment_fingerprint, "deployment_fingerprint"))
        return self

    @property
    def endpoint_fingerprint(self) -> str:
        document = [self.provider_type, self.endpoint, self.deployment_fingerprint]
        return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


class ModelBinding(_FrozenModel):
    connection_id: str
    model_id: str
    role: str
    temperature: float = Field(ge=0.0, le=2.0)
    max_output_tokens: int = Field(gt=0)
    budget_policy: TokenPlan

    @model_validator(mode="after")
    def _validate(self) -> "ModelBinding":
        object.__setattr__(self, "connection_id", _required_text(self.connection_id, "connection_id"))
        object.__setattr__(self, "model_id", _required_text(self.model_id, "model_id"))
        if self.role not in AGENT_ROLES:
            raise ValueError("model_binding.role must be one of %s." % ", ".join(AGENT_ROLES))
        if self.max_output_tokens > self.budget_policy.output_token_limit:
            raise ValueError("max_output_tokens exceeds the TokenPlan output limit.")
        return self


class AgentModelPlan(_FrozenModel):
    compiler: ModelBinding
    planner: ModelBinding
    auditor: ModelBinding
    repairer: ModelBinding

    @model_validator(mode="after")
    def _validate(self) -> "AgentModelPlan":
        for role in AGENT_ROLES:
            if getattr(self, role).role != role:
                raise ValueError("agent_model_plan.%s must bind role=%s." % (role, role))
        return self

    def binding_for(self, role: str) -> ModelBinding:
        if role not in AGENT_ROLES:
            raise KeyError("unbound agent role: %s" % role)
        return getattr(self, role)

    def bindings(self) -> Tuple[ModelBinding, ...]:
        return tuple(getattr(self, role) for role in AGENT_ROLES)


class ModelRequest(_FrozenModel):
    """Provider-neutral request; ModelRuntime resolves its role binding."""

    tenant_id: str
    security_scope_hash: str
    role: str
    prompt_version: str
    system_prompt: str
    user_input: str
    tool_contract: Dict[str, Any] = Field(default_factory=dict)
    tools: List[Dict[str, Any]] = Field(default_factory=list)
    capability_hash: str
    context_projection: Dict[str, Any]
    domain_rule_hash: str
    generation_params: Dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""

    @model_validator(mode="after")
    def _validate(self) -> "ModelRequest":
        if self.role not in AGENT_ROLES:
            raise ValueError("model request role is not present in AgentModelPlan.")
        for value, label in (
            (self.tenant_id, "tenant_id"),
            (self.security_scope_hash, "security_scope_hash"),
            (self.prompt_version, "prompt_version"),
            (self.system_prompt, "system_prompt"),
            (self.user_input, "user_input"),
            (self.capability_hash, "capability_hash"),
            (self.domain_rule_hash, "domain_rule_hash"),
        ):
            _required_text(value, label)
        return self


class ResolvedModelCall(_FrozenModel):
    request: ModelRequest
    connection: ProviderConnection
    binding: ModelBinding

    @property
    def call_key(self) -> str:
        components = [
            self.request.tenant_id,
            self.request.security_scope_hash,
            self.connection.provider_type,
            self.connection.connection_id,
            self.binding.model_id,
            self.connection.endpoint_fingerprint,
            self.connection.deployment_fingerprint,
            self.binding.role,
            self.request.prompt_version,
            digest(self.request.system_prompt),
            digest(self.request.user_input),
            digest(self.request.tool_contract),
            digest(self.request.tools),
            self.request.capability_hash,
            digest(self.request.context_projection),
            self.request.domain_rule_hash,
            digest(self.request.generation_params),
            digest({
                "temperature": self.binding.temperature,
                "max_output_tokens": self.binding.max_output_tokens,
                "token_plan": self.binding.budget_policy.model_dump(mode="json"),
            }),
        ]
        return hashlib.sha256(canonical_json(components).encode("utf-8")).hexdigest()

    def evidence(self) -> Dict[str, Any]:
        return model_binding_evidence(self.connection, self.binding)


def model_binding_evidence(connection: ProviderConnection,
                           binding: ModelBinding) -> Dict[str, Any]:
    """Canonical provider/model facts persisted for every resolved call."""
    return {
        "connection_id": connection.connection_id,
        "provider": connection.provider_type,
        "model": binding.model_id,
        "endpoint_fingerprint": connection.endpoint_fingerprint,
        "deployment_fingerprint": connection.deployment_fingerprint,
        "credential_ref": connection.credential_ref,
        "role": binding.role,
        "parameters": {
            "temperature": binding.temperature,
            "max_output_tokens": binding.max_output_tokens,
        },
        "token_plan": binding.budget_policy.model_dump(mode="json"),
    }


class ModelResult(_FrozenModel):
    call_key: str
    status: str
    response: Optional[Dict[str, Any]] = None
    usage: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    @property
    def cacheable(self) -> bool:
        return self.status == "succeeded" and self.response is not None

    @property
    def recoverable(self) -> bool:
        return self.status == "uncertain"


@dataclass(frozen=True)
class ProviderInvocation:
    model_id: str
    messages: List[Dict[str, str]]
    structured_contract: Optional[StructuredOutputContract]
    tools: List[Dict[str, Any]]
    temperature: float
    max_output_tokens: int
    context_token_limit: int


@dataclass(frozen=True)
class ProviderResponse:
    response: Dict[str, Any]
    usage: Dict[str, Any]


TokenCallback = Callable[[str], None]
