from __future__ import annotations

import html
import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..adapter import ProviderError
from ..contracts import (
    ProviderConnection,
    ProviderInvocation,
    ProviderResponse,
    StructuredOutputContract,
    TokenCallback,
)
from ..credentials import CredentialVault


MINIMAX_PROVIDER = "minimax"
MINIMAX_MODEL = "MiniMax-M3"
MODEL_REQUEST_TIMEOUT_SECONDS = 300
MINIMAX_TEXT_TOOL_CALL_RE = re.compile(r"<minimax:tool_call>(.*?)</minimax:tool_call>", re.IGNORECASE | re.DOTALL)
MINIMAX_TEXT_INVOKE_RE = re.compile(r"<invoke\s+name=\"([^\"]+)\">(.*?)</invoke>", re.IGNORECASE | re.DOTALL)
MINIMAX_TEXT_PARAMETER_RE = re.compile(r"<parameter\s+name=\"([^\"]+)\">(.*?)</parameter>", re.IGNORECASE | re.DOTALL)
MINIMAX_THINKING_BLOCK_RE = re.compile(r"<think[^>]*>.*?</think>", re.IGNORECASE | re.DOTALL)


class MiniMaxError(Exception):
    pass


class MiniMaxProtocolError(MiniMaxError):
    def __init__(self, message: str, evidence: Dict[str, Any]):
        super().__init__(message)
        self.evidence = evidence


class MiniMaxHttpError(MiniMaxError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class _MiniMaxWireClient:
    """Provider-private MiniMax wire client."""

    def __init__(self, api_key: str, endpoint: str,
                 timeout: int = MODEL_REQUEST_TIMEOUT_SECONDS):
        if not api_key:
            raise ValueError("MiniMax adapter requires an explicit credential.")
        self.api_key = api_key
        self.base_url = endpoint.rstrip("/")
        self.timeout = timeout

    def chat_structured(
        self,
        messages: List[Dict[str, str]],
        contract: StructuredOutputContract,
        model: str,
        temperature: float,
        max_output_tokens: int,
    ) -> Dict[str, Any]:
        payload = self._post_chat_completion({
            "model": model,
            "messages": messages,
            "tools": [{
                "type": "function",
                "function": {
                    "name": contract.name,
                    "description": contract.description,
                    "parameters": contract.schema,
                },
            }],
            "tool_choice": {
                "type": "function",
                "function": {"name": contract.name},
            },
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        })
        message = payload["choices"][0]["message"]
        message = _normalize_minimax_agent_message(message)
        result = _openai_structured_result(message, contract, payload)
        result = _normalize_minimax_structured_result(result, contract.schema)
        result["_usage"] = normalize_usage(payload.get("usage", {}))
        result["_provider_response"] = payload
        return result

    def chat_structured_stream(self, messages: List[Dict[str, str]],
                               contract: StructuredOutputContract, on_token,
                               model: str, temperature: float,
                               max_output_tokens: int) -> Dict[str, Any]:
        """Use the provider's SSE completion stream and retain one final tool call."""
        body = {
            "model": model, "messages": messages, "stream": True,
            "stream_options": {"include_usage": True},
            "tools": [{"type": "function", "function": {
                "name": contract.name, "description": contract.description,
                "parameters": contract.schema}}],
            "tool_choice": {"type": "function", "function": {"name": contract.name}},
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        tool_calls, usage, text_parts = {}, {}, []
        for event in self._stream_chat_completion(body):
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            choices = event.get("choices") or ()
            for choice in choices:
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    text_parts.append(content)
                    on_token(content)
                for call in delta.get("tool_calls") or ():
                    index = int(call.get("index", 0))
                    item = tool_calls.setdefault(index, {"function": {"name": "", "arguments": ""}})
                    function = call.get("function") or {}
                    if function.get("name"):
                        item["function"]["name"] += function["name"]
                    if function.get("arguments"):
                        item["function"]["arguments"] += function["arguments"]
        message = {"tool_calls": [tool_calls[index] for index in sorted(tool_calls)]}
        if not message["tool_calls"] and text_parts:
            message = _normalize_minimax_agent_message({"content": "".join(text_parts)})
        result = _openai_structured_result(message, contract, {"choices": [{"message": message}]})
        result = _normalize_minimax_structured_result(result, contract.schema)
        result["_usage"] = normalize_usage(usage)
        return result

    def chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        model: str,
        temperature: float,
        max_output_tokens: int,
    ) -> Dict[str, Any]:
        """Multi-tool structured call (native function calling).

        Unlike ``chat_structured`` (single contract, forced tool_choice), this
        method exposes all ``tools`` to the model and uses ``tool_choice:
        "auto"`` so the model picks the right one(s).  Returns a dict with a
        ``tool_calls`` list: ``[{"name": ..., "arguments": {...}}, ...]``.
        """
        payload = self._post_chat_completion({
            "model": model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        })
        message = payload["choices"][0]["message"]
        message = _normalize_minimax_agent_message(message)
        result = _openai_tool_calls_result(message, payload)
        result["_usage"] = normalize_usage(payload.get("usage", {}))
        result["_provider_response"] = payload
        return result

    def chat_with_tools_stream(self, messages: List[Dict[str, str]],
                               tools: List[Dict[str, Any]], on_token,
                               model: str, temperature: float,
                               max_output_tokens: int) -> Dict[str, Any]:
        body = {"model": model, "messages": messages, "tools": tools,
                "tool_choice": "auto", "temperature": temperature,
                "max_tokens": max_output_tokens, "stream": True,
                "stream_options": {"include_usage": True}}
        tool_calls, usage, text_parts = {}, {}, []
        for event in self._stream_chat_completion(body):
            if isinstance(event.get("usage"), dict):
                usage = event["usage"]
            for choice in event.get("choices") or ():
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    text_parts.append(content)
                    on_token(content)
                for call in delta.get("tool_calls") or ():
                    index = int(call.get("index", 0))
                    item = tool_calls.setdefault(index, {"function": {"name": "", "arguments": ""}})
                    function = call.get("function") or {}
                    if function.get("name"):
                        item["function"]["name"] += function["name"]
                    if function.get("arguments"):
                        item["function"]["arguments"] += function["arguments"]
        message = {"tool_calls": [tool_calls[index] for index in sorted(tool_calls)]}
        if not message["tool_calls"] and text_parts:
            message = _normalize_minimax_agent_message({"content": "".join(text_parts)})
        result = _openai_tool_calls_result(message, {"choices": [{"message": message}]})
        result["_usage"] = normalize_usage(usage)
        return result

    def _post_chat_completion(self, body: Dict[str, Any]) -> Dict[str, Any]:
        body = dict(body)
        url = "%s/chat/completions" % self.base_url
        return self._post_json(
            url,
            body,
            {
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % self.api_key,
            },
        )

    def _stream_chat_completion(self, body: Dict[str, Any]):
        data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request("%s/chat/completions" % self.base_url, data=data,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                     "Authorization": "Bearer %s" % self.api_key}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data_line = line[5:].strip()
                    if data_line == "[DONE]":
                        return
                    try:
                        yield json.loads(data_line)
                    except ValueError:
                        raise MiniMaxProtocolError("SSE 数据帧不是有效 JSON。", {"data": data_line})
        except urllib.error.HTTPError as exc:
            raise MiniMaxHttpError(exc.code, minimax_http_error(
                exc.code, exc.read().decode("utf-8", errors="replace")
            ))
        except (TimeoutError, socket.timeout):
            raise MiniMaxError("MiniMax SSE 响应超时。")
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise MiniMaxError(minimax_network_error(exc))

    def _post_json(
        self,
        url: str,
        body: Dict[str, Any],
        headers: Dict[str, str],
    ) -> Dict[str, Any]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            request = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise MiniMaxHttpError(exc.code, minimax_http_error(exc.code, detail))
        except (TimeoutError, socket.timeout):
            raise MiniMaxError("MiniMax 响应超时：已等待 %s 秒。" % self.timeout)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise MiniMaxError(minimax_network_error(exc))
        try:
            return json.loads(response_text)
        except ValueError:
            raise MiniMaxError("MiniMax 响应格式错误：接口没有返回有效 JSON。")


def _openai_structured_result(
    message: Dict[str, Any],
    contract: StructuredOutputContract,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    tool_calls = message.get("tool_calls") if isinstance(message, dict) else None
    if isinstance(tool_calls, list) and len(tool_calls) == 1:
        function = tool_calls[0].get("function") if isinstance(tool_calls[0], dict) else None
        if isinstance(function, dict) and function.get("name") == contract.name:
            arguments = function.get("arguments")
            try:
                parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            except ValueError:
                raise MiniMaxProtocolError("结构化响应工具参数不是有效 JSON。", payload)
            if not isinstance(parsed, dict):
                raise MiniMaxProtocolError("结构化响应工具参数必须是对象。", payload)
            return dict(parsed)
    raise MiniMaxProtocolError(
        "结构化响应必须且只能包含一次工具调用。",
        payload,
    )


def _openai_tool_calls_result(
    message: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Parse multi-tool-call responses (native function calling).

    Returns ``{"tool_calls": [{"name": str, "arguments": dict}, ...]}``.
    Fail closed if the model did not produce valid tool_calls.
    """
    raw_calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(raw_calls, list) or not raw_calls:
        raise MiniMaxProtocolError(
            "模型未返回工具调用。请检查任务描述是否清晰。",
            payload,
        )
    parsed_calls: List[Dict[str, Any]] = []
    for raw in raw_calls:
        if not isinstance(raw, dict):
            continue
        function = raw.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        arguments = function.get("arguments")
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except ValueError:
            raise MiniMaxProtocolError("工具参数不是有效 JSON。", payload)
        if not isinstance(parsed, dict):
            raise MiniMaxProtocolError("工具参数必须是对象。", payload)
        parsed_calls.append({"name": name, "arguments": parsed})
    if not parsed_calls:
        raise MiniMaxProtocolError(
            "模型未返回有效的工具调用。",
            payload,
        )
    return {"tool_calls": parsed_calls}


def _normalize_minimax_structured_result(value, schema):
    """Canonicalize only MiniMax wire shapes that the active schema proves.

    MiniMax-M3 can serialize a schema array as the exact object
    ``{"item": [...]}``.  This shape is not accepted generically: the
    transformation is applied only at an array node of the exact tool schema.
    Ambiguous ``oneOf`` branches and unsupported references remain untouched
    so the domain parser fails closed.
    """
    return _normalize_minimax_schema_value(value, schema, schema)


def _normalize_minimax_schema_value(value, schema, root_schema):
    schema = _resolve_local_schema(schema, root_schema)
    if not isinstance(schema, dict):
        return value

    branches = schema.get("oneOf")
    if isinstance(branches, list):
        branch = _unique_schema_branch(value, branches, root_schema)
        if branch is None:
            return value
        return _normalize_minimax_schema_value(value, branch, root_schema)

    schema_type = schema.get("type")
    if schema_type == "array":
        if (
            isinstance(value, dict)
            and set(value) == {"item"}
            and isinstance(value["item"], list)
        ):
            value = value["item"]
        if not isinstance(value, list):
            return value
        item_schema = schema.get("items")
        if not isinstance(item_schema, dict):
            return list(value)
        return [
            _normalize_minimax_schema_value(item, item_schema, root_schema)
            for item in value
        ]

    if schema_type == "object" and isinstance(value, dict):
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return dict(value)
        return {
            key: _normalize_minimax_schema_value(item, properties[key], root_schema)
            if key in properties else item
            for key, item in value.items()
        }
    return value


def _resolve_local_schema(schema, root_schema):
    if not isinstance(schema, dict) or "$ref" not in schema:
        return schema
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return None
    current = root_schema
    for token in reference[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            return None
        current = current[token]
    return current if isinstance(current, dict) else None


def _unique_schema_branch(value, branches, root_schema):
    candidates = [
        branch for branch in branches
        if _schema_branch_matches(value, branch, root_schema)
    ]
    return candidates[0] if len(candidates) == 1 else None


def _schema_branch_matches(value, branch, root_schema):
    branch = _resolve_local_schema(branch, root_schema)
    if not isinstance(branch, dict):
        return False
    schema_type = branch.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            return False
        properties = branch.get("properties", {})
        required = branch.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            return False
        if not set(required).issubset(value):
            return False
        if branch.get("additionalProperties") is False and not set(value).issubset(properties):
            return False
        for key, property_schema in properties.items():
            if key not in value:
                continue
            resolved = _resolve_local_schema(property_schema, root_schema)
            if not isinstance(resolved, dict) or "const" not in resolved:
                continue
            if value[key] != resolved["const"]:
                return False
        return True
    if schema_type == "array":
        return isinstance(value, list) or (
            isinstance(value, dict)
            and set(value) == {"item"}
            and isinstance(value["item"], list)
        )
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return False


def normalize_usage(usage: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(usage or {})
    result["provider"] = MINIMAX_PROVIDER
    return result


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


def minimax_network_error(exc: BaseException) -> str:
    reason = getattr(exc, "reason", exc)
    errno = getattr(reason, "errno", getattr(exc, "errno", None))
    text = "%s %s" % (reason.__class__.__name__, reason)
    lowered = text.lower()
    if isinstance(reason, socket.gaierror) or errno == 11001 or "getaddrinfo" in lowered:
        detail = "无法解析模型接口域名"
    elif isinstance(reason, (TimeoutError, socket.timeout)) or errno == 10060 or "timed out" in lowered or "timeout" in lowered:
        detail = "连接模型接口超时"
    elif errno == 10061 or "connection refused" in lowered:
        detail = "模型接口拒绝连接"
    elif errno in (10051, 10065) or "network is unreachable" in lowered:
        detail = "当前网络无法到达模型接口"
    elif isinstance(exc, ValueError):
        detail = "模型接口地址不合法"
    else:
        detail = "无法连接模型接口"
    return "MiniMax 网络连接失败：%s。请检查网络、DNS、代理、防火墙。" % detail


def minimax_http_error(status_code: int, detail: str) -> str:
    message = _extract_http_error_message(detail)
    readable = message or detail
    if status_code == 401:
        return "MiniMax Token Plan API Key 无效。请在右上角“API Key”里重新保存。原始信息：%s" % readable
    return "MiniMax HTTP %s：%s" % (status_code, readable)


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


class MiniMaxAdapter:
    """First production ProviderAdapter; all MiniMax wire details stay here."""

    provider_type = MINIMAX_PROVIDER

    def __init__(self, connection: ProviderConnection,
                 credential_vault: CredentialVault):
        if connection.provider_type != self.provider_type:
            raise ValueError("MiniMaxAdapter requires a minimax ProviderConnection.")
        if not connection.credential_ref:
            raise ValueError("MiniMaxAdapter requires connection.credential_ref.")
        self.connection = connection
        self.connection_id = connection.connection_id
        self._credential_vault = credential_vault

    def invoke(self, call: ProviderInvocation,
               on_token: Optional[TokenCallback] = None) -> ProviderResponse:
        if call.model_id not in self.connection.enabled_models:
            raise ProviderError("protocol", "MiniMax model is not enabled for this connection.")
        try:
            api_key = self._credential_vault.get(self.connection.credential_ref)
        except (KeyError, ValueError) as exc:
            raise ProviderError(
                "protocol", "MiniMax credential_ref cannot be resolved."
            ) from exc
        client = _MiniMaxWireClient(
            api_key=api_key,
            endpoint=self.connection.endpoint,
        )
        try:
            if call.tools:
                if on_token is None:
                    raw = client.chat_with_tools(
                        call.messages, call.tools, call.model_id,
                        call.temperature, call.max_output_tokens,
                    )
                else:
                    raw = client.chat_with_tools_stream(
                        call.messages, call.tools, on_token, call.model_id,
                        call.temperature, call.max_output_tokens,
                    )
            elif on_token is None:
                if call.structured_contract is None:
                    raise ProviderError("protocol", "MiniMax structured call has no contract.")
                raw = client.chat_structured(
                    call.messages, call.structured_contract, call.model_id,
                    call.temperature, call.max_output_tokens,
                )
            else:
                if call.structured_contract is None:
                    raise ProviderError("protocol", "MiniMax structured call has no contract.")
                raw = client.chat_structured_stream(
                    call.messages, call.structured_contract, on_token,
                    call.model_id, call.temperature, call.max_output_tokens,
                )
        except MiniMaxProtocolError as exc:
            raise ProviderError("protocol", str(exc), exc.evidence) from exc
        except MiniMaxHttpError as exc:
            if exc.status_code == 429:
                kind = "quota"
            elif 400 <= exc.status_code < 500:
                kind = "protocol"
            else:
                kind = "transport"
            raise ProviderError(kind, str(exc)) from exc
        except MiniMaxError as exc:
            raise ProviderError("transport", str(exc)) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(
                "protocol", "MiniMax response violates the adapter contract: %s" % exc
            ) from exc
        usage = raw.get("_usage") or raw.get("usage") or {}
        response = {
            key: value for key, value in raw.items()
            if key not in ("_provider_response", "_usage")
        }
        return ProviderResponse(response=response, usage=usage)
