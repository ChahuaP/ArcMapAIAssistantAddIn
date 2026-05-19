from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import data_dir


DB_PATH = data_dir() / "workflows.sqlite"


class WorkflowStore:
    def __init__(self, path: Path | None = None):
        self.path = path or DB_PATH
        self._init()

    def _connect(self):
        return sqlite3.connect(str(self.path))

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    command TEXT NOT NULL,
                    context_hash TEXT NOT NULL,
                    workflow_json TEXT NOT NULL,
                    selected_operations_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    result_json TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def create_draft(self, command: str, context_hash: str, workflow: Dict[str, Any], selected_operations: List[str]) -> Dict[str, Any]:
        workflow_id = str(uuid.uuid4())
        now = time.time()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO workflows
                (id, status, command, context_hash, workflow_json, selected_operations_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    "draft",
                    command,
                    context_hash,
                    json.dumps(workflow, ensure_ascii=False, sort_keys=True),
                    json.dumps(selected_operations, ensure_ascii=False),
                    now,
                    now
                )
            )
        return self.get(workflow_id)

    def get(self, workflow_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, status, command, context_hash, workflow_json, selected_operations_json, created_at, updated_at, result_json FROM workflows WHERE id = ?",
                (workflow_id,)
            ).fetchone()
        if row is None:
            raise KeyError(workflow_id)
        return _row_to_dict(row)

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, status, command, context_hash, workflow_json, selected_operations_json, created_at, updated_at, result_json FROM workflows ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def latest_clarification(self, max_age_seconds: int = 1800) -> Optional[Dict[str, Any]]:
        clarifications = self.recent_clarifications(max_age_seconds=max_age_seconds, limit=10)
        return clarifications[0] if clarifications else None

    def recent_clarifications(self, max_age_seconds: int = 1800, limit: int = 10) -> List[Dict[str, Any]]:
        cutoff = time.time() - max_age_seconds
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, status, command, context_hash, workflow_json, selected_operations_json, created_at, updated_at, result_json
                FROM workflows
                WHERE status = 'draft' AND updated_at >= ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (cutoff, limit)
            ).fetchall()
        clarifications = []
        for row in rows:
            item = _row_to_dict(row)
            if item["workflow"].get("action") == "clarify":
                clarifications.append(item)
                continue
            if item["workflow"].get("action") in ("execute", "unsupported"):
                break
        return clarifications

    def clear_workflows(self) -> Dict[str, Any]:
        with self._connection() as conn:
            conn.execute("DELETE FROM workflows")
        return {"ok": True}

    def clear_state(self, key: str) -> Dict[str, Any]:
        with self._connection() as conn:
            conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
        return {"ok": True}

    def delete(self, workflow_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
        if cursor.rowcount == 0:
            raise KeyError(workflow_id)
        return {"ok": True}

    def approve(self, workflow_id: str) -> Dict[str, Any]:
        return self._set_status(workflow_id, "approved_for_arcmap")

    def pending(self) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, status, command, context_hash, workflow_json, selected_operations_json, created_at, updated_at, result_json
                FROM workflows
                WHERE status = 'approved_for_arcmap'
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        return _row_to_dict(row) if row else None

    def claim(self, workflow_id: str) -> Dict[str, Any]:
        return self._set_status(workflow_id, "claimed_by_arcmap")

    def mark_executing(self, workflow_id: str) -> Dict[str, Any]:
        return self._set_status(workflow_id, "executing")

    def finish(self, workflow_id: str, status: str, result: Dict[str, Any]) -> Dict[str, Any]:
        if status not in ("succeeded", "failed"):
            raise ValueError(status)
        with self._connection() as conn:
            conn.execute(
                "UPDATE workflows SET status = ?, result_json = ?, updated_at = ? WHERE id = ?",
                (status, json.dumps(result, ensure_ascii=False, sort_keys=True), time.time(), workflow_id)
            )
        return self.get(workflow_id)

    def set_state(self, key: str, value: Dict[str, Any]) -> Dict[str, Any]:
        now = time.time()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO app_state (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False, sort_keys=True), now)
            )
        return {"key": key, "value": value, "updated_at": now}

    def get_state(self, key: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT key, value_json, updated_at FROM app_state WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return {"key": row[0], "value": json.loads(row[1]), "updated_at": row[2]}

    def _set_status(self, workflow_id: str, status: str) -> Dict[str, Any]:
        with self._connection() as conn:
            conn.execute("UPDATE workflows SET status = ?, updated_at = ? WHERE id = ?", (status, time.time(), workflow_id))
        return self.get(workflow_id)


def _row_to_dict(row) -> Dict[str, Any]:
    result = {
        "id": row[0],
        "status": row[1],
        "command": row[2],
        "context_hash": row[3],
        "workflow": json.loads(row[4]),
        "selected_operations": json.loads(row[5]),
        "created_at": row[6],
        "updated_at": row[7],
        "result": json.loads(row[8]) if row[8] else None
    }
    return result
