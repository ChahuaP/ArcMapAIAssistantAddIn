"""Append-only operation journal for the boundary server.

Every tool call leaves a complete fact row here: pre-check outcome, lease
triple, receipt, staged artifacts and errors. This is the authoritative
GIS-side evidence chain; the dsh session log covers the model side, and the
two correlate by run_id/op_id.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


class OpJournal:
    """SQLite journal; one writer lock, append-mostly."""

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            from .paths import localappdata_dir
            directory = localappdata_dir() / "boundary"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "journal.db"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS ops (
                op_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                tool TEXT NOT NULL,
                operation TEXT,
                arguments TEXT,
                precheck TEXT,
                context_digest TEXT,
                lease_id TEXT,
                epoch INTEGER,
                plan_hash TEXT,
                receipt_status TEXT,
                result TEXT,
                error TEXT,
                started_at REAL NOT NULL,
                finished_at REAL
            )""")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                op_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT,
                recorded_at REAL NOT NULL
            )""")
        self._db.commit()

    def start_op(self, tool: str, operation: str = "",
                 arguments: Optional[Dict[str, Any]] = None,
                 precheck: Optional[Dict[str, Any]] = None) -> str:
        op_id = "op-" + uuid.uuid4().hex[:12]
        with self._lock:
            self._db.execute(
                "INSERT INTO ops (op_id, run_id, tool, operation, arguments, precheck,"
                " context_digest, started_at) VALUES (?,?,?,?,?,?,?,?)",
                (op_id, op_id, tool, operation,
                 _dump(arguments), _dump(precheck), "", time.time()))
            self._db.commit()
        return op_id

    def bind_run(self, op_id: str, run_id: str, context_digest: str,
                 lease_id: str, epoch: int, plan_hash: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE ops SET run_id=?, context_digest=?, lease_id=?, epoch=?,"
                " plan_hash=? WHERE op_id=?", (run_id, context_digest, lease_id,
                                               epoch, plan_hash, op_id))
            self._db.commit()

    def finish_op(self, op_id: str, receipt_status: str = "",
                  result: Optional[Dict[str, Any]] = None,
                  error: str = "") -> None:
        with self._lock:
            self._db.execute(
                "UPDATE ops SET receipt_status=?, result=?, error=?, finished_at=?"
                " WHERE op_id=?", (receipt_status, _dump(result), error,
                                   time.time(), op_id))
            self._db.commit()

    def event(self, op_id: str, kind: str, payload: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO events (op_id, kind, payload, recorded_at)"
                " VALUES (?,?,?,?)", (op_id, kind, _dump(payload), time.time()))
            self._db.commit()

    def get_op(self, op_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT op_id, run_id, tool, operation, arguments, precheck,"
                " context_digest, lease_id, epoch, plan_hash, receipt_status,"
                " result, error, started_at, finished_at FROM ops WHERE op_id=?",
                (op_id,)).fetchone()
        return _row_to_doc(row) if row else None

    def recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT op_id, run_id, tool, operation, arguments, precheck,"
                " context_digest, lease_id, epoch, plan_hash, receipt_status,"
                " result, error, started_at, finished_at FROM ops"
                " ORDER BY started_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [_row_to_doc(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()


_COLUMNS = ("op_id", "run_id", "tool", "operation", "arguments", "precheck",
            "context_digest", "lease_id", "epoch", "plan_hash", "receipt_status",
            "result", "error", "started_at", "finished_at")


def _row_to_doc(row) -> Dict[str, Any]:
    document = dict(zip(_COLUMNS, row))
    for key in ("arguments", "precheck", "result"):
        if isinstance(document.get(key), str) and document[key]:
            try:
                document[key] = json.loads(document[key])
            except ValueError:
                pass
    return document


def _dump(value: Optional[Dict[str, Any]]) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
