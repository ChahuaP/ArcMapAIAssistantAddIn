from __future__ import annotations

from gateway_py3.llm_providers import DEEPSEEK_PROVIDER, SUPPORTED_PROVIDERS
from gateway_py3.validators import ValidationError


QUIET_ACCESS_PATHS = (
    "/api/workflows",
    "/config",
    "/context",
    "/events",
    "/health",
    "/projects",
)


def config_payload(payload):
    allowed = {}
    for key in ("default_mode", "semi_agent_provider", "semi_agent_model", "full_agent_provider", "full_agent_model"):
        if payload.get(key):
            allowed[key] = payload[key]
    providers = payload.get("providers") if isinstance(payload.get("providers"), dict) else {}
    allowed_providers = {}
    for provider_id in SUPPORTED_PROVIDERS:
        source = providers.get(provider_id) or {}
        item = {}
        for field in ("api_key", "model", "base_url"):
            if isinstance(source.get(field), str) and source[field].strip():
                item[field] = source[field].strip()
        if provider_id == DEEPSEEK_PROVIDER and item.get("api_key") and not item["api_key"].startswith("sk-"):
            raise ValueError("DeepSeek API key must start with sk-.")
        if item:
            allowed_providers[provider_id] = item
    if allowed_providers:
        allowed["providers"] = allowed_providers
    speech = payload.get("speech") if isinstance(payload.get("speech"), dict) else {}
    allowed_speech = {}
    for field in ("provider", "model"):
        if isinstance(speech.get(field), str) and speech[field].strip():
            allowed_speech[field] = speech[field].strip()
    if allowed_speech:
        allowed["speech"] = allowed_speech
    return allowed


def public_operation(operation, detail: bool = False):
    schema = operation.get("parameters_schema", {})
    properties = schema.get("properties", {})
    result = {
        "id": operation["id"],
        "category": operation["category"],
        "summary": operation["summary"],
        "model_card": operation.get("model_card", ""),
        "side_effects": operation["side_effects"],
        "required": schema.get("required", []),
        "parameters": sorted(properties.keys()),
        "context_requirements": operation.get("context_requirements", {}),
        "example": (operation.get("examples") or [{}])[0].get("user", "")
    }
    if detail:
        result["parameters_schema"] = schema
        result["output_policy"] = operation.get("output_policy", {})
    return result


def bool_query(query, name: str, default: bool = False) -> bool:
    value = (query or {}).get(name, [""])[0]
    if value == "":
        return default
    return value.lower() in ("1", "true", "yes", "on")


def int_query(query, name: str, default: int, minimum: int = 1, maximum: int = 200) -> int:
    raw = (query or {}).get(name, [str(default)])[0]
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("%s must be an integer." % name)
    return max(minimum, min(value, maximum))


def float_query(query, name: str) -> float | None:
    raw = (query or {}).get(name, [""])[0]
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        raise ValueError("%s must be a number." % name)


def optional_query(query, name: str) -> str | None:
    raw = (query or {}).get(name, [""])[0].strip()
    return raw or None


def is_quiet_access_message(message):
    if " 200 " not in message:
        return False
    return any('"GET %s HTTP/' % path in message for path in QUIET_ACCESS_PATHS)


def public_error(exc):
    if isinstance(exc, ValidationError):
        return "任务信息不完整或参数不符合要求。请换一种更明确的说法。"
    if isinstance(exc, KeyError):
        return "没有找到对应记录，请刷新页面后再试。"
    message = str(exc)
    if message.startswith("DeepSeek API key must start with sk-."):
        return "DeepSeek API Key 格式不对，请检查后重新填写。"
    return message
