from __future__ import annotations

from typing import Any, Dict, List


def mutation_events(path: str, result: Dict[str, Any] | None) -> List[str]:
    if result is None:
        return []
    if path == "/plan":
        return ["workflows.changed", "tools.changed", "catalog.changed"]
    if path == "/agent/workflows/propose":
        return ["workflows.changed"]
    if path == "/arcmap/sync" or path == "/context":
        return ["context.changed"]
    if path in ("/arcmap/register", "/arcmap/active", "/arcmap/permission"):
        return ["arcmap.changed"]
    if path in ("/arcmap/execute-approved", "/arcmap/execute-workflow"):
        return ["workflows.changed", "arcmap.changed"]
    if path == "/config":
        return ["config.changed"]
    if path in ("/projects", "/projects/active"):
        return ["projects.changed"]
    if path.startswith("/projects/") and path.endswith("/delete"):
        return ["projects.changed", "workflows.changed"]
    if path.startswith("/workflows/") or path == "/execution-result" or path == "/workflows/clear":
        return ["workflows.changed", "projects.changed"]
    if path.startswith("/tools/"):
        return ["tools.changed", "catalog.changed"]
    return []


def publish_mutation_events(state: Any, path: str, result: Dict[str, Any] | None) -> None:
    for event_type in mutation_events(path, result):
        state.events.publish(event_type, {"path": path})
