from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from .paths import appdata_dir, config_path, localappdata_dir


class DeepSeekError(Exception):
    pass


class DeepSeekClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None, timeout: int = 60):
        self.api_key = api_key or load_api_key()
        self.model = model or load_config().get("model") or "deepseek-chat"
        self.base_url = (base_url or load_config().get("base_url") or "https://api.deepseek.com").rstrip("/")
        self.timeout = timeout

    def chat_json(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        if not self.api_key:
            raise DeepSeekError(missing_api_key_message())

        body = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.1
        }
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise DeepSeekError(f"DeepSeek HTTP {exc.code}: {detail}")
        except Exception as exc:
            raise DeepSeekError(str(exc))

        content = payload["choices"][0]["message"]["content"]
        try:
            result = json.loads(content)
        except ValueError:
            raise DeepSeekError("DeepSeek returned non-JSON content.")
        result["_usage"] = payload.get("usage", {})
        return result

    def chat_agent(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not self.api_key:
            raise DeepSeekError(missing_api_key_message())

        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0
        }
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise DeepSeekError(f"DeepSeek HTTP {exc.code}: {detail}")
        except Exception as exc:
            raise DeepSeekError(str(exc))

        return {
            "message": payload["choices"][0]["message"],
            "usage": payload.get("usage", {})
        }


def load_config() -> Dict[str, Any]:
    path = active_config_path()
    if not path or not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise DeepSeekError("DeepSeek 配置文件格式错误：config.json 必须是 JSON 对象。")
    return data


def save_config(config: Dict[str, Any]) -> Dict[str, Any]:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_config()
    existing.update(config)
    with path.open("w", encoding="utf-8-sig") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2, sort_keys=True)
    return public_config(existing)


def public_config(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    config = config or load_config()
    status = config_status(config)
    return {
        "has_deepseek_api_key": bool(config.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY")),
        "model": config.get("model") or "deepseek-chat",
        "base_url": config.get("base_url") or "https://api.deepseek.com",
        "config_path": str(status["active_path"]),
        "config_file_exists": bool(status["active_path"].exists()),
        "checked_config_paths": [str(path) for path in status["checked_paths"]]
    }


def load_api_key() -> str | None:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    config = load_config()
    return config.get("deepseek_api_key")


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
        "has_deepseek_api_key": bool(config.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY")),
        "has_env_key": bool(os.environ.get("DEEPSEEK_API_KEY")),
        "known_keys": sorted(config.keys())
    }


def missing_api_key_message() -> str:
    status = config_status({})
    active_path = status["active_path"]
    if active_path.exists():
        try:
            config = load_config()
        except Exception as exc:
            return "DeepSeek API Key 配置文件读取失败：%s。请在网页右上角重新保存 Key。" % exc
        keys = sorted(config.keys())
        if keys:
            return "DeepSeek API Key 未找到。已读取配置文件：%s，但没有 deepseek_api_key 字段。" % active_path
        return "DeepSeek API Key 未找到。配置文件为空：%s。请在网页右上角重新保存 Key。" % active_path
    checked = "；".join(str(path) for path in status["checked_paths"])
    return "DeepSeek API Key 未配置。请在网页右上角配置 Key。已检查路径：%s" % checked
