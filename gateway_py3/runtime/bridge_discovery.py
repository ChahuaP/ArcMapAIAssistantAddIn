"""Bridge discovery: find the active Bridge target.

Reads the ready-file first (written by the C# Bridge on startup);
falls back to port scanning for compatibility with older Bridges.
"""
from __future__ import annotations

import json as _json
import os
import urllib.request


def _ready_file_path() -> str:
    return os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "ArcMapAIAssistant", "bridge.ready",
    )


def discover_bridge_target() -> dict:
    """Find the active Bridge target.

    Tries the ready-file first; falls back to port scanning.
    Raises RuntimeError if no Bridge is online.
    """
    target = _read_ready_file()
    if target is not None:
        return target
    return _scan_ports()


def _read_ready_file() -> dict | None:
    """Read bridge.ready written by the C# Bridge."""
    path = _ready_file_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        port = int(data.get("port", 0))
        pid = int(data.get("pid", 0))
        if port <= 0 or pid <= 0:
            return None
        # Verify the Bridge is still alive and get ArcMap target info
        url = "http://127.0.0.1:%d/health" % port
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            raw = resp.read()
            try:
                health = _json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError:
                health = _json.loads(raw.decode("gbk", errors="replace"))
        if not health.get("ok"):
            return None
        targets = health.get("summary", {}).get("targets", [])
        if not targets:
            return None
        t = targets[0]
        bridge_pid = int(health.get("bridge_pid", 0))
        arcmap_pid = int(t.get("arcmap_pid", 0))
        hwnd = int(t.get("hwnd", 0))
        if bridge_pid <= 0 or arcmap_pid <= 0 or hwnd <= 0:
            return None
        return {
            "bridge_pid": bridge_pid,
            "bridge_port": port,
            "arcmap_pid": arcmap_pid,
            "hwnd": hwnd,
        }
    except (FileNotFoundError, ValueError, KeyError, OSError):
        return None
    except Exception:
        return None


def _scan_ports() -> dict:
    """Fallback: scan known ports for a Bridge."""
    for port in (8766, 8767, 8768):
        try:
            url = "http://127.0.0.1:%d/health" % port
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                raw = resp.read()
                try:
                    data = _json.loads(raw.decode("utf-8"))
                except UnicodeDecodeError:
                    data = _json.loads(raw.decode("gbk", errors="replace"))
            if not data.get("ok"):
                continue
            targets = data.get("summary", {}).get("targets", [])
            if not targets:
                continue
            t = targets[0]
            bridge_pid = int(data.get("bridge_pid", 0))
            arcmap_pid = int(t.get("arcmap_pid", 0))
            hwnd = int(t.get("hwnd", 0))
            if bridge_pid <= 0 or arcmap_pid <= 0 or hwnd <= 0:
                continue
            return {
                "bridge_pid": bridge_pid,
                "bridge_port": port,
                "arcmap_pid": arcmap_pid,
                "hwnd": hwnd,
            }
        except Exception:
            continue
    raise RuntimeError("没有找到 ArcMap Bridge 目标。")
