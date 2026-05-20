from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List

from .paths import config_path


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
            raise DeepSeekError("DeepSeek API key not found. Set DEEPSEEK_API_KEY or %APPDATA%/ArcMapAIAssistant/config.json.")

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
            raise DeepSeekError("DeepSeek API key not found. Set DEEPSEEK_API_KEY or %APPDATA%/ArcMapAIAssistant/config.json.")

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
    path = config_path()
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: Dict[str, Any]) -> Dict[str, Any]:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_config()
    existing.update(config)
    with path.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2, sort_keys=True)
    return public_config(existing)


def public_config(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    config = config or load_config()
    return {
        "has_deepseek_api_key": bool(config.get("deepseek_api_key") or os.environ.get("DEEPSEEK_API_KEY")),
        "model": config.get("model") or "deepseek-chat",
        "base_url": config.get("base_url") or "https://api.deepseek.com"
    }


def load_api_key() -> str | None:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    config = load_config()
    return config.get("deepseek_api_key")
