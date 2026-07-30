import json
import sqlite3
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from arcmap_runtime_py2.execution_outbox import result_hash
from gateway_py3.gateway_state import GatewayState
from gateway_py3.run_store import RunStore
from gateway_py3.validators import context_hash


TARGET = {"bridge_pid": 7, "bridge_port": 8766, "arcmap_pid": 70, "hwnd": 9}


def capture():
    context = {"layers": []}
    return {
        "context": context,
        "context_hash": context_hash(context),
        "bridge": TARGET,
        "captured_at": 1.0,
    }


class RunStoreScaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.temp.name) / "runs.sqlite")

    def tearDown(self):
        self.temp.cleanup()

    def test_report_exports_all_260_runs_and_complete_statistics(self):
        self._seed_succeeded(260, created_after=1.0)
        report = self.store.export_runs()
        self.assertEqual(len(report["runs"]), 260)
        self.assertEqual(report["statistics"], {
            "eligible_runs": 260,
            "succeeded": 260,
            "failed": 0,
            "indeterminate": 0,
        })

    def test_recent_page_rejects_silent_over_limit_requests(self):
        with self.assertRaisesRegex(ValueError, "recent run limit"):
            self.store.list_recent(limit=201)

    def test_removed_database_tables_are_rejected_not_migrated(self):
        path = Path(self.temp.name) / "removed.sqlite"
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaisesRegex(RuntimeError, "removed tables"):
            RunStore(path)

    def test_gateway_restart_recovers_active_runs_older_than_250_newer_rows(self):
        executing = self._approved_run("early-executing")
        self.store.claim_for_execution(executing["id"], TARGET, "executing-owner", now=1)
        executed = self._approved_run("early-executed")
        self.store.claim_for_execution(executed["id"], TARGET, "executed-owner", now=1)
        result = {"ok": True}
        self.store.complete_execution(
            executed["id"], "executed", result, "executed-owner", result_hash(result), TARGET
        )
        self.store.claim_context_finalization(executed["id"], "dead-gateway", now=1)
        with self.store._connection() as conn:
            conn.execute("UPDATE runs SET created_at = 0 WHERE id IN (?, ?)", (executing["id"], executed["id"]))
        self._seed_succeeded(251, created_after=time.time())

        state = GatewayState(store=self.store)
        self.assertEqual(self.store.get(executing["id"])["status"], "recovery_required")
        self.assertEqual(self.store.run_trace(executed["id"])["context_finalizer"]["claimed_at"], 0)
        state.resume_interrupted_runs(
            lambda run_id, target, phase, fence: capture(),
            lambda target, args: target(*args),
        )
        self.assertEqual(self.store.get(executed["id"])["status"], "succeeded")

    def test_recovery_resolver_scans_all_260_rows(self):
        rows = []
        for index in range(260):
            run_id = str(uuid.uuid4())
            trace = {
                "contract": "geopilot-run/v2",
                "context_hash": "ctx-%d" % index,
                "execution_owner": {
                    "owner_id": "owner-%d" % index,
                    "target": TARGET,
                    "started_at": 0,
                    "heartbeat_at": 0,
                },
                "recovery": {"required_at": 0, "deadline_at": 1, "reason": "test"},
                "stages": [],
            }
            rows.append((
                run_id, "recovery_required", "context_single", "recovery-%d" % index,
                "ctx-%d" % index, "{}",
                json.dumps([{"type": "run", "run": trace}], sort_keys=True),
                float(index), float(index), None,
            ))
        with self.store._connection() as conn:
            conn.executemany(
                """
                INSERT INTO runs
                (id, status, mode, command, context_hash, workflow_json,
                 agent_trace_json, created_at, updated_at, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        self.assertEqual(len(self.store.resolve_expired_recoveries(now=2)), 260)
        report = self.store.export_runs()
        self.assertEqual(report["statistics"]["eligible_runs"], 0)
        self.assertEqual(report["statistics"]["indeterminate"], 260)

    def _approved_run(self, command):
        run = self.store.create_run(command, "context_single")
        self.store.bind_context(run["id"], capture())
        self.store.update_run(run["id"], "planned")
        return self.store.update_run(run["id"], "approved")

    def _seed_succeeded(self, count, created_after):
        rows = []
        for index in range(count):
            run_id = str(uuid.uuid4())
            created_at = created_after + index
            trace = {
                "contract": "geopilot-run/v2",
                "mode": "context_single",
                "context_hash": "ctx-%d" % index,
                "started_at": created_at,
                "turns": [],
                "workflow_versions": [],
                "audits": [],
                "validations": [],
                "usage": [],
                "stages": [],
                "counts": {"revisions": 0},
            }
            rows.append((
                run_id,
                "succeeded",
                "context_single",
                "episode-%d" % index,
                "ctx-%d" % index,
                "{}",
                json.dumps([{"type": "run", "run": trace}], sort_keys=True),
                created_at,
                created_at,
                json.dumps({"ok": True}),
            ))
        with self.store._connection() as conn:
            conn.executemany(
                """
                INSERT INTO runs
                (id, status, mode, command, context_hash, workflow_json,
                 agent_trace_json, created_at, updated_at, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )


if __name__ == "__main__":
    unittest.main()
