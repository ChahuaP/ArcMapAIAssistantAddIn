from __future__ import annotations

import json
from typing import Any, Dict


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


def init_database(conn) -> None:
    migrate_workflows_schema(conn)
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


def migrate_workflows_schema(conn) -> None:
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


def workflow_row_to_dict(row) -> Dict[str, Any]:
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


def project_row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "workdir": row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }


def pending_tool_row_to_dict(row) -> Dict[str, Any]:
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
