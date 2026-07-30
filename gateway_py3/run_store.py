from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from .paths import data_dir
from .run_store_schema import (
    init_database,
    pending_tool_row_to_dict,
    run_row_to_dict,
)


DB_PATH = data_dir() / "runs.sqlite"
RUN_TRANSITIONS = {
    "running": {"planned", "failed", "cancelled", "clarify", "reject"},
    "planned": {"approved", "cancelled", "failed"},
    "approved": {"executing", "cancelled", "failed"},
    "executing": {"executed", "failed", "recovery_required"},
    "recovery_required": {"executing", "executed", "failed", "indeterminate"},
    "indeterminate": {"executed", "failed"},
    "executed": {"succeeded", "context_failed"},
    "succeeded": set(), "failed": set(), "context_failed": set(),
    "cancelled": set(), "clarify": set(), "reject": set(),
}
ACTIVE_RUN_STATUSES = {
    "running", "planned", "approved", "executing", "executed", "recovery_required"
}
PROTECTED_RUN_STATUSES = {"indeterminate"}
RECENT_RUN_LIMIT = 200
RECOVERY_WINDOW_SECONDS = 300.0


class RunStore:
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

    def create_run(self, command: str, mode: str) -> Dict[str, Any]:
        """Create the durable run before any model or ArcMap stage begins."""
        with self._connection() as conn:
            run_id = self._insert_run(conn, command, mode)
        return self.get(run_id)

    def create_run_for_target(self, command: str, mode: str, target: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically reject an uncertain target or create its durable episode."""
        target = _target_identity(target)
        target_key = _target_episode_key(target)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            quarantined = conn.execute(
                "SELECT 1 FROM target_episodes WHERE target_key = ? AND state = 'active' "
                "AND run_id IN (SELECT id FROM runs WHERE status IN ('recovery_required', 'indeterminate')) LIMIT 1",
                (target_key,),
            ).fetchone()
            if quarantined is not None:
                raise ValueError("ArcMap target is quarantined pending authoritative result; recover the interrupted episode first.")
            run_id = self._insert_run(conn, command, mode)
            now = time.time()
            conn.execute(
                "INSERT INTO target_episodes(run_id, target_key, target_json, state, created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?)",
                (run_id, target_key, json.dumps(target, ensure_ascii=False, sort_keys=True), now, now),
            )
        return self.get(run_id)

    @staticmethod
    def _insert_run(conn, command: str, mode: str) -> str:
        trace = {
            "contract": "geopilot-run/v2",
            "mode": mode,
            "context_hash": "",
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
        run_id = str(uuid.uuid4())
        now = time.time()
        conn.execute(
            """
            INSERT INTO runs
            (id, status, mode, command, context_hash, workflow_json,
             agent_trace_json, created_at, updated_at)
            VALUES (?, 'running', ?, ?, '', '{}', ?, ?, ?)
            """,
            (run_id, mode, command, json.dumps([{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True), now, now),
        )
        return run_id

    def bind_context(self, run_id: str, capture: Dict[str, Any]) -> Dict[str, Any]:
        """Bind the one planning snapshot atomically, before planning begins."""
        context = capture.get("context") if isinstance(capture, dict) else None
        if not isinstance(context, dict):
            raise ValueError("ArcMap context capture is invalid.")
        digest = capture.get("context_hash")
        if not isinstance(digest, str) or not digest:
            raise ValueError("captured context hash is required.")
        captured_at = capture.get("captured_at")
        window = capture.get("bridge")
        _target_identity(window)
        with self._connection() as conn:
            row = conn.execute("SELECT status, context_hash, agent_trace_json FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row[0] != "running" or row[1]:
                raise ValueError("run context can only be bound once before planning.")
            trace = json.loads(row[2])[0]["run"]
            fixed = {
                "context_hash": digest,
                "snapshot_hash": capture.get("snapshot_hash"),
                "captured_at": captured_at,
                "window": window,
            }
            trace["context_hash"] = digest
            trace["context"] = fixed
            trace.setdefault("context_captures", []).append(dict({"phase": "before_planning"}, **fixed))
            cursor = conn.execute(
                "UPDATE runs SET context_hash = ?, agent_trace_json = ?, updated_at = ? WHERE id = ? AND status = 'running' AND context_hash = ''",
                (digest, json.dumps([{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True), time.time(), run_id),
            )
        if cursor.rowcount != 1:
            raise ValueError("concurrent context binding rejected.")
        return self.get(run_id)

    def reserve_target_episode(self, run_id: str, target: Dict[str, Any]) -> Dict[str, Any]:
        target = _target_identity(target)
        now = time.time()
        target_key = _target_episode_key(target)
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO target_episodes(run_id, target_key, target_json, state, created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?)",
                (run_id, target_key, json.dumps(target, ensure_ascii=False, sort_keys=True), now, now),
            )
        return {"run_id": run_id, "target": target, "state": "queued"}

    def finalize_target_episode(self, run_id: str) -> None:
        row = self.get(run_id)
        if row["status"] in ("running", "planned", "approved", "succeeded", "context_failed", "failed", "cancelled", "clarify", "reject"):
            self.release_target_episode(run_id)

    def claim_target_episode(self, run_id: str) -> bool:
        now = time.time()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT sequence, target_key, state FROM target_episodes WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ValueError("target episode reservation is missing.")
            if row[2] == "active":
                return True
            if row[2] != "queued":
                return False
            head = conn.execute(
                "SELECT target_episodes.run_id, runs.status FROM target_episodes "
                "JOIN runs ON runs.id = target_episodes.run_id "
                "WHERE target_key = ? AND state != 'released' ORDER BY sequence LIMIT 1",
                (row[1],),
            ).fetchone()
            if head is None:
                return False
            if head[0] != run_id:
                if head[1] in ("recovery_required", "indeterminate"):
                    raise ValueError("ArcMap target is quarantined pending authoritative result; recover the interrupted episode first.")
                return False
            conn.execute(
                "UPDATE target_episodes SET state = 'active', updated_at = ? WHERE run_id = ? AND state = 'queued'",
                (now, run_id),
            )
            return True

    def release_target_episode(self, run_id: str) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE target_episodes SET state = 'released', updated_at = ? WHERE run_id = ? AND state != 'released'",
                (time.time(), run_id),
            )

    def run_trace(self, run_id: str) -> Dict[str, Any]:
        return _run_trace_from_row(self.get(run_id))

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
            "executed",
            "recovery_required",
            "indeterminate",
            "succeeded",
            "failed",
            "context_failed",
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
                UPDATE runs
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
        row = self.update_run(
            run_id,
            "failed",
            trace=trace,
            result={"error": trace["failure"]},
        )
        self.finalize_target_episode(run_id)
        return row

    def is_cancel_requested(self, run_id: str) -> bool:
        return self.get(run_id)["status"] == "cancelled"

    def get(self, run_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, status, mode, command, context_hash, workflow_json,
                       agent_trace_json, created_at, updated_at, result_json
                FROM runs
                WHERE id = ?
                """,
                (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return run_row_to_dict(row)

    def list_recent(
        self,
        limit: int = 50,
        mode: str | None = None,
        since: float | None = None,
        include_trace: bool = True
    ) -> List[Dict[str, Any]]:
        """Return one explicitly bounded UI/API page; internal scans use iter_runs."""
        limit = int(limit)
        if limit < 1 or limit > RECENT_RUN_LIMIT:
            raise ValueError("recent run limit must be between 1 and %d." % RECENT_RUN_LIMIT)
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
                FROM runs
                %s
                ORDER BY created_at DESC, id DESC LIMIT ?
                """ % where,
                params
            ).fetchall()
        return [run_row_to_dict(row, include_trace=include_trace) for row in rows]

    def iter_runs(
        self,
        mode: str | None = None,
        statuses: Sequence[str] | None = None,
        include_trace: bool = True,
        batch_size: int = 200,
    ) -> Iterator[Dict[str, Any]]:
        """Iterate every matching durable run with stable keyset pagination."""
        page_size = max(1, min(int(batch_size), 1000))
        normalized_statuses = tuple(dict.fromkeys(statuses or ()))
        cursor_created_at = None
        cursor_id = None
        while True:
            clauses = []
            params: List[Any] = []
            if mode:
                clauses.append("mode = ?")
                params.append(mode)
            if normalized_statuses:
                clauses.append("status IN (%s)" % ",".join("?" for _ in normalized_statuses))
                params.extend(normalized_statuses)
            if cursor_created_at is not None:
                clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
                params.extend((cursor_created_at, cursor_created_at, cursor_id))
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            params.append(page_size)
            with self._connection() as conn:
                rows = conn.execute(
                    """
                    SELECT id, status, mode, command, context_hash, workflow_json,
                           agent_trace_json, created_at, updated_at, result_json
                    FROM runs
                    %s
                    ORDER BY created_at DESC, id DESC LIMIT ?
                    """ % where,
                    params,
                ).fetchall()
            if not rows:
                return
            for row in rows:
                yield run_row_to_dict(row, include_trace=include_trace)
            cursor_created_at = rows[-1][7]
            cursor_id = rows[-1][0]

    def clear_runs(self, mode: str | None = None) -> Dict[str, Any]:
        with self._connection() as conn:
            undeletable = ACTIVE_RUN_STATUSES | PROTECTED_RUN_STATUSES
            clauses = ["status NOT IN (%s)" % ",".join("?" for _ in undeletable)]
            params = list(sorted(undeletable))
            if mode:
                clauses.append("mode = ?")
                params.append(mode)
            run_count = conn.execute(
                "DELETE FROM runs WHERE " + " AND ".join(clauses), params
            ).rowcount
            active_clauses = ["status IN (%s)" % ",".join("?" for _ in ACTIVE_RUN_STATUSES)]
            active_params = list(sorted(ACTIVE_RUN_STATUSES))
            if mode:
                active_clauses.append("mode = ?")
                active_params.append(mode)
            preserved_active = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE " + " AND ".join(active_clauses), active_params
            ).fetchone()[0]
            protected_clauses = ["status = 'indeterminate'"]
            protected_params: List[Any] = []
            if mode:
                protected_clauses.append("mode = ?")
                protected_params.append(mode)
            preserved_indeterminate = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE " + " AND ".join(protected_clauses),
                protected_params,
            ).fetchone()[0]
        return {
            "ok": True,
            "cleared": {
                "runs": run_count,
            },
            "preserved_active": preserved_active,
            "preserved_indeterminate": preserved_indeterminate,
        }

    def clear_state(self, key: str) -> Dict[str, Any]:
        with self._connection() as conn:
            conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
        return {"ok": True}

    def delete(self, run_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row[0] in ACTIVE_RUN_STATUSES:
                raise ValueError("active runs cannot be deleted.")
            if row[0] in PROTECTED_RUN_STATUSES:
                raise ValueError(
                    "indeterminate runs are protected until an authoritative result is received."
                )
            cursor = conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        if cursor.rowcount == 0:
            raise KeyError(run_id)
        return {"ok": True}

    def claim_for_execution(self, run_id: str, target: Dict[str, Any], owner_id: str, now: float | None = None) -> Dict[str, Any]:
        """Atomically let the bound ArcMap runtime claim one approved run."""
        normalized = _target_identity(target)
        if not isinstance(owner_id, str) or not owner_id:
            raise ValueError("execution owner_id is required.")
        claimed_at = time.time() if now is None else float(now)
        with self._connection() as conn:
            row = conn.execute(
                "SELECT status, agent_trace_json FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            trace = json.loads(row[1])[0]["run"]
            expected = _target_identity(trace.get("context", {}).get("window", {}))
            if normalized != expected:
                raise ValueError("ArcMap execution target does not match the bound planning target.")
            trace["execution_owner"] = {
                "owner_id": owner_id,
                "target": normalized,
                "started_at": claimed_at,
                "heartbeat_at": claimed_at,
            }
            cursor = conn.execute(
                "UPDATE runs SET status = ?, agent_trace_json = ?, updated_at = ? WHERE id = ? AND status = ?",
                ("executing", json.dumps([{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True), claimed_at, run_id, "approved"),
            )
        if cursor.rowcount != 1:
            raise ValueError("run is not approved for runtime claim.")
        return self.get(run_id)

    def heartbeat_execution(self, run_id: str, owner_id: str, now: float | None = None) -> Dict[str, Any]:
        heartbeat_at = time.time() if now is None else float(now)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, agent_trace_json, updated_at FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row[0] not in ("executing", "recovery_required"):
                raise ValueError("run is not recoverable by an ArcMap heartbeat.")
            trace = json.loads(row[1])[0]["run"]
            owner = trace.get("execution_owner") or {}
            if owner.get("owner_id") != owner_id:
                raise ValueError("execution owner does not match.")
            if row[0] == "recovery_required" and heartbeat_at >= _recovery_deadline(trace, row[2]):
                trace = _indeterminate_trace(trace, heartbeat_at)
                cursor = conn.execute(
                    """
                    UPDATE runs SET status = 'indeterminate', agent_trace_json = ?, updated_at = ?
                    WHERE id = ? AND status = 'recovery_required' AND agent_trace_json = ?
                    """,
                    (
                        json.dumps([{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True),
                        heartbeat_at,
                        run_id,
                        row[1],
                    ),
                )
                expired = cursor.rowcount == 1
            else:
                expired = False
            if not expired:
                owner["heartbeat_at"] = heartbeat_at
                trace["execution_owner"] = owner
                cursor = conn.execute(
                    "UPDATE runs SET status = 'executing', agent_trace_json = ?, updated_at = ? WHERE id = ? AND status = ?",
                    (json.dumps([{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True), heartbeat_at, run_id, row[0]),
                )
        if expired:
            raise ValueError("execution recovery window has expired.")
        if cursor.rowcount != 1:
            raise ValueError("concurrent execution heartbeat rejected.")
        return self.get(run_id)

    def recover_stale_executions(
        self,
        now: float | None = None,
        lease_seconds: float = 30.0,
        recovery_window_seconds: float = RECOVERY_WINDOW_SECONDS,
    ) -> List[str]:
        current = time.time() if now is None else float(now)
        recovered = []
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, agent_trace_json FROM runs WHERE status = 'executing'"
            ).fetchall()
            for run_id, stored_trace_json in rows:
                trace = json.loads(stored_trace_json)[0]["run"]
                owner = trace.get("execution_owner") or {}
                heartbeat_at = float(owner.get("heartbeat_at") or owner.get("started_at") or 0)
                if current - heartbeat_at <= float(lease_seconds):
                    continue
                trace["recovery"] = {
                    "required_at": current,
                    "deadline_at": current + float(recovery_window_seconds),
                    "reason": "ArcMap heartbeat lease expired before authoritative result acknowledgement",
                }
                recovered_trace_json = json.dumps(
                    [{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True
                )
                cursor = conn.execute(
                    """
                    UPDATE runs SET status = 'recovery_required', agent_trace_json = ?, updated_at = ?
                    WHERE id = ? AND status = 'executing' AND agent_trace_json = ?
                    """,
                    (recovered_trace_json, current, run_id, stored_trace_json),
                )
                if cursor.rowcount == 1:
                    recovered.append(run_id)
        return recovered

    def require_execution_recovery(
        self,
        run_id: str,
        reason: str,
        now: float | None = None,
        recovery_window_seconds: float = RECOVERY_WINDOW_SECONDS,
    ) -> Dict[str, Any]:
        required_at = time.time() if now is None else float(now)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, agent_trace_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row[0] != "executing":
                raise ValueError("only an executing run can require recovery.")
            trace = json.loads(row[1])[0]["run"]
            trace["recovery"] = {
                "required_at": required_at,
                "deadline_at": required_at + float(recovery_window_seconds),
                "reason": str(reason),
            }
            cursor = conn.execute(
                """
                UPDATE runs SET status = 'recovery_required', agent_trace_json = ?, updated_at = ?
                WHERE id = ? AND status = 'executing' AND agent_trace_json = ?
                """,
                (
                    json.dumps([{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True),
                    required_at,
                    run_id,
                    row[1],
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("concurrent recovery transition rejected.")
        return self.get(run_id)

    def resolve_expired_recoveries(
        self, now: float | None = None, recovery_window_seconds: float = RECOVERY_WINDOW_SECONDS
    ) -> List[str]:
        current = time.time() if now is None else float(now)
        resolved = []
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, agent_trace_json, updated_at FROM runs WHERE status = 'recovery_required'"
            ).fetchall()
            for run_id, stored_trace_json, updated_at in rows:
                trace = json.loads(stored_trace_json)[0]["run"]
                deadline = _recovery_deadline(trace, updated_at, recovery_window_seconds)
                if current < deadline:
                    continue
                trace = _indeterminate_trace(trace, current)
                cursor = conn.execute(
                    """
                    UPDATE runs SET status = 'indeterminate', agent_trace_json = ?, updated_at = ?
                    WHERE id = ? AND status = 'recovery_required' AND agent_trace_json = ?
                    """,
                    (
                        json.dumps([{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True),
                        current,
                        run_id,
                        stored_trace_json,
                    ),
                )
                if cursor.rowcount == 1:
                    resolved.append(run_id)
        return resolved

    def complete_execution(
        self,
        run_id: str,
        status: str,
        result: Dict[str, Any],
        owner_id: str,
        result_hash: str,
        target: Dict[str, Any],
    ) -> Dict[str, Any]:
        if status not in ("executed", "failed"):
            raise ValueError(status)
        normalized_target = _target_identity(target)
        if not isinstance(result, dict) or _result_hash(result) != result_hash:
            raise ValueError("execution result hash does not match result payload.")
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, agent_trace_json, updated_at FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            trace = json.loads(row[1])[0]["run"]
            owner = trace.get("execution_owner") or {}
            if owner.get("owner_id") != owner_id:
                raise ValueError("execution owner does not match.")
            if _target_identity(owner.get("target")) != normalized_target:
                raise ValueError("execution target does not match.")
            receipt = trace.get("execution_receipt")
            expected_receipt = {
                "owner_id": owner_id,
                "status": status,
                "result_hash": result_hash,
                "target": normalized_target,
            }
            if receipt is not None:
                if receipt != expected_receipt:
                    raise ValueError("conflicting execution completion replay.")
            elif row[0] not in ("executing", "recovery_required", "indeterminate"):
                raise ValueError("run is not executing in ArcMap.")
            if receipt is None:
                completed_at = time.time()
                after_deadline = (
                    row[0] == "recovery_required"
                    and completed_at >= _recovery_deadline(trace, row[2])
                )
                if after_deadline:
                    trace = _indeterminate_trace(trace, completed_at)
                if row[0] == "indeterminate" or after_deadline:
                    trace["recovered_after_indeterminate"] = {
                        "owner_id": owner_id,
                        "target": normalized_target,
                        "status": status,
                        "result_hash": result_hash,
                        "recovered_at": completed_at,
                    }
                trace["execution_receipt"] = expected_receipt
                cursor = conn.execute(
                    """
                    UPDATE runs
                    SET status = ?, result_json = ?, agent_trace_json = ?, updated_at = ?
                    WHERE id = ? AND status = ? AND agent_trace_json = ?
                    """,
                    (
                        status,
                        json.dumps(result, ensure_ascii=False, sort_keys=True),
                        json.dumps([{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True),
                        completed_at,
                        run_id,
                        row[0],
                        row[1],
                    ),
                )
                if cursor.rowcount != 1:
                    raise ValueError("concurrent execution completion rejected.")
        completed = self.get(run_id)
        if status == "failed":
            self.finalize_target_episode(run_id)
        return completed

    def claim_context_finalization(
        self, run_id: str, owner_id: str, now: float | None = None, lease_seconds: float = 45.0
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(owner_id, str) or not owner_id:
            raise ValueError("context finalizer owner_id is required.")
        claimed_at = time.time() if now is None else float(now)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, agent_trace_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row[0] != "executed":
                return None
            trace = json.loads(row[1])[0]["run"]
            current = trace.get("context_finalizer") or {}
            if current and claimed_at - float(current.get("claimed_at") or 0) <= lease_seconds:
                return None
            epoch = int(trace.get("context_finalizer_epoch") or 0) + 1
            fence = {"owner_id": owner_id, "epoch": epoch}
            trace["context_finalizer_epoch"] = epoch
            trace["context_finalizer"] = dict(fence, claimed_at=claimed_at)
            cursor = conn.execute(
                """
                UPDATE runs SET agent_trace_json = ?, updated_at = ?
                WHERE id = ? AND status = 'executed' AND agent_trace_json = ?
                """,
                (json.dumps([{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True), claimed_at, run_id, row[1]),
            )
        return fence if cursor.rowcount == 1 else None

    def context_finalizer_expired(self, run_id: str, now: float | None = None, lease_seconds: float = 45.0) -> bool:
        row = self.get(run_id)
        if row["status"] != "executed":
            return False
        current = self.run_trace(run_id).get("context_finalizer") or {}
        current_time = time.time() if now is None else float(now)
        return not current or current_time - float(current.get("claimed_at") or 0) > lease_seconds

    def reset_context_finalizers(self) -> None:
        for row in self.iter_runs(statuses=("executed",), include_trace=True):
            trace = _run_trace_from_row(row)
            finalizer = trace.get("context_finalizer")
            if finalizer is not None:
                finalizer["claimed_at"] = 0
                self.update_run(row["id"], "executed", trace=trace)

    def update_context_finalization(
        self, run_id: str, fence: Dict[str, Any], trace: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self._write_context_finalization(run_id, fence, "executed", trace)

    def complete_context_sync(
        self, run_id: str, fence: Dict[str, Any], trace: Dict[str, Any]
    ) -> Dict[str, Any]:
        row = self._write_context_finalization(run_id, fence, "succeeded", trace)
        self.finalize_target_episode(run_id)
        return row

    def fail_context_sync(
        self, run_id: str, fence: Dict[str, Any], exc: Exception, trace: Dict[str, Any]
    ) -> Dict[str, Any]:
        trace = dict(trace)
        trace["failure"] = {
            "stage": "context_after_execution",
            "type": type(exc).__name__,
            "summary": "post-execution context capture failed",
        }
        row = self._write_context_finalization(run_id, fence, "context_failed", trace)
        self.finalize_target_episode(run_id)
        return row

    def _write_context_finalization(
        self, run_id: str, fence: Dict[str, Any], status: str, trace: Dict[str, Any]
    ) -> Dict[str, Any]:
        expected = _finalizer_fence(fence)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, agent_trace_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            current_trace = json.loads(row[1])[0]["run"]
            if row[0] != "executed" or _finalizer_fence(current_trace.get("context_finalizer")) != expected:
                raise ValueError("context finalizer fence is no longer current.")
            trace = dict(trace)
            trace["context_finalizer"] = current_trace["context_finalizer"]
            trace["context_finalizer_epoch"] = current_trace.get("context_finalizer_epoch")
            cursor = conn.execute(
                """
                UPDATE runs SET status = ?, agent_trace_json = ?, updated_at = ?
                WHERE id = ? AND status = 'executed' AND agent_trace_json = ?
                """,
                (
                    status,
                    json.dumps([{"type": "run", "run": trace}], ensure_ascii=False, sort_keys=True),
                    time.time(),
                    run_id,
                    row[1],
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("context finalizer fence is no longer current.")
        return self.get(run_id)

    def cancel(self, run_id: str) -> Dict[str, Any]:
        row = self.get(run_id)
        if row["status"] in ("executing", "executed", "recovery_required"):
            raise ValueError("an executing ArcMap run cannot be cancelled because the runtime has no cancellation protocol.")
        if row["status"] not in ("running", "planned", "approved"):
            raise ValueError("run is already terminal.")
        row = self.update_run(run_id, "cancelled")
        self.finalize_target_episode(run_id)
        return row

    def export_runs(self, mode: str | None = None) -> Dict[str, Any]:
        runs = []
        for row in self.iter_runs(mode=mode, include_trace=True):
            trace = row.get("agent_trace") or []
            if len(trace) == 1 and trace[0].get("type") == "run":
                bound = bool(row["context_hash"])
                runs.append({"id": row["id"], "status": row["status"], "mode": row["mode"], "command": row["command"], "context_hash": row["context_hash"], "context": {"bound": bound}, "trace": trace[0]["run"], "result": row["result"]})
        eligible = [
            item for item in runs
            if item["context"]["bound"]
            and item["status"] in ("succeeded", "failed", "context_failed")
        ]
        return {
            "contract": "geopilot-report/v1",
            "runs": runs,
            "statistics": {
                "eligible_runs": len(eligible),
                "succeeded": sum(item["status"] == "succeeded" for item in eligible),
                "failed": sum(item["status"] in ("failed", "context_failed") for item in eligible),
                "indeterminate": sum(item["status"] == "indeterminate" for item in runs),
            },
        }

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

    def consume_context_sync(
        self,
        sync_token: str,
        run_id: str,
        phase: str,
        target: Dict[str, Any],
        context: Dict[str, Any],
    ) -> None:
        if phase not in ("before_planning", "after_execution"):
            raise ValueError("invalid ArcMap context sync phase.")
        key = "arcmap_context_sync:" + sync_token
        normalized_target = _target_identity(target)
        expected_status = "running" if phase == "before_planning" else "executed"
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state_row = conn.execute(
                "SELECT value_json FROM app_state WHERE key = ?", (key,)
            ).fetchone()
            run_row = conn.execute(
                "SELECT status, agent_trace_json FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            pending = json.loads(state_row[0]) if state_row else {}
            current_finalizer = None
            if run_row and phase == "after_execution":
                current_trace = json.loads(run_row[1])[0]["run"]
                current_finalizer = _finalizer_fence(current_trace.get("context_finalizer"))
            if (
                not state_row
                or not run_row
                or run_row[0] != expected_status
                or pending.get("consumed")
                or pending.get("run_id") != run_id
                or pending.get("phase") != phase
                or pending.get("bridge") != normalized_target
                or (
                    phase == "after_execution"
                    and _finalizer_fence(pending.get("finalizer")) != current_finalizer
                )
            ):
                raise ValueError("unexpected ArcMap context callback.")
            pending["context"] = context
            pending["consumed"] = True
            conn.execute(
                "UPDATE app_state SET value_json = ?, updated_at = ? WHERE key = ?",
                (json.dumps(pending, ensure_ascii=False, sort_keys=True), time.time(), key),
            )

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


def _target_identity(value: Dict[str, Any]) -> Dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("ArcMap target identity is required.")
    identity = {name: int(value.get(name) or 0) for name in ("bridge_pid", "bridge_port", "arcmap_pid", "hwnd")}
    if any(item <= 0 for item in identity.values()):
        raise ValueError("ArcMap target identity requires bridge_pid, bridge_port, arcmap_pid and hwnd.")
    return identity


def _target_episode_key(target: Dict[str, int]) -> str:
    return "%d:%d" % (target["arcmap_pid"], target["hwnd"])


def _result_hash(result: Dict[str, Any]) -> str:
    payload = json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _finalizer_fence(value: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("context finalizer fence is required.")
    owner_id = value.get("owner_id")
    epoch = value.get("epoch")
    if not isinstance(owner_id, str) or not owner_id or not isinstance(epoch, int) or epoch <= 0:
        raise ValueError("context finalizer fence is invalid.")
    return {"owner_id": owner_id, "epoch": epoch}


def _recovery_deadline(
    trace: Dict[str, Any], updated_at: float, recovery_window_seconds: float = RECOVERY_WINDOW_SECONDS
) -> float:
    recovery = trace.get("recovery") or {}
    deadline = float(recovery.get("deadline_at") or 0)
    if deadline > 0:
        return deadline
    return float(updated_at) + float(recovery_window_seconds)


def _indeterminate_trace(trace: Dict[str, Any], resolved_at: float) -> Dict[str, Any]:
    owner = trace.get("execution_owner") or {}
    trace["indeterminate"] = {
        "owner_id": owner.get("owner_id"),
        "target": owner.get("target"),
        "last_heartbeat_at": owner.get("heartbeat_at") or owner.get("started_at"),
        "resolved_at": resolved_at,
        "reason": "authoritative ArcMap result unavailable after recovery deadline",
    }
    return trace


def _run_trace_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    trace = row.get("agent_trace") or []
    if len(trace) != 1 or trace[0].get("type") != "run":
        raise ValueError("not a run.")
    return trace[0]["run"]
