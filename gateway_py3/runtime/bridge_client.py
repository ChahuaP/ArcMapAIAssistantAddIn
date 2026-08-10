"""Real ArcMap Bridge client implementing the §6.7 BridgeClient protocol.

Talks to the C# Bridge (in ArcMap) over HTTP using the lease protocol
(docs/GEOPILOT_BRIDGE_LEASE_PROTOCOL.md §3):

- ``dispatch`` POSTs /dispatch with the lease triple + plan and sealed content hash; the
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
        self._probes: Dict[str, Dict[str, Any]] = {}
        self._probe_events: Dict[str, threading.Event] = {}
        self._samples: Dict[str, Dict[str, Any]] = {}
        self._sample_events: Dict[str, threading.Event] = {}
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
            # ArcMap must capture the live map immediately before execution.
            # Never send the planning snapshot back as purported live context.
            "context_snapshot": {"sealed_content_hash": context_snapshot.content_hash},
        }
        event = self._new_receipt_event(lease.run_id)
        try:
            self._post(lease.bridge_port, "/dispatch", payload)
        except ArcMapBridgeError:
            self._discard_receipt_waiter(lease.run_id, event)
            raise
        return lease.run_id

    def wait_for_receipt(self, receipt_token: str, timeout: float) -> Optional[Dict[str, Any]]:
        """Block until the Py2 runtime posts /runs/:id/receipt for the token.

        Returns the receipt dict or None on timeout (→ ExecutionIndeterminate,
        never auto-replay).
        """
        with self._lock:
            receipt = self._receipts.pop(receipt_token, None)
            event = self._receipt_events.get(receipt_token)
            if receipt is not None:
                self._receipt_events.pop(receipt_token, None)
                return receipt
            if event is None:
                raise ArcMapBridgeError("receipt waiter was not registered before dispatch.")
        if not event.wait(timeout=float(timeout)):
            self._discard_receipt_waiter(receipt_token, event)
            return None
        with self._lock:
            return self._receipts.pop(receipt_token, None)

    def reconcile(self, lease: RuntimeLease, run_id: str) -> Optional[Dict[str, Any]]:
        """Ask the Bridge whether execution happened (post-dispatch recovery).

        The Bridge answers from its own view (heartbeat / outbox state).
        Unprovable → None.
        """
        if run_id != lease.run_id:
            raise ArcMapBridgeError("reconcile run_id does not match the lease.")
        event = self._new_receipt_event(run_id)
        try:
            self._post(lease.bridge_port, "/reconcile", {
                "lease_id": lease.lease_id, "epoch": lease.epoch,
                "plan_hash": lease.plan_digest, "run_id": run_id,
                "hwnd": lease.target_hwnd,
            })
        except ArcMapBridgeError:
            self._discard_receipt_waiter(run_id, event)
            return None
        if not event.wait(timeout=RECEIPT_WAIT_TIMEOUT_SECONDS):
            self._discard_receipt_waiter(run_id, event)
            return None
        with self._lock:
            return self._receipts.pop(run_id, None)

    def probe_output(self, lease: RuntimeLease, plan: VerifiedPlan, output_id: str,
                     kind: str, staged_path: str) -> Optional[Dict[str, Any]]:
        """Request a fenced, independent ArcPy acceptance probe."""
        token = "%s:%s" % (lease.run_id, output_id)
        event = self._new_probe_event(token)
        self._post(lease.bridge_port, "/acceptance-probe", {
            "lease_id": lease.lease_id, "epoch": lease.epoch,
            "plan_hash": plan.digest, "run_id": lease.run_id,
            "deployment_hash": lease.deployment_hash, "hwnd": lease.target_hwnd,
            "output_id": output_id, "kind": kind, "staged_path": staged_path,
        })
        if not event.wait(timeout=BRIDGE_REQUEST_TIMEOUT_SECONDS):
            return None
        with self._lock:
            return self._probes.pop(token, None)

    def probe_unit(self, lease: RuntimeLease, plan: VerifiedPlan,
                   source_publish_unit_path: str) -> Optional[Dict[str, Any]]:
        """Fence one complete staged FileGDB inventory before per-output probes."""
        token = "%s:__unit__" % lease.run_id
        event = self._new_probe_event(token)
        self._post(lease.bridge_port, "/acceptance-probe", {
            "lease_id": lease.lease_id, "epoch": lease.epoch, "plan_hash": plan.digest,
            "run_id": lease.run_id, "deployment_hash": lease.deployment_hash,
            "hwnd": lease.target_hwnd, "source_publish_unit_path": source_publish_unit_path,
        })
        if not event.wait(timeout=BRIDGE_REQUEST_TIMEOUT_SECONDS):
            return None
        with self._lock:
            return self._probes.pop(token, None)

    def probe_map_state(self, lease: RuntimeLease, plan: VerifiedPlan, output_id: str,
                        postcondition: Dict[str, Any], arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Request a fenced live-map probe bound to one sealed workflow step."""
        token = "%s:%s" % (lease.run_id, output_id)
        event = self._new_probe_event(token)
        self._post(lease.bridge_port, "/acceptance-probe", {
            "lease_id": lease.lease_id, "epoch": lease.epoch, "plan_hash": plan.digest,
            "run_id": lease.run_id, "deployment_hash": lease.deployment_hash,
            "hwnd": lease.target_hwnd, "probe_type": "map_state", "output_id": output_id,
            "postcondition": postcondition, "arguments": arguments,
        })
        if not event.wait(timeout=BRIDGE_REQUEST_TIMEOUT_SECONDS):
            return None
        with self._lock:
            return self._probes.pop(token, None)

    def sample_values(self, lease: RuntimeLease, layer_ref: str, fields, max_rows: int, max_samples: int):
        """Delegated to the Bridge's /sample-values endpoint (lazy §4.2)."""
        token = "%s:%s" % (lease.run_id, layer_ref)
        event = self._new_sample_event(token)
        self._post(lease.bridge_port, "/sample-values", {
            "lease_id": lease.lease_id, "epoch": lease.epoch,
            "plan_hash": lease.plan_digest, "run_id": lease.run_id, "hwnd": lease.target_hwnd,
            "layer_ref": layer_ref, "fields": list(fields),
            "max_rows": int(max_rows), "max_samples": int(max_samples),
        })
        if not event.wait(timeout=BRIDGE_REQUEST_TIMEOUT_SECONDS):
            raise ArcMapBridgeError("Bridge did not return lazy samples.")
        with self._lock:
            result = self._samples.pop(token, None)
        if not isinstance(result, dict):
            raise ArcMapBridgeError("Bridge returned an invalid sample-values document.")
        return {"values": result}

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

    def receive_probe(self, run_id: str, document: Dict[str, Any]) -> bool:
        token = "%s:%s" % (run_id, "__unit__" if document.get("probe_type") == "unit" else document.get("output_id", ""))
        with self._lock:
            event = self._probe_events.pop(token, None)
            if event is None:
                return False
            self._probes[token] = document
            event.set()
            return True

    def receive_sample(self, run_id: str, layer_ref: str, values: Dict[str, Any]) -> bool:
        token = "%s:%s" % (run_id, layer_ref)
        with self._lock:
            event = self._sample_events.pop(token, None)
            if event is None:
                return False
            self._samples[token] = values
            event.set()
            return True

    # -- internals -----------------------------------------------------------

    def _new_receipt_event(self, receipt_token: str) -> threading.Event:
        with self._lock:
            event = self._receipt_events.setdefault(receipt_token, threading.Event())
            self._receipts.pop(receipt_token, None)
        return event

    def _discard_receipt_waiter(self, receipt_token: str, event: threading.Event) -> None:
        with self._lock:
            if self._receipt_events.get(receipt_token) is event:
                self._receipt_events.pop(receipt_token, None)
            self._receipts.pop(receipt_token, None)

    def _new_probe_event(self, token: str) -> threading.Event:
        with self._lock:
            event = self._probe_events.setdefault(token, threading.Event())
            self._probes.pop(token, None)
        return event

    def _new_sample_event(self, token: str) -> threading.Event:
        with self._lock:
            event = self._sample_events.setdefault(token, threading.Event())
            self._samples.pop(token, None)
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
