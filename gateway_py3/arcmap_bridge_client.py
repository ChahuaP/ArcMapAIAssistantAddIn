from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Dict
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError


BASE_URL = "http://127.0.0.1:8766"


class ArcMapBridgeError(Exception):
    pass


def health(port: int | None = None) -> Dict[str, Any]:
    return _request("GET", "/health", port=port, timeout=0.6)


def sync_context_target(port: int | None = None, hwnd: int | None = None) -> Dict[str, Any]:
    payload = {}
    if hwnd:
        payload["hwnd"] = int(hwnd)
    return _request("POST", "/sync-context", payload, port=port)


def execute_approved(allow_edits: bool = False, port: int | None = None, hwnd: int | None = None) -> Dict[str, Any]:
    payload = {"allow_edits": bool(allow_edits)}
    if hwnd:
        payload["hwnd"] = int(hwnd)
    return _request("POST", "/execute-approved", payload, timeout=360, port=port)


def ensure_running() -> bool:
    for port in [8766] + list(range(8767, 8790)):
        if not _is_local_port_open(port):
            continue
        try:
            health(port=port)
            return True
        except ArcMapBridgeError:
            continue
    exe = _bridge_exe_path()
    if not exe:
        return False
    subprocess.Popen(
        [str(exe)],
        cwd=str(exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    deadline = time.time() + 5
    while time.time() < deadline:
        for port in [8766] + list(range(8767, 8790)):
            if not _is_local_port_open(port):
                continue
            try:
                health(port=port)
                return True
            except ArcMapBridgeError:
                continue
        time.sleep(0.2)
    return False


def _is_local_port_open(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.05)
    try:
        return sock.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        sock.close()


def _request(
    method: str,
    path: str,
    payload: Dict[str, Any] | None = None,
    timeout: float = 30,
    port: int | None = None
) -> Dict[str, Any]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    base_url = BASE_URL if port is None else "http://127.0.0.1:%s" % int(port)
    req = request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        result = _error_payload(exc)
        raise ArcMapBridgeError(result.get("error") or "ArcMap Bridge request failed.")
    except URLError as exc:
        raise ArcMapBridgeError("ArcMap Bridge 未连接。请确认 ArcMap 已打开并加载 GeoPilot Add-in。%s" % exc.reason)
    if result.get("ok") is False:
        raise ArcMapBridgeError(result.get("error") or "ArcMap Bridge request failed.")
    return result


def _error_payload(exc: HTTPError) -> Dict[str, Any]:
    try:
        return json.loads(exc.read().decode("utf-8"))
    except Exception:
        return {"error": "HTTP %s" % exc.code}


def _bridge_exe_path() -> Path | None:
    env_path = os.environ.get("GEOPILOT_ARCMAP_BRIDGE")
    candidates = []
    if env_path:
        candidates.append(Path(env_path))
    here = Path(__file__).resolve()
    repo_root = here.parent.parent
    candidates.extend([
        repo_root / "ArcMapBridgeExternal" / "bin" / "Release" / "ArcMapBridge.exe",
        repo_root / "app" / "bridge" / "ArcMapBridge.exe",
        Path(sys.executable).resolve().parent / "bridge" / "ArcMapBridge.exe",
        Path(sys.executable).resolve().parent.parent / "bridge" / "ArcMapBridge.exe",
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None
