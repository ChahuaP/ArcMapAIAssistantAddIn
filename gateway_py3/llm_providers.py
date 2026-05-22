from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from .paths import config_path, localappdata_dir


DEEPSEEK_PROVIDER = "deepseek"
MINIMAX_PROVIDER = "minimax"
SEMI_AGENT_MODE = "semi_agent"
FULL_AGENT_MODE = "full_agent"
MINIMAX_TOKEN_PLAN_BASE_URL = "https://api.minimaxi.com/v1"
MINIMAX_LEGACY_BASE_URLS = {
    "https://api.minimax.io/v1",
}

DEFAULT_CONFIG = {
    "default_mode": SEMI_AGENT_MODE,
    "semi_agent_provider": DEEPSEEK_PROVIDER,
    "full_agent_provider": MINIMAX_PROVIDER,
    "providers": {
        DEEPSEEK_PROVIDER: {
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
        },
        MINIMAX_PROVIDER: {
            "model": "MiniMax-M2.7",
            "base_url": MINIMAX_TOKEN_PLAN_BASE_URL,
        },
    },
}

ENV_KEYS = {
    DEEPSEEK_PROVIDER: "DEEPSEEK_API_KEY",
    MINIMAX_PROVIDER: "MINIMAX_API_KEY",
}


class ProviderError(Exception):
    pass


class ChatProvider:
    provider_id = ""

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None, timeout: int = 60):
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

    def _post_chat_completion(self, body: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key:
            raise ProviderError(missing_api_key_message(self.provider_id))
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
        except Exception as exc:
            raise ProviderError(str(exc))


class DeepSeekProvider(ChatProvider):
    provider_id = DEEPSEEK_PROVIDER


class MiniMaxProvider(ChatProvider):
    provider_id = MINIMAX_PROVIDER


def create_provider(mode: str | None = None, provider_id: str | None = None) -> ChatProvider:
    selected = provider_id or provider_for_mode(mode or public_config()["default_mode"])
    if selected == DEEPSEEK_PROVIDER:
        return DeepSeekProvider()
    if selected == MINIMAX_PROVIDER:
        return MiniMaxProvider()
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
    existing = load_config()
    merged = _merge_config(existing, config)
    with path.open("w", encoding="utf-8-sig") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2, sort_keys=True)
    return public_config(merged)


def public_config(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    config = config or load_config()
    status = config_status(config)
    providers = {}
    for provider_id in (DEEPSEEK_PROVIDER, MINIMAX_PROVIDER):
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
        "full_agent_provider": config["full_agent_provider"],
        "providers": providers,
        "config_path": str(status["active_path"]),
        "config_file_exists": bool(status["active_path"].exists()),
        "checked_config_paths": [str(path) for path in status["checked_paths"]],
        "has_deepseek_api_key": providers[DEEPSEEK_PROVIDER]["has_api_key"],
        "has_minimax_api_key": providers[MINIMAX_PROVIDER]["has_api_key"],
    }


def provider_for_mode(mode: str | None, config: Dict[str, Any] | None = None) -> str:
    config = config or load_config()
    if mode == FULL_AGENT_MODE:
        return config["full_agent_provider"]
    return config["semi_agent_provider"]


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
    env_key = ENV_KEYS[provider_id]
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
        except Exception as exc:
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


def provider_label(provider_id: str) -> str:
    return {
        DEEPSEEK_PROVIDER: "DeepSeek",
        MINIMAX_PROVIDER: "MiniMax",
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
    for provider_id in (DEEPSEEK_PROVIDER, MINIMAX_PROVIDER):
        normalized["providers"][provider_id].update(providers.get(provider_id) or {})
    _migrate_minimax_token_plan_defaults(normalized)

    if config.get("deepseek_api_key"):
        normalized["providers"][DEEPSEEK_PROVIDER]["api_key"] = config["deepseek_api_key"]
    if config.get("model"):
        normalized["providers"][DEEPSEEK_PROVIDER]["model"] = config["model"]
    if config.get("base_url"):
        normalized["providers"][DEEPSEEK_PROVIDER]["base_url"] = str(config["base_url"]).rstrip("/")

    for key in ("default_mode", "semi_agent_provider", "full_agent_provider"):
        if config.get(key):
            normalized[key] = config[key]
    _validate_config(normalized)
    return normalized


def _migrate_minimax_token_plan_defaults(config: Dict[str, Any]) -> None:
    minimax = config["providers"][MINIMAX_PROVIDER]
    base_url = str(minimax.get("base_url") or "").strip().rstrip("/")
    if base_url in MINIMAX_LEGACY_BASE_URLS:
        minimax["base_url"] = MINIMAX_TOKEN_PLAN_BASE_URL


def _merge_config(existing: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = _normalized_config(existing)
    for key in ("default_mode", "semi_agent_provider", "full_agent_provider"):
        if patch.get(key):
            merged[key] = str(patch[key]).strip()
    providers = patch.get("providers") if isinstance(patch.get("providers"), dict) else {}
    for provider_id in (DEEPSEEK_PROVIDER, MINIMAX_PROVIDER):
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
        if config[key] not in (DEEPSEEK_PROVIDER, MINIMAX_PROVIDER):
            raise ProviderError("未知模型供应商：%s。" % config[key])
