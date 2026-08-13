# -*- coding: utf-8 -*-
"""Public contract for the machine-wide active GeoPilot conversation."""
from __future__ import absolute_import

import tempfile
import threading
import unittest
import http.client
import json
import sqlite3
import time
from types import SimpleNamespace
from pathlib import Path

from gateway_py3.kernel.store import JournalStore
from gateway_py3.api.http_adapter import GeoPilotHttpAdapter, HttpError
from gateway_py3.kernel.coordinator import GeoPilotKernel
from tests.kernel import fakes
from gateway_py3 import app


class SingleActiveSessionTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "geopilot.sqlite"

    def test_concurrent_get_returns_one_active_session(self):
        sessions = []
        barrier = threading.Barrier(8)
        store = JournalStore(self.path)

        def get_active():
            barrier.wait()
            sessions.append(store.get_active_session())

        workers = [threading.Thread(target=get_active) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual({item["session_id"] for item in sessions}, {sessions[0]["session_id"]})
        self.assertEqual({item["epoch"] for item in sessions}, {1})

    def test_clear_archives_then_atomically_rotates_epoch(self):
        store = JournalStore(self.path)
        previous = store.get_active_session()
        current = store.clear_active_session()
        self.assertNotEqual(previous["session_id"], current["session_id"])
        self.assertEqual(previous["epoch"] + 1, current["epoch"])
        self.assertEqual(store.list_archived_sessions()[0]["session_id"], previous["session_id"])
        self.assertFalse(store.is_active_session(previous["session_id"], previous["epoch"]))
        self.assertTrue(store.is_active_session(current["session_id"], current["epoch"]))

    def test_clear_cancels_inflight_runs_in_the_archived_session(self):
        store = JournalStore(self.path)
        adapter = GeoPilotHttpAdapter(GeoPilotKernel(fakes.build_fake_ports(store)))
        active = adapter.handle_get("/api/v1/active-session")
        headers = {"X-Session-Id": active["session_id"], "X-Session-Epoch": str(active["epoch"]),
                   "Origin": "http://127.0.0.1:8765", "X-CSRF-Token": active["csrf_token"]}
        from tests.kernel.test_http_adapter import HttpAdapterTest
        run = adapter.handle_post("/api/v1/runs", HttpAdapterTest._submit_payload(
            "stop with the old conversation"), headers)["run"]
        adapter.handle_post("/api/v1/active-session/clear", {}, headers)
        archived = store.get_run(run["run_id"])
        self.assertEqual("cancelled", archived["stage"])
        self.assertEqual("Cancelled", archived["outcome_kind"])

    def test_clear_releases_every_old_session_lease_and_blocks_callback_or_control(self):
        store = JournalStore(self.path)
        kernel = GeoPilotKernel(fakes.build_fake_ports(store))
        adapter = GeoPilotHttpAdapter(kernel)
        active = adapter.handle_get("/api/v1/active-session")
        headers = {"X-Session-Id": active["session_id"], "X-Session-Epoch": str(active["epoch"]),
                   "Origin": "http://127.0.0.1:8765", "X-CSRF-Token": active["csrf_token"]}
        from tests.kernel.test_http_adapter import HttpAdapterTest, _lease
        run = adapter.handle_post("/api/v1/runs", HttpAdapterTest._submit_payload(
            "lease must be revoked with its session"), headers)["run"]
        lease = _lease(run["run_id"])
        store.store_runtime_lease(lease)
        adapter.handle_post("/api/v1/active-session/clear", {}, headers)
        self.assertIsNone(store.get_runtime_lease(run["run_id"]))
        with self.assertRaises(ValueError) as heartbeat:
            kernel.heartbeat_callback(run["run_id"], {"lease_id": lease.lease_id,
                "epoch": lease.epoch, "plan_hash": lease.plan_digest})
        self.assertIn("SessionArchived", str(heartbeat.exception))
        with self.assertRaises(ValueError) as control:
            kernel.resume(run["run_id"])
        self.assertIn("SessionArchived", str(control.exception))

    def test_archived_execution_indeterminate_resume_never_reconciles(self):
        store = JournalStore(self.path)
        ports = fakes.build_fake_ports(store)
        kernel = GeoPilotKernel(ports)
        active = store.get_active_session()
        from tests.kernel.test_kernel_end_to_end import _envelope
        run = store.create_run(_envelope(active["session_id"]))
        store.append_event(run["run_id"], "indeterminate", "execution_indeterminate", {},
                           outcome=__import__("gateway_py3.kernel.contracts", fromlist=["outcome_paused"]).outcome_paused(
                               "ExecutionIndeterminate", "test", "indeterminate", "paused"))
        store.clear_active_session()
        with self.assertRaisesRegex(ValueError, "SessionArchived"):
            kernel.resume(run["run_id"])
        self.assertEqual([], ports.executor.reconcile_calls)

    def test_archived_publication_indeterminate_resume_never_publishes(self):
        store = JournalStore(self.path)
        ports = fakes.build_fake_ports(store)
        ports.acceptance.publish_calls = []
        original_recover = ports.acceptance.recover
        ports.acceptance.recover = lambda *args: (ports.acceptance.publish_calls.append(args), original_recover(*args))[1]
        kernel = GeoPilotKernel(ports)
        active = store.get_active_session()
        from tests.kernel.test_kernel_end_to_end import _envelope
        run = store.create_run(_envelope(active["session_id"]))
        store.append_event(run["run_id"], "indeterminate", "publication_indeterminate", {},
                           outcome=__import__("gateway_py3.kernel.contracts", fromlist=["outcome_paused"]).outcome_paused(
                               "PublicationIndeterminate", "test", "indeterminate", "paused"))
        store.clear_active_session()
        with self.assertRaisesRegex(ValueError, "SessionArchived"):
            kernel.resume(run["run_id"])
        self.assertEqual([], ports.acceptance.publish_calls)

    def test_released_lease_cannot_be_reinserted_after_clear(self):
        store = JournalStore(self.path)
        active = store.get_active_session()
        from tests.kernel.test_kernel_end_to_end import _envelope
        from tests.kernel.test_http_adapter import _lease
        run = store.create_run(_envelope(active["session_id"]))
        lease = _lease(run["run_id"])
        store.store_runtime_lease(lease)
        store.clear_active_session()
        with self.assertRaisesRegex(ValueError, "released"):
            store.store_runtime_lease(lease)
        self.assertIsNone(store.get_runtime_lease(run["run_id"]))

    def test_kernel_cannot_create_a_second_session(self):
        store = JournalStore(self.path)
        active = store.get_active_session()
        with self.assertRaises(ValueError):
            store.assert_active_session("00000000-0000-0000-0000-000000000001", "local-tenant")
        self.assertEqual(active, store.get_active_session())

    def test_cleared_run_cannot_be_advanced_by_a_late_worker(self):
        store = JournalStore(self.path)
        kernel = GeoPilotKernel(fakes.build_fake_ports(store))
        adapter = GeoPilotHttpAdapter(kernel)
        active = adapter.handle_get("/api/v1/active-session")
        headers = {"X-Session-Id": active["session_id"], "X-Session-Epoch": str(active["epoch"]),
                   "Origin": "http://127.0.0.1:8765", "X-CSRF-Token": active["csrf_token"]}
        from tests.kernel.test_http_adapter import HttpAdapterTest
        run = adapter.handle_post("/api/v1/runs", HttpAdapterTest._submit_payload(
            "late worker must not continue"), headers)["run"]
        adapter.handle_post("/api/v1/active-session/clear", {}, headers)
        after = kernel._drive_lifecycle(run["run_id"])
        self.assertEqual("cancelled", after.stage)

    def test_restart_keeps_active_session(self):
        first = JournalStore(self.path).get_active_session()
        self.assertEqual(first, JournalStore(self.path).get_active_session())

    def test_v14_multisession_journal_normalizes_once_without_deleting_runs(self):
        store = JournalStore(self.path)
        first = store.get_active_session()
        old_id = "00000000-0000-0000-0000-000000000001"
        conn = sqlite3.connect(str(self.path))
        try:
            conn.execute("INSERT INTO sessions(session_id, tenant_id, created_at) VALUES (?, ?, ?)",
                         (old_id, "local-tenant", time.time() + 1.0))
            conn.execute("DELETE FROM active_session")
            conn.execute("DELETE FROM session_normalization_evidence")
            conn.execute("UPDATE schema_meta SET value=? WHERE key='schema_marker'",
                         ("geopilot-journal-v14-append-only-model-attempt-lineage",))
            conn.commit()
        finally:
            conn.close()
        normalized = JournalStore(self.path)
        active = normalized.get_active_session()
        self.assertEqual(old_id, active["session_id"])
        archives = normalized.list_archived_sessions()
        self.assertIn(first["session_id"], [item["session_id"] for item in archives])
        conn = sqlite3.connect(str(self.path))
        try:
            row = conn.execute("SELECT selected_session_id, candidate_count FROM session_normalization_evidence").fetchone()
        finally:
            conn.close()
        self.assertEqual((old_id, 2), row)

    def test_stale_session_cannot_submit_or_list_runs(self):
        store = JournalStore(self.path)
        adapter = GeoPilotHttpAdapter(GeoPilotKernel(fakes.build_fake_ports(store)))
        old = adapter.handle_get("/api/v1/active-session")
        headers = {"X-Session-Id": old["session_id"], "X-Session-Epoch": str(old["epoch"]),
                   "Origin": "http://127.0.0.1:8765", "X-CSRF-Token": old["csrf_token"]}
        adapter.handle_post("/api/v1/active-session/clear", {}, headers)
        with self.assertRaises(HttpError) as ctx:
            adapter.handle_get("/api/v1/runs", headers=headers)
        self.assertEqual(409, ctx.exception.status)

    def test_clear_endpoint_replaces_active_and_archives_visible_history(self):
        store = JournalStore(self.path)
        adapter = GeoPilotHttpAdapter(GeoPilotKernel(fakes.build_fake_ports(store)))
        old = adapter.handle_get("/api/v1/active-session")
        headers = {"X-Session-Id": old["session_id"], "X-Session-Epoch": str(old["epoch"]),
                   "Origin": "http://127.0.0.1:8765", "X-CSRF-Token": old["csrf_token"]}
        fresh = adapter.handle_post("/api/v1/active-session/clear", {}, headers)
        self.assertNotEqual(old["session_id"], fresh["session_id"])
        headers.update({"X-Session-Id": fresh["session_id"], "X-Session-Epoch": str(fresh["epoch"]),
                        "X-CSRF-Token": fresh["csrf_token"]})
        archived = adapter.handle_get("/api/v1/archived-sessions", headers=headers)["sessions"]
        self.assertEqual(old["session_id"], archived[0]["session_id"])
        detail = adapter.handle_get("/api/v1/archived-sessions/" + old["session_id"], headers=headers)
        self.assertEqual(old["session_id"], detail["session_id"])
        self.assertEqual([], detail["runs"])

    def test_archives_require_current_session_authorization(self):
        store = JournalStore(self.path)
        adapter = GeoPilotHttpAdapter(GeoPilotKernel(fakes.build_fake_ports(store)))
        active = adapter.handle_get("/api/v1/active-session")
        headers = {"X-Session-Id": active["session_id"], "X-Session-Epoch": str(active["epoch"]),
                   "Origin": "http://127.0.0.1:8765", "X-CSRF-Token": active["csrf_token"]}
        adapter.handle_post("/api/v1/active-session/clear", {}, headers)
        with self.assertRaises(HttpError) as ctx:
            adapter.handle_get("/api/v1/archived-sessions")
        self.assertEqual(409, ctx.exception.status)

    def test_web_and_automation_reads_share_the_same_server_active_session(self):
        adapter = GeoPilotHttpAdapter(GeoPilotKernel(fakes.build_fake_ports(JournalStore(self.path))))
        web = adapter.handle_get("/api/v1/active-session")
        automation = adapter.handle_get("/api/v1/active-session")
        self.assertEqual(web["session_id"], automation["session_id"])
        self.assertEqual(web["epoch"], automation["epoch"])

    def test_stale_run_controls_fail_before_run_lookup(self):
        store = JournalStore(self.path)
        adapter = GeoPilotHttpAdapter(GeoPilotKernel(fakes.build_fake_ports(store)))
        old = adapter.handle_get("/api/v1/active-session")
        headers = {"X-Session-Id": old["session_id"], "X-Session-Epoch": str(old["epoch"]),
                   "Origin": "http://127.0.0.1:8765", "X-CSRF-Token": old["csrf_token"]}
        adapter.handle_post("/api/v1/active-session/clear", {}, headers)
        run_id = "00000000-0000-0000-0000-000000000001"
        for suffix in ("/resume", "/resume-quota", "/clarifications"):
            with self.assertRaises(HttpError) as ctx:
                adapter.handle_post("/api/v1/runs/" + run_id + suffix, {}, headers)
            self.assertEqual(409, ctx.exception.status)

    def test_forged_post_is_rejected_before_kernel_creates_a_run(self):
        store = JournalStore(self.path)
        adapter = GeoPilotHttpAdapter(GeoPilotKernel(fakes.build_fake_ports(store)))
        active = adapter.handle_get("/api/v1/active-session")
        headers = {"X-Session-Id": "00000000-0000-0000-0000-000000000001",
                   "X-Session-Epoch": str(active["epoch"]), "Origin": "http://127.0.0.1:8765",
                   "X-CSRF-Token": active["csrf_token"]}
        with self.assertRaises(HttpError) as ctx:
            adapter.handle_post("/api/v1/runs", {"text": "must not run"}, headers)
        self.assertEqual(409, ctx.exception.status)
        self.assertEqual([], store.list_recent_runs())

    def test_sse_rejects_stale_epoch_at_public_http_seam(self):
        store = JournalStore(self.path)
        adapter = GeoPilotHttpAdapter(GeoPilotKernel(fakes.build_fake_ports(store)))
        active = adapter.handle_get("/api/v1/active-session")
        old = dict(active)
        headers = {"X-Session-Id": active["session_id"], "X-Session-Epoch": str(active["epoch"]),
                   "Origin": "http://127.0.0.1:8765", "X-CSRF-Token": active["csrf_token"]}
        adapter.handle_post("/api/v1/active-session/clear", {}, headers)
        previous = app.STATE
        app.STATE = SimpleNamespace(adapter=adapter, projection=None, store=store)
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
            connection.request("GET", "/events?session_id=%s&epoch=%s" % (old["session_id"], old["epoch"]))
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            connection.close()
            self.assertEqual(409, response.status)
            self.assertIn("ContractFailed", body["error"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
            app.STATE = previous


if __name__ == "__main__":
    unittest.main()
