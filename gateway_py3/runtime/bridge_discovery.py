"""Bridge discovery: find the active Bridge target via the ready-file.

The C# Bridge writes ``bridge.ready`` on startup with its port and pid. This
is the single deterministic discovery source: there is no port scanning and
no auto-launch. If the ready-file is missing or the Bridge does not respond,
discovery fails fast — the operator must open ArcMap and load the Bridge
add-in so the ready-file is written.
"""
from __future__ import annotations

import json as _json
import urllib.request
import re

from shared_runtime.platform_paths import localappdata_path
from ..kernel.contracts import TargetSelector

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _ready_file_path() -> str:
    return localappdata_path("bridge.ready")


def discover_bridge_target(selector: TargetSelector) -> dict:
    """Find the active Bridge target from the ready-file.

    Raises RuntimeError if the ready-file is missing or the Bridge is not
    responding, with an actionable message for the operator.
    """
    return _read_ready_file(selector)


def list_bridge_targets() -> list[dict]:
    """Return every live ArcMap target; selection is deliberately left to UI."""
    path = _ready_file_path()
    with open(path, "r", encoding="utf-8") as f:
        data = _json.load(f)
    if not isinstance(data, dict):
        raise RuntimeError("ArcMap Bridge ready-file is not an object: %s" % path)
    port = int(data["port"])
    ready_pid = int(data["pid"])
    if port <= 0 or ready_pid <= 0:
        raise RuntimeError("ArcMap Bridge ready-file has an invalid port or pid: %s" % path)
    base_url = "http://127.0.0.1:%d" % port
    with urllib.request.urlopen(urllib.request.Request(base_url + "/health"), timeout=2.0) as resp:
        health = _json.loads(resp.read().decode("utf-8"))
    if not isinstance(health, dict) or health.get("ok") is not True:
        raise RuntimeError("ArcMap Bridge health check failed.")
    source_sha256 = health.get("deployment_hash")
    if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
        raise RuntimeError("ArcMap Bridge health response has no valid deployment hash.")
    bridge_pid = int(health["bridge_pid"])
    bridge_port = int(health["bridge_port"])
    if bridge_pid != ready_pid or bridge_port != port:
        raise RuntimeError("ArcMap Bridge health identity does not match its ready-file.")
    with urllib.request.urlopen(urllib.request.Request(base_url + "/targets"), timeout=5.0) as resp:
        target_document = _json.loads(resp.read().decode("utf-8"))
    if not isinstance(target_document, dict) or target_document.get("ok") is not True:
        raise RuntimeError("ArcMap Bridge target discovery failed.")
    targets = target_document.get("targets")
    if not isinstance(targets, list):
        raise RuntimeError("ArcMap Bridge target response lacks a target list.")
    result = []
    for item in targets:
        if not isinstance(item, dict):
            raise RuntimeError("ArcMap Bridge returned a malformed target.")
        arcmap_pid, hwnd = int(item["arcmap_pid"]), int(item["hwnd"])
        if arcmap_pid <= 0 or hwnd <= 0:
            raise RuntimeError("ArcMap Bridge returned a target with an invalid identity.")
        if not isinstance(item.get("active"), bool):
            raise RuntimeError("ArcMap Bridge returned a target without a boolean active flag.")
        result.append({"bridge_pid": bridge_pid, "bridge_port": port,
                       "arcmap_pid": arcmap_pid, "hwnd": hwnd,
                       "deployment_hash": source_sha256,
                       "active": item["active"]})
    return result


def _read_ready_file(selector: TargetSelector) -> dict:
    """Resolve exactly one explicitly selected target."""
    targets = list_bridge_targets()
    if not isinstance(selector, TargetSelector):
        raise TypeError("ArcMap target selector must be a TargetSelector.")
    wanted = selector.model_dump(mode="json")
    required = ("bridge_pid", "bridge_port", "arcmap_pid", "hwnd", "deployment_hash")
    matches = [target for target in targets if all(target[name] == wanted[name] for name in required)]
    if len(matches) != 1:
        raise RuntimeError("ArcMap target selector resolved %d targets; exactly one is required." % len(matches))
    target = dict(matches[0])
    target["source_sha256"] = target.pop("deployment_hash")
    target.pop("active")
    return target
