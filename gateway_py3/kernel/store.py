"""JournalStore: SQLite event source + state projection for GeoPilotKernel.

Target architecture §7. ``run_events`` is the append-only source of truth;
``runs`` is a transaction-updated state projection. The UI event stream is a
projection of the journal, never the source of state.

Old databases are rejected on sight (§7, §2): no migration, no fallback. If a
legacy ``runs.sqlite`` (the v1 schema with ``pending_tools``/``target_episodes``
or the v2 ``agent_trace_json`` column) is found, startup raises and instructs
the operator to remove it.
"""
from __future__ import annotations

import json
import logging
import hashlib
import sqlite3
import time
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from . import contracts
from .contracts import (
    AUTHORIZATION_REQUIRED,
    PUBLISHED,
    RECEIVED,
    RUN_TRANSITIONS,
    ACTIVE_RUN_STAGES,
    TERMINAL_STAGES,
    PAUSED_STAGES,
    is_valid_transition,
    stage_for_outcome,
)
from ..paths import data_dir
from ..secure_storage import protect_json, unprotect_json


GEOPILOT_DB_PATH = data_dir() / "geopilot.sqlite"
SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_JOURNAL_MODE = "wal"
LOGGER = logging.getLogger(__name__)


class QuotaStoppedError(Exception):
    """A model call previously hit a quota stop; it must never be retried."""


class UncertainCallError(Exception):
    """A model call previously finished uncertain; it needs human adjudication."""

SCHEMA_VERSION = 12
SCHEMA_MARKER = "geopilot-journal-v12-dpapi-chain-full-projection-anchor"

# Legacy table/column names that mark an incompatible old database. If any are
# present, the store refuses to start (§7: no migration).
LEGACY_TABLES = (
    "pending_tools", "target_episodes", "app_state", "workflows",
    "projects", "project_memories", "project_events",
)
LEGACY_RUNS_COLUMNS = {"agent_trace_json", "workflow_json", "context_hash"}


def _now() -> float:
    return time.time()


def _canonical_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_dumps(value: Any) -> str:
    return protect_json(value)


def _json_loads(value: str) -> Any:
    return unprotect_json(value)


def _encrypted_json_dumps(value: Any) -> str:
    return _json_dumps(value)


def _encrypted_json_loads(value: str) -> Any:
    return _json_loads(value)


def _event_chain_hash(previous_hash: str, run_id: str, kind: str, stage: str,
                      encrypted_payload: str, recorded_at: float) -> str:
    document = {
        "previous_hash": previous_hash, "run_id": run_id, "kind": kind,
        "stage": stage, "payload": encrypted_payload, "recorded_at": recorded_at,
    }
    return hashlib.sha256(_canonical_json_dumps(document).encode("utf-8")).hexdigest()


def _projection_digest(row: Sequence[Any]) -> str:
    document = {
        "session_id": row[0], "request_id": row[1], "tenant_id": row[2],
        "stage": row[3], "outcome_kind": row[4], "outcome_json": row[5],
        "text": row[6], "execute": row[7], "created_at": row[8],
        "updated_at": row[9],
    }
    return hashlib.sha256(_canonical_json_dumps(document).encode("utf-8")).hexdigest()


# --- schema ----------------------------------------------------------------

def _create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            closed_at REAL
        );

        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            outcome_kind TEXT,
            outcome_json TEXT,
            text TEXT NOT NULL,
            execute INTEGER NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        );
        CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_runs_stage ON runs(stage, updated_at);
        CREATE INDEX IF NOT EXISTS idx_runs_tenant_updated ON runs(tenant_id, updated_at);

        CREATE TABLE IF NOT EXISTS run_events (
            event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            stage TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            recorded_at REAL NOT NULL,
            previous_hash TEXT NOT NULL,
            chain_hash TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_run_events_run ON run_events(run_id, event_seq);
        CREATE INDEX IF NOT EXISTS idx_run_events_kind ON run_events(kind, recorded_at);
        CREATE TABLE IF NOT EXISTS run_event_anchors (
            run_id TEXT PRIMARY KEY,
            anchor_json TEXT NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS planning_node_updates (
            run_id TEXT NOT NULL, node TEXT NOT NULL, attempt INTEGER NOT NULL,
            status TEXT NOT NULL, update_json TEXT NOT NULL, recorded_at REAL NOT NULL,
            PRIMARY KEY (run_id, node, attempt), FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS experiment_pair_baselines (
            pair_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, content_hash TEXT NOT NULL,
            planning_context_hash TEXT NOT NULL,
            intent_digest TEXT NOT NULL, context_digest TEXT NOT NULL,
            capability_digest TEXT NOT NULL, task_contract_json TEXT NOT NULL,
            context_json TEXT NOT NULL, intent_json TEXT NOT NULL, capability_json TEXT NOT NULL,
            binding_json TEXT NOT NULL,
            baseline_digest TEXT NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL, created_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS planning_context_snapshots (
            run_id TEXT PRIMARY KEY,
            digest TEXT NOT NULL,
            lease_id TEXT NOT NULL,
            document_json TEXT NOT NULL,
            captured_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS captured_context_snapshots (
            run_id TEXT PRIMARY KEY, document_json TEXT NOT NULL, captured_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_planning_context_snapshots_digest ON planning_context_snapshots(digest);

        CREATE TABLE IF NOT EXISTS capability_snapshots (
            run_id TEXT PRIMARY KEY,
            digest TEXT NOT NULL,
            document_json TEXT NOT NULL,
            frozen_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_capability_snapshots_digest ON capability_snapshots(digest);

        CREATE TABLE IF NOT EXISTS intent_specs (
            run_id TEXT PRIMARY KEY,
            digest TEXT NOT NULL,
            document_json TEXT NOT NULL,
            compiled_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_intent_specs_digest ON intent_specs(digest);

        CREATE TABLE IF NOT EXISTS verified_plans (
            run_id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL,
            digest TEXT NOT NULL,
            version INTEGER NOT NULL,
            document_json TEXT NOT NULL,
            sealed_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_verified_plans_digest ON verified_plans(digest);

        CREATE TABLE IF NOT EXISTS model_calls (
            call_key TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            response_json TEXT,
            ledger_json TEXT NOT NULL,
            reserved_at REAL NOT NULL,
            committed_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_model_calls_run ON model_calls(run_id);
        CREATE INDEX IF NOT EXISTS idx_model_calls_status ON model_calls(status, committed_at);

        CREATE TABLE IF NOT EXISTS model_call_bindings (
            call_key TEXT NOT NULL,
            run_id TEXT NOT NULL,
            bound_at REAL NOT NULL,
            PRIMARY KEY (call_key, run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_model_call_bindings_run ON model_call_bindings(run_id);

        CREATE TABLE IF NOT EXISTS runtime_leases (
            run_id TEXT PRIMARY KEY,
            lease_id TEXT NOT NULL,
            plan_digest TEXT NOT NULL,
            epoch INTEGER NOT NULL,
            document_json TEXT NOT NULL,
            acquired_at REAL NOT NULL,
            last_heartbeat REAL NOT NULL,
            released INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_runtime_leases_active ON runtime_leases(released, last_heartbeat);

        CREATE TABLE IF NOT EXISTS authorization_grants (
            run_id TEXT PRIMARY KEY,
            grant_id TEXT NOT NULL,
            plan_digest TEXT NOT NULL,
            document_json TEXT NOT NULL,
            granted_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS execution_receipts (
            run_id TEXT PRIMARY KEY,
            receipt_id TEXT NOT NULL,
            lease_id TEXT NOT NULL,
            plan_digest TEXT NOT NULL,
            status TEXT NOT NULL,
            result_hash TEXT NOT NULL,
            document_json TEXT NOT NULL,
            received_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            run_id TEXT NOT NULL,
            output_id TEXT NOT NULL,
            identity_json TEXT NOT NULL,
            staged INTEGER NOT NULL DEFAULT 0,
            published INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            PRIMARY KEY (run_id, output_id),
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts(run_id, staged, published);

        CREATE TABLE IF NOT EXISTS acceptance_reports (
            run_id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL,
            plan_digest TEXT NOT NULL,
            passed INTEGER NOT NULL,
            document_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );

        CREATE TABLE IF NOT EXISTS publication_receipts (
            run_id TEXT PRIMARY KEY,
            publication_id TEXT NOT NULL,
            grant_id TEXT NOT NULL,
            document_json TEXT NOT NULL,
            published_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        CREATE TABLE IF NOT EXISTS publication_prepared (
            run_id TEXT PRIMARY KEY,
            publication_id TEXT NOT NULL,
            document_json TEXT NOT NULL,
            prepared_at REAL NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs(run_id)
        );
        """
    )


def _validate_schema(conn: sqlite3.Connection) -> None:
    """Refuse incompatible old databases (§7). No migration, no fallback."""
    placeholders = ",".join("?" for _ in LEGACY_TABLES)
    legacy = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (%s)" % placeholders,
        LEGACY_TABLES,
    ).fetchall()
    if legacy:
        names = ", ".join(sorted(row[0] for row in legacy))
        raise RuntimeError(
            "existing database contains legacy GeoPilot tables (%s); "
            "remove it explicitly before starting GeoPilot." % names
        )
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
    ).fetchone()
    if table is None:
        return
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if columns & LEGACY_RUNS_COLUMNS:
        raise RuntimeError(
            "existing runs table uses the legacy GeoPilot v2 schema; "
            "remove it explicitly before starting GeoPilot."
        )
    artifact_columns = {row[1] for row in conn.execute("PRAGMA table_info(artifacts)").fetchall()}
    if artifact_columns and ("path" in artifact_columns or "document_json" in artifact_columns):
        raise RuntimeError("existing artifacts table uses legacy path/document schema; remove database explicitly")
    prepared_columns = {row[1] for row in conn.execute("PRAGMA table_info(publication_prepared)").fetchall()}
    if "manifest_json" in prepared_columns or "target_unit_path" in prepared_columns:
        raise RuntimeError("existing publication_prepared table uses legacy plaintext schema; remove database explicitly")
    node_columns = {row[1] for row in conn.execute("PRAGMA table_info(planning_node_updates)").fetchall()}
    if node_columns and "update_json" not in node_columns:
        raise RuntimeError("existing planning_node_updates table lacks update facts; remove database explicitly")
    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(run_events)").fetchall()}
    if event_columns and not {"previous_hash", "chain_hash"}.issubset(event_columns):
        raise RuntimeError("existing run_events table lacks the DPAPI chain contract; remove database explicitly")
    anchor_table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='run_event_anchors'"
    ).fetchone()
    if table is not None and anchor_table is None:
        raise RuntimeError("existing database lacks run event anchors; remove database explicitly")
    marker = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_marker'"
    ).fetchone()
    if marker is None:
        raise RuntimeError(
            "existing database is not a GeoPilot journal; "
            "remove it explicitly before starting GeoPilot."
        )
    if marker[0] != SCHEMA_MARKER:
        raise RuntimeError(
            "existing database schema marker is %r, expected %r; "
            "remove it explicitly before starting GeoPilot." % (marker[0], SCHEMA_MARKER)
        )


class JournalStore:
    """Append-only journal + state projection (§7).

    The kernel is the only writer. Every state transition appends a
    ``run_events`` row and updates the ``runs`` projection in the same SQLite
    transaction (§6.1). Snapshots of sealed contracts are stored once and
    referenced by digest.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else GEOPILOT_DB_PATH
        self._event_listeners: list = []
        self._io_lock = threading.RLock()
        self._init()

    def add_event_listener(self, listener) -> None:
        """Register a listener called after each ``append_event``.

        The listener receives ``(event_seq, run_id, kind, stage, payload)``.
        Used by the SSE projection so it wakes without monkey-patching.
        """
        self._event_listeners.append(listener)

    # -- connection ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0)
        conn.execute("PRAGMA busy_timeout = %d" % SQLITE_BUSY_TIMEOUT_MS)
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._io_lock:
            conn = self._connect()
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    def _init(self) -> None:
        conn = sqlite3.connect(str(self.path), timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0)
        try:
            conn.execute("PRAGMA busy_timeout = %d" % SQLITE_BUSY_TIMEOUT_MS)
            journal_mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]
            if str(journal_mode).lower() != SQLITE_JOURNAL_MODE:
                raise RuntimeError("geopilot.sqlite must use WAL journal mode.")
            conn.execute("PRAGMA synchronous = NORMAL")
            _validate_schema(conn)
            with conn:
                _create_tables(conn)
                conn.execute(
                    "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_marker', ?)",
                    (SCHEMA_MARKER,),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
            self._verify_all_run_facts(conn)
        finally:
            conn.close()

    @staticmethod
    def _verify_all_event_chains(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT run_id, kind, stage, payload_json, recorded_at, previous_hash, chain_hash "
            "FROM run_events ORDER BY run_id, event_seq"
        ).fetchall()
        previous_by_run: Dict[str, str] = {}
        for run_id, kind, stage, payload, recorded_at, previous_hash, chain_hash in rows:
            expected_previous = previous_by_run.get(run_id, "")
            expected_hash = _event_chain_hash(expected_previous, run_id, kind, stage, payload, recorded_at)
            if previous_hash != expected_previous or chain_hash != expected_hash:
                raise RuntimeError("run_events hash chain verification failed; database was modified or corrupted")
            _encrypted_json_loads(payload)
            previous_by_run[run_id] = chain_hash

    def _verify_run_event_chain(self, conn: sqlite3.Connection, run_id: str) -> None:
        rows = conn.execute(
            "SELECT kind, stage, payload_json, recorded_at, previous_hash, chain_hash "
            "FROM run_events WHERE run_id=? ORDER BY event_seq", (run_id,)
        ).fetchall()
        previous_hash = ""
        for kind, stage, payload, recorded_at, stored_previous, chain_hash in rows:
            expected_hash = _event_chain_hash(previous_hash, run_id, kind, stage, payload, recorded_at)
            if stored_previous != previous_hash or chain_hash != expected_hash:
                raise RuntimeError("run_events hash chain verification failed; database was modified or corrupted")
            _encrypted_json_loads(payload)
            previous_hash = chain_hash

    def _verify_all_run_facts(self, conn: sqlite3.Connection) -> None:
        self._verify_all_event_chains(conn)
        self._verify_all_event_anchors(conn)

    @staticmethod
    def _verify_all_event_anchors(conn: sqlite3.Connection) -> None:
        for row in conn.execute("SELECT run_id FROM runs").fetchall():
            JournalStore._verify_run_anchor(conn, row[0])

    @staticmethod
    def _verify_run_anchor(conn: sqlite3.Connection, run_id: str) -> None:
        projection = conn.execute(
            "SELECT session_id, request_id, tenant_id, stage, outcome_kind, outcome_json, text, execute, "
            "created_at, updated_at "
            "FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if projection is None:
            raise KeyError(run_id)
        anchor_row = conn.execute(
            "SELECT anchor_json FROM run_event_anchors WHERE run_id=?", (run_id,)
        ).fetchone()
        if anchor_row is None:
            raise RuntimeError("run event anchor is missing; database was modified or corrupted")
        anchor = _json_loads(anchor_row[0])
        events = conn.execute(
            "SELECT chain_hash, payload_json FROM run_events WHERE run_id=? ORDER BY event_seq",
            (run_id,),
        ).fetchall()
        if not events:
            raise RuntimeError("run has no event facts; database was modified or corrupted")
        payload = _encrypted_json_loads(events[-1][1])
        try:
            outcome = _json_loads(projection[5]) if projection[5] else None
        except ValueError as exc:
            raise RuntimeError("run projection outcome cannot be decrypted; database was modified or corrupted") from exc
        expected = {
            "event_count": len(events), "head_hash": events[-1][0],
            "projection_digest": _projection_digest(projection),
        }
        if anchor != expected or payload.get("projected_stage") != projection[3] or payload.get("outcome_kind") != projection[4]:
            raise RuntimeError(
                "run event anchor verification failed; database was modified or corrupted "
                "(anchor=%r expected=%r event_stage=%r run_stage=%r event_outcome=%r run_outcome=%r)"
                % (anchor, expected, payload.get("projected_stage"), projection[3],
                   payload.get("outcome_kind"), projection[4])
            )
        if payload.get("outcome") != outcome:
            raise RuntimeError("run event outcome does not match its projection")

    def _write_event_anchor_locked(self, conn: sqlite3.Connection, run_id: str) -> None:
        count = conn.execute("SELECT COUNT(*) FROM run_events WHERE run_id=?", (run_id,)).fetchone()[0]
        row = conn.execute(
            "SELECT chain_hash FROM run_events WHERE run_id=? ORDER BY event_seq DESC LIMIT 1", (run_id,)
        ).fetchone()
        if not count or row is None or not row[0]:
            raise RuntimeError("cannot anchor a run without journal events")
        projection = conn.execute(
            "SELECT session_id, request_id, tenant_id, stage, outcome_kind, outcome_json, text, execute, "
            "created_at, updated_at "
            "FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if projection is None:
            raise KeyError(run_id)
        anchor = {"event_count": count, "head_hash": row[0],
                  "projection_digest": _projection_digest(projection)}
        conn.execute(
            "INSERT OR REPLACE INTO run_event_anchors(run_id, anchor_json, updated_at) VALUES (?, ?, ?)",
            (run_id, _json_dumps(anchor), _now()),
        )

    # -- sessions (§5) ------------------------------------------------------

    def create_session(self, session_id: str, tenant_id: str) -> Dict[str, Any]:
        now = _now()
        with self._connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sessions(session_id, tenant_id, created_at) VALUES (?, ?, ?)",
                (session_id, tenant_id, now),
            )
        return {"session_id": session_id, "tenant_id": tenant_id, "created_at": now}

    def session_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Return the conversation messages for one session only.

        §5.1: a new task's model input reads only the current session_id's
        records. History is retained for audit but never crosses sessions.
        """
        with self._connection() as conn:
            self._verify_all_run_facts(conn)
            run_ids = [row[0] for row in conn.execute(
                "SELECT run_id FROM runs WHERE session_id=?", (session_id,)
            ).fetchall()]
            for run_id in run_ids:
                self._verify_run_event_chain(conn, run_id)
                self._verify_run_anchor(conn, run_id)
            rows = conn.execute(
                "SELECT run_id, kind, stage, payload_json, recorded_at FROM run_events "
                "WHERE run_id IN (SELECT run_id FROM runs WHERE session_id = ?) "
                "AND kind IN ('user_text', 'assistant_message') "
                "ORDER BY event_seq",
                (session_id,),
            ).fetchall()
        return [
            {"kind": row[1], "stage": row[2], "payload": _encrypted_json_loads(row[3]), "recorded_at": row[4]}
            for row in rows
        ]

    # -- runs + events (§6.1, §7) -------------------------------------------

    def create_run(self, request: contracts.RequestEnvelope) -> Dict[str, Any]:
        """Create the durable run and the first journal event (§6.1 received)."""
        run_id = str(uuid.uuid4())
        now = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO runs(run_id, session_id, request_id, tenant_id, stage,
                                  outcome_kind, outcome_json, text, execute,
                                  created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
                """,
                (run_id, request.session_id, request.request_id, request.caller.tenant_id,
                 RECEIVED, protect_json(request.text), 1 if request.execute else 0, now, now),
            )
            self._append_event_locked(
                conn, run_id, "run_received", RECEIVED,
                {"text": request.text, "execute": request.execute,
                 "caller": _caller_document(request.caller),
                 "side_effects": _side_effects_document(request.side_effects),
                 "inputs": list(request.inputs),
                 "target_selector": request.target_selector.model_dump(mode="json"),
                 "experiment": request.experiment.model_dump(mode="json") if request.experiment else None,
                 "projected_stage": RECEIVED},
                now,
            )
            self._write_event_anchor_locked(conn, run_id)
        return self.get_run(run_id)

    def append_event(self, run_id: str, kind: str, stage: str,
                     payload: Dict[str, Any],
                     outcome: Optional[contracts.Outcome] = None,
                     now: Optional[float] = None,
                     clear_outcome: bool = False) -> Dict[str, Any]:
        """Append one journal event and advance the run projection.

        Validates the stage transition (§6.1) and, if ``outcome`` is set,
        projects the run to its terminal/paused stage (§4.8). State change and
        event append share one transaction.

        ``clear_outcome`` clears any previously-set outcome_kind/outcome_json
        (used when a paused run resumes — e.g. ExecutionIndeterminate
        reconciled to EXECUTED — so the lifecycle driver does not stop on the stale
        outcome).

        Returns the run row with an extra ``_event_seq`` key for SSE.
        """
        recorded_at = _now() if now is None else float(now)
        event_payload = dict(payload)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT stage FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise KeyError(run_id)
            current_stage = row[0]
            if current_stage in TERMINAL_STAGES:
                raise ValueError("terminal run cannot append events: %s" % current_stage)
            if outcome is not None:
                target_stage = stage_for_outcome(outcome)
                event_payload["outcome_kind"] = outcome.kind
                event_payload["outcome"] = _outcome_document(outcome)
            else:
                target_stage = stage
                if current_stage != target_stage and not is_valid_transition(current_stage, target_stage):
                    raise ValueError(
                        "invalid run transition: %s -> %s" % (current_stage, target_stage)
                    )
            event_payload["projected_stage"] = target_stage
            event_seq = self._append_event_locked(conn, run_id, kind, stage, event_payload, recorded_at)
            if outcome is not None:
                conn.execute(
                    """
                    UPDATE runs SET stage = ?, outcome_kind = ?, outcome_json = ?, updated_at = ?
                    WHERE run_id = ?
                    """,
                    (target_stage, outcome.kind, _json_dumps(_outcome_document(outcome)),
                     recorded_at, run_id),
                )
            elif clear_outcome:
                conn.execute(
                    "UPDATE runs SET stage = ?, outcome_kind = NULL, outcome_json = NULL, updated_at = ? "
                    "WHERE run_id = ?",
                    (stage, recorded_at, run_id),
                )
            else:
                conn.execute(
                    "UPDATE runs SET stage = ?, updated_at = ? WHERE run_id = ?",
                    (stage, recorded_at, run_id),
                )
            self._write_event_anchor_locked(conn, run_id)
        result = self.get_run(run_id)
        result["_event_seq"] = event_seq
        self._notify_listeners(event_seq, run_id, kind, stage, event_payload)
        return result

    def get_planning_node_update(self, run_id: str, node: str, attempt: int) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT status, update_json FROM planning_node_updates WHERE run_id=? AND node=? AND attempt=?", (run_id, node, attempt)).fetchone()
        return None if row is None else {"status": row[0], "update": _json_loads(row[1])}

    def record_planning_node_update(self, run_id: str, node: str, attempt: int,
                                    status: str, update: Dict[str, Any]) -> None:
        """Write each LangGraph node fact once, keyed by run/node/attempt."""
        now = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT status, update_json FROM planning_node_updates WHERE run_id=? AND node=? AND attempt=?", (run_id,node,attempt)).fetchone()
            if existing is not None:
                if existing[0] != status or _json_loads(existing[1]) != update:
                    raise ValueError("planning node fact already exists and differs")
                return
            inserted = conn.execute(
                "INSERT INTO planning_node_updates(run_id,node,attempt,status,update_json,recorded_at) VALUES (?,?,?,?,?,?)",
                (run_id, node, attempt, status, _json_dumps(update), now),
            ).rowcount
            if inserted:
                row = conn.execute("SELECT stage FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                if row is None:
                    raise KeyError(run_id)
                event_seq = self._append_event_locked(conn, run_id, "planning.node_update", row[0],
                                                      {"node": node, "attempt": attempt, "status": status,
                                                       "projected_stage": row[0], "outcome_kind": None}, now)
                self._write_event_anchor_locked(conn, run_id)
        if inserted:
            self._notify_listeners(event_seq, run_id, "planning.node_update", row[0],
                                   {"node": node, "attempt": attempt, "status": status,
                                    "projected_stage": row[0], "outcome_kind": None})

    def _append_event_locked(self, conn: sqlite3.Connection, run_id: str, kind: str,
                             stage: str, payload: Dict[str, Any],
                             recorded_at: float) -> int:
        encrypted_payload = _encrypted_json_dumps(payload)
        row = conn.execute(
            "SELECT chain_hash FROM run_events WHERE run_id=? ORDER BY event_seq DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        previous_hash = row[0] if row else ""
        chain_hash = _event_chain_hash(previous_hash, run_id, kind, stage, encrypted_payload, recorded_at)
        cursor = conn.execute(
            """
            INSERT INTO run_events(run_id, kind, stage, payload_json, recorded_at, previous_hash, chain_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, kind, stage, encrypted_payload, recorded_at, previous_hash, chain_hash),
        )
        return cursor.lastrowid

    def get_run(self, run_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            self._verify_run_event_chain(conn, run_id)
            self._verify_run_anchor(conn, run_id)
            row = conn.execute(
                """
                SELECT run_id, session_id, request_id, tenant_id, stage,
                       outcome_kind, outcome_json, text, execute,
                       created_at, updated_at
                FROM runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return _run_row_to_dict(row)

    def list_recent_runs(self, session_id: Optional[str] = None,
                         limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        with self._connection() as conn:
            self._verify_all_run_facts(conn)
            if session_id:
                rows = conn.execute(
                    """
                    SELECT run_id, session_id, request_id, tenant_id, stage,
                           outcome_kind, outcome_json, text, execute,
                           created_at, updated_at
                    FROM runs WHERE session_id = ?
                    ORDER BY created_at DESC, run_id DESC LIMIT ?
                    """,
                    (session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT run_id, session_id, request_id, tenant_id, stage,
                           outcome_kind, outcome_json, text, execute,
                           created_at, updated_at
                    FROM runs
                    ORDER BY created_at DESC, run_id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            for row in rows:
                self._verify_run_event_chain(conn, row[0])
                self._verify_run_anchor(conn, row[0])
        return [_run_row_to_dict(row) for row in rows]

    def list_active_runs(self) -> List[Dict[str, Any]]:
        placeholders = ",".join("?" for _ in ACTIVE_RUN_STAGES)
        with self._connection() as conn:
            self._verify_all_run_facts(conn)
            rows = conn.execute(
                """
                SELECT run_id, session_id, request_id, tenant_id, stage,
                       outcome_kind, outcome_json, text, execute,
                       created_at, updated_at
                FROM runs WHERE stage IN (%s)
                ORDER BY created_at ASC
                """ % placeholders,
                tuple(ACTIVE_RUN_STAGES),
            ).fetchall()
            for row in rows:
                self._verify_run_event_chain(conn, row[0])
                self._verify_run_anchor(conn, row[0])
        return [_run_row_to_dict(row) for row in rows]

    def run_events(self, run_id: str) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            self._verify_run_event_chain(conn, run_id)
            self._verify_run_anchor(conn, run_id)
            rows = conn.execute(
                "SELECT event_seq, kind, stage, payload_json, recorded_at "
                "FROM run_events WHERE run_id = ? ORDER BY event_seq",
                (run_id,),
            ).fetchall()
        return [
            {"event_seq": row[0], "kind": row[1], "stage": row[2],
             "payload": _encrypted_json_loads(row[3]), "recorded_at": row[4]}
            for row in rows
        ]

    def events_after(self, last_seq: int, limit: int = 200,
                     session_id: str = "") -> List[Dict[str, Any]]:
        """Return journal events with seq > last_seq (SSE reconnect source).

        §14.3: SSE history comes from the journal, not memory, so a reconnect
        with ``Last-Event-ID`` never loses events. §5: sessions are isolation
        boundaries — when ``session_id`` is given, only events for runs in that
        session are returned.
        """
        limit = max(1, min(int(limit), 1000))
        with self._connection() as conn:
            self._verify_all_run_facts(conn)
            if session_id:
                rows = conn.execute(
                    "SELECT e.event_seq, e.run_id, e.kind, e.stage, e.payload_json, e.recorded_at "
                    "FROM run_events AS e "
                    "INNER JOIN runs AS r ON r.run_id = e.run_id "
                    "WHERE e.event_seq > ? AND r.session_id = ? "
                    "ORDER BY e.event_seq LIMIT ?",
                    (int(last_seq), session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT event_seq, run_id, kind, stage, payload_json, recorded_at "
                    "FROM run_events WHERE event_seq > ? ORDER BY event_seq LIMIT ?",
                    (int(last_seq), limit),
                ).fetchall()
        return [
            {"event_seq": row[0], "run_id": row[1], "kind": row[2],
             "stage": row[3], "payload": _encrypted_json_loads(row[4]), "recorded_at": row[5]}
            for row in rows
        ]

    # -- sealed snapshots (§4) ---------------------------------------------

    def store_planning_context_snapshot(self, run_id: str, snapshot: contracts.ContextSnapshot) -> None:
        document = _seal_snapshot(snapshot)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO planning_context_snapshots(run_id, digest, lease_id,
                                                        document_json, captured_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, snapshot.digest, snapshot.lease_id,
                 _json_dumps(document), snapshot.captured_at),
            )

    def store_captured_context_snapshot(self, run_id: str, snapshot: contracts.ContextSnapshot) -> None:
        with self._connection() as conn:
            conn.execute("INSERT OR REPLACE INTO captured_context_snapshots(run_id,document_json,captured_at) VALUES (?,?,?)",
                         (run_id, _json_dumps(_seal_snapshot(snapshot)), snapshot.captured_at))

    def get_captured_context_snapshot(self, run_id: str) -> Optional[contracts.ContextSnapshot]:
        with self._connection() as conn:
            row = conn.execute("SELECT document_json FROM captured_context_snapshots WHERE run_id=?", (run_id,)).fetchone()
        return None if row is None else contracts.ContextSnapshot.model_validate(_json_loads(row[0]))

    def store_capability_snapshot(self, run_id: str,
                                  snapshot: contracts.CapabilitySnapshot) -> None:
        document = _seal_snapshot(snapshot)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO capability_snapshots(run_id, digest,
                                                          document_json, frozen_at)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, snapshot.digest, _json_dumps(document), _now()),
            )

    def store_intent_spec(self, run_id: str, intent: contracts.IntentSpec) -> None:
        document = _seal_snapshot(intent)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO intent_specs(run_id, digest, document_json, compiled_at)
                VALUES (?, ?, ?, ?)
                """,
                (run_id, intent.digest, _json_dumps(document), _now()),
            )

    def store_verified_plan(self, run_id: str, plan: contracts.VerifiedPlan) -> None:
        document = _seal_snapshot(plan)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO verified_plans(run_id, plan_id, digest, version,
                                                     document_json, sealed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, plan.plan_id, plan.digest, plan.version,
                 _json_dumps(document), _now()),
            )

    def get_verified_plan_document(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Return the raw sealed plan document for a run; the coordinator owns
        object reconstruction. Storage only persists and retrieves JSON."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT document_json FROM verified_plans WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _json_loads(row[0])

    def get_planning_context_snapshot(self, run_id: str) -> Optional[contracts.ContextSnapshot]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT document_json FROM planning_context_snapshots WHERE run_id = ? "
                "ORDER BY captured_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return contracts.ContextSnapshot.model_validate(_json_loads(row[0]))

    def get_capability_snapshot(self, run_id: str) -> Optional[contracts.CapabilitySnapshot]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT document_json FROM capability_snapshots WHERE run_id = ? "
                "ORDER BY frozen_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return contracts.CapabilitySnapshot.model_validate(_json_loads(row[0]))

    def get_intent_spec(self, run_id: str) -> Optional[contracts.IntentSpec]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT document_json FROM intent_specs WHERE run_id = ? "
                "ORDER BY compiled_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return contracts.IntentSpec.model_validate(_json_loads(row[0]))

    def freeze_experiment_baseline(self, pair_id: str, run_id: str,
                                   context: contracts.ContextSnapshot,
                                   capabilities: contracts.CapabilitySnapshot,
                                   intent: contracts.IntentSpec,
                                   provider: str, model: str, binding: Dict[str, Any]) -> None:
        task_contract = intent.derived_facts.get("task_contract")
        if not isinstance(task_contract, dict):
            raise ValueError("experiment baseline requires a validated task contract")
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO experiment_pair_baselines(pair_id,run_id,content_hash,planning_context_hash,intent_digest,context_digest,capability_digest,task_contract_json,context_json,intent_json,capability_json,binding_json,baseline_digest,provider,model,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pair_id, run_id, context.content_hash, contracts.planning_context_hash(context), intent.digest, context.digest,
                 capabilities.digest, _json_dumps(task_contract),
                 _json_dumps(context.model_dump(mode="json")), _json_dumps(intent.model_dump(mode="json")),
                 _json_dumps(capabilities.model_dump(mode="json")), _json_dumps(binding),
                 contracts.digest({"context": context.digest, "intent": intent.digest,
                                   "capabilities": capabilities.digest, "task_contract": task_contract,
                                   "binding": binding, "provider": provider, "model": model}), provider, model, _now()),
            )

    def get_experiment_baseline(self, pair_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT run_id,content_hash,planning_context_hash,intent_digest,context_digest,capability_digest,task_contract_json,context_json,intent_json,capability_json,binding_json,baseline_digest,provider,model FROM experiment_pair_baselines WHERE pair_id=?",
                (pair_id,),
            ).fetchone()
        if row is None:
            return None
        context = contracts.ContextSnapshot.model_validate(_json_loads(row[7]))
        intent = contracts.IntentSpec.model_validate(_json_loads(row[8]))
        capabilities = contracts.CapabilitySnapshot.model_validate(_json_loads(row[9]))
        task_contract = _json_loads(row[6])
        binding = _json_loads(row[10]); baseline_digest = contracts.digest({"context": context.digest, "intent": intent.digest,
            "capabilities": capabilities.digest, "task_contract": task_contract,
            "binding": binding, "provider": row[12], "model": row[13]})
        if (context.digest, intent.digest, capabilities.digest, contracts.planning_context_hash(context), baseline_digest) != (row[4], row[3], row[5], row[2], row[11]):
            raise ValueError("experiment baseline sealed documents do not match their digests")
        return {"run_id": row[0], "content_hash": row[1], "planning_context_hash": row[2], "intent_digest": row[3],
                "context_digest": row[4], "capability_digest": row[5], "task_contract": task_contract, "context": context,
                "intent": intent, "capabilities": capabilities, "binding": binding, "baseline_digest": row[11], "provider": row[12], "model": row[13]}

    def get_verified_plan(self, run_id: str) -> Optional[contracts.VerifiedPlan]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT document_json FROM verified_plans WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return contracts.VerifiedPlan.model_validate(_json_loads(row[0]))

    def store_authorization_grant(self, grant: contracts.AuthorizationGrant) -> None:
        document = _seal_snapshot(grant)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO authorization_grants(run_id, grant_id, plan_digest,
                                                           document_json, granted_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (grant.run_id, grant.grant_id, grant.plan_digest,
                 _json_dumps(document), _now(), grant.expires_at),
            )

    def store_runtime_lease(self, lease: contracts.RuntimeLease) -> None:
        document = _seal_snapshot(lease)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_leases(run_id, lease_id, plan_digest, epoch,
                                                      document_json, acquired_at, last_heartbeat)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (lease.run_id, lease.lease_id, lease.plan_digest, lease.epoch,
                 _json_dumps(document), lease.acquired_at, lease.last_heartbeat),
            )

    def get_runtime_lease(self, run_id: str) -> Optional[contracts.RuntimeLease]:
        """Return the current lease document for a run (fencing authority)."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT document_json FROM runtime_leases WHERE run_id = ? "
                "ORDER BY acquired_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return contracts.RuntimeLease.model_validate(_json_loads(row[0]))

    def get_authorization_grant(self, run_id: str) -> Optional[contracts.AuthorizationGrant]:
        """Return the persisted grant for a run, if any."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT document_json FROM authorization_grants WHERE run_id = ? "
                "ORDER BY granted_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return contracts.AuthorizationGrant.model_validate(_json_loads(row[0]))

    def store_execution_receipt(self, receipt_id: str, run_id: str,
                                lease_id: str, plan_digest: str,
                                status: str, result_hash: str,
                                document: Dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO execution_receipts(run_id, receipt_id, lease_id,
                                                       plan_digest, status, result_hash,
                                                       document_json, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, receipt_id, lease_id, plan_digest, status, result_hash,
                 _json_dumps(document), _now()),
            )

    def store_artifact(self, run_id: str, identity: contracts.ArtifactIdentity,
                       staged: bool = True) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts(run_id, output_id, identity_json,
                                                staged, published, created_at)
                VALUES (?, ?, ?, ?, 0, ?)
                """,
                (run_id, identity.output_id, _json_dumps(identity.model_dump(mode="json")),
                 1 if staged else 0, _now()),
            )

    def store_acceptance_report(self, report_id: str, run_id: str,
                                plan_digest: str, passed: bool,
                                document: Dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO acceptance_reports(run_id, report_id, plan_digest,
                                                         passed, document_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, report_id, plan_digest, 1 if passed else 0,
                 _json_dumps(document), _now()),
            )

    def finalize_publication(self, run_id: str, grant_id: str, document: Dict[str, Any]) -> Dict[str, Any]:
        """Atomically persist the sealed receipt and the published transition."""
        publication_id = document["publication_id"]
        recorded_at = _now()
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT publication_id, grant_id, document_json FROM publication_receipts WHERE run_id=?", (run_id,)).fetchone()
            if existing is not None:
                if existing[0] != publication_id or existing[1] != grant_id or _json_loads(existing[2]) != document:
                    raise ValueError("publication receipt facts already exist and differ")
            else:
                conn.execute("INSERT INTO publication_receipts(run_id, publication_id, grant_id, document_json, published_at) VALUES (?, ?, ?, ?, ?)",
                             (run_id, publication_id, grant_id, _json_dumps(document), recorded_at))
            expected = tuple(sorted(item["output_id"] for item in document.get("artifacts", ())
                                    if isinstance(item, dict) and isinstance(item.get("output_id"), str)))
            if len(expected) != len(set(expected)):
                raise ValueError("publication artifact identities are duplicated")
            rows = conn.execute("SELECT output_id FROM artifacts WHERE run_id=? AND staged=1 AND published=0 ORDER BY output_id", (run_id,)).fetchall()
            actual = tuple(row[0] for row in rows)
            if actual != expected:
                raise ValueError("publication artifacts do not exactly match staged identities")
            if actual:
                updated = conn.execute("UPDATE artifacts SET staged=0, published=1 WHERE run_id=? AND staged=1 AND published=0", (run_id,)).rowcount
                if updated != len(actual):
                    raise RuntimeError("publication artifact transition count mismatch")
            row = conn.execute("SELECT stage FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(run_id)
            if row[0] != PUBLISHED and not is_valid_transition(row[0], PUBLISHED):
                raise ValueError("invalid publication transition: %s -> %s" % (row[0], PUBLISHED))
            event_seq = self._append_event_locked(conn, run_id, "published", PUBLISHED,
                                                  {"projected_stage": PUBLISHED}, recorded_at)
            conn.execute("UPDATE runs SET stage=?, updated_at=? WHERE run_id=?", (PUBLISHED, recorded_at, run_id))
            self._write_event_anchor_locked(conn, run_id)
        result = self.get_run(run_id)
        result["_event_seq"] = event_seq
        self._notify_listeners(event_seq, run_id, "published", PUBLISHED,
                               {"projected_stage": PUBLISHED})
        return result

    def _notify_listeners(self, event_seq: int, run_id: str, kind: str,
                          stage: str, payload: Dict[str, Any]) -> None:
        """Project committed facts best-effort; listeners cannot undo SQLite state."""
        for listener in self._event_listeners:
            try:
                listener(event_seq, run_id, kind, stage, payload)
            except Exception:
                LOGGER.exception("journal event listener failed after commit", extra={
                    "event_seq": event_seq, "run_id": run_id, "kind": kind,
                })

    # -- stage D read helpers (execution / acceptance / publication) --------

    def get_execution_outcome(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest execution receipt document for a run, if any."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT document_json FROM execution_receipts WHERE run_id = ? "
                "ORDER BY received_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _json_loads(row[0])

    def list_staged_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        """Return the staged (not yet published) artifacts of a run."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT identity_json "
                "FROM artifacts WHERE run_id = ? AND staged = 1 AND published = 0 "
                "ORDER BY created_at",
                (run_id,),
            ).fetchall()
        return [contracts.ArtifactIdentity.model_validate(_json_loads(row[0])) for row in rows]

    def list_artifacts(self, run_id: str) -> List[Dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT identity_json, staged, published FROM artifacts WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
        return [{"identity": _json_loads(row[0]), "staged": bool(row[1]), "published": bool(row[2])} for row in rows]

    def prepare_publication(self, run_id: str, publication: Dict[str, Any]) -> None:
        publication_id = publication["publication_id"]
        target_unit_path = publication.get("target_unit_path")
        if publication.get("publication_kind") != "state_change" and not target_unit_path:
            raise ValueError("file publication requires target_unit_path")
        with self._connection() as conn:
            existing = conn.execute("SELECT publication_id, document_json FROM publication_prepared WHERE run_id = ?", (run_id,)).fetchone()
            if existing is not None:
                if existing[0] != publication_id or _json_loads(existing[1]) != publication:
                    raise ValueError("prepared publication facts already exist and differ")
                return
            conn.execute("INSERT INTO publication_prepared(run_id, publication_id, document_json, prepared_at) VALUES (?, ?, ?, ?)",
                         (run_id, publication_id, _json_dumps(publication), _now()))

    def get_prepared_publication(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT publication_id, document_json FROM publication_prepared WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else _json_loads(row[1])

    def get_publication_receipt(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT document_json FROM publication_receipts WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else _json_loads(row[0])

    def get_acceptance_report(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Return the latest acceptance report document for a run, if any."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT document_json FROM acceptance_reports WHERE run_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return _json_loads(row[0])

    # -- model call ledger (§6.4) -------------------------------------------

    def reserve_model_call(self, call_key: str, run_id: str, provider: str,
                           model: str, request_hash: str,
                           ledger: Dict[str, Any]) -> bool:
        """Single-flight reservation (§6.4). Returns True if this caller wins.

        Only ``reserved`` blocks (another caller is in-flight). ``failed`` is
        overwritten so transient failures can be retried. ``quota_stopped`` and
        ``uncertain`` are terminal money-sensitive states (§7: no automatic
        retry on quota stop) and raise rather than let a caller double-charge.
        A ``succeeded`` record should never reach here (cache lookup reuses it)
        and raises to surface the bug.
        """
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT status FROM model_calls WHERE call_key = ?", (call_key,)
            ).fetchone()
            if existing is not None:
                status = existing[0]
                if status == "reserved":
                    return False
                if status == "quota_stopped":
                    raise QuotaStoppedError(call_key)
                if status == "uncertain":
                    raise UncertainCallError(call_key)
                if status == "succeeded":
                    raise RuntimeError(
                        "model call %s already succeeded; cache lookup should have "
                        "prevented reserve" % call_key
                    )
                # failed: overwrite so the caller can retry.
            conn.execute(
                """
                INSERT OR REPLACE INTO model_calls(call_key, run_id, provider, model, status,
                                        request_hash, response_json, ledger_json,
                                        reserved_at, committed_at)
                VALUES (?, ?, ?, ?, 'reserved', ?, NULL, ?, ?, NULL)
                """,
                (call_key, run_id, provider, model, request_hash,
                 _encrypted_json_dumps(ledger), _now()),
            )
        return True

    def get_model_call(self, call_key: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT call_key, run_id, provider, model, status, request_hash, "
                "response_json, ledger_json, reserved_at, committed_at "
                "FROM model_calls WHERE call_key = ?",
                (call_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "call_key": row[0], "run_id": row[1], "provider": row[2], "model": row[3],
            "status": row[4], "request_hash": row[5],
            "response": _encrypted_json_loads(row[6]) if row[6] else None,
            "ledger": _encrypted_json_loads(row[7]), "reserved_at": row[8], "committed_at": row[9],
        }

    def record_cache_hit(self, call_key: str, run_id: str) -> None:
        """Record that ``run_id`` consumed a cached model call (§6.4).

        The cached ledger row belongs to the original run that produced it; a
        later run that hits the same cache must still be able to export the
        evidence for its own audit trail. This binding is read by
        ``list_model_calls_for_run`` so cache consumers see the call too.
        """
        with self._connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO model_call_bindings(call_key, run_id, bound_at) "
                "VALUES (?, ?, ?)",
                (call_key, run_id, _now()),
            )

    def list_model_calls_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        """Return every model-call ledger row bound to one run (evidence export).

        Includes calls the run issued directly (model_calls.run_id) plus calls
        it consumed from the cache (model_call_bindings) — both are evidence of
        what the run's computation depended on. Cached rows are flagged
        ``cache_hit=True`` with a ``bound_at`` timestamp so auditors can tell a
        real invocation from a cache reuse.
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT m.call_key, m.provider, m.model, m.status, m.request_hash, "
                "m.response_json, m.ledger_json, m.reserved_at, m.committed_at, "
                "0 AS cache_hit, NULL AS bound_at "
                "FROM model_calls AS m "
                "WHERE m.run_id = ? "
                "UNION ALL "
                "SELECT m.call_key, m.provider, m.model, m.status, m.request_hash, "
                "m.response_json, m.ledger_json, m.reserved_at, m.committed_at, "
                "1 AS cache_hit, b.bound_at "
                "FROM model_calls AS m "
                "INNER JOIN model_call_bindings AS b ON b.call_key = m.call_key "
                "WHERE b.run_id = ? "
                "ORDER BY reserved_at",
                (run_id, run_id),
            ).fetchall()
        return [
            {"call_key": r[0], "provider": r[1], "model": r[2], "status": r[3],
             "request_hash": r[4],
             "response": _encrypted_json_loads(r[5]) if r[5] else None,
             "ledger": _encrypted_json_loads(r[6]) if r[6] else None,
             "reserved_at": r[7], "committed_at": r[8],
             "cache_hit": bool(r[9]), "bound_at": r[10]}
            for r in rows
        ]

    def commit_model_call(self, call_key: str, status: str,
                          response: Optional[Dict[str, Any]],
                          ledger: Dict[str, Any]) -> None:
        """Finalize a model call (§6.4). Only reserved calls can commit."""
        if status not in ("succeeded", "failed", "quota_stopped", "uncertain"):
            raise ValueError("invalid model call status: %s" % status)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM model_calls WHERE call_key = ?", (call_key,)
            ).fetchone()
            if row is None:
                raise KeyError(call_key)
            if row[0] != "reserved":
                raise ValueError("model call %s already committed: %s" % (call_key, row[0]))
            conn.execute(
                """
                UPDATE model_calls SET status = ?, response_json = ?, ledger_json = ?,
                                       committed_at = ?
                WHERE call_key = ? AND status = 'reserved'
                """,
                (status, _encrypted_json_dumps(response) if response is not None else None,
                 _encrypted_json_dumps(ledger), _now(), call_key),
            )

    # -- recovery (§7) ------------------------------------------------------

    def iter_active_runs(self) -> Iterator[Dict[str, Any]]:
        """Used at startup to resume or fail interrupted runs (§7)."""
        for run in self.list_active_runs():
            yield run

    def quarantine_reserved_model_calls(self) -> int:
        """Quarantine every still-reserved model call as uncertain (§7).

        A process crash can leave ``reserved`` rows behind: the request was
        sent but no receipt came back. The call may have reached the provider
        and been billed, so it must NOT be retried automatically. Marking it
        ``uncertain`` ensures ``reserve_model_call`` raises
        ``UncertainCallError`` on the next attempt, forcing human adjudication.
        """
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT call_key, ledger_json FROM model_calls WHERE status='reserved'"
            ).fetchall()
            for call_key, encrypted_ledger in rows:
                ledger = _encrypted_json_loads(encrypted_ledger)
                ledger["status"] = "uncertain"
                ledger["crash_recovery"] = 1
                conn.execute(
                    "UPDATE model_calls SET status='uncertain', ledger_json=?, committed_at=? "
                    "WHERE call_key=? AND status='reserved'",
                    (_encrypted_json_dumps(ledger), _now(), call_key),
                )
            return len(rows)


# --- row mapping -----------------------------------------------------------

def _run_row_to_dict(row) -> Dict[str, Any]:
    return {
        "run_id": row[0],
        "session_id": row[1],
        "request_id": row[2],
        "tenant_id": row[3],
        "stage": row[4],
        "outcome_kind": row[5],
        "outcome": _json_loads(row[6]) if row[6] else None,
        "text": unprotect_json(row[7]),
        "execute": bool(row[8]),
        "created_at": row[9],
        "updated_at": row[10],
    }


def _caller_document(caller: contracts.CallerIdentity) -> Dict[str, Any]:
    return caller.model_dump(mode="json")


def _side_effects_document(scope: Optional[contracts.SideEffectScope]) -> Optional[Dict[str, Any]]:
    if scope is None:
        return None
    return scope.model_dump(mode="json")


def _outcome_document(outcome: contracts.Outcome) -> Dict[str, Any]:
    return outcome.model_dump(mode="json")


def _seal_snapshot(value: Any) -> Any:
    """Canonical document for a sealed Pydantic contract.

    Uses ``model_dump(mode='json')`` so nested BaseModels serialize
    consistently. Non-BaseModel values (plain dicts/lists passed as evidence)
    pass through unchanged.
    """
    from pydantic import BaseModel
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value
