"""Strict DPAPI serializer for LangGraph checkpoints."""
from __future__ import annotations

from typing import Any

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.base import SerializerProtocol

from ..secure_storage import protect_bytes, unprotect_bytes


class DpapiCheckpointSerializer(SerializerProtocol):
    """Encrypt every LangGraph checkpoint value for the current Windows user."""

    _TYPE = "geopilot-dpapi-v1"

    def __init__(self) -> None:
        self._inner = JsonPlusSerializer(pickle_fallback=False)

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        inner_type, payload = self._inner.dumps_typed(obj)
        if not isinstance(inner_type, str) or b"\0" in inner_type.encode("utf-8"):
            raise TypeError("checkpoint serializer received an invalid inner type.")
        plaintext = inner_type.encode("utf-8") + b"\0" + payload
        return self._TYPE, protect_bytes(plaintext).encode("ascii")

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        type_name, envelope = data
        if type_name != self._TYPE or not isinstance(envelope, bytes):
            raise ValueError("legacy or unprotected LangGraph checkpoint blob is forbidden.")
        plaintext = unprotect_bytes(envelope.decode("ascii"))
        inner_type, separator, payload = plaintext.partition(b"\0")
        if not separator:
            raise ValueError("DPAPI checkpoint payload has no type separator.")
        return self._inner.loads_typed((inner_type.decode("utf-8"), payload))
