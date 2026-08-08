"""SSE event projection from the run_events journal (§14).

The EventBus is a projection, never the source of truth (§7): every SSE
event is derived from the ``run_events`` append-only table. A reconnect
resumes from ``Last-Event-ID`` by re-reading the journal, so no event is
lost even if the in-memory history is truncated.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

from ..kernel.store import JournalStore


class JournalEventProjection:
    """Maps run_events rows to SSE event types (§14.2).

    ``append_event`` is called by the kernel store after each journal write;
    it projects the row to a typed SSE event and wakes SSE waiters. History
    for reconnect comes from the journal, not from memory.
    """

    # run_events.kind -> SSE event type (§14.2)
    # Terminal/outcome stages are also mapped to run.stage_changed so the
    # front-end can react to them via a single event listener.
    _EVENT_TYPES = {
        "run_received": "run.stage_changed",
        "context_frozen": "run.stage_changed",
        "intent_compiled": "run.stage_changed",
        "clarification_required": "run.stage_changed",
        "plan_failed": "run.stage_changed",
        "intent_failed": "run.stage_changed",
        "context_failed": "run.stage_changed",
        "plan_verified": "run.stage_changed",
        "authorization_required": "run.stage_changed",
        "authorization_auto": "run.stage_changed",
        "authorization_approved": "run.stage_changed",
        "authorization_denied": "run.stage_changed",
        "authorization_skipped": "run.stage_changed",
        "authorization_failed": "run.stage_changed",
        "runtime_acquired": "run.stage_changed",
        "runtime_failed": "run.stage_changed",
        "execution_started": "run.stage_changed",
        "execution_failed": "run.stage_changed",
        "executed": "run.stage_changed",
        "accepted": "run.stage_changed",
        "acceptance_failed": "run.stage_changed",
        "published": "run.stage_changed",
        "publish_failed": "run.stage_changed",
        "succeeded": "run.stage_changed",
        "planning.node_update": "planning.node_update",
        "model.token": "model.token",
        "model.call_started": "model.call_started",
        "model.call_finished": "model.call_finished",
    }

    def __init__(self, store: JournalStore):
        self.store = store
        self._condition = threading.Condition()
        self._latest_seq = 0
        self._max_history = 500

    # -- kernel integration -------------------------------------------------

    def notify(self, event_seq: int, run_id: str, kind: str, stage: str,
               payload: Dict[str, Any]) -> None:
        """Called by the store after appending a journal row."""
        with self._condition:
            if event_seq and event_seq > 0:
                self._latest_seq = max(self._latest_seq, int(event_seq))
            self._condition.notify_all()

    # -- SSE waiters --------------------------------------------------------

    def wait_after(self, last_seq: int, timeout: float = 25.0) -> List[Dict[str, Any]]:
        """Block until new journal events exist; return projected events.

        Events are read from the journal (source of truth), so a waiter that
        missed in-memory state still sees them.
        """
        with self._condition:
            self._condition.wait_for(lambda: self._latest_seq > last_seq, timeout=timeout)
            return self.projection_after(last_seq, limit=self._max_history)

    def projection_after(self, last_seq: int, limit: int = 200) -> List[Dict[str, Any]]:
        """Project journal events after ``last_seq`` into SSE events (§14.2)."""
        events = self.store.events_after(last_seq, limit=limit)
        return [self._project(row) for row in events]

    def latest_seq(self) -> int:
        with self._condition:
            return self._latest_seq

    # -- projection ---------------------------------------------------------

    def _project(self, row: Dict[str, Any]) -> Dict[str, Any]:
        kind = row["kind"]
        event_type = self._EVENT_TYPES.get(kind, "run.stage_changed")
        payload = dict(row.get("payload") or {})
        payload.setdefault("run_id", row["run_id"])
        # Use the event KIND as the stage signal, not the event's stage field.
        # The event's stage field is the transition checkpoint (e.g.
        # "intent_compiled"), but for outcome events (clarification_required,
        # plan_failed, etc.) the actual run stage is different.
        # Map event kinds to their projected stage so the front-end gets
        # the correct terminal/paused state.
        kind_to_stage = {
            "run_received": "received",
            "user_text": "received",
            "context_frozen": "context_frozen",
            "context_failed": "infrastructure_failed",
            "intent_compiled": "intent_compiled",
            "intent_failed": "contract_failed",
            "clarification_required": "clarification_required",
            "plan_verified": "plan_verified",
            "plan_failed": "contract_failed",
            "authorization_required": "authorization_required",
            "authorization_auto": "authorized",
            "authorization_approved": "authorized",
            "authorization_skipped": "authorized",
            "authorization_denied": "policy_denied",
            "authorization_failed": "policy_denied",
            "runtime_acquired": "runtime_acquired",
            "runtime_failed": "infrastructure_failed",
            "execution_started": "executing",
            "execution_failed": "capability_failed",
            "executed": "executed",
            "accepted": "accepted",
            "acceptance_failed": "acceptance_failed",
            "published": "published",
            "publish_failed": "infrastructure_failed",
            "succeeded": "succeeded",
        }
        stage = kind_to_stage.get(kind, row.get("stage", ""))
        payload["stage"] = stage
        return {
            "id": row["event_seq"],
            "type": event_type,
            "payload": payload,
            "created_at": row["recorded_at"],
        }
