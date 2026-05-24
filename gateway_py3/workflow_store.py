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
WORKFLOW_COLUMNS = {
    "id",
    "status",
    "mode",
    "project_id",
    "command",
    "context_hash",
    "workflow_json",
    "agent_trace_json",
    "created_at",
    "updated_at",
    "result_json"
}
WORKFLOW_COLUMN_DEFINITIONS = {
    "status": "TEXT NOT NULL DEFAULT 'draft'",
    "mode": "TEXT NOT NULL DEFAULT 'semi_agent'",
    "project_id": "TEXT NOT NULL DEFAULT ''",
    "command": "TEXT NOT NULL DEFAULT ''",
    "context_hash": "TEXT NOT NULL DEFAULT ''",
    "workflow_json": "TEXT NOT NULL DEFAULT '{}'",
    "agent_trace_json": "TEXT NOT NULL DEFAULT '[]'",
    "created_at": "REAL NOT NULL DEFAULT 0",
    "updated_at": "REAL NOT NULL DEFAULT 0",
    "result_json": "TEXT"
}
PROJECT_OUTPUT_DIR_NAME = "GeoPilot_Output"
PROJECT_MEMORY_COMPACT_LIMIT = 80
PROJECT_MEMORY_KEEP_RECENT = 30
PROJECT_MEMORY_SUMMARY_MAX_CHARS = 6000


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
            _migrate_workflows_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    context_hash TEXT NOT NULL,
                    workflow_json TEXT NOT NULL,
                    agent_trace_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    result_json TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_project_updated ON workflows(project_id, updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_mode_updated ON workflows(mode, updated_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    workdir TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_memories (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS project_events (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_project_memories_project_created ON project_memories(project_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_project_events_project_created ON project_events(project_id, created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_tools (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    name TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    files_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
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

    def create_draft(
        self,
        command: str,
        context_hash: str,
        workflow: Dict[str, Any],
        agent_trace: List[Dict[str, Any]],
        mode: str = "semi_agent",
        project_id: str = ""
    ) -> Dict[str, Any]:
        workflow_id = str(uuid.uuid4())
        now = time.time()
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO workflows
                (id, status, mode, project_id, command, context_hash, workflow_json, agent_trace_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    "draft",
                    mode,
                    project_id or "",
                    command,
                    context_hash,
                    json.dumps(workflow, ensure_ascii=False, sort_keys=True),
                    json.dumps(agent_trace, ensure_ascii=False, sort_keys=True),
                    now,
                    now
                )
            )
            if project_id:
                _insert_project_event(conn, project_id, "workflow_created", {
                    "workflow_id": workflow_id,
                    "command": command,
                    "summary": workflow.get("summary", ""),
                    "action": workflow.get("action", ""),
                }, now)
        return self.get(workflow_id)

    def get(self, workflow_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, status, mode, project_id, command, context_hash, workflow_json, agent_trace_json, created_at, updated_at, result_json FROM workflows WHERE id = ?",
                (workflow_id,)
            ).fetchone()
        if row is None:
            raise KeyError(workflow_id)
        return _row_to_dict(row)

    def list_recent(self, limit: int = 50, project_id: str | None = None, mode: str | None = None) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            if project_id and mode:
                rows = conn.execute(
                    """
                    SELECT id, status, mode, project_id, command, context_hash, workflow_json, agent_trace_json, created_at, updated_at, result_json
                    FROM workflows
                    WHERE project_id = ? AND mode = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (project_id, mode, limit)
                ).fetchall()
            elif project_id:
                rows = conn.execute(
                    """
                    SELECT id, status, mode, project_id, command, context_hash, workflow_json, agent_trace_json, created_at, updated_at, result_json
                    FROM workflows
                    WHERE project_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (project_id, limit)
                ).fetchall()
            elif mode:
                rows = conn.execute(
                    """
                    SELECT id, status, mode, project_id, command, context_hash, workflow_json, agent_trace_json, created_at, updated_at, result_json
                    FROM workflows
                    WHERE mode = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (mode, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, status, mode, project_id, command, context_hash, workflow_json, agent_trace_json, created_at, updated_at, result_json
                    FROM workflows
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (limit,)
                ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def clear_workflows(self, project_id: str | None = None, mode: str | None = None) -> Dict[str, Any]:
        with self._connection() as conn:
            if project_id:
                workflow_count = conn.execute("DELETE FROM workflows WHERE project_id = ?", (project_id,)).rowcount
                memory_count = conn.execute("DELETE FROM project_memories WHERE project_id = ?", (project_id,)).rowcount
                event_count = conn.execute("DELETE FROM project_events WHERE project_id = ?", (project_id,)).rowcount
            elif mode:
                workflow_count = conn.execute("DELETE FROM workflows WHERE mode = ?", (mode,)).rowcount
                memory_count = 0
                event_count = 0
            else:
                workflow_count = conn.execute("DELETE FROM workflows").rowcount
                memory_count = conn.execute("DELETE FROM project_memories").rowcount
                event_count = conn.execute("DELETE FROM project_events").rowcount
        return {
            "ok": True,
            "cleared": {
                "workflows": workflow_count,
                "project_memories": memory_count,
                "project_events": event_count,
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
        return self._set_status(workflow_id, "approved_for_arcmap")

    def pending(self) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, status, mode, project_id, command, context_hash, workflow_json, agent_trace_json, created_at, updated_at, result_json
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
        row = self.get(workflow_id)
        if row.get("project_id"):
            self.add_project_event(row["project_id"], "workflow_finished", {
                "workflow_id": workflow_id,
                "status": status,
                "summary": row["workflow"].get("summary", ""),
                "result": result,
            })
            if status == "succeeded":
                self.add_project_memory(row["project_id"], row["workflow"].get("summary", ""), kind="workflow_result")
        return row

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

    def create_project(self, name: str, workdir: str) -> Dict[str, Any]:
        path = Path(workdir).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise ValueError("项目工作目录不存在：%s" % workdir)
        output_dir = path / PROJECT_OUTPUT_DIR_NAME
        output_dir.mkdir(parents=True, exist_ok=True)
        project_id = str(uuid.uuid4())
        now = time.time()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO projects (id, name, workdir, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (project_id, name.strip() or path.name, str(path), now, now)
            )
            _insert_project_event(conn, project_id, "project_created", {
                "name": name.strip() or path.name,
                "workdir": str(path),
                "output_workspace": str(output_dir)
            }, now)
        self.set_state("active_project_id", {"id": project_id})
        return self.get_project(project_id)

    def list_projects(self) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT id, name, workdir, created_at, updated_at FROM projects ORDER BY created_at DESC"
            ).fetchall()
        return [_project_row_to_dict(row) for row in rows]

    def get_project(self, project_id: str | None) -> Optional[Dict[str, Any]]:
        if not project_id:
            return None
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, name, workdir, created_at, updated_at FROM projects WHERE id = ?",
                (project_id,)
            ).fetchone()
        return _project_row_to_dict(row) if row else None

    def set_active_project(self, project_id: str) -> Dict[str, Any]:
        project = self.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        with self._connection() as conn:
            conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (time.time(), project_id))
        self.set_state("active_project_id", {"id": project_id})
        return self.get_project(project_id)

    def get_active_project(self) -> Optional[Dict[str, Any]]:
        state = self.get_state("active_project_id")
        if not state:
            return None
        return self.get_project((state.get("value") or {}).get("id"))

    def add_project_memory(self, project_id: str, content: str, kind: str = "note") -> Dict[str, Any]:
        if not content.strip():
            raise ValueError("记忆内容不能为空。")
        memory_id = str(uuid.uuid4())
        now = time.time()
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO project_memories (id, project_id, kind, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (memory_id, project_id, kind, content.strip(), now)
            )
        if kind != "summary":
            self.compact_project_memory(project_id)
        return {"id": memory_id, "project_id": project_id, "kind": kind, "content": content.strip(), "created_at": now}

    def list_project_memories(self, project_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, project_id, kind, content, created_at
                FROM project_memories
                WHERE project_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (project_id, limit)
            ).fetchall()
        return [
            {"id": row[0], "project_id": row[1], "kind": row[2], "content": row[3], "created_at": row[4]}
            for row in rows
        ]

    def add_project_event(self, project_id: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        event_id = str(uuid.uuid4())
        now = time.time()
        with self._connection() as conn:
            _insert_project_event(conn, project_id, event_type, payload, now, event_id)
        return {"id": event_id, "project_id": project_id, "event_type": event_type, "payload": payload, "created_at": now}

    def list_project_events(self, project_id: str, limit: int = 80) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, project_id, event_type, payload_json, created_at
                FROM project_events
                WHERE project_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (project_id, limit)
            ).fetchall()
        return [
            {"id": row[0], "project_id": row[1], "event_type": row[2], "payload": json.loads(row[3]), "created_at": row[4]}
            for row in rows
        ]

    def delete_project(self, project_id: str) -> Dict[str, Any]:
        active = self.get_active_project()
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            if cursor.rowcount == 0:
                raise KeyError(project_id)
            conn.execute("DELETE FROM workflows WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM project_memories WHERE project_id = ?", (project_id,))
            conn.execute("DELETE FROM project_events WHERE project_id = ?", (project_id,))
            if active and active.get("id") == project_id:
                conn.execute("DELETE FROM app_state WHERE key = 'active_project_id'")
        return {"ok": True}

    def compact_project_memory(
        self,
        project_id: str,
        max_items: int = PROJECT_MEMORY_COMPACT_LIMIT,
        keep_recent: int = PROJECT_MEMORY_KEEP_RECENT
    ) -> Dict[str, Any]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, project_id, kind, content, created_at
                FROM project_memories
                WHERE project_id = ?
                ORDER BY created_at ASC
                """,
                (project_id,)
            ).fetchall()
            memories = [
                {"id": row[0], "project_id": row[1], "kind": row[2], "content": row[3], "created_at": row[4]}
                for row in rows
            ]
            if len(memories) <= max_items:
                return {"ok": True, "compacted": False, "count": len(memories)}
            keep_recent = max(1, min(keep_recent, max_items - 1))
            older = memories[:-keep_recent]
            recent = memories[-keep_recent:]
            summary = _compact_memory_text(older)
            delete_ids = [item["id"] for item in older]
            placeholders = ",".join("?" for _ in delete_ids)
            conn.execute("DELETE FROM project_memories WHERE id IN (%s)" % placeholders, delete_ids)
            now = time.time()
            summary_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO project_memories (id, project_id, kind, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (summary_id, project_id, "summary", summary, min(recent[0]["created_at"], now) - 0.001)
            )
            _insert_project_event(conn, project_id, "memory_compacted", {
                "compacted_count": len(older),
                "kept_recent_count": len(recent),
                "summary_id": summary_id
            }, now)
        return {"ok": True, "compacted": True, "compacted_count": len(older), "kept_recent_count": len(recent)}

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
        return [_pending_tool_row_to_dict(row) for row in rows]

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
        return _pending_tool_row_to_dict(row)

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


def _migrate_workflows_schema(conn) -> None:
    table = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'workflows'").fetchone()
    if table is None:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(workflows)").fetchall()}
    critical_missing = {"id"} - columns
    if critical_missing:
        raise RuntimeError("workflows 表结构损坏，缺少字段：%s" % "、".join(sorted(critical_missing)))
    for column in sorted(WORKFLOW_COLUMNS - columns):
        definition = WORKFLOW_COLUMN_DEFINITIONS.get(column)
        if not definition:
            raise RuntimeError("workflows 表无法迁移，缺少字段：%s" % column)
        conn.execute("ALTER TABLE workflows ADD COLUMN %s %s" % (column, definition))


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "status": row[1],
        "mode": row[2],
        "project_id": row[3],
        "command": row[4],
        "context_hash": row[5],
        "workflow": json.loads(row[6]),
        "agent_trace": json.loads(row[7]),
        "created_at": row[8],
        "updated_at": row[9],
        "result": json.loads(row[10]) if row[10] else None
    }


def _project_row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "workdir": row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }


def _pending_tool_row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "status": row[1],
        "name": row[2],
        "capability": row[3],
        "payload": json.loads(row[4]),
        "files": json.loads(row[5]),
        "created_at": row[6],
        "updated_at": row[7],
    }


def _insert_project_event(conn, project_id: str, event_type: str, payload: Dict[str, Any], now: float, event_id: str | None = None) -> None:
    conn.execute(
        "INSERT INTO project_events (id, project_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (event_id or str(uuid.uuid4()), project_id, event_type, json.dumps(payload, ensure_ascii=False, sort_keys=True), now)
    )


def _compact_memory_text(memories: List[Dict[str, Any]]) -> str:
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
