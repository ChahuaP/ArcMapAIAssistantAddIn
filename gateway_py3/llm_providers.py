from __future__ import annotations

import html
import json
import os
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from .paths import config_path, localappdata_dir


DEEPSEEK_PROVIDER = "deepseek"
MINIMAX_PROVIDER = "minimax"
ZHIPU_PROVIDER = "zhipu"
SUPPORTED_PROVIDERS = (DEEPSEEK_PROVIDER, MINIMAX_PROVIDER, ZHIPU_PROVIDER)
SEMI_AGENT_MODE = "semi_agent"
FULL_AGENT_MODE = "full_agent"
MINIMAX_TOKEN_PLAN_BASE_URL = "https://api.minimaxi.com/v1"
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
MODEL_REQUEST_TIMEOUT_SECONDS = 300
MINIMAX_TEXT_TOOL_CALL_RE = re.compile(r"<minimax:tool_call>(.*?)</minimax:tool_call>", re.IGNORECASE | re.DOTALL)
MINIMAX_TEXT_INVOKE_RE = re.compile(r"<invoke\s+name=\"([^\"]+)\">(.*?)</invoke>", re.IGNORECASE | re.DOTALL)
MINIMAX_TEXT_PARAMETER_RE = re.compile(r"<parameter\s+name=\"([^\"]+)\">(.*?)</parameter>", re.IGNORECASE | re.DOTALL)

MODEL_OPTIONS = (
    {
        "provider": DEEPSEEK_PROVIDER,
        "model": "deepseek-v4-flash",
        "label": "DeepSeek V4 Flash 思考",
        "thinking": True,
    },
    {
        "provider": DEEPSEEK_PROVIDER,
        "model": "deepseek-v4-pro",
        "label": "DeepSeek V4 Pro 思考",
        "thinking": True,
    },
    {
        "provider": ZHIPU_PROVIDER,
        "model": "glm-5.1",
        "label": "智谱 GLM-5.1",
        "thinking": True,
    },
    {
        "provider": MINIMAX_PROVIDER,
        "model": "MiniMax-M2.7",
        "label": "MiniMax M2.7",
        "thinking": False,
    },
)
MODEL_OPTIONS_BY_PROVIDER = {
    provider_id: [item for item in MODEL_OPTIONS if item["provider"] == provider_id]
    for provider_id in SUPPORTED_PROVIDERS
}
THINKING_MODELS = {
    (item["provider"], item["model"])
    for item in MODEL_OPTIONS
    if item.get("thinking")
}

DEFAULT_CONFIG = {
    "default_mode": SEMI_AGENT_MODE,
    "semi_agent_provider": DEEPSEEK_PROVIDER,
    "semi_agent_model": "deepseek-v4-flash",
    "full_agent_provider": MINIMAX_PROVIDER,
    "full_agent_model": "MiniMax-M2.7",
    "providers": {
        DEEPSEEK_PROVIDER: {
            "model": "deepseek-v4-flash",
            "base_url": "https://api.deepseek.com",
        },
        MINIMAX_PROVIDER: {
            "model": "MiniMax-M2.7",
            "base_url": MINIMAX_TOKEN_PLAN_BASE_URL,
        },
        ZHIPU_PROVIDER: {
            "model": "glm-5.1",
            "base_url": ZHIPU_BASE_URL,
        },
    },
}

ENV_KEYS = {
    DEEPSEEK_PROVIDER: "DEEPSEEK_API_KEY",
    MINIMAX_PROVIDER: "MINIMAX_API_KEY",
    ZHIPU_PROVIDER: "ZHIPU_API_KEY",
}
ENV_KEY_ALIASES = {
    DEEPSEEK_PROVIDER: ("DEEPSEEK_API_KEY",),
    MINIMAX_PROVIDER: ("MINIMAX_API_KEY",),
    ZHIPU_PROVIDER: ("ZHIPU_API_KEY", "BIGMODEL_API_KEY"),
}
PROVIDER_OPTIONS = (
    {
        "id": DEEPSEEK_PROVIDER,
        "label": "DeepSeek",
        "env_key": ENV_KEYS[DEEPSEEK_PROVIDER],
        "key_placeholder": "DeepSeek API Key",
    },
    {
        "id": MINIMAX_PROVIDER,
        "label": "MiniMax",
        "env_key": ENV_KEYS[MINIMAX_PROVIDER],
        "key_placeholder": "MiniMax Token Plan Key",
    },
    {
        "id": ZHIPU_PROVIDER,
        "label": "智谱",
        "env_key": ENV_KEYS[ZHIPU_PROVIDER],
        "key_placeholder": "智谱开放平台 Key",
    },
)


class ProviderError(Exception):
    pass


class ChatProvider:
    provider_id = ""

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None, timeout: int = MODEL_REQUEST_TIMEOUT_SECONDS):
        provider_config = provider_settings(self.provider_id)
        self.api_key = api_key or provider_api_key(self.provider_id)
        self.model = model or provider_config["model"]
        self.base_url = (base_url or provider_config["base_url"]).rstrip("/")
        self.timeout = timeout

    def chat_json(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        payload = self._post_chat_completion({
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = payload["choices"][0]["message"]["content"]
        try:
            result = json.loads(content)
        except ValueError:
            raise ProviderError("%s returned non-JSON content." % self.provider_id)
        result["_usage"] = normalize_usage(self.provider_id, payload.get("usage", {}))
        return result

    def chat_agent(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload = self._post_chat_completion({
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0,
        })
        return {
            "message": payload["choices"][0]["message"],
            "usage": normalize_usage(self.provider_id, payload.get("usage", {})),
        }

    def _prepare_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        return body

    def _post_chat_completion(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key:
            raise ProviderError(missing_api_key_message(self.provider_id))
        body = self._prepare_body(dict(body))
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            "%s/chat/completions" % self.base_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ProviderError(provider_http_error(self.provider_id, exc.code, detail))
        except (TimeoutError, socket.timeout):
            raise ProviderError("%s 响应超时：已等待 %s 秒。请重试，或切换到更快的模型。" % (provider_label(self.provider_id), self.timeout))
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise ProviderError(str(exc))


class DeepSeekProvider(ChatProvider):
    provider_id = DEEPSEEK_PROVIDER

    def _prepare_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if _thinking_enabled(self.provider_id, self.model):
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = "max"
            body.pop("temperature", None)
        return body


class MiniMaxProvider(ChatProvider):
    provider_id = MINIMAX_PROVIDER

    def chat_agent(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        payload = self._post_chat_completion({
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0,
        })
        return {
            "message": _normalize_minimax_agent_message(payload["choices"][0]["message"]),
            "usage": normalize_usage(self.provider_id, payload.get("usage", {})),
        }


class ZhipuProvider(ChatProvider):
    provider_id = ZHIPU_PROVIDER

    def _prepare_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if _thinking_enabled(self.provider_id, self.model):
            body["thinking"] = {"type": "enabled"}
        return body


def create_provider(mode: str | None = None, provider_id: str | None = None) -> ChatProvider:
    selected = provider_id or provider_for_mode(mode or public_config()["default_mode"])
    model = None if provider_id else model_for_mode(mode or public_config()["default_mode"])
    if selected == DEEPSEEK_PROVIDER:
        return DeepSeekProvider(model=model)
    if selected == MINIMAX_PROVIDER:
        return MiniMaxProvider(model=model)
    if selected == ZHIPU_PROVIDER:
        return ZhipuProvider(model=model)
    raise ProviderError("未知模型供应商：%s。" % selected)


def load_config() -> Dict[str, Any]:
    path = active_config_path()
    if not path or not path.exists():
        return _normalized_config({})
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ProviderError("配置文件格式错误：config.json 必须是 JSON 对象。")
    return _normalized_config(data)


def save_config(config: Dict[str, Any]) -> Dict[str, Any]:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = load_config()
    except ProviderError as exc:
        if "旧字段" not in str(exc):
            raise
        existing = _normalized_config({})
    merged = _merge_config(existing, config)
    with path.open("w", encoding="utf-8-sig") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
    return public_config(merged)


def public_config(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    config = config or load_config()
    status = config_status(config)
    providers = {}
    for provider_id in SUPPORTED_PROVIDERS:
        settings = provider_settings(provider_id, config)
        providers[provider_id] = {
            "label": provider_label(provider_id),
            "has_api_key": bool(provider_api_key(provider_id, config)),
            "model": settings["model"],
            "base_url": settings["base_url"],
            "env_key": ENV_KEYS[provider_id],
        }
    return {
        "default_mode": config["default_mode"],
        "semi_agent_provider": config["semi_agent_provider"],
        "semi_agent_model": config["semi_agent_model"],
        "full_agent_provider": config["full_agent_provider"],
        "full_agent_model": config["full_agent_model"],
        "providers": providers,
        "provider_options": [dict(item) for item in PROVIDER_OPTIONS],
        "model_options": [dict(item) for item in MODEL_OPTIONS],
        "config_path": str(status["active_path"]),
        "config_file_exists": bool(status["active_path"].exists()),
        "checked_config_paths": [str(path) for path in status["checked_paths"]],
        "has_deepseek_api_key": providers[DEEPSEEK_PROVIDER]["has_api_key"],
        "has_minimax_api_key": providers[MINIMAX_PROVIDER]["has_api_key"],
        "has_zhipu_api_key": providers[ZHIPU_PROVIDER]["has_api_key"],
    }


def provider_for_mode(mode: str | None, config: Dict[str, Any] | None = None) -> str:
    config = config or load_config()
    if mode == FULL_AGENT_MODE:
        return config["full_agent_provider"]
    return config["semi_agent_provider"]


def model_for_mode(mode: str | None, config: Dict[str, Any] | None = None) -> str:
    config = config or load_config()
    if mode == FULL_AGENT_MODE:
        return config["full_agent_model"]
    return config["semi_agent_model"]


def provider_settings(provider_id: str, config: Dict[str, Any] | None = None) -> Dict[str, str]:
    config = config or load_config()
    providers = config.get("providers") or {}
    settings = providers.get(provider_id) or {}
    defaults = DEFAULT_CONFIG["providers"][provider_id]
    return {
        "model": str(settings.get("model") or defaults["model"]).strip(),
        "base_url": str(settings.get("base_url") or defaults["base_url"]).strip().rstrip("/"),
    }


def provider_api_key(provider_id: str, config: Dict[str, Any] | None = None) -> str | None:
    for env_key in ENV_KEY_ALIASES[provider_id]:
        if os.environ.get(env_key):
            return os.environ[env_key]
    config = config or load_config()
    settings = (config.get("providers") or {}).get(provider_id) or {}
    key = settings.get("api_key")
    return key if isinstance(key, str) and key.strip() else None


def checked_config_paths() -> List[Path]:
    paths = []
    override = os.environ.get("ARCMAP_AI_CONFIG")
    if override:
        paths.append(Path(override))
    paths.append(config_path())
    paths.append(localappdata_dir() / "config.json")
    result = []
    seen = set()
    for path in paths:
        normalized = str(path).lower()
        if normalized not in seen:
            result.append(path)
            seen.add(normalized)
    return result


def active_config_path() -> Path:
    paths = checked_config_paths()
    for path in paths:
        if path.exists():
            return path
    return config_path()


def config_status(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    active_path = active_config_path()
    if config is None:
        config = load_config()
    return {
        "active_path": active_path,
        "checked_paths": checked_config_paths(),
        "known_keys": sorted(config.keys()),
    }


def missing_api_key_message(provider_id: str) -> str:
    label = provider_label(provider_id)
    active_path = active_config_path()
    if active_path.exists():
        try:
            config = load_config()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return "%s API Key 配置文件读取失败：%s。请在网页右上角重新保存 Key。" % (label, exc)
        settings = (config.get("providers") or {}).get(provider_id) or {}
        if settings:
            return "%s API Key 未找到。已读取配置文件：%s，但没有 providers.%s.api_key 字段。" % (label, active_path, provider_id)
        return "%s API Key 未找到。配置文件里没有 %s 配置。请在网页右上角重新保存 Key。" % (label, provider_id)
    checked = "；".join(str(path) for path in checked_config_paths())
    return "%s API Key 未配置。请在网页右上角配置 Key。已检查路径：%s" % (label, checked)


def normalize_usage(provider_id: str, usage: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(usage or {})
    result["provider"] = provider_id
    return result


def _thinking_enabled(provider_id: str, model: str) -> bool:
    return (provider_id, model) in THINKING_MODELS


def _normalize_minimax_agent_message(message: Dict[str, Any]) -> Dict[str, Any]:
    if message.get("tool_calls"):
        return message
    calls = _minimax_text_tool_calls(message.get("content"))
    if not calls:
        return message
    normalized = dict(message)
    normalized["content"] = None
    normalized["tool_calls"] = calls
    return normalized


def _minimax_text_tool_calls(content: Any) -> List[Dict[str, Any]]:
    if not isinstance(content, str) or "<minimax:tool_call>" not in content.lower():
        return []
    calls = []
    for block in MINIMAX_TEXT_TOOL_CALL_RE.findall(content):
        for name, body in MINIMAX_TEXT_INVOKE_RE.findall(block):
            calls.append({
                "id": "minimax_text_call_%d" % (len(calls) + 1),
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(_minimax_text_tool_arguments(body), ensure_ascii=False)
                }
            })
    return calls


def _minimax_text_tool_arguments(body: str) -> Dict[str, Any]:
    arguments: Dict[str, Any] = {}
    for name, raw_value in MINIMAX_TEXT_PARAMETER_RE.findall(body):
        arguments[name] = _parse_minimax_text_tool_parameter(raw_value)
    return arguments


def _parse_minimax_text_tool_parameter(raw_value: str) -> Any:
    text = html.unescape(raw_value).strip()
    if not text:
        return ""
    if text[0] in "[{\"" or text in ("true", "false", "null") or re.match(r"^-?\d+(\.\d+)?$", text):
        try:
            return json.loads(text)
        except ValueError:
            return text
    return text


def provider_label(provider_id: str) -> str:
    return {
        DEEPSEEK_PROVIDER: "DeepSeek",
        MINIMAX_PROVIDER: "MiniMax",
        ZHIPU_PROVIDER: "智谱",
    }.get(provider_id, provider_id)


def provider_http_error(provider_id: str, status_code: int, detail: str) -> str:
    label = provider_label(provider_id)
    message = _extract_http_error_message(detail)
    readable = message or detail
    if status_code == 401:
        if provider_id == MINIMAX_PROVIDER:
            return "MiniMax Token Plan API Key 无效。请在右上角“API Key”里重新保存从 MiniMax Token Plan 页面获取的 Key，并确认接口地址为 https://api.minimaxi.com。原始信息：%s" % readable
        if provider_id == DEEPSEEK_PROVIDER:
            return "DeepSeek API Key 无效。请在右上角“API Key”里重新保存 DeepSeek API Key。原始信息：%s" % readable
        if provider_id == ZHIPU_PROVIDER:
            return "智谱 API Key 无效。请在右上角“API Key”里重新保存智谱 API Key。原始信息：%s" % readable
    return "%s HTTP %s：%s" % (label, status_code, readable)


def _extract_http_error_message(detail: str) -> str:
    try:
        payload = json.loads(detail)
    except ValueError:
        return detail.strip()
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    message = payload.get("message") if isinstance(payload, dict) else None
    if isinstance(message, str) and message.strip():
        return message.strip()
    return detail.strip()


def _normalized_config(config: Dict[str, Any]) -> Dict[str, Any]:
    normalized = json.loads(json.dumps(DEFAULT_CONFIG))
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    for provider_id in SUPPORTED_PROVIDERS:
        normalized["providers"][provider_id].update(providers.get(provider_id) or {})

    obsolete_keys = [key for key in ("deepseek_api_key", "model", "base_url") if key in config]
    if obsolete_keys:
        raise ProviderError("配置文件使用旧字段：%s。请在网页右上角重新保存 API Key。" % "、".join(obsolete_keys))

    for key in ("default_mode", "semi_agent_provider", "full_agent_provider"):
        if config.get(key):
            normalized[key] = config[key]
    for key in ("semi_agent_model", "full_agent_model"):
        if config.get(key):
            normalized[key] = config[key]
    for model_key, provider_key in (("semi_agent_model", "semi_agent_provider"), ("full_agent_model", "full_agent_provider")):
        if not config.get(model_key):
            normalized[model_key] = _default_model_for_provider(normalized[provider_key])
    _validate_config(normalized)
    return normalized


def _merge_config(existing: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = _normalized_config(existing)
    for key in ("default_mode", "semi_agent_provider", "full_agent_provider"):
        if patch.get(key):
            merged[key] = str(patch[key]).strip()
    for key in ("semi_agent_model", "full_agent_model"):
        if patch.get(key):
            merged[key] = str(patch[key]).strip()
    providers = patch.get("providers") if isinstance(patch.get("providers"), dict) else {}
    for provider_id in SUPPORTED_PROVIDERS:
        provider_patch = providers.get(provider_id) or {}
        for field in ("api_key", "model", "base_url"):
            value = provider_patch.get(field)
            if isinstance(value, str) and value.strip():
                merged["providers"][provider_id][field] = value.strip().rstrip("/") if field == "base_url" else value.strip()
    _validate_config(merged)
    return merged


def _validate_config(config: Dict[str, Any]) -> None:
    for mode_key in ("default_mode",):
        if config[mode_key] not in (SEMI_AGENT_MODE, FULL_AGENT_MODE):
            raise ProviderError("未知工作模式：%s。" % config[mode_key])
    for key in ("semi_agent_provider", "full_agent_provider"):
        if config[key] not in SUPPORTED_PROVIDERS:
            raise ProviderError("未知模型供应商：%s。" % config[key])
    _validate_mode_model(config, "semi_agent_provider", "semi_agent_model")
    _validate_mode_model(config, "full_agent_provider", "full_agent_model")


def _validate_mode_model(config: Dict[str, Any], provider_key: str, model_key: str) -> None:
    provider_id = config[provider_key]
    model = config[model_key]
    known_models = {item["model"] for item in MODEL_OPTIONS_BY_PROVIDER[provider_id]}
    if model not in known_models:
        raise ProviderError("模型 %s 不属于供应商 %s。" % (model, provider_label(provider_id)))


def _default_model_for_provider(provider_id: str) -> str:
    options = MODEL_OPTIONS_BY_PROVIDER.get(provider_id) or []
    if not options:
        raise ProviderError("未知模型供应商：%s。" % provider_id)
    return options[0]["model"]
