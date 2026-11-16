"""Persist task records and coordinate their supervising processes."""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import re
import sqlite3

from guizhi.domain.contracts import Job, Run, TaskSpec

SOURCE_ROOT = Path(__file__).resolve().parents[3]


def external_root(path: Path) -> Path:
    if not path.is_absolute():
        raise ValueError("Workspace must be an absolute path")
    root = path.resolve()
    if root.is_relative_to(SOURCE_ROOT) or SOURCE_ROOT.is_relative_to(root):
        raise ValueError("Workspace must be outside the source tree and its ancestors")
    return root


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def supervisor_lock(path: Path):
    """Hold a non-inherited OS lock until the supervising process exits."""
    with path.open("r+b") as handle:
        if os.name == "nt":
            from msvcrt import locking, LK_NBLCK

            def acquire():
                locking(handle.fileno(), LK_NBLCK, 1)
        else:
            from fcntl import flock, LOCK_EX, LOCK_NB

            def acquire():
                flock(handle, LOCK_EX | LOCK_NB)
        try:
            acquire()
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                raise
            yield False
        else:
            yield True


class Workspace:
    def __init__(self, root: Path, *, create: bool = False):
        self.root = external_root(root)
        self.db = self.root / "workspace.sqlite"
        self.runs = self.root / "runs"
        for path in (self.db, self.runs):
            if path.resolve() != path:
                raise ValueError(f"Workspace entries must not redirect: {path}")
        if create:
            self.runs.mkdir(parents=True, exist_ok=True)
            with self.connect(create=True) as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS runs (
                        id TEXT PRIMARY KEY, task TEXT NOT NULL,
                        run TEXT NOT NULL, job TEXT NOT NULL,
                        status TEXT NOT NULL, created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL, result TEXT, error TEXT
                    );
                    CREATE TABLE IF NOT EXISTS events (
                        seq INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                        time TEXT NOT NULL, step TEXT NOT NULL, details TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS crs_catalog (
                        kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                        body TEXT NOT NULL, PRIMARY KEY (kind, id, revision)
                    );
                    CREATE TABLE IF NOT EXISTS operation_requests (
                        key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                        run_id TEXT NOT NULL REFERENCES runs(id)
                    );
                    CREATE TABLE IF NOT EXISTS skills (
                        id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
                        PRIMARY KEY (id, revision)
                    );
                    CREATE TABLE IF NOT EXISTS map_flows (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS compute_cache (
                        namespace TEXT NOT NULL, key TEXT NOT NULL, body TEXT NOT NULL,
                        size INTEGER NOT NULL, touched REAL NOT NULL, PRIMARY KEY (namespace, key)
                    );
                """)

    @contextmanager
    def connect(self, *, create=False):
        mode = "rwc" if create else "rw"
        db = sqlite3.connect(f"{self.db.as_uri()}?mode={mode}", uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            with db:
                yield db
        finally:
            db.close()

    def directory(self, run_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("Invalid run ID")
        path = self.runs / run_id
        if path.resolve() != path:
            raise ValueError("Run directory must not redirect")
        return path

    def start(self, task: TaskSpec, run: Run, job: Job, *, key=None, fingerprint=None):
        records = [json.dumps(asdict(record)) for record in (task, run, job)]
        stamp = now()
        with self.connect() as db:
            if key is not None:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute(
                    "SELECT fingerprint, run_id FROM operation_requests WHERE key = ?", (key,),
                ).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise ValueError("Idempotency key is already bound to different parameters")
                    return previous["run_id"]
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, 'running', ?, ?, NULL, NULL)",
                (run.id, *records, stamp, stamp),
            )
            if key is not None:
                db.execute("INSERT INTO operation_requests VALUES (?, ?, ?)", (key, fingerprint, run.id))
        return run.id

    def read(self, run_id: str) -> dict:
        directory = self.directory(run_id)
        with self.connect() as db:
            db.execute("BEGIN")
            record = db.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if record is None:
                raise ValueError(f"Unknown run: {run_id}")
            events = db.execute(
                "SELECT time, step, details FROM events WHERE run_id = ? ORDER BY seq",
                (run_id,),
            ).fetchall()
        result = dict(record)
        for key in ("task", "run", "job", "result"):
            result[key] = json.loads(result[key]) if result[key] is not None else None
        result["directory"] = str(directory)
        result["events"] = [
            {"time": event["time"], "step": event["step"], "details": json.loads(event["details"])}
            for event in events
        ]
        return result

    def status(self, run_id: str) -> str:
        with self.connect() as db:
            row = db.execute("SELECT status FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row["status"]

    def event(self, run_id: str, step: str, **details):
        with self.connect() as db:
            inserted = db.execute("""
                INSERT INTO events (run_id, time, step, details)
                SELECT id, ?, ?, ? FROM runs WHERE id = ? AND status = 'running'
            """, (now(), step, json.dumps(details), run_id))
            if inserted.rowcount != 1:
                raise RuntimeError("Run is no longer active")

    def request_cancel(self, run_id: str):
        with self.connect() as db:
            db.execute("""
                UPDATE runs SET status = 'cancelling', updated_at = ?
                WHERE id = ? AND status = 'running'
            """, (now(), run_id))

    def finish(self, run_id: str, status: str, *, result=None, error=None):
        with self.connect() as db:
            db.execute("""
                UPDATE runs SET
                    status = CASE WHEN status = 'cancelling' THEN 'cancelled' ELSE ? END,
                    updated_at = ?,
                    result = CASE WHEN status = 'running' THEN ? ELSE NULL END,
                    error = CASE WHEN status = 'running' THEN ? ELSE NULL END
                WHERE id = ? AND status IN ('running', 'cancelling')
            """, (status, now(), json.dumps(result) if result is not None else None, error, run_id))
