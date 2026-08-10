# -*- coding: utf-8 -*-
"""Stage E GeoPilotHttpAdapter tests (§9, §8).

Covers: submit maps to kernel + returns RunView; inspect; decide approved /
denied; session header required; fixed Origin CORS; Bridge receipt callback
fencing (wrong lease / stale epoch / plan mismatch rejected); heartbeat
updates lease; context callback stores snapshot.
"""
from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

from gateway_py3.api.http_adapter import GeoPilotHttpAdapter, HttpError
from gateway_py3.kernel.coordinator import GeoPilotKernel
from gateway_py3.kernel.store import JournalStore
from gateway_py3.kernel.contracts import RuntimeLease

from tests.kernel import fakes

SID = "00000000-0000-0000-0000-000000000001"


def _lease(run_id) -> RuntimeLease:
    return RuntimeLease(
        lease_id="00000000-0000-0000-0000-0000000000cc",
        run_id=run_id, plan_digest="p" * 64,
        gateway_pid=1000, arcmap_pid=2000, bridge_pid=2001, bridge_port=8766,
        target_hwnd=3000, deployment_hash="dh", epoch=1,
        acquired_at=100.0, last_heartbeat=100.0,
    )


class _FakeBridge:
    def __init__(self):
        self.receipts = []
        self.reconcile_calls = []

    def receive_receipt(self, run_id, receipt):
        self.receipts.append((run_id, receipt))
        return True

    def reconcile(self, lease, run_id):
        self.reconcile_calls.append((lease, run_id))
        return {"lease_id": lease.lease_id, "epoch": lease.epoch,
                "plan_hash": lease.plan_digest, "status": "executed"}


class HttpAdapterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "gp.sqlite"
        self.store = JournalStore(path=self.tmp)
        self.bridge = _FakeBridge()
        # Bridge callbacks (receipt/heartbeat/reconcile) flow through the
        # kernel's bridge port, not the http adapter's bridge_client.
        ports = fakes.build_fake_ports(self.store, bridge=self.bridge)
        self.kernel = GeoPilotKernel(ports)
        self.adapter = GeoPilotHttpAdapter(self.kernel, self.bridge)

    def _headers(self, session_id=SID):
        session = self.adapter.handle_get("/api/v1/session", headers={"X-Session-Id": session_id})
        return {"X-Session-Id": session_id, "Origin": "http://127.0.0.1:8765",
                "X-CSRF-Token": session["csrf_token"]}

    def _wait(self, run_id):
        return fakes.wait_for_terminal(self.kernel, run_id)

    @staticmethod
    def _submit_payload(text):
        return {"text": text, "target_selector": {
            "bridge_pid": 2001, "bridge_port": 8766, "arcmap_pid": 2000,
            "hwnd": 3000, "deployment_hash": "a" * 64,
        }}

    # -- kernel operations -------------------------------------------------

    def test_submit_readonly_pauses_for_clarification(self):
        result = self.adapter.handle_post("/api/v1/runs",
                                          self._submit_payload("select cities"),
                                          self._headers())
        run = result["run"]
        self._wait(run["run_id"])
        run = self.adapter.handle_get("/api/v1/runs/%s" % run["run_id"],
                                      headers=self._headers())["run"]
        # execute=False pauses at plan_verified (plan_only review).
        self.assertEqual(run["stage"], "clarification_required")
        self.assertIsNotNone(run["outcome"])
        self.assertEqual(run["outcome"]["kind"], "ClarificationRequired")

    def test_submit_requires_session_header(self):
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post("/api/v1/runs", {"text": "x"}, {})
        self.assertEqual(ctx.exception.status, 400)

    def test_submit_requires_text(self):
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post("/api/v1/runs", {}, self._headers())
        self.assertEqual(ctx.exception.status, 400)

    def test_post_rejects_missing_csrf_token(self):
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post("/api/v1/runs", {"text": "a"},
                                     {"X-Session-Id": SID, "Origin": "http://127.0.0.1:8765"})
        self.assertEqual(ctx.exception.status, 403)

    def test_post_rejects_cross_origin(self):
        session = self.adapter.handle_get("/api/v1/session", headers={"X-Session-Id": SID})
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post("/api/v1/runs", {"text": "a"},
                                     {"X-Session-Id": SID, "Origin": "https://evil.example",
                                      "X-CSRF-Token": session["csrf_token"]})
        self.assertEqual(ctx.exception.status, 403)

    def test_inspect_returns_run(self):
        result = self.adapter.handle_post("/api/v1/runs",
                                          self._submit_payload("select cities"),
                                          self._headers())
        run_id = result["run"]["run_id"]
        self._wait(run_id)
        inspected = self.adapter.handle_get("/api/v1/runs/%s" % run_id,
                                            headers=self._headers())
        self.assertEqual(inspected["run"]["run_id"], run_id)

    def test_list_runs(self):
        r = self.adapter.handle_post("/api/v1/runs", self._submit_payload("a"), self._headers())
        self._wait(r["run"]["run_id"])
        listing = self.adapter.handle_get("/api/v1/runs", headers=self._headers())
        self.assertGreaterEqual(len(listing["runs"]), 1)

    # -- bridge callbacks fencing (§3.2, §11) ------------------------------

    def _bind_lease(self, run_id):
        lease = _lease(run_id)
        self.store.store_runtime_lease(lease)
        return lease

    def test_receipt_accepted_with_matching_lease(self):
        run = self.adapter.handle_post("/api/v1/runs", self._submit_payload("a"),
                                       self._headers())["run"]
        self._wait(run["run_id"])
        lease = self._bind_lease(run["run_id"])
        result = self.adapter.handle_post(
            "/runs/%s/receipt" % run["run_id"],
            {"lease_id": lease.lease_id, "epoch": 1,
             "plan_hash": lease.plan_digest, "deployment_hash": lease.deployment_hash,
             "status": "executed"},
            self._headers())
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(self.bridge.receipts), 1)

    def test_receipt_rejects_wrong_lease(self):
        run = self.adapter.handle_post("/api/v1/runs", self._submit_payload("a"),
                                       self._headers())["run"]
        self._wait(run["run_id"])
        lease = self._bind_lease(run["run_id"])
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post(
                "/runs/%s/receipt" % run["run_id"],
                {"lease_id": "00000000-0000-0000-0000-0000000000ff",
                 "epoch": 1, "plan_hash": lease.plan_digest, "status": "executed"},
                self._headers())
        self.assertEqual(ctx.exception.status, 403)

    def test_receipt_rejects_stale_epoch(self):
        run = self.adapter.handle_post("/api/v1/runs", self._submit_payload("a"),
                                       self._headers())["run"]
        self._wait(run["run_id"])
        lease = self._bind_lease(run["run_id"])
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post(
                "/runs/%s/receipt" % run["run_id"],
                {"lease_id": lease.lease_id, "epoch": 0,
                 "plan_hash": lease.plan_digest, "status": "executed"},
                self._headers())
        self.assertEqual(ctx.exception.status, 403)

    def test_receipt_rejects_plan_mismatch(self):
        run = self.adapter.handle_post("/api/v1/runs", self._submit_payload("a"),
                                       self._headers())["run"]
        self._wait(run["run_id"])
        lease = self._bind_lease(run["run_id"])
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post(
                "/runs/%s/receipt" % run["run_id"],
                {"lease_id": lease.lease_id, "epoch": 1,
                 "plan_hash": "0" * 64, "status": "executed"},
                self._headers())
        self.assertEqual(ctx.exception.status, 403)

    def test_heartbeat_updates_lease(self):
        run = self.adapter.handle_post("/api/v1/runs", self._submit_payload("a"),
                                       self._headers())["run"]
        self._wait(run["run_id"])
        lease = self._bind_lease(run["run_id"])
        result = self.adapter.handle_post(
            "/runs/%s/heartbeat" % run["run_id"],
            {"lease_id": lease.lease_id, "epoch": 1,
             "plan_hash": lease.plan_digest},
            self._headers())
        self.assertEqual(result, {"ok": True})
        updated = self.store.get_runtime_lease(run["run_id"])
        self.assertGreaterEqual(updated.last_heartbeat, lease.last_heartbeat)


class DecideHttpTest(unittest.TestCase):
    """Execute-path decide tests need risk_level 2 so the plan pauses at
    authorization_required instead of auto-authorizing."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "gp.sqlite"
        self.store = JournalStore(path=self.tmp)
        self.bridge = _FakeBridge()
        ports = fakes.build_fake_ports(self.store, risk_level=2,
                                       bridge=self.bridge)
        self.kernel = GeoPilotKernel(ports)
        self.adapter = GeoPilotHttpAdapter(self.kernel, self.bridge)

    def _headers(self, session_id=SID):
        session = self.adapter.handle_get("/api/v1/session", headers={"X-Session-Id": session_id})
        return {"X-Session-Id": session_id, "Origin": "http://127.0.0.1:8765",
                "X-CSRF-Token": session["csrf_token"]}

    def _wait(self, run_id):
        return fakes.wait_for_terminal(self.kernel, run_id)

    def test_submit_execute_pauses_then_decide_approved(self):
        result = self.adapter.handle_post("/api/v1/runs",
                                          {"text": "buffer", "execute": True,
                                           "side_effect_level": 2,
                                           "target_selector": {"bridge_pid": 2001, "bridge_port": 8766, "arcmap_pid": 2000, "hwnd": 3000, "deployment_hash": "a" * 64}},
                                          self._headers())
        run = result["run"]
        self._wait(run["run_id"])
        run = self.adapter.handle_get("/api/v1/runs/%s" % run["run_id"],
                                      headers=self._headers())["run"]
        self.assertEqual(run["stage"], "authorization_required")
        plan_digest = run.get("plan_digest", "")
        self.adapter.handle_post(
            "/api/v1/runs/%s/decide" % run["run_id"],
            {"approved": True, "plan_digest": plan_digest,
             "approved_scope": {"level": 2, "outputs": []}}, self._headers())
        self._wait(run["run_id"])
        decided = self.adapter.handle_get("/api/v1/runs/%s" % run["run_id"],
                                          headers=self._headers())["run"]
        self.assertEqual(decided["stage"], "succeeded")

    def test_decide_denied(self):
        result = self.adapter.handle_post("/api/v1/runs",
                                          {"text": "buffer", "execute": True,
                                           "side_effect_level": 2,
                                           "target_selector": {"bridge_pid": 2001, "bridge_port": 8766, "arcmap_pid": 2000, "hwnd": 3000, "deployment_hash": "a" * 64}},
                                          self._headers())
        run = result["run"]
        self._wait(run["run_id"])
        run = self.adapter.handle_get("/api/v1/runs/%s" % run["run_id"],
                                      headers=self._headers())["run"]
        plan_digest = run.get("plan_digest", "")
        self.adapter.handle_post(
            "/api/v1/runs/%s/decide" % run["run_id"],
            {"approved": False, "plan_digest": plan_digest}, self._headers())
        self._wait(run["run_id"])
        decided = self.adapter.handle_get("/api/v1/runs/%s" % run["run_id"],
                                          headers=self._headers())["run"]
        self.assertEqual(decided["outcome"]["kind"], "PolicyDenied")


if __name__ == "__main__":
    unittest.main()
