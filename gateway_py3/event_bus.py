from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List


MAX_EVENT_HISTORY = 200
EVENT_WAIT_SECONDS = 25


class EventBus:
    def __init__(self):
        self._condition = threading.Condition()
        self._events: List[Dict[str, Any]] = []
        self._next_id = 0

    def publish(self, event_type: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if not event_type:
            raise ValueError("event_type is required.")
        event_payload = payload or {}
        with self._condition:
            self._next_id += 1
            event = {
                "id": self._next_id,
                "type": event_type,
                "payload": event_payload,
                "created_at": time.time(),
            }
            self._events.append(event)
            if len(self._events) > MAX_EVENT_HISTORY:
                self._events = self._events[-MAX_EVENT_HISTORY:]
            self._condition.notify_all()
            return event

    def latest_id(self) -> int:
        with self._condition:
            return self._next_id

    def wait_after(self, last_event_id: int, timeout: float = EVENT_WAIT_SECONDS) -> List[Dict[str, Any]]:
        with self._condition:
            self._condition.wait_for(lambda: self._next_id > last_event_id, timeout=timeout)
            return [event for event in self._events if int(event["id"]) > last_event_id]


def serve_event_stream(handler: Any, bus: EventBus) -> None:
    last_event_id = _last_event_id(handler.headers.get("Last-Event-ID"), 0)
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    _write_sse(handler, "ready", {"last_event_id": bus.latest_id()})
    while True:
        events = bus.wait_after(last_event_id)
        if not events:
            _write_comment(handler, "keep-alive")
            continue
        for event in events:
            _write_sse(handler, event["type"], event["payload"], event_id=int(event["id"]))
            last_event_id = int(event["id"])


def _last_event_id(value: str | None, default: int) -> int:
    if not value:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def _write_sse(handler: Any, event_type: str, payload: Dict[str, Any], event_id: int | None = None) -> None:
    lines = []
    if event_id is not None:
        lines.append("id: %s" % event_id)
    lines.append("event: %s" % event_type)
    lines.append("data: %s" % json.dumps(payload, ensure_ascii=False, sort_keys=True))
    lines.append("")
    _write(handler, "\n".join(lines) + "\n")


def _write_comment(handler: Any, text: str) -> None:
    _write(handler, ": %s\n\n" % text)


def _write(handler: Any, text: str) -> None:
    try:
        handler.wfile.write(text.encode("utf-8"))
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        raise
