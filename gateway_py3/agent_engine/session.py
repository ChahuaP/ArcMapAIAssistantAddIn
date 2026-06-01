from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class AgentSession:
    command: str
    context: Dict[str, Any]
    mode: str
    context_hash: str
    operation_count: int
    request_id: str = ""
    permissions: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)
