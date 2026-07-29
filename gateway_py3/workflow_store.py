from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import data_dir
from .workflow_store_schema import (
    init_database,
    pending_tool_row_to_dict,
    workflow_row_to_dict,
)


DB_PATH = data_dir() / "workflows.sqlite"
RUN_TRANSITIONS = {
    "running": {"planned", "failed", "cancelled", "clarify", "reject"},
    "planned": {"approved", "cancelled", "failed"},
    "approved": {"executing", "cancelled", "failed"},
    "executing": {"succeeded", "failed", "cancelled"},
    "succeeded": set(), "failed": set(), "cancelled": set(), "clarify": set(), "reject": set(),
}


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
            init_database(conn)

    def create_draft(
        self,
        command: str,
        context_hash: str,
        workflow: Dict[str, Any],
        agent_trace: List[Dict[str, Any]],
        mode: str = "context_single"
    ) -> Dict[str, Any]:
        workflow_id = str(uuid.uuid4())
        now = time.time()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO workflows
                (id, status, mode, command, context_hash, workflow_json, agent_trace_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    "draft",
                    mode,
                    command,
                    context_hash,
                    json.dumps(workflow, ensure_ascii=False, sort_keys=True),
                    json.dumps(agent_trace, ensure_ascii=False, sort_keys=True),
                    now,
                    now
                )
            )
        return self.get(workflow_id)

    def create_run(self, command: str, mode: str, context_digest: str) -> Dict[str, Any]:
        """Create the durable run before any model or ArcMap stage begins."""
        trace = {
            "contract": "geopilot-run/v2",
            "mode": mode,
            "context_hash": context_digest,
            "started_at": time.time(),
            "turns": [],
            "task_semantics": None,
            "workflow_versions": [],
            "audits": [],
            "validations": [],
            "usage": [],
            "stages": [],
            "counts": {"revisions": 0},
        }
        return self._insert(command, mode, context_digest, {}, trace, "running")

    def _insert(self, command, mode, context_digest, workflow, trace, status):
        workflow_id = str(uuid.uuid4())
        now = time.time()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO workflows
                (id, status, mode, command, context_hash, workflow_json,
                 agent_trace_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    status,
                    mode,
                    command,
                    context_digest,
                    json.dumps(workflow, ensure_ascii=False, sort_keys=True),
                    json.dumps(
                        [{"type": "run", "run": trace}],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    now,
                    now,
                ),
            )
        return self.get(workflow_id)

    def run_trace(self, run_id: str) -> Dict[str, Any]:
        trace = self.get(run_id).get("agent_trace") or []
        if len(trace) != 1 or trace[0].get("type") != "run":
            raise ValueError("not a run.")
        return trace[0]["run"]

    def update_run(
        self,
        run_id: str,
        status: str,
        workflow: Dict[str, Any] | None = None,
        trace: Dict[str, Any] | None = None,
        result: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        valid_statuses = {
            "running",
            "planned",
            "approved",
            "executing",
            "succeeded",
            "failed",
            "cancelled",
            "clarify",
            "reject",
        }
        if status not in valid_statuses:
            raise ValueError(status)
        row = self.get(run_id)
        current = self.run_trace(run_id)
        if (
            status != row["status"]
            and status not in RUN_TRANSITIONS.get(row["status"], set())
        ):
            raise ValueError("invalid run transition: %s -> %s" % (row["status"], status))
        trace = trace or current
        payload = workflow if workflow is not None else row["workflow"]
        stored_result = result
        if stored_result is None:
            stored_result = row["result"]
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE workflows
                SET status = ?, workflow_json = ?, agent_trace_json = ?,
                    result_json = ?, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    status,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(
                        [{"type": "run", "run": trace}],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    (
                        json.dumps(stored_result, ensure_ascii=False, sort_keys=True)
                        if stored_result is not None
                        else None
                    ),
                    time.time(),
                    run_id,
                    row["status"],
                ),
            )
        if cursor.rowcount != 1:
            raise ValueError("concurrent run transition rejected.")
        return self.get(run_id)

    def fail_run(
        self,
        run_id: str,
        stage: str,
        exc: Exception,
        trace: Dict[str, Any],
    ) -> Dict[str, Any]:
        trace["failure"] = {
            "stage": stage,
            "type": type(exc).__name__,
            "summary": "stage failed",
        }
        return self.update_run(
            run_id,
            "failed",
            trace=trace,
            result={"error": trace["failure"]},
        )

    def is_cancel_requested(self, run_id: str) -> bool:
        return self.get(run_id)["status"] == "cancelled"

    def get(self, workflow_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, status, mode, command, context_hash, workflow_json,
                       agent_trace_json, created_at, updated_at, result_json
                FROM workflows
                WHERE id = ?
                """,
                (workflow_id,)
            ).fetchone()
        if row is None:
            raise KeyError(workflow_id)
        return workflow_row_to_dict(row)

    def list_recent(
        self,
        limit: int = 50,
        mode: str | None = None,
        since: float | None = None,
        include_trace: bool = True
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        clauses = []
        params: List[Any] = []
        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        if since is not None:
            clauses.append("updated_at > ?")
            params.append(float(since))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, status, mode, command, context_hash, workflow_json, agent_trace_json, created_at, updated_at, result_json
                FROM workflows
                %s
                ORDER BY created_at DESC LIMIT ?
                """ % where,
                params
            ).fetchall()
        return [workflow_row_to_dict(row, include_trace=include_trace) for row in rows]

    def clear_workflows(self, mode: str | None = None) -> Dict[str, Any]:
        with self._connection() as conn:
            if mode:
                workflow_count = conn.execute("DELETE FROM workflows WHERE mode = ?", (mode,)).rowcount
            else:
                workflow_count = conn.execute("DELETE FROM workflows").rowcount
        return {
            "ok": True,
            "cleared": {
                "workflows": workflow_count,
            }
        }

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
        row = self.get(workflow_id)
        if (row.get("agent_trace") or [{}])[0].get("type") == "run":
            return self._set_status(workflow_id, "approved")
        return self._set_status(workflow_id, "approved_for_arcmap")

    def pending(self) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, status, mode, command, context_hash, workflow_json, agent_trace_json, created_at, updated_at, result_json
                FROM workflows
                WHERE status IN ('approved_for_arcmap', 'approved')
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        return workflow_row_to_dict(row) if row else None

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

    def cancel(self, workflow_id: str) -> Dict[str, Any]:
        row = self.get(workflow_id)
        if row["status"] not in ("running", "planned", "approved", "executing"):
            raise ValueError("run is already terminal.")
        if (row.get("agent_trace") or [{}])[0].get("type") == "run":
            return self.update_run(workflow_id, "cancelled")
        return self._set_status(workflow_id, "cancelled")

    def export_runs(self, mode: str | None = None) -> Dict[str, Any]:
        runs = []
        for row in self.list_recent(limit=200, mode=mode, include_trace=True):
            trace = row.get("agent_trace") or []
            if len(trace) == 1 and trace[0].get("type") == "run":
                runs.append({"id": row["id"], "status": row["status"], "mode": row["mode"], "command": row["command"], "context_hash": row["context_hash"], "trace": trace[0]["run"], "result": row["result"]})
        return {"contract": "geopilot-report/v1", "runs": runs}

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

    def delete_state(self, key: str) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM app_state WHERE key = ?", (key,))

    def list_state(self, prefix: str = "") -> List[Dict[str, Any]]:
        with self._connection() as conn:
            if prefix:
                rows = conn.execute(
                    "SELECT key, value_json, updated_at FROM app_state WHERE key LIKE ? ORDER BY updated_at DESC",
                    (prefix + "%",)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT key, value_json, updated_at FROM app_state ORDER BY updated_at DESC"
                ).fetchall()
        return [{"key": row[0], "value": json.loads(row[1]), "updated_at": row[2]} for row in rows]

    def _set_status(self, workflow_id: str, status: str) -> Dict[str, Any]:
        with self._connection() as conn:
            conn.execute("UPDATE workflows SET status = ?, updated_at = ? WHERE id = ?", (status, time.time(), workflow_id))
        return self.get(workflow_id)

    def create_pending_tool(self, name: str, capability: str, payload: Dict[str, Any], files: Dict[str, str], tool_id: str | None = None) -> Dict[str, Any]:
        draft_id = tool_id or str(uuid.uuid4())
        now = time.time()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO pending_tools
                (id, status, name, capability, payload_json, files_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    "pending_review",
                    name,
                    capability,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(files, ensure_ascii=False, sort_keys=True),
                    now,
                    now
                )
            )
        return self.get_pending_tool(draft_id)

    def list_pending_tools(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, status, name, capability, payload_json, files_json, created_at, updated_at
                FROM pending_tools
                ORDER BY updated_at DESC
                """
            ).fetchall()
        return [pending_tool_row_to_dict(row) for row in rows]

    def get_pending_tool(self, tool_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, status, name, capability, payload_json, files_json, created_at, updated_at
                FROM pending_tools WHERE id = ?
                """,
                (tool_id,)
            ).fetchone()
        if row is None:
            raise KeyError(tool_id)
        return pending_tool_row_to_dict(row)

    def set_pending_tool_status(self, tool_id: str, status: str) -> Dict[str, Any]:
        if status not in ("pending_review", "enabled", "rejected"):
            raise ValueError(status)
        with self._connection() as conn:
            conn.execute("UPDATE pending_tools SET status = ?, updated_at = ? WHERE id = ?", (status, time.time(), tool_id))
        return self.get_pending_tool(tool_id)

    def update_pending_tool(
        self,
        tool_id: str,
        status: str,
        name: str,
        capability: str,
        payload: Dict[str, Any],
        files: Dict[str, str]
    ) -> Dict[str, Any]:
        if status not in ("pending_review", "enabled", "rejected"):
            raise ValueError(status)
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE pending_tools
                SET status = ?, name = ?, capability = ?, payload_json = ?, files_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    name,
                    capability,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(files, ensure_ascii=False, sort_keys=True),
                    time.time(),
                    tool_id,
                )
            )
        if cursor.rowcount == 0:
            raise KeyError(tool_id)
        return self.get_pending_tool(tool_id)

    def delete_pending_tool(self, tool_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM pending_tools WHERE id = ?", (tool_id,))
        if cursor.rowcount == 0:
            raise KeyError(tool_id)
        return {"ok": True, "id": tool_id}
