from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class AgentSession:
    command: str
    context: Dict[str, Any]
    mode: str
    project_id: str
    project: Dict[str, Any] | None
    context_hash: str
    operation_count: int
    permissions: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)
