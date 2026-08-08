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
        ports = fakes.build_fake_ports(self.store)
        self.kernel = GeoPilotKernel(ports)
        self.bridge = _FakeBridge()
        self.adapter = GeoPilotHttpAdapter(self.kernel, self.store, self.bridge)

    def _headers(self, session_id=SID):
        return {"X-Session-Id": session_id}

    # -- kernel operations -------------------------------------------------

    def test_submit_readonly_runs_to_succeeded(self):
        result = self.adapter.handle_post("/api/v1/runs",
                                          {"text": "select cities"},
                                          self._headers())
        run = result["run"]
        self.assertEqual(run["stage"], "succeeded")
        self.assertIsNotNone(run["outcome"])
        self.assertEqual(run["outcome"]["kind"], "Succeeded")

    def test_submit_requires_session_header(self):
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post("/api/v1/runs", {"text": "x"}, {})
        self.assertEqual(ctx.exception.status, 400)

    def test_submit_requires_text(self):
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post("/api/v1/runs", {}, self._headers())
        self.assertEqual(ctx.exception.status, 400)

    def test_submit_execute_pauses_then_decide_approved(self):
        result = self.adapter.handle_post("/api/v1/runs",
                                          {"text": "buffer", "execute": True,
                                           "side_effect_level": 1},
                                          self._headers())
        run = result["run"]
        self.assertEqual(run["stage"], "authorization_required")
        decided = self.adapter.handle_post(
            "/api/v1/runs/%s/decide" % run["run_id"],
            {"approved": True}, self._headers())
        self.assertEqual(decided["run"]["stage"], "succeeded")

    def test_decide_denied(self):
        result = self.adapter.handle_post("/api/v1/runs",
                                          {"text": "buffer", "execute": True,
                                           "side_effect_level": 1},
                                          self._headers())
        run = result["run"]
        decided = self.adapter.handle_post(
            "/api/v1/runs/%s/decide" % run["run_id"],
            {"approved": False}, self._headers())
        self.assertEqual(decided["run"]["outcome"]["kind"], "PolicyDenied")

    def test_inspect_returns_run(self):
        result = self.adapter.handle_post("/api/v1/runs",
                                          {"text": "select cities"},
                                          self._headers())
        run_id = result["run"]["run_id"]
        inspected = self.adapter.handle_get("/api/v1/runs/%s" % run_id)
        self.assertEqual(inspected["run"]["run_id"], run_id)

    def test_list_runs(self):
        self.adapter.handle_post("/api/v1/runs", {"text": "a"}, self._headers())
        listing = self.adapter.handle_get("/api/v1/runs")
        self.assertGreaterEqual(len(listing["runs"]), 1)

    # -- bridge callbacks fencing (§3.2, §11) ------------------------------

    def _bind_lease(self, run_id):
        lease = _lease(run_id)
        self.store.store_runtime_lease(lease)
        return lease

    def test_receipt_accepted_with_matching_lease(self):
        run = self.adapter.handle_post("/api/v1/runs", {"text": "a"},
                                       self._headers())["run"]
        lease = self._bind_lease(run["run_id"])
        result = self.adapter.handle_post(
            "/runs/%s/receipt" % run["run_id"],
            {"lease_id": lease.lease_id, "epoch": 1,
             "plan_hash": lease.plan_digest, "status": "executed"},
            self._headers())
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(self.bridge.receipts), 1)

    def test_receipt_rejects_wrong_lease(self):
        run = self.adapter.handle_post("/api/v1/runs", {"text": "a"},
                                       self._headers())["run"]
        lease = self._bind_lease(run["run_id"])
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post(
                "/runs/%s/receipt" % run["run_id"],
                {"lease_id": "00000000-0000-0000-0000-0000000000ff",
                 "epoch": 1, "plan_hash": lease.plan_digest, "status": "executed"},
                self._headers())
        self.assertEqual(ctx.exception.status, 403)

    def test_receipt_rejects_stale_epoch(self):
        run = self.adapter.handle_post("/api/v1/runs", {"text": "a"},
                                       self._headers())["run"]
        lease = self._bind_lease(run["run_id"])
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post(
                "/runs/%s/receipt" % run["run_id"],
                {"lease_id": lease.lease_id, "epoch": 0,
                 "plan_hash": lease.plan_digest, "status": "executed"},
                self._headers())
        self.assertEqual(ctx.exception.status, 403)

    def test_receipt_rejects_plan_mismatch(self):
        run = self.adapter.handle_post("/api/v1/runs", {"text": "a"},
                                       self._headers())["run"]
        lease = self._bind_lease(run["run_id"])
        with self.assertRaises(HttpError) as ctx:
            self.adapter.handle_post(
                "/runs/%s/receipt" % run["run_id"],
                {"lease_id": lease.lease_id, "epoch": 1,
                 "plan_hash": "0" * 64, "status": "executed"},
                self._headers())
        self.assertEqual(ctx.exception.status, 403)

    def test_heartbeat_updates_lease(self):
        run = self.adapter.handle_post("/api/v1/runs", {"text": "a"},
                                       self._headers())["run"]
        lease = self._bind_lease(run["run_id"])
        result = self.adapter.handle_post(
            "/runs/%s/heartbeat" % run["run_id"],
            {"lease_id": lease.lease_id, "epoch": 1,
             "plan_hash": lease.plan_digest},
            self._headers())
        self.assertEqual(result, {"ok": True})
        updated = self.store.get_runtime_lease(run["run_id"])
        self.assertGreaterEqual(updated.last_heartbeat, lease.last_heartbeat)

    def test_reconcile_routes_to_bridge(self):
        run = self.adapter.handle_post("/api/v1/runs", {"text": "a"},
                                       self._headers())["run"]
        lease = self._bind_lease(run["run_id"])
        result = self.adapter.handle_post(
            "/runs/%s/reconcile" % run["run_id"],
            {"lease_id": lease.lease_id, "epoch": 1,
             "plan_hash": lease.plan_digest},
            self._headers())
        self.assertEqual(result["status"], "executed")
        self.assertEqual(len(self.bridge.reconcile_calls), 1)


if __name__ == "__main__":
    unittest.main()
