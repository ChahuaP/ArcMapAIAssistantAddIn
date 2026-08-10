"""Strict Anthropic Messages adapter used by explicit compatible connections."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from ..adapter import ProviderError
from ..contracts import ProviderConnection, ProviderInvocation, ProviderResponse, TokenCallback
from ..credentials import CredentialVault


ANTHROPIC_COMPATIBLE_PROVIDER_TYPES = frozenset({"zhipu_coding"})
_TIMEOUT_SECONDS = 300


class AnthropicCompatibleAdapter:
    def __init__(self, connection: ProviderConnection,
                 credential_vault: CredentialVault):
        if connection.provider_type not in ANTHROPIC_COMPATIBLE_PROVIDER_TYPES:
            raise ValueError("connection is not Anthropic-compatible.")
        if connection.credential_ref is None:
            raise ValueError("Anthropic-compatible connection requires a credential.")
        self.connection = connection
        self.connection_id = connection.connection_id
        self.provider_type = connection.provider_type
        self._credential_vault = credential_vault

    def invoke(self, call: ProviderInvocation,
               on_token: Optional[TokenCallback] = None) -> ProviderResponse:
        if call.model_id not in self.connection.enabled_models:
            raise ProviderError("protocol", "model is not enabled for this connection.")
        system, messages = _messages(call.messages)
        body: Dict[str, Any] = {
            "model": call.model_id,
            "system": system,
            "messages": messages,
            "temperature": call.temperature,
            "max_tokens": call.max_output_tokens,
        }
        if call.structured_contract is not None:
            contract = call.structured_contract
            body["tools"] = [{"name": contract.name,
                              "description": contract.description,
                              "input_schema": contract.schema}]
            body["tool_choice"] = {"type": "tool", "name": contract.name}
        elif call.tools:
            body["tools"] = [_tool(tool) for tool in call.tools]
            body["tool_choice"] = {"type": "auto"}
        else:
            raise ProviderError("protocol", "structured invocation requires tools or a contract.")
        payload = self._post_json(body)
        content = payload.get("content")
        if not isinstance(content, list):
            raise ProviderError("protocol", "Anthropic response content must be an array.")
        if on_token is not None:
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    on_token(block["text"])
        calls = [{"name": block.get("name"), "arguments": block.get("input")}
                 for block in content
                 if isinstance(block, dict) and block.get("type") == "tool_use"]
        if not calls or any(not isinstance(item["name"], str) or
                            not isinstance(item["arguments"], dict) for item in calls):
            raise ProviderError("protocol", "Anthropic response did not contain valid tool use.")
        if call.structured_contract is not None:
            if len(calls) != 1 or calls[0]["name"] != call.structured_contract.name:
                raise ProviderError("protocol", "structured response called the wrong tool.")
            response = calls[0]["arguments"]
        else:
            response = {"tool_calls": calls}
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        return ProviderResponse(response=response, usage={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        })

    def _post_json(self, body: Dict[str, Any]) -> Dict[str, Any]:
        try:
            credential = self._credential_vault.get(self.connection.credential_ref)
        except (KeyError, ValueError) as exc:
            raise ProviderError("protocol", "provider credential cannot be resolved.") from exc
        request = urllib.request.Request(
            self.connection.endpoint.rstrip("/") + "/v1/messages",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": "Bearer %s" % credential,
                     "X-Api-Key": credential, "Anthropic-Version": "2023-06-01"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            kind = "quota" if exc.code == 429 else ("transport" if exc.code >= 500 else "protocol")
            raise ProviderError(kind, "zhipu_coding HTTP %d: %s" % (exc.code, detail[:500])) from exc
        except (TimeoutError, socket.timeout, OSError, urllib.error.URLError) as exc:
            raise ProviderError("transport", "zhipu_coding transport failed: %s" % exc) from exc
        try:
            document = json.loads(raw)
        except ValueError as exc:
            raise ProviderError("protocol", "provider returned invalid JSON.") from exc
        if not isinstance(document, dict):
            raise ProviderError("protocol", "provider response must be an object.")
        return document


def _messages(messages: List[Dict[str, str]]) -> Tuple[str, List[Dict[str, str]]]:
    system: List[str] = []
    conversation: List[Dict[str, str]] = []
    for message in messages:
        role, content = message.get("role"), message.get("content")
        if not isinstance(content, str):
            raise ProviderError("protocol", "message content must be text.")
        if role == "system":
            system.append(content)
        elif role in ("user", "assistant"):
            conversation.append({"role": role, "content": content})
        else:
            raise ProviderError("protocol", "unsupported Anthropic message role.")
    if not conversation:
        raise ProviderError("protocol", "Anthropic request requires a conversation message.")
    return "\n\n".join(system), conversation


def _tool(tool: Dict[str, Any]) -> Dict[str, Any]:
    function = tool.get("function") if isinstance(tool, dict) else None
    if tool.get("type") != "function" or not isinstance(function, dict):
        raise ProviderError("protocol", "Anthropic adapter requires function tools.")
    name, description, schema = function.get("name"), function.get("description"), function.get("parameters")
    if not isinstance(name, str) or not isinstance(description, str) or not isinstance(schema, dict):
        raise ProviderError("protocol", "function tool contract is malformed.")
    return {"name": name, "description": description, "input_schema": schema}
