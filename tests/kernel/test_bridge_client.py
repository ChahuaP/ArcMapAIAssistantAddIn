# -*- coding: utf-8 -*-
"""Stage E RealBridgeClient tests (§6.7 lease protocol).

Runs a fake C# Bridge (an http.server thread) that records dispatched
payloads and answers /reconcile. Verifies dispatch payload shape, receipt
wait/notify, reconcile unprovable -> None, and error mapping.
"""
from __future__ import absolute_import

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gateway_py3.kernel.contracts import (
    AuthorizationGrant, CallerIdentity, ContextSnapshot, RuntimeLease,
    SideEffectScope, VerifiedPlan, WorkflowStep,
)
from gateway_py3.runtime.bridge_client import (
    ArcMapBridgeError, RealBridgeClient,
)
from gateway_py3.runtime import bridge_client


def _plan() -> VerifiedPlan:
    return VerifiedPlan(
        plan_id="00000000-0000-0000-0000-0000000000bb",
        version=1, intent_digest="i", context_digest="c", capability_digest="k",
        workflow=(WorkflowStep(id="s1", operation="analysis.buffer",
                               arguments={}, reason="r"),),
        validation_report={"ok": True},
        model_identity="fake", prompt_version="v1", risk_level=1,
    )


def _lease(run_id="00000000-0000-0000-0000-00000000000c",
           bridge_port=8766) -> RuntimeLease:
    plan = _plan()
    return RuntimeLease(
        lease_id="00000000-0000-0000-0000-0000000000cc",
        run_id=run_id, plan_digest=plan.digest,
        gateway_pid=1000, arcmap_pid=2000, bridge_pid=2001, bridge_port=bridge_port,
        target_hwnd=3000, deployment_hash="deploy-v1", epoch=1,
        acquired_at=100.0, last_heartbeat=100.0,
    )


def _grant(lease: RuntimeLease) -> AuthorizationGrant:
    return AuthorizationGrant(
        grant_id="00000000-0000-0000-0000-0000000000dd",
        run_id=lease.run_id, plan_digest=lease.plan_digest,
        actor=CallerIdentity(user_id="u1", tenant_id="t1", role="analyst"),
        lease_id=lease.lease_id, lease_epoch=lease.epoch,
        expires_at=1000.0, nonce="n", allowed_side_effect_level=1,
    )


def _context(lease: RuntimeLease) -> ContextSnapshot:
    return ContextSnapshot(
        lease_id=lease.lease_id, arcmap_pid=lease.arcmap_pid,
        bridge_pid=lease.bridge_pid, bridge_port=lease.bridge_port,
        target_hwnd=lease.target_hwnd,
        document_identity={"mxd": "a.mxd"}, deployment_hash="deploy-v1",
        content_hash="ch", captured_at=1.0,
    )


class _FakeBridgeHandler(BaseHTTPRequestHandler):
    """Records /dispatch payloads; /reconcile answers from script."""

    dispatched = []
    reconcile_status = "executed"
    fail_dispatch = False

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.path == "/dispatch":
            if self.fail_dispatch:
                self._json({"ok": False, "error": "bridge busy"}, 500)
                return
            _FakeBridgeHandler.dispatched.append(body)
            self._json({"ok": True, "run_id": body.get("run_id")})
        elif self.path == "/reconcile":
            self._json({"ok": True, "status": self.reconcile_status,
                        "message": "confirmed"})
        elif self.path == "/sample-values":
            self._json({"ok": True, "values": {}})
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_GET(self):
        self._json({"ok": True, "bridge_pid": 2001, "bridge_port": self.server.server_address[1]})

    def _json(self, payload, status=200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


class _FakeBridgeServer:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeBridgeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class RealBridgeClientTest(unittest.TestCase):
    def setUp(self):
        _FakeBridgeHandler.dispatched = []
        _FakeBridgeHandler.fail_dispatch = False
        _FakeBridgeHandler.reconcile_status = "executed"
        self.fake = _FakeBridgeServer()
        self.client = RealBridgeClient()
        self.lease = _lease()
        self.lease = self.lease.model_copy(update={"bridge_port": self.fake.port})

    def tearDown(self):
        self.fake.close()

    def test_dispatch_posts_lease_triple(self):
        plan = _plan()
        token = self.client.dispatch(self.lease, plan, _grant(self.lease),
                                     _context(self.lease))
        self.assertEqual(token, self.lease.run_id)
        self.assertEqual(len(_FakeBridgeHandler.dispatched), 1)
        payload = _FakeBridgeHandler.dispatched[0]
        self.assertEqual(payload["lease_id"], self.lease.lease_id)
        self.assertEqual(payload["epoch"], self.lease.epoch)
        self.assertEqual(payload["plan_hash"], plan.digest)
        self.assertEqual(payload["run_id"], self.lease.run_id)

    def test_wait_for_receipt_blocks_until_notify(self):
        token = self.lease.run_id
        self.client._new_receipt_event(token)
        result = {}

        def deliver():
            import time
            time.sleep(0.2)
            self.client.receive_receipt(token, {
                "lease_id": self.lease.lease_id, "epoch": 1,
                "plan_hash": self.lease.plan_digest, "status": "executed",
            })

        threading.Thread(target=deliver, daemon=True).start()
        receipt = self.client.wait_for_receipt(token, timeout=5.0)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["status"], "executed")

    def test_wait_for_receipt_timeout_returns_none(self):
        self.client._new_receipt_event("00000000-0000-0000-0000-0000000000ee")
        receipt = self.client.wait_for_receipt("00000000-0000-0000-0000-0000000000ee",
                                               timeout=0.5)
        self.assertIsNone(receipt)

    def test_reconcile_confirms_executed(self):
        def deliver():
            import time
            time.sleep(0.1)
            self.client.receive_receipt(self.lease.run_id, {
                "lease_id": self.lease.lease_id, "epoch": self.lease.epoch,
                "plan_hash": self.lease.plan_digest, "status": "executed",
                "result": {"ok": True}, "result_hash": "e3b0c44298fc1c149afbf4c8996fb924"
            })
        threading.Thread(target=deliver, daemon=True).start()
        outcome = self.client.reconcile(self.lease, self.lease.run_id)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome["status"], "executed")
        self.assertEqual(outcome["lease_id"], self.lease.lease_id)

    def test_reconcile_unprovable_returns_none(self):
        original = bridge_client.RECEIPT_WAIT_TIMEOUT_SECONDS
        bridge_client.RECEIPT_WAIT_TIMEOUT_SECONDS = 0.1
        try:
            outcome = self.client.reconcile(self.lease, self.lease.run_id)
        finally:
            bridge_client.RECEIPT_WAIT_TIMEOUT_SECONDS = original
        self.assertIsNone(outcome)

    def test_dispatch_error_raises(self):
        _FakeBridgeHandler.fail_dispatch = True
        with self.assertRaises(ArcMapBridgeError):
            self.client.dispatch(self.lease, _plan(), _grant(self.lease),
                                 _context(self.lease))

    def test_dispatch_requires_bridge_port(self):
        # lease port 0; context carries the fake port so construction succeeds
        # and dispatch's own port check fires first
        lease = self.lease.model_copy(update={"bridge_port": 0})
        context = _context(self.lease)
        with self.assertRaises(ArcMapBridgeError):
            self.client.dispatch(lease, _plan(), _grant(lease), context)


if __name__ == "__main__":
    unittest.main()
