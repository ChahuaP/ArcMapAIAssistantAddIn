from __future__ import annotations

import base64
import binascii
import json
import re
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List

from gateway_py3.llm_providers import (
    MODEL_REQUEST_TIMEOUT_SECONDS,
    ProviderError,
    QWEN_ASR_MODEL,
    QWEN_PROVIDER,
    create_provider,
    provider_api_key,
    provider_http_error,
    provider_settings,
    speech_settings,
)
from gateway_py3.layer_profiles import layer_value_profile, matching_layers_exact


MAX_AUDIO_DATA_URI_BYTES = 10 * 1024 * 1024
MAX_VOICE_VALUE_PROFILE_LAYERS = 3
AUDIO_DATA_URI_RE = re.compile(r"^data:audio/[a-z0-9.+-]+(?:;[^,]*)?;base64,([a-z0-9+/=\r\n]+)$", re.IGNORECASE)


def transcribe_and_correct(payload: Dict[str, Any], stored_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    mode = str(payload.get("mode") or "").strip() or None
    audio_data_uri = payload.get("audio_data_uri")
    if not isinstance(audio_data_uri, str) or not audio_data_uri.strip():
        raise ValueError("缺少语音录音数据。")
    context = payload.get("context") if isinstance(payload.get("context"), dict) else stored_context
    raw_text = transcribe_qwen_asr(audio_data_uri.strip())
    command = correct_voice_command(raw_text, context or {}, mode)
    return {
        "raw_text": raw_text,
        "text": command,
        "speech": {
            "provider": "qwen_asr",
            "model": QWEN_ASR_MODEL,
        },
    }


def transcribe_qwen_asr(audio_data_uri: str) -> str:
    validate_audio_data_uri(audio_data_uri)
    settings = speech_settings()
    api_key = provider_api_key(QWEN_PROVIDER)
    if not api_key:
        raise ProviderError("语音识别需要配置千问 API Key。请在右上角“模型配置”里填写千问 Key，语音识别和千问模型共用同一个 DASHSCOPE_API_KEY。")
    payload = {
        "model": settings["model"],
        "stream": False,
        "messages": [{
            "role": "user",
            "content": [{
                "type": "input_audio",
                "input_audio": {"data": audio_data_uri},
            }],
        }],
        "asr_options": {
            "language": "zh",
            "enable_itn": True,
        },
    }
    response = post_qwen_chat_completion(payload, api_key)
    content = (((response.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise ProviderError("Qwen-ASR 没有返回可用文字。请重新录音。")
    return content


def validate_audio_data_uri(audio_data_uri: str) -> None:
    if len(audio_data_uri.encode("utf-8")) > MAX_AUDIO_DATA_URI_BYTES:
        raise ValueError("录音太长。请控制在 10MB 以内，或分段说。")
    match = AUDIO_DATA_URI_RE.match(audio_data_uri)
    if not match:
        raise ValueError("录音格式不支持。请使用浏览器录音生成的 webm、ogg 或 wav 音频。")
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", match.group(1)), validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("录音数据不是有效的 base64 音频。")
    if not raw:
        raise ValueError("录音为空。请重新录音。")


def post_qwen_chat_completion(payload: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    base_url = provider_settings(QWEN_PROVIDER)["base_url"]
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        "%s/chat/completions" % base_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % api_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=MODEL_REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ProviderError(provider_http_error(QWEN_PROVIDER, exc.code, detail))
    except (TimeoutError, socket.timeout):
        raise ProviderError("Qwen-ASR 响应超时。请缩短录音后重试。")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise ProviderError(str(exc))


def correct_voice_command(raw_text: str, context: Dict[str, Any], mode: str | None) -> str:
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("语音识别结果为空。")
    provider = create_provider(mode=mode)
    compact_context = compact_arcmap_context(context)
    attribute_value_profiles = attribute_value_profiles_for_voice(provider, text, compact_context, context)
    result = provider.chat_text([
        {
            "role": "system",
            "content": (
                "你是 GeoPilot 的语音指令校正器。根据 ArcMap 当前上下文修正语音识别文字，"
                "特别是图层名、字段名、字段值、路径、数字和单位。字段值只能根据 attribute_value_profiles "
                "里的 value_samples 校正；没有样本时不要补造字段值。不要规划工作流，不要补造用户没说的任务，"
                "不要解释。只输出校正后的用户指令正文，一行文本；不要输出 JSON、Markdown、引号或前后缀。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "asr_text": text,
                "arcmap_context": compact_context,
                "attribute_value_profiles": attribute_value_profiles,
            }, ensure_ascii=False, sort_keys=True),
        },
    ])
    command = result.get("text")
    if not isinstance(command, str) or not command.strip():
        raise ProviderError("语音校正模型没有返回可用文字。")
    return command.strip()


def attribute_value_profiles_for_voice(
    provider: Any,
    text: str,
    compact_context: Dict[str, Any],
    context: Dict[str, Any],
) -> List[Dict[str, Any]]:
    decision = provider.chat_json([
        {
            "role": "system",
            "content": (
                "你是 GeoPilot 的语音字段值样本读取判定器。判断 ASR 文本是否可能包含需要按属性值校正的字段值。"
                "只返回 JSON：{\"needs_value_profile\": true|false, \"layers\": [\"layer:0\"]}。"
                "只有当文本里出现行政区名、分类值、编号、地名、设施名等可能需要和属性表取值匹配的内容时，"
                "才设置 needs_value_profile=true。只是图层名、字段名、路径、数字、单位、缓冲距离、导出格式或几何操作时返回 false。"
                "layers 只能填写 arcmap_context.layers 里已有的 layer_ref、name 或 longName，最多 3 个；不要编造图层。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "asr_text": text,
                "arcmap_context": compact_context,
            }, ensure_ascii=False, sort_keys=True),
        },
    ])
    if not isinstance(decision, dict):
        raise ProviderError("语音字段值判定模型返回格式无效。")
    needs_profile = decision.get("needs_value_profile")
    if not isinstance(needs_profile, bool):
        raise ProviderError("语音字段值判定模型缺少 needs_value_profile 布尔值。")
    if not needs_profile:
        return []
    layers = decision.get("layers")
    if not isinstance(layers, list):
        raise ProviderError("语音字段值判定模型缺少 layers 数组。")
    layer_values = normalized_requested_layers(layers)
    if not layer_values:
        raise ProviderError("语音字段值判定模型认为需要字段值样本，但没有返回图层引用。")
    profiles = field_value_profiles_for_layers(context, layer_values)
    if not profiles:
        raise ProviderError("语音校正需要字段值样本，但当前 ArcMap 上下文没有匹配图层的字段值样本。请先同步上下文。")
    return profiles


def normalized_requested_layers(layers: List[Any]) -> List[str]:
    result = []
    seen = set()
    for value in layers:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
        if len(result) >= MAX_VOICE_VALUE_PROFILE_LAYERS:
            break
    return result


def field_value_profiles_for_layers(context: Dict[str, Any], layer_values: List[str]) -> List[Dict[str, Any]]:
    profiles = []
    seen = set()
    layers = context.get("layers") or []
    for layer_value in layer_values:
        matches = matching_layers_exact(layer_value, layers)
        if len(matches) != 1:
            if len(matches) > 1:
                raise ProviderError("语音校正字段值样本图层“%s”不唯一，请先同步后使用 layer_ref。" % layer_value)
            raise ProviderError("语音校正字段值样本图层“%s”不存在。请先同步 ArcMap 上下文。" % layer_value)
        layer = matches[0]
        key = str(layer.get("layer_ref") or layer.get("longName") or layer.get("name") or layer_value)
        if key in seen:
            continue
        seen.add(key)
        profile = layer_value_profile(layer, only_fields_with_samples=True)
        if profile["fields"]:
            profiles.append(profile)
    return profiles


def compact_arcmap_context(context: Dict[str, Any]) -> Dict[str, Any]:
    layers = []
    for layer in (context.get("layers") or [])[:80]:
        if not isinstance(layer, dict):
            continue
        layers.append({
            "name": layer.get("name") or "",
            "longName": layer.get("longName") or "",
            "layer_ref": layer.get("layer_ref") or "",
            "geometry_type": layer.get("geometry_type") or "",
            "selected_count": layer.get("selected_count") or 0,
            "fields": compact_fields(layer.get("fields") or []),
        })
    spatial_reference = context.get("spatial_reference") if isinstance(context.get("spatial_reference"), dict) else {}
    return {
        "mxd_path": context.get("mxd_path") or "",
        "is_saved": bool(context.get("is_saved")),
        "spatial_reference": {
            "name": spatial_reference.get("name") or "",
            "type": spatial_reference.get("type") or "",
        },
        "layers": layers,
    }


def compact_fields(fields: List[Any]) -> List[Dict[str, str]]:
    result = []
    for field in fields[:80]:
        if isinstance(field, dict):
            result.append({
                "name": str(field.get("name") or ""),
                "type": str(field.get("type") or ""),
            })
        elif isinstance(field, str):
            result.append({"name": field, "type": ""})
    return result
