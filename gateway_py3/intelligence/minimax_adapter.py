"""MiniMax-M3 adapter for ModelRuntime (§6.4).

The only production model adapter. Implements ``ModelAdapter`` by delegating to
``MiniMaxProvider`` from ``llm_providers`` for the MiniMax-specific wire
normalization (thinking-block stripping, text tool-call extraction, schema
array unwrapping). GLM, DeepSeek, Qwen and auto-selection are not carried into
the new intelligence layer; only MiniMax-M3 remains as a planning provider.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..llm_providers import (
    MINIMAX_MODEL,
    MINIMAX_PROVIDER,
    MiniMaxProvider,
    StructuredOutputContract,
    create_provider,
    provider_api_key,
)


class MiniMaxAdapter:
    """``ModelAdapter`` over ``MiniMaxProvider`` for MiniMax-M3.

    Construction reads the MiniMax API key from the shared config/env
    resolution so operators configure keys once. ``chat_structured`` returns
    the normalized structured result with ``_usage`` for the call ledger.
    """

    provider = MINIMAX_PROVIDER
    model = MINIMAX_MODEL

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self._api_key = api_key
        self._base_url = base_url
        self._provider: MiniMaxProvider | None = None

    def _resolve(self) -> MiniMaxProvider:
        if self._provider is not None:
            return self._provider
        if self._api_key is None:
            self._api_key = provider_api_key(MINIMAX_PROVIDER)
        self._provider = create_provider(
            provider_id=MINIMAX_PROVIDER,
            model_id=MINIMAX_MODEL,
        )
        return self._provider

    def chat_structured(self, messages: List[Dict[str, str]],
                        contract: StructuredOutputContract) -> Dict[str, Any]:
        provider = self._resolve()
        return provider.chat_structured(messages, contract)

    def chat_with_tools(self, messages: List[Dict[str, str]],
                        tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        provider = self._resolve()
        return provider.chat_with_tools(messages, tools)
