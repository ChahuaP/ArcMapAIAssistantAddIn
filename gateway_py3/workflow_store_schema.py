from __future__ import annotations

import json
from typing import Any, Dict


WORKFLOW_COLUMNS = {
    "id",
    "status",
    "mode",
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
    "mode": "TEXT NOT NULL DEFAULT 'context_single'",
    "command": "TEXT NOT NULL DEFAULT ''",
    "context_hash": "TEXT NOT NULL DEFAULT ''",
    "workflow_json": "TEXT NOT NULL DEFAULT '{}'",
    "agent_trace_json": "TEXT NOT NULL DEFAULT '[]'",
    "created_at": "REAL NOT NULL DEFAULT 0",
    "updated_at": "REAL NOT NULL DEFAULT 0",
    "result_json": "TEXT"
}


def init_database(conn) -> None:
    validate_database_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflows (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            mode TEXT NOT NULL,
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_mode_updated ON workflows(mode, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workflows_status_updated ON workflows(status, updated_at)")
    drop_removed_tables(conn)
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


def validate_database_schema(conn) -> None:
    table = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'workflows'").fetchone()
    if table is None:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(workflows)").fetchall()}
    if columns != WORKFLOW_COLUMNS:
        raise RuntimeError("existing workflows.sqlite is incompatible with the GeoPilot run schema; remove it explicitly before starting GeoPilot.")
    legacy = conn.execute("SELECT 1 FROM workflows WHERE agent_trace_json NOT LIKE '%\"contract\": \"geopilot-run/v2\"%' LIMIT 1").fetchone()
    if legacy:
        raise RuntimeError("existing workflows.sqlite contains a legacy workflow record; remove it explicitly before starting GeoPilot.")


def drop_removed_tables(conn) -> None:
    for table in ("projects", "project_memories", "project_events"):
        conn.execute("DROP TABLE IF EXISTS %s" % table)


def workflow_row_to_dict(row, include_trace: bool = True) -> Dict[str, Any]:
    result = {
        "id": row[0],
        "status": row[1],
        "mode": row[2],
        "command": row[3],
        "context_hash": row[4],
        "workflow": json.loads(row[5]),
        "created_at": row[7],
        "updated_at": row[8],
        "result": json.loads(row[9]) if row[9] else None
    }
    if include_trace:
        result["agent_trace"] = json.loads(row[6])
    return result


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
