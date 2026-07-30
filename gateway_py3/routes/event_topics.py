from __future__ import annotations

from typing import Any, Dict, List


def mutation_events(path: str, result: Dict[str, Any] | None) -> List[str]:
    if result is None:
        return []
    if path.startswith("/runs/") and path.endswith("/context"):
        return ["context.changed"]
    if path == "/runs" or path.startswith("/runs/"):
        return ["runs.changed"]
    if path in ("/arcmap/register", "/arcmap/active", "/arcmap/permission"):
        return ["arcmap.changed"]
    if path == "/config":
        return ["config.changed"]
    if path.startswith("/tools/"):
        events = ["tools.changed"]
        if path.endswith("/enable") or path.endswith("/delete"):
            events.append("catalog.changed")
        return events
    return []


def publish_mutation_events(state: Any, path: str, result: Dict[str, Any] | None) -> None:
    for event_type in mutation_events(path, result):
        state.events.publish(event_type, {"path": path})
