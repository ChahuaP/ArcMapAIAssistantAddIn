"""BridgeContextProvider: captures ArcMap context via the Bridge (§6.7).

Extracted from app.py build_kernel to be independently testable.
"""
from __future__ import annotations

import json as _json
import threading
import time
import urllib.request

from ..kernel.contracts import ContextSnapshot, LayerSnapshot, LayerRef, FieldColumn
from ..kernel.store import JournalStore


class BridgeContextProvider:
    """Captures ArcMap context via the Bridge /capture-context endpoint."""

    def __init__(self, store: JournalStore, deployment_hash: str):
        self.store = store
        self.deployment_hash = deployment_hash

    def capture(self, run_id: str, lease) -> ContextSnapshot:
        port = lease.bridge_port
        hwnd = lease.target_hwnd
        # Capture context via the Bridge under the real lease. The lease was
        # acquired at CONTEXT_LEASED before this call; the Bridge must fence on
        # lease_id + epoch (+ plan_hash empty for before_planning).
        url = "http://127.0.0.1:%d/capture-context" % port
        payload = _json.dumps({
            "run_id": run_id,
            "phase": "before_planning",
            "hwnd": hwnd,
            "lease_id": lease.lease_id,
            "epoch": lease.epoch,
            "plan_hash": lease.plan_digest,
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

        required = ("layers", "mxd_path", "data_frame", "is_saved", "content_hash",
                    "edit_session_state", "active_view", "extent")
        missing = [name for name in required if name not in context_data]
        if missing:
            raise RuntimeError("ArcMap context callback is missing: %s" % ", ".join(missing))
        layers_data = context_data["layers"]
        if not isinstance(layers_data, list):
            raise RuntimeError("ArcMap context layers must be a list.")
        mxd = context_data["mxd_path"]
        data_frame = context_data["data_frame"]
        is_saved = context_data["is_saved"]
        content_hash = context_data["content_hash"]
        layers = []
        for layer in layers_data:
            if not isinstance(layer, dict):
                raise RuntimeError("ArcMap context contains a malformed layer.")
            for name in ("name", "layer_ref", "long_name", "visible", "data_source", "layer_type", "fields",
                         "geometry_type", "spatial_reference", "selected_count", "selection_hash"):
                if name not in layer:
                    raise RuntimeError("ArcMap layer %r is missing %s." % (layer.get("name"), name))
            fields = layer["fields"]
            if not isinstance(fields, list):
                raise RuntimeError("ArcMap layer fields must be a list.")
            if any(not isinstance(field, dict) or "name" not in field or "type" not in field
                   for field in fields):
                raise RuntimeError("ArcMap layer contains a malformed field.")
            layers.append(LayerSnapshot(
                identity=LayerRef(name=layer["name"], layer_ref=layer["layer_ref"],
                                  data_source=layer["data_source"], layer_type=layer["layer_type"]),
                fields=tuple(FieldColumn(name=field["name"], dtype=field["type"])
                             for field in fields),
                geometry_type=layer["geometry_type"], coordinate_system=layer["spatial_reference"],
                selection_count=int(layer["selected_count"]),
                long_name=layer["long_name"], visible=bool(layer["visible"]),
                selection_hash=layer["selection_hash"],
            ))

        return ContextSnapshot(
            lease_id=lease.lease_id,
            arcmap_pid=lease.arcmap_pid,
            bridge_pid=lease.bridge_pid,
            bridge_port=port,
            target_hwnd=hwnd,
            document_identity={"mxd": mxd, "active_data_frame": data_frame},
            layers=tuple(layers),
            active_data_frame=data_frame,
            edit_session_state=context_data["edit_session_state"],
            is_saved=bool(is_saved),
            view_state={"active_view": context_data["active_view"], "extent": context_data["extent"]},
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
        snapshot = store.get_planning_context_snapshot(run_id)
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
                "data_source": l.identity.data_source,
                "layer_type": l.identity.layer_type,
                "fields": [{"name": field.name, "type": field.dtype} for field in l.fields],
                "geometry_type": l.geometry_type,
                "spatial_reference": l.coordinate_system,
                "selected_count": l.selection_count,
                "long_name": l.long_name,
                "visible": l.visible,
                "selection_hash": l.selection_hash,
            }
            for l in snapshot.layers
        ],
        "mxd_path": snapshot.document_identity.get("mxd", ""),
        "data_frame": snapshot.active_data_frame,
        "active_view": (snapshot.view_state or {}).get("active_view"),
        "extent": (snapshot.view_state or {}).get("extent"),
        "edit_session_state": snapshot.edit_session_state,
        "is_saved": snapshot.is_saved,
        "content_hash": snapshot.content_hash,
    }
