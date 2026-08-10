"""Strict persistent provider connections and per-role model bindings."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..kernel.contracts import canonical_json
from ..paths import model_configuration_path
from .adapters import create_provider_adapter
from .contracts import (
    AgentModelPlan, ModelBinding, ProviderConnection, TokenPlan,
    normalize_endpoint,
)
from .credentials import DpapiCredentialVault
from .registry import ProviderRegistry


_SCHEMA = "geopilot-model-configuration-v1"
_ROLES = ("compiler", "planner", "auditor", "repairer")
_PROVIDER_PRESETS = (
    {"provider_type": "minimax", "label": "MiniMax",
     "default_endpoint": "https://api.minimaxi.com/v1", "credential_required": True},
    {"provider_type": "deepseek", "label": "DeepSeek",
     "default_endpoint": "https://api.deepseek.com/v1", "credential_required": True},
    {"provider_type": "qwen", "label": "阿里百炼",
     "default_endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1", "credential_required": True},
    {"provider_type": "zhipu", "label": "智谱开放平台",
     "default_endpoint": "https://open.bigmodel.cn/api/paas/v4", "credential_required": True},
    {"provider_type": "zhipu_coding", "label": "智谱 Coding Plan",
     "default_endpoint": "https://open.bigmodel.cn/api/anthropic", "credential_required": True},
    {"provider_type": "ollama", "label": "Ollama",
     "default_endpoint": "http://127.0.0.1:11434/v1", "credential_required": False},
    {"provider_type": "openai_compatible", "label": "OpenAI 兼容服务",
     "default_endpoint": "http://127.0.0.1:8000/v1", "credential_required": False},
)
_PRESET_BY_TYPE = {item["provider_type"]: item for item in _PROVIDER_PRESETS}


@dataclass(frozen=True)
class RuntimeModelConfiguration:
    connections: Tuple[ProviderConnection, ...]
    plan: AgentModelPlan


class ModelConfigurationStore:
    """Deep configuration module; callers only load, save, or project public state."""

    def __init__(self, path: Optional[Path] = None,
                 vault: Optional[DpapiCredentialVault] = None):
        self.path = Path(path) if path is not None else model_configuration_path()
        self.vault = vault or DpapiCredentialVault()

    def load(self) -> RuntimeModelConfiguration:
        if not self.path.exists():
            return _default_configuration()
        with self.path.open("r", encoding="utf-8") as stream:
            document = json.load(stream)
        if not isinstance(document, dict) or set(document) != {
            "schema", "connections", "agent_model_plan",
        }:
            raise ValueError("model configuration document has an invalid contract.")
        if document["schema"] != _SCHEMA:
            raise ValueError("model configuration schema is incompatible.")
        connections = tuple(ProviderConnection.model_validate(item)
                            for item in document["connections"])
        plan = AgentModelPlan.model_validate(document["agent_model_plan"])
        configuration = RuntimeModelConfiguration(connections=connections, plan=plan)
        _validate_configuration(configuration, self.vault)
        return configuration

    def save(self, payload: Dict[str, Any]) -> RuntimeModelConfiguration:
        if not isinstance(payload, dict) or set(payload) != {
            "connections", "agent_model_plan",
        }:
            raise ValueError("model configuration request has an invalid contract.")
        connection_inputs = payload["connections"]
        plan_input = payload["agent_model_plan"]
        if not isinstance(connection_inputs, list) or not connection_inputs:
            raise ValueError("connections must be a non-empty array.")
        if not isinstance(plan_input, dict) or set(plan_input) != set(_ROLES):
            raise ValueError("agent_model_plan must bind every Agent role exactly once.")

        pending_credentials: Dict[str, str] = {}
        clear_credentials = set()
        connections = []
        for item in connection_inputs:
            connection, secret, clear = self._connection(item)
            connections.append(connection)
            if secret is not None:
                pending_credentials[connection.credential_ref] = secret
            if clear:
                clear_credentials.add(connection.credential_ref)

        plan = AgentModelPlan(**{
            role: _binding(role, plan_input[role]) for role in _ROLES
        })
        configuration = RuntimeModelConfiguration(tuple(connections), plan)
        _validate_configuration(configuration, self.vault)

        for ref, secret in pending_credentials.items():
            self.vault.put(ref, secret)
        for ref in clear_credentials:
            self.vault.delete(ref)
        self._write(configuration)
        return configuration

    def public(self) -> Dict[str, Any]:
        configuration = self.load()
        return {
            "provider_options": [dict(item) for item in _PROVIDER_PRESETS],
            "connections": [{
                "connection_id": item.connection_id,
                "provider_type": item.provider_type,
                "endpoint": item.endpoint,
                "enabled_models": list(item.enabled_models),
                "has_credential": bool(item.credential_ref and self.vault.has(item.credential_ref)),
                "credential_required": _PRESET_BY_TYPE[item.provider_type]["credential_required"],
            } for item in configuration.connections],
            "agent_model_plan": {
                role: {
                    "connection_id": configuration.plan.binding_for(role).connection_id,
                    "model_id": configuration.plan.binding_for(role).model_id,
                } for role in _ROLES
            },
        }

    def _connection(self, value: Any):
        if not isinstance(value, dict):
            raise ValueError("connection must be an object.")
        unknown = set(value) - {
            "connection_id", "provider_type", "endpoint", "enabled_models",
            "api_key", "clear_api_key",
        }
        if unknown:
            raise ValueError("connection contains unknown fields: %s" % ", ".join(sorted(unknown)))
        connection_id = _canonical_id(value.get("connection_id"), "connection_id")
        provider_type = _canonical_id(value.get("provider_type"), "provider_type")
        preset = _PRESET_BY_TYPE.get(provider_type)
        if preset is None:
            raise ValueError("unsupported provider_type: %s" % provider_type)
        credential_ref = "credential:%s" % connection_id
        secret = value.get("api_key")
        if secret is not None and (not isinstance(secret, str) or not secret.strip()):
            raise ValueError("api_key must be non-empty text when supplied.")
        clear = value.get("clear_api_key") is True
        if secret is not None and clear:
            raise ValueError("api_key and clear_api_key cannot be supplied together.")
        models = value.get("enabled_models")
        if not isinstance(models, list):
            raise ValueError("enabled_models must be an array.")
        endpoint = normalize_endpoint(value.get("endpoint"))
        fingerprint = hashlib.sha256(canonical_json({
            "provider_type": provider_type, "endpoint": endpoint,
        }).encode("utf-8")).hexdigest()
        connection = ProviderConnection(
            connection_id=connection_id,
            provider_type=provider_type,
            endpoint=endpoint,
            credential_ref=(credential_ref if preset["credential_required"] or
                            secret is not None or self.vault.has(credential_ref) else None),
            enabled_models=tuple(models),
            deployment_fingerprint=fingerprint,
        )
        return connection, (secret.strip() if isinstance(secret, str) else None), clear

    def _write(self, configuration: RuntimeModelConfiguration) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".writing")
        document = {
            "schema": _SCHEMA,
            "connections": [item.model_dump(mode="json")
                            for item in configuration.connections],
            "agent_model_plan": configuration.plan.model_dump(mode="json"),
        }
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(self.path))


def build_provider_registry(configuration: RuntimeModelConfiguration,
                            vault: DpapiCredentialVault) -> ProviderRegistry:
    registry = ProviderRegistry()
    for connection in configuration.connections:
        registry.register(connection, create_provider_adapter(connection, vault))
    return registry


def _default_configuration() -> RuntimeModelConfiguration:
    connection = ProviderConnection(
        connection_id="minimax-official",
        provider_type="minimax",
        endpoint="https://api.minimaxi.com/v1",
        credential_ref="credential:minimax-official",
        enabled_models=("MiniMax-M3",),
        deployment_fingerprint="minimax-public-api-v1",
    )
    return RuntimeModelConfiguration(
        connections=(connection,),
        plan=AgentModelPlan(**{
            role: _binding(role, {
                "connection_id": connection.connection_id,
                "model_id": "MiniMax-M3",
            }) for role in _ROLES
        }),
    )


def _binding(role: str, value: Any) -> ModelBinding:
    if not isinstance(value, dict) or set(value) != {"connection_id", "model_id"}:
        raise ValueError("Agent role binding must contain connection_id and model_id.")
    budget = TokenPlan(
        call_budget=24,
        context_token_limit=200_000,
        output_token_limit=16_384,
        concurrency_limit=2,
        requests_per_minute=30,
        tokens_per_minute=500_000,
        cost_limit_microusd=None,
    )
    return ModelBinding(
        connection_id=value["connection_id"],
        model_id=value["model_id"],
        role=role,
        temperature=0.0,
        max_output_tokens=8_192,
        budget_policy=budget,
    )


def _validate_configuration(configuration: RuntimeModelConfiguration,
                            vault: DpapiCredentialVault) -> None:
    by_id = {item.connection_id: item for item in configuration.connections}
    if len(by_id) != len(configuration.connections):
        raise ValueError("connection_id values must be unique.")
    for connection in configuration.connections:
        if connection.provider_type not in _PRESET_BY_TYPE:
            raise ValueError("unsupported provider_type: %s" % connection.provider_type)
        create_provider_adapter(connection, vault)
    for binding in configuration.plan.bindings():
        connection = by_id.get(binding.connection_id)
        if connection is None:
            raise ValueError("Agent role references an unknown connection: %s" % binding.connection_id)
        if binding.model_id not in connection.enabled_models:
            raise ValueError("Agent role model is not enabled by its connection: %s" % binding.model_id)


def _canonical_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError("%s must be canonical text." % label)
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
    if any(character not in allowed for character in value):
        raise ValueError("%s contains unsupported characters." % label)
    return value
