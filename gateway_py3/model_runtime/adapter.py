"""The strict provider adapter seam used only by ModelRuntime."""
from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable

from .contracts import ProviderInvocation, ProviderResponse, TokenCallback


class ProviderError(RuntimeError):
    """Provider-neutral failure classification; never triggers provider switching."""

    def __init__(self, kind: str, message: str,
                 evidence: Optional[Dict[str, Any]] = None):
        if kind not in ("quota", "protocol", "transport", "uncertain"):
            raise ValueError("invalid provider error kind: %s" % kind)
        super().__init__(message)
        self.kind = kind
        self.evidence = dict(evidence or {})


@runtime_checkable
class ProviderAdapter(Protocol):
    provider_type: str
    connection_id: str

    def invoke(self, call: ProviderInvocation,
               on_token: Optional[TokenCallback] = None) -> ProviderResponse: ...
