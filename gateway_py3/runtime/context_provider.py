"""BridgeContextProvider: captures ArcMap context via the Bridge (§6.7).

Extracted from app.py build_kernel to be independently testable.
"""
from __future__ import annotations

import json as _json
import threading
import time
import urllib.request
import uuid

from ..kernel.contracts import ContextSnapshot, LayerSnapshot, LayerRef
from ..kernel.store import JournalStore


class BridgeContextProvider:
    """Captures ArcMap context via the Bridge /capture-context endpoint."""

    def __init__(self, store: JournalStore, deployment_hash: str):
        self.store = store
        self.deployment_hash = deployment_hash

    def capture(self, run_id: str) -> ContextSnapshot:
        from .bridge_discovery import discover_bridge_target
        target = discover_bridge_target()
        port = target["bridge_port"]
        hwnd = target["hwnd"]
        # Capture context via the Bridge. At this stage no lease exists yet
        # (lease is acquired later in _acquire_runtime). The Bridge uses hwnd
        # to identify the ArcMap window.
        url = "http://127.0.0.1:%d/capture-context" % port
        payload = _json.dumps({
            "run_id": run_id,
            "phase": "before_planning",
            "hwnd": hwnd,
        })
        req = urllib.request.Request(url, data=payload.encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            try:
                result = _json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError:
                result = _json.loads(raw.decode("gbk", errors="replace"))

        context_data = _wait_for_context_callback(self.store, run_id, result)

        layers_data = context_data.get("layers") or []
        mxd = context_data.get("mxd_path", "")
        data_frame = context_data.get("data_frame") or context_data.get("active_data_frame", "")
        is_saved = context_data.get("is_saved", False)
        content_hash = context_data.get("content_hash", "")

        return ContextSnapshot(
            lease_id=str(uuid.uuid4()),
            arcmap_pid=target["arcmap_pid"],
            bridge_pid=target["bridge_pid"],
            bridge_port=port,
            target_hwnd=hwnd,
            document_identity={"mxd": mxd, "active_data_frame": data_frame},
            layers=tuple(
                LayerSnapshot(
                    identity=LayerRef(name=l.get("name", ""), layer_ref=l.get("layer_ref", "")),
                    geometry_type=l.get("geometry_type"),
                    coordinate_system=l.get("spatial_reference"),
                    selection_count=int(l.get("selected_count", 0)),
                )
                for l in layers_data
                if isinstance(l, dict)
            ),
            active_data_frame=data_frame,
            edit_session_active=bool(is_saved),
            is_saved=bool(is_saved),
            captured_at=time.time(),
            deployment_hash=self.deployment_hash,
            content_hash=str(content_hash),
        )


def _wait_for_context_callback(store: JournalStore, run_id: str,
                               bridge_result: dict, timeout: float = 30.0) -> dict:
    """Wait for the Py2 runtime's context callback to arrive in the store."""
    if bridge_result.get("layers") or bridge_result.get("mxd_path"):
        return bridge_result
    event = threading.Event()
    store.add_event_listener(lambda *args: event.set())
    deadline = time.time() + timeout
    while time.time() < deadline:
        snapshot = store.get_context_snapshot(run_id)
        if snapshot is not None:
            return _snapshot_to_context_data(snapshot)
        event.wait(timeout=0.5)
        event.clear()
    raise RuntimeError("上下文回调超时（%ss），Py2 runtime 未响应。" % timeout)


def _snapshot_to_context_data(snapshot) -> dict:
    return {
        "layers": [
            {
                "name": l.identity.name,
                "layer_ref": l.identity.layer_ref,
                "geometry_type": l.geometry_type,
                "spatial_reference": l.coordinate_system,
                "selected_count": l.selection_count,
            }
            for l in snapshot.layers
        ],
        "mxd_path": snapshot.document_identity.get("mxd", ""),
        "data_frame": snapshot.active_data_frame,
        "is_saved": snapshot.is_saved,
        "content_hash": snapshot.content_hash,
    }
