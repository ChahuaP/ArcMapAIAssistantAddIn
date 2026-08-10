"""Strict adapter for providers implementing OpenAI Chat Completions."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..adapter import ProviderError
from ..contracts import ProviderConnection, ProviderInvocation, ProviderResponse, TokenCallback
from ..credentials import CredentialVault


OPENAI_COMPATIBLE_PROVIDER_TYPES = frozenset({
    "deepseek", "qwen", "zhipu", "ollama", "openai_compatible",
})
_TIMEOUT_SECONDS = 300


class OpenAICompatibleAdapter:
    """One deep adapter for the shared Chat Completions wire contract."""

    def __init__(self, connection: ProviderConnection,
                 credential_vault: CredentialVault):
        if connection.provider_type not in OPENAI_COMPATIBLE_PROVIDER_TYPES:
            raise ValueError("connection is not OpenAI-compatible.")
        self.connection = connection
        self.connection_id = connection.connection_id
        self.provider_type = connection.provider_type
        self._credential_vault = credential_vault

    def invoke(self, call: ProviderInvocation,
               on_token: Optional[TokenCallback] = None) -> ProviderResponse:
        if call.model_id not in self.connection.enabled_models:
            raise ProviderError("protocol", "model is not enabled for this connection.")
        body: Dict[str, Any] = {
            "model": call.model_id,
            "messages": call.messages,
            "temperature": call.temperature,
            "max_tokens": call.max_output_tokens,
        }
        if call.structured_contract is not None:
            contract = call.structured_contract
            body["tools"] = [{
                "type": "function",
                "function": {
                    "name": contract.name,
                    "description": contract.description,
                    "parameters": contract.schema,
                },
            }]
            body["tool_choice"] = {
                "type": "function", "function": {"name": contract.name},
            }
        elif call.tools:
            body["tools"] = call.tools
            body["tool_choice"] = "auto"
        else:
            raise ProviderError("protocol", "structured invocation requires tools or a contract.")

        payload = self._post_json(body)
        message = _response_message(payload)
        content = message.get("content")
        if on_token is not None and isinstance(content, str) and content:
            on_token(content)
        calls = _parse_tool_calls(message, payload)
        if call.structured_contract is not None:
            if len(calls) != 1 or calls[0]["name"] != call.structured_contract.name:
                raise ProviderError(
                    "protocol", "structured response called the wrong tool.",
                    {"connection_id": self.connection_id},
                )
            response = calls[0]["arguments"]
        else:
            response = {"tool_calls": calls}
        return ProviderResponse(response=response, usage=_normalize_usage(payload.get("usage")))

    def _post_json(self, body: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        credential = self._credential()
        if credential is not None:
            headers["Authorization"] = "Bearer %s" % credential
        request = urllib.request.Request(
            self.connection.endpoint.rstrip("/") + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            kind = "quota" if exc.code == 429 else ("transport" if exc.code >= 500 else "protocol")
            raise ProviderError(kind, "%s HTTP %d: %s" % (
                self.provider_type, exc.code, detail[:500],
            )) from exc
        except (TimeoutError, socket.timeout, OSError, urllib.error.URLError) as exc:
            raise ProviderError("transport", "%s transport failed: %s" % (
                self.provider_type, exc,
            )) from exc
        try:
            document = json.loads(raw)
        except ValueError as exc:
            raise ProviderError("protocol", "provider returned invalid JSON.") from exc
        if not isinstance(document, dict):
            raise ProviderError("protocol", "provider response must be an object.")
        return document

    def _credential(self) -> Optional[str]:
        ref = self.connection.credential_ref
        if ref is None:
            return None
        try:
            return self._credential_vault.get(ref)
        except (KeyError, ValueError) as exc:
            raise ProviderError("protocol", "provider credential cannot be resolved.") from exc


def _response_message(payload: Dict[str, Any]) -> Dict[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ProviderError("protocol", "provider response must contain exactly one choice.")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ProviderError("protocol", "provider response choice has no message.")
    return message


def _parse_tool_calls(message: Dict[str, Any], payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        raise ProviderError("protocol", "provider did not return a tool call.", {"response": payload})
    result: List[Dict[str, Any]] = []
    for raw_call in raw_calls:
        function = raw_call.get("function") if isinstance(raw_call, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ProviderError("protocol", "provider returned a malformed tool call.")
        arguments = function.get("arguments")
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except ValueError as exc:
            raise ProviderError("protocol", "tool arguments are not valid JSON.") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("protocol", "tool arguments must be an object.")
        result.append({"name": function["name"], "arguments": parsed})
    return result


def _normalize_usage(value: Any) -> Dict[str, Any]:
    usage = value if isinstance(value, dict) else {}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    return {"input_tokens": prompt, "output_tokens": completion, "total_tokens": total}
