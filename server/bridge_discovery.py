"""Bridge target discovery from the ready-file: deterministic, no scanning.

The C# Bridge writes ``bridge.ready`` with its port and pid on startup. This
is the only discovery source; missing file or dead bridge fails fast. Target
liveness (ArcMap AND Bridge pids) is the caller's responsibility — see
BridgeSession.targets.
"""
from __future__ import annotations

import json
import re
import urllib.request
from typing import Any, Dict, List

from shared_runtime.platform_paths import localappdata_path

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def ready_file_path() -> str:
    return localappdata_path("bridge.ready")


def list_bridge_targets() -> List[Dict[str, Any]]:
    """Every live ArcMap target reported by the Bridge ready-file + /targets."""
    path = ready_file_path()
    with open(path, "r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, dict):
        raise RuntimeError("ArcMap Bridge ready-file is not an object: %s" % path)
    port = int(data["port"])
    ready_pid = int(data["pid"])
    if port <= 0 or ready_pid <= 0:
        raise RuntimeError("ArcMap Bridge ready-file has an invalid port or pid: %s" % path)
    base_url = "http://127.0.0.1:%d" % port
    with urllib.request.urlopen(urllib.request.Request(base_url + "/health"), timeout=2.0) as response:
        health = json.loads(response.read().decode("utf-8"))
    if not isinstance(health, dict) or health.get("ok") is not True:
        raise RuntimeError("ArcMap Bridge health check failed.")
    source_sha256 = health.get("deployment_hash")
    if not isinstance(source_sha256, str) or _SHA256.fullmatch(source_sha256) is None:
        raise RuntimeError("ArcMap Bridge health response has no valid deployment hash.")
    bridge_pid = int(health["bridge_pid"])
    bridge_port = int(health["bridge_port"])
    if bridge_pid != ready_pid or bridge_port != port:
        raise RuntimeError("ArcMap Bridge health identity does not match its ready-file.")
    with urllib.request.urlopen(urllib.request.Request(base_url + "/targets"), timeout=5.0) as response:
        target_document = json.loads(response.read().decode("utf-8"))
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
