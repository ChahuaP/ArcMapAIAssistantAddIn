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
QWEN_PROVIDER = "qwen"
SUPPORTED_PROVIDERS = (DEEPSEEK_PROVIDER, MINIMAX_PROVIDER, ZHIPU_PROVIDER, QWEN_PROVIDER)
SEMI_AGENT_MODE = "semi_agent"
FULL_AGENT_MODE = "full_agent"
MINIMAX_TOKEN_PLAN_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_MODEL = "MiniMax-M3"
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_ASR_MODEL = "qwen3-asr-flash"
MODEL_REQUEST_TIMEOUT_SECONDS = 300
MINIMAX_TEXT_TOOL_CALL_RE = re.compile(r"<minimax:tool_call>(.*?)</minimax:tool_call>", re.IGNORECASE | re.DOTALL)
MINIMAX_TEXT_INVOKE_RE = re.compile(r"<invoke\s+name=\"([^\"]+)\">(.*?)</invoke>", re.IGNORECASE | re.DOTALL)
MINIMAX_TEXT_PARAMETER_RE = re.compile(r"<parameter\s+name=\"([^\"]+)\">(.*?)</parameter>", re.IGNORECASE | re.DOTALL)
MINIMAX_THINKING_BLOCK_RE = re.compile(r"<think[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)


def _model_option(provider: str, option_id: str, api_model: str, label: str, thinking: bool = False) -> Dict[str, Any]:
    return {
        "provider": provider,
        "model": option_id,
        "id": option_id,
        "api_model": api_model,
        "label": label,
        "thinking": thinking,
    }


MODEL_OPTIONS = (
    _model_option(DEEPSEEK_PROVIDER, "deepseek-v4-flash-thinking", "deepseek-v4-flash", "DeepSeek V4 Flash 思考", True),
    _model_option(DEEPSEEK_PROVIDER, "deepseek-v4-flash-non-thinking", "deepseek-v4-flash", "DeepSeek V4 Flash 非思考"),
    _model_option(DEEPSEEK_PROVIDER, "deepseek-v4-pro-thinking", "deepseek-v4-pro", "DeepSeek V4 Pro 思考", True),
    _model_option(DEEPSEEK_PROVIDER, "deepseek-v4-pro-non-thinking", "deepseek-v4-pro", "DeepSeek V4 Pro 非思考"),
    _model_option(ZHIPU_PROVIDER, "glm-5.1-thinking", "glm-5.1", "智谱 GLM-5.1 思考", True),
    _model_option(MINIMAX_PROVIDER, "MiniMax-M2.5", "MiniMax-M2.5", "MiniMax M2.5"),
    _model_option(MINIMAX_PROVIDER, "MiniMax-M2.7", "MiniMax-M2.7", "MiniMax M2.7"),
    _model_option(MINIMAX_PROVIDER, MINIMAX_MODEL, MINIMAX_MODEL, "MiniMax M3"),
    _model_option(QWEN_PROVIDER, "qwen3.7-plus", "qwen3.7-plus", "阿里百炼 Qwen3.7 Plus"),
    _model_option(QWEN_PROVIDER, "deepseek-v4-flash", "deepseek-v4-flash", "阿里百炼 DeepSeek V4 Flash"),
    _model_option(QWEN_PROVIDER, "qwen3.6-flash-2026-04-16", "qwen3.6-flash-2026-04-16", "阿里百炼 Qwen3.6 Flash"),
    _model_option(QWEN_PROVIDER, "qwen3.6-35b-a3b", "qwen3.6-35b-a3b", "阿里百炼 Qwen3.6 35B A3B"),
    _model_option(QWEN_PROVIDER, "qwen3.7-max-2026-05-17", "qwen3.7-max-2026-05-17", "阿里百炼 Qwen3.7 Max"),
    _model_option(QWEN_PROVIDER, "glm-5.1", "glm-5.1", "阿里百炼 GLM-5.1"),
    _model_option(QWEN_PROVIDER, "qwen3.6-plus-2026-04-02", "qwen3.6-plus-2026-04-02", "阿里百炼 Qwen3.6 Plus"),
    _model_option(QWEN_PROVIDER, "qwen3.7-max-preview", "qwen3.7-max-preview", "阿里百炼 Qwen3.7 Max Preview"),
)
MODEL_OPTIONS_BY_PROVIDER = {
    provider_id: [item for item in MODEL_OPTIONS if item["provider"] == provider_id]
    for provider_id in SUPPORTED_PROVIDERS
}
MODEL_OPTIONS_BY_KEY = {
    (item["provider"], item["model"]): item
    for item in MODEL_OPTIONS
}
THINKING_MODELS = {
    (item["provider"], item["model"])
    for item in MODEL_OPTIONS
    if item.get("thinking")
}

DEFAULT_CONFIG = {
    "default_mode": SEMI_AGENT_MODE,
    "semi_agent_provider": DEEPSEEK_PROVIDER,
    "semi_agent_model": "deepseek-v4-flash-thinking",
    "full_agent_provider": MINIMAX_PROVIDER,
    "full_agent_model": MINIMAX_MODEL,
    "providers": {
        DEEPSEEK_PROVIDER: {
            "model": "deepseek-v4-flash-thinking",
            "base_url": "https://api.deepseek.com",
        },
        MINIMAX_PROVIDER: {
            "model": MINIMAX_MODEL,
            "base_url": MINIMAX_TOKEN_PLAN_BASE_URL,
        },
        ZHIPU_PROVIDER: {
            "model": "glm-5.1-thinking",
            "base_url": ZHIPU_BASE_URL,
        },
        QWEN_PROVIDER: {
            "model": "qwen3.6-flash-2026-04-16",
            "base_url": QWEN_BASE_URL,
        },
    },
    "speech": {
        "provider": "qwen_asr",
        "model": QWEN_ASR_MODEL,
    },
}

ENV_KEYS = {
    DEEPSEEK_PROVIDER: "DEEPSEEK_API_KEY",
    MINIMAX_PROVIDER: "MINIMAX_API_KEY",
    ZHIPU_PROVIDER: "ZHIPU_API_KEY",
    QWEN_PROVIDER: "DASHSCOPE_API_KEY",
}
ENV_KEY_ALIASES = {
    DEEPSEEK_PROVIDER: ("DEEPSEEK_API_KEY",),
    MINIMAX_PROVIDER: ("MINIMAX_API_KEY",),
    ZHIPU_PROVIDER: ("ZHIPU_API_KEY", "BIGMODEL_API_KEY"),
    QWEN_PROVIDER: ("BAILIAN_TOKEN_PLAN_API_KEY", "DASHSCOPE_TOKEN_PLAN_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "BAILIAN_API_KEY"),
}
PROVIDER_SECRET_FIELDS = {
    DEEPSEEK_PROVIDER: ("api_key",),
    MINIMAX_PROVIDER: ("api_key",),
    ZHIPU_PROVIDER: ("api_key",),
    QWEN_PROVIDER: ("token_plan_api_key", "api_key"),
}
PROVIDER_OPTIONS = (
    {
        "id": DEEPSEEK_PROVIDER,
        "label": "DeepSeek",
        "env_key": ENV_KEYS[DEEPSEEK_PROVIDER],
        "key_placeholder": "DeepSeek API Key",
        "key_fields": [
            {"field": "api_key", "label": "API Key", "placeholder": "DeepSeek API Key"},
        ],
    },
    {
        "id": MINIMAX_PROVIDER,
        "label": "MiniMax",
        "env_key": ENV_KEYS[MINIMAX_PROVIDER],
        "key_placeholder": "MiniMax Token Plan Key",
        "key_fields": [
            {"field": "api_key", "label": "Token Plan API Key", "placeholder": "MiniMax Token Plan API Key"},
        ],
    },
    {
        "id": ZHIPU_PROVIDER,
        "label": "智谱",
        "env_key": ENV_KEYS[ZHIPU_PROVIDER],
        "key_placeholder": "智谱开放平台 Key",
        "key_fields": [
            {"field": "api_key", "label": "API Key", "placeholder": "智谱开放平台 API Key"},
        ],
    },
    {
        "id": QWEN_PROVIDER,
        "label": "阿里百炼",
        "env_key": ENV_KEYS[QWEN_PROVIDER],
        "env_keys": list(ENV_KEY_ALIASES[QWEN_PROVIDER]),
        "key_placeholder": "阿里云百炼 API Key",
        "key_fields": [
            {"field": "api_key", "label": "API Key", "placeholder": "阿里百炼 API Key"},
            {"field": "token_plan_api_key", "label": "Token Plan API Key", "placeholder": "阿里百炼 Token Plan API Key"},
        ],
    },
)


class ProviderError(Exception):
    pass


class ChatProvider:
    provider_id = ""

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None, timeout: int = MODEL_REQUEST_TIMEOUT_SECONDS):
        provider_config = provider_settings(self.provider_id) if model is None else {}
        self.api_key = api_key or provider_api_key(self.provider_id)
        self.model_id = model or provider_config["model"]
        self.model = api_model_for(self.provider_id, self.model_id)
        self.base_url = (base_url or provider_config.get("base_url") or DEFAULT_CONFIG["providers"][self.provider_id]["base_url"]).rstrip("/")
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

    def chat_text(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        payload = self._post_chat_completion({
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
        })
        content = payload["choices"][0]["message"].get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("%s returned empty text content." % self.provider_id)
        return {
            "text": content.strip(),
            "_usage": normalize_usage(self.provider_id, payload.get("usage", {})),
        }

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
        if _thinking_enabled(self.provider_id, self.model_id):
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = "max"
            body.pop("temperature", None)
        return body


class MiniMaxProvider(ChatProvider):
    provider_id = MINIMAX_PROVIDER

    def chat_json(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        payload = self._post_chat_completion({
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        })
        content = _strip_minimax_thinking(payload["choices"][0]["message"]["content"])
        try:
            result = json.loads(content)
        except ValueError:
            raise ProviderError("%s returned non-JSON content." % self.provider_id)
        result["_usage"] = normalize_usage(self.provider_id, payload.get("usage", {}))
        return result

    def chat_text(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        result = super().chat_text(messages)
        text = _strip_minimax_thinking(result["text"])
        if not text:
            raise ProviderError("%s returned empty text content." % self.provider_id)
        result["text"] = text
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
            "message": _normalize_minimax_agent_message(payload["choices"][0]["message"]),
            "usage": normalize_usage(self.provider_id, payload.get("usage", {})),
        }


class ZhipuProvider(ChatProvider):
    provider_id = ZHIPU_PROVIDER

    def _prepare_body(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if _thinking_enabled(self.provider_id, self.model_id):
            body["thinking"] = {"type": "enabled"}
        return body


class QwenProvider(ChatProvider):
    provider_id = QWEN_PROVIDER


def create_provider(mode: str | None = None, provider_id: str | None = None) -> ChatProvider:
    config = load_config()
    selected = provider_id or provider_for_mode(mode or config["default_mode"], config)
    model = provider_settings(selected, config)["model"] if provider_id else model_for_mode(mode or config["default_mode"], config)
    base_url = provider_settings(selected, config)["base_url"]
    if selected == DEEPSEEK_PROVIDER:
        return DeepSeekProvider(model=model, base_url=base_url)
    if selected == MINIMAX_PROVIDER:
        return MiniMaxProvider(model=model, base_url=base_url)
    if selected == ZHIPU_PROVIDER:
        return ZhipuProvider(model=model, base_url=base_url)
    if selected == QWEN_PROVIDER:
        return QwenProvider(model=model, base_url=base_url)
    raise ProviderError("未知模型供应商：%s。" % selected)


def load_config() -> Dict[str, Any]:
    path = active_config_path()
    if not path or not path.exists():
        return _normalized_config({})
    return _normalized_config(_read_config_payload(path))


def save_config(config: Dict[str, Any]) -> Dict[str, Any]:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = load_config()
    except ProviderError:
        existing = _normalized_config(_read_existing_config_payload(), validate=False)
    merged = _merge_config(existing, config)
    with path.open("w", encoding="utf-8-sig") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
    return public_config(merged)


def public_config(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    config_error = ""
    if config is None:
        try:
            config = load_config()
        except ProviderError as exc:
            config = _normalized_config(_read_existing_config_payload(), validate=False)
            config_error = str(exc)
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
            "key_status": provider_key_status(provider_id, config),
            "api_key_source": provider_api_key_source(provider_id, config),
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
        "speech": public_speech_config(config),
        "config_path": str(status["active_path"]),
        "config_file_exists": bool(status["active_path"].exists()),
        "config_error": config_error,
        "checked_config_paths": [str(path) for path in status["checked_paths"]],
        "has_deepseek_api_key": providers[DEEPSEEK_PROVIDER]["has_api_key"],
        "has_minimax_api_key": providers[MINIMAX_PROVIDER]["has_api_key"],
        "has_zhipu_api_key": providers[ZHIPU_PROVIDER]["has_api_key"],
        "has_bailian_api_key": providers[QWEN_PROVIDER]["has_api_key"],
    }


def public_speech_config(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    settings = speech_settings(config)
    source = provider_api_key_source(QWEN_PROVIDER, config)
    return {
        "provider": settings["provider"],
        "model": settings["model"],
        "base_url": provider_settings(QWEN_PROVIDER, config)["base_url"],
        "has_api_key": bool(provider_api_key(QWEN_PROVIDER, config)),
        "api_key_source": source,
        "uses_provider": QWEN_PROVIDER,
    }


def speech_settings(config: Dict[str, Any] | None = None) -> Dict[str, str]:
    config = config or load_config()
    speech = config.get("speech") if isinstance(config.get("speech"), dict) else {}
    defaults = DEFAULT_CONFIG["speech"]
    return {
        "provider": str(speech.get("provider") or defaults["provider"]).strip(),
        "model": str(speech.get("model") or defaults["model"]).strip(),
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
    return provider_api_key_resolution(provider_id, config)["value"]


def provider_api_key_source(provider_id: str, config: Dict[str, Any] | None = None) -> Dict[str, str]:
    resolution = provider_api_key_resolution(provider_id, config)
    return {
        "field": resolution["field"],
        "source": resolution["source"],
        "env_key": resolution["env_key"],
        "label": resolution["label"],
    }


def provider_api_key_resolution(provider_id: str, config: Dict[str, Any] | None = None) -> Dict[str, str | None]:
    config = config or load_config()
    settings = (config.get("providers") or {}).get(provider_id) or {}
    for item in _api_key_resolution_order(provider_id):
        source = item["source"]
        field = item.get("field") or ""
        env_key = item.get("env_key") or ""
        value = None
        if source == "config":
            candidate = settings.get(field)
            if isinstance(candidate, str) and candidate.strip():
                value = candidate.strip()
        elif source == "env" and env_key and os.environ.get(env_key):
            value = os.environ[env_key].strip()
        if value:
            return {
                "value": value,
                "field": field,
                "source": source,
                "env_key": env_key,
                "label": item["label"],
            }
    return {"value": None, "field": "", "source": "", "env_key": "", "label": "未配置"}


def provider_key_status(provider_id: str, config: Dict[str, Any] | None = None) -> Dict[str, bool]:
    config = config or load_config()
    settings = (config.get("providers") or {}).get(provider_id) or {}
    return {
        field: bool(isinstance(settings.get(field), str) and settings[field].strip())
        for field in PROVIDER_SECRET_FIELDS[provider_id]
    }


def _api_key_resolution_order(provider_id: str) -> List[Dict[str, str]]:
    if provider_id == QWEN_PROVIDER:
        return [
            {"source": "config", "field": "token_plan_api_key", "label": "Token Plan API Key（配置文件）"},
            {"source": "env", "field": "token_plan_api_key", "env_key": "BAILIAN_TOKEN_PLAN_API_KEY", "label": "Token Plan API Key（环境变量 BAILIAN_TOKEN_PLAN_API_KEY）"},
            {"source": "env", "field": "token_plan_api_key", "env_key": "DASHSCOPE_TOKEN_PLAN_API_KEY", "label": "Token Plan API Key（环境变量 DASHSCOPE_TOKEN_PLAN_API_KEY）"},
            {"source": "config", "field": "api_key", "label": "API Key（配置文件）"},
            {"source": "env", "field": "api_key", "env_key": "DASHSCOPE_API_KEY", "label": "API Key（环境变量 DASHSCOPE_API_KEY）"},
            {"source": "env", "field": "api_key", "env_key": "QWEN_API_KEY", "label": "API Key（环境变量 QWEN_API_KEY）"},
            {"source": "env", "field": "api_key", "env_key": "BAILIAN_API_KEY", "label": "API Key（环境变量 BAILIAN_API_KEY）"},
        ]
    return [
        *[
            {"source": "env", "field": "api_key", "env_key": env_key, "label": "API Key（环境变量 %s）" % env_key}
            for env_key in ENV_KEY_ALIASES[provider_id]
        ],
        {"source": "config", "field": "api_key", "label": "API Key（配置文件）"},
    ]


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


def _read_existing_config_payload() -> Dict[str, Any]:
    path = active_config_path()
    if not path.exists():
        return {}
    return _read_config_payload(path)


def _read_config_payload(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ProviderError("配置文件格式错误：config.json 必须是 JSON 对象。")
    return data


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
            if provider_id == QWEN_PROVIDER:
                return "%s Key 未找到。已读取配置文件：%s，但没有 providers.%s.api_key 或 providers.%s.token_plan_api_key 字段。" % (label, active_path, provider_id, provider_id)
            return "%s API Key 未找到。已读取配置文件：%s，但没有 providers.%s.api_key 字段。" % (label, active_path, provider_id)
        return "%s API Key 未找到。配置文件里没有 %s 配置。请在网页右上角重新保存 Key。" % (label, provider_id)
    checked = "；".join(str(path) for path in checked_config_paths())
    return "%s API Key 未配置。请在网页右上角配置 Key。已检查路径：%s" % (label, checked)


def normalize_usage(provider_id: str, usage: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(usage or {})
    result["provider"] = provider_id
    return result


def api_model_for(provider_id: str, model: str) -> str:
    option = MODEL_OPTIONS_BY_KEY.get((provider_id, model))
    if not option:
        raise ProviderError("模型 %s 不属于供应商 %s。" % (model, provider_label(provider_id)))
    return str(option.get("api_model") or option["model"]).strip()


def _thinking_enabled(provider_id: str, model: str) -> bool:
    return (provider_id, model) in THINKING_MODELS


def _normalize_minimax_agent_message(message: Dict[str, Any]) -> Dict[str, Any]:
    message = _strip_minimax_message_thinking(message)
    if message.get("tool_calls"):
        return message
    calls = _minimax_text_tool_calls(message.get("content"))
    if not calls:
        return message
    normalized = dict(message)
    normalized["content"] = None
    normalized["tool_calls"] = calls
    return normalized


def _strip_minimax_message_thinking(message: Dict[str, Any]) -> Dict[str, Any]:
    content = message.get("content")
    if not isinstance(content, str):
        return message
    normalized = dict(message)
    cleaned = _strip_minimax_thinking(content)
    normalized["content"] = cleaned if cleaned else None
    return normalized


def _strip_minimax_thinking(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    return MINIMAX_THINKING_BLOCK_RE.sub("", content).strip()


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
        QWEN_PROVIDER: "阿里百炼",
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
        if provider_id == QWEN_PROVIDER:
            return "阿里百炼 Key 无效。请在右上角“模型配置”里重新保存阿里百炼 API Key 或 Token Plan API Key，并确认接口地址为 https://dashscope.aliyuncs.com/compatible-mode/v1。原始信息：%s" % readable
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


def _normalized_config(config: Dict[str, Any], validate: bool = True) -> Dict[str, Any]:
    normalized = json.loads(json.dumps(DEFAULT_CONFIG))
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    for provider_id in SUPPORTED_PROVIDERS:
        normalized["providers"][provider_id].update(providers.get(provider_id) or {})
        normalized["providers"][provider_id]["model"] = _normalize_model_id(normalized["providers"][provider_id]["model"])

    obsolete_keys = [key for key in ("deepseek_api_key", "model", "base_url") if key in config]
    if obsolete_keys:
        raise ProviderError("配置文件使用旧字段：%s。请在网页右上角重新保存 API Key。" % "、".join(obsolete_keys))

    for key in ("default_mode", "semi_agent_provider", "full_agent_provider"):
        if config.get(key):
            normalized[key] = config[key]
    for key in ("semi_agent_model", "full_agent_model"):
        if config.get(key):
            normalized[key] = _normalize_model_id(config[key])
    for model_key, provider_key in (("semi_agent_model", "semi_agent_provider"), ("full_agent_model", "full_agent_provider")):
        if not config.get(model_key):
            normalized[model_key] = _default_model_for_provider(normalized[provider_key])
    if isinstance(config.get("speech"), dict):
        speech = config["speech"]
        for key in ("provider", "model"):
            if isinstance(speech.get(key), str) and speech[key].strip():
                normalized["speech"][key] = speech[key].strip()
    if validate:
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
        clear_secret_fields = provider_patch.get("clear_secret_fields")
        if isinstance(clear_secret_fields, list):
            for field in clear_secret_fields:
                if field in PROVIDER_SECRET_FIELDS[provider_id]:
                    merged["providers"][provider_id].pop(field, None)
        for field in ("model", "base_url") + PROVIDER_SECRET_FIELDS[provider_id]:
            value = provider_patch.get(field)
            if isinstance(value, str) and value.strip():
                if field == "model":
                    merged["providers"][provider_id][field] = _normalize_model_id(value)
                else:
                    merged["providers"][provider_id][field] = value.strip().rstrip("/") if field == "base_url" else value.strip()
    speech = patch.get("speech") if isinstance(patch.get("speech"), dict) else {}
    for field in ("provider", "model"):
        value = speech.get(field)
        if isinstance(value, str) and value.strip():
            merged["speech"][field] = value.strip()
    _validate_config(merged)
    return merged


def _validate_config(config: Dict[str, Any]) -> None:
    for mode_key in ("default_mode",):
        if config[mode_key] not in (SEMI_AGENT_MODE, FULL_AGENT_MODE):
            raise ProviderError("未知工作模式：%s。" % config[mode_key])
    for key in ("semi_agent_provider", "full_agent_provider"):
        if config[key] not in SUPPORTED_PROVIDERS:
            raise ProviderError("未知模型供应商：%s。" % config[key])
    for provider_id in SUPPORTED_PROVIDERS:
        _validate_provider_model(provider_id, config["providers"][provider_id]["model"])
    _validate_mode_model(config, "semi_agent_provider", "semi_agent_model")
    _validate_mode_model(config, "full_agent_provider", "full_agent_model")
    speech = speech_settings(config)
    if speech["provider"] != "qwen_asr":
        raise ProviderError("未知语音识别供应商：%s。" % speech["provider"])
    if speech["model"] != QWEN_ASR_MODEL:
        raise ProviderError("未知语音识别模型：%s。" % speech["model"])


def _validate_mode_model(config: Dict[str, Any], provider_key: str, model_key: str) -> None:
    provider_id = config[provider_key]
    model = config[model_key]
    _validate_provider_model(provider_id, model)


def _validate_provider_model(provider_id: str, model: str) -> None:
    if (provider_id, model) not in MODEL_OPTIONS_BY_KEY:
        raise ProviderError("模型 %s 不属于供应商 %s。" % (model, provider_label(provider_id)))


def _normalize_model_id(model: str) -> str:
    return str(model).strip()


def _default_model_for_provider(provider_id: str) -> str:
    options = MODEL_OPTIONS_BY_PROVIDER.get(provider_id) or []
    if not options:
        raise ProviderError("未知模型供应商：%s。" % provider_id)
    default = DEFAULT_CONFIG["providers"].get(provider_id, {}).get("model")
    if default in {item["model"] for item in options}:
        return default
    return options[0]["model"]
