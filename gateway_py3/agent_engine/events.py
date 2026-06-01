from __future__ import annotations

from typing import Any, Dict


AGENT_PROGRESS_EVENT = "agent.progress"


def publish_agent_progress(state: Any, session: Any, stage: str, label: str, detail: str = "") -> None:
    events = getattr(state, "events", None)
    if events is None:
        return
    events.publish(AGENT_PROGRESS_EVENT, {
        "stage": stage,
        "label": label,
        "detail": detail,
        "mode": session.mode,
        "project_id": session.project_id,
    })
