from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List


PROJECT_OUTPUT_DIR_NAME = "GeoPilot_Output"
PROJECT_MEMORY_COMPACT_LIMIT = 80
PROJECT_MEMORY_KEEP_RECENT = 30
PROJECT_MEMORY_SUMMARY_MAX_CHARS = 6000


def insert_project_event(conn, project_id: str, event_type: str, payload: Dict[str, Any], now: float, event_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO project_events (id, project_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (event_id or str(uuid.uuid4()), project_id, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), now)
    )


def compact_memory_text(memories: List[Dict[str, Any]]) -> str:
    lines = ["项目长期记忆摘要："]
    for item in memories:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        kind = str(item.get("kind") or "note")
        if kind == "summary":
            lines.append(content)
        else:
            lines.append("- [%s] %s" % (kind, content))
    text = "\n".join(lines)
    if len(text) <= PROJECT_MEMORY_SUMMARY_MAX_CHARS:
        return text
    return text[:PROJECT_MEMORY_SUMMARY_MAX_CHARS].rstrip() + "\n- 早期记忆过长，已截断。"
