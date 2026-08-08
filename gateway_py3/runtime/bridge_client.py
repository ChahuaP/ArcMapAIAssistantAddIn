"""Real ArcMap Bridge client implementing the §6.7 BridgeClient protocol.

Talks to the C# Bridge (in ArcMap) over HTTP using the lease protocol
(docs/GEOPILOT_BRIDGE_LEASE_PROTOCOL.md §3):

- ``dispatch`` POSTs /dispatch with the lease triple + plan + context; the
  Bridge writes the silent command file and triggers Py2 execution.
- ``wait_for_receipt`` blocks until the Py2 runtime posts
  /runs/:id/receipt back to the gateway (the HTTP adapter routes that
  callback into this client's receipt registry, which wakes the waiter).
- ``reconcile`` asks the Bridge whether execution happened; unprovable ->
  None (the runtime then produces ExecutionIndeterminate).

No port scanning, no "first healthy bridge" fallback, no silent restart
(§6.7): the Bridge address comes from the lease, which itself was acquired
from the exact bound target.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from ..kernel.contracts import (
    AuthorizationGrant, ContextSnapshot, RuntimeLease, VerifiedPlan,
)

DEFAULT_BRIDGE_HOST = "127.0.0.1"
BRIDGE_REQUEST_TIMEOUT_SECONDS = 30.0
RECEIPT_WAIT_TIMEOUT_SECONDS = 600.0


class ArcMapBridgeError(Exception):
    pass


class RealBridgeClient:
    """§6.7 BridgeClient over the C# Bridge lease protocol.

    One instance serves the whole gateway. Receipt callbacks arrive on HTTP
    adapter threads; they are matched to waiters by (run_id, lease_id, epoch)
    and wake the blocked ``wait_for_receipt`` caller.
    """

    def __init__(self, host: str = DEFAULT_BRIDGE_HOST):
        self.host = host
        self._receipts: Dict[str, Dict[str, Any]] = {}
        self._receipt_events: Dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # -- BridgeClient protocol (§6.7) ---------------------------------------

    def dispatch(self, lease: RuntimeLease, plan: VerifiedPlan,
                 grant: AuthorizationGrant,
                 context_snapshot: ContextSnapshot) -> str:
        """POST /dispatch on the Bridge; returns the receipt token.

        The token is ``run_id`` — the receipt callback arrives on
        /runs/:id/receipt with the same run_id.
        """
        payload = {
            "lease_id": lease.lease_id,
            "epoch": lease.epoch,
            "plan_hash": plan.digest,
            "run_id": lease.run_id,
            "allow_edits": grant.allowed_side_effect_level >= 4,
            "hwnd": lease.target_hwnd,
            "context_snapshot": context_snapshot.model_dump(mode="json"),
        }
        self._post(lease.bridge_port, "/dispatch", payload)
        return lease.run_id

    def wait_for_receipt(self, receipt_token: str, timeout: float) -> Optional[Dict[str, Any]]:
        """Block until the Py2 runtime posts /runs/:id/receipt for the token.

        Returns the receipt dict or None on timeout (→ ExecutionIndeterminate,
        never auto-replay).
        """
        event = self._new_receipt_event(receipt_token)
        if not event.wait(timeout=float(timeout)):
            return None
        with self._lock:
            return self._receipts.pop(receipt_token, None)

    def reconcile(self, lease: RuntimeLease, run_id: str) -> Optional[Dict[str, Any]]:
        """Ask the Bridge whether execution happened (post-dispatch recovery).

        The Bridge answers from its own view (heartbeat / outbox state).
        Unprovable → None.
        """
        try:
            payload = {
                "lease_id": lease.lease_id,
                "epoch": lease.epoch,
                "plan_hash": lease.plan_digest,
                "run_id": run_id,
            }
            result = self._post(lease.bridge_port, "/reconcile", payload)
        except ArcMapBridgeError:
            return None
        if not isinstance(result, dict) or result.get("status") not in ("executed", "failed"):
            return None
        return {
            "lease_id": lease.lease_id,
            "epoch": lease.epoch,
            "plan_hash": lease.plan_digest,
            "status": result["status"],
            "message": result.get("message", ""),
        }

    def sample_values(self, layer_ref: str, fields, max_rows: int, max_samples: int):
        """Delegated to the Bridge's /sample-values endpoint (lazy §4.2)."""
        try:
            result = self._post(0, "/sample-values", {
                "layer_ref": layer_ref,
                "fields": list(fields),
                "max_rows": int(max_rows),
                "max_samples": int(max_samples),
            })
        except ArcMapBridgeError:
            return {}
        return result if isinstance(result, dict) else {}

    # -- receipt callback entry (called by the HTTP adapter) ----------------

    def receive_receipt(self, run_id: str, receipt: Dict[str, Any]) -> bool:
        """HTTP adapter calls this when /runs/:id/receipt arrives.

        Fencing is checked by the adapter against the stored lease; this
        method only wakes the matching waiter. Returns True if a waiter was
        found.
        """
        with self._lock:
            event = self._receipt_events.pop(run_id, None)
            if event is not None:
                self._receipts[run_id] = receipt
                event.set()
                return True
        return False

    # -- internals -----------------------------------------------------------

    def _new_receipt_event(self, receipt_token: str) -> threading.Event:
        with self._lock:
            event = self._receipt_events.setdefault(receipt_token, threading.Event())
            self._receipts.pop(receipt_token, None)
        return event

    def _post(self, bridge_port: int, path: str, payload: Dict[str, Any],
              timeout: float = BRIDGE_REQUEST_TIMEOUT_SECONDS) -> Dict[str, Any]:
        if bridge_port <= 0:
            raise ArcMapBridgeError("bridge_port is required for Bridge requests.")
        url = "http://%s:%d%s" % (self.host, int(bridge_port), path)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(body)
                message = detail.get("error") or body
            except ValueError:
                message = body
            raise ArcMapBridgeError("Bridge %s failed: %s" % (path, message))
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise ArcMapBridgeError("Bridge %s unreachable: %s" % (path, exc))
        if not isinstance(result, dict) or result.get("ok") is False:
            raise ArcMapBridgeError(
                "Bridge %s returned error: %s" % (path, result.get("error") if isinstance(result, dict) else result)
            )
        return result
