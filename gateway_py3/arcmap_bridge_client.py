from __future__ import annotations

import json
import socket
import subprocess
import time
from typing import Any, Dict
from pathlib import Path
from urllib import request
from urllib.error import HTTPError, URLError

from .paths import appdata_dir

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


def ensure_running() -> None:
    for port in [8766] + list(range(8767, 8790)):
        if not _is_local_port_open(port):
            continue
        try:
            health(port=port)
            return
        except ArcMapBridgeError:
            continue
    exe = _bridge_exe_path()
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
                return
            except ArcMapBridgeError:
                continue
        time.sleep(0.2)
    raise ArcMapBridgeError("ArcMapBridge.exe 启动后没有在 8766-8789 端口响应。")


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
        raise ArcMapBridgeError("ArcMap Bridge 未连接：%s。请确认 ArcMap 已打开并加载 GeoPilot Add-in。" % _local_network_reason(exc))
    if result.get("ok") is False:
        raise ArcMapBridgeError(result.get("error") or "ArcMap Bridge request failed.")
    return result


def _error_payload(exc: HTTPError) -> Dict[str, Any]:
    try:
        return json.loads(exc.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": "HTTP %s" % exc.code}


def _local_network_reason(exc: URLError) -> str:
    reason = getattr(exc, "reason", exc)
    errno = getattr(reason, "errno", None)
    text = str(reason).lower()
    if errno == 10061 or "connection refused" in text:
        return "本地 Bridge 端口拒绝连接"
    if errno == 10060 or "timed out" in text or "timeout" in text:
        return "连接本地 Bridge 超时"
    if errno == 11001 or "getaddrinfo" in text:
        return "本机地址解析失败"
    return "无法连接本地 Bridge 服务"


def _bridge_exe_path() -> Path | None:
    config = _install_config()
    value = config.get("bridge_exe")
    if not isinstance(value, str) or not value.strip():
        raise ArcMapBridgeError("install.json 缺少 bridge_exe。请重新安装 GeoPilot。")
    exe = Path(value)
    if not exe.is_file():
        raise ArcMapBridgeError("ArcMapBridge.exe 不存在：%s。请重新安装 GeoPilot。" % exe)
    return exe


def _install_config() -> Dict[str, Any]:
    path = appdata_dir() / "install.json"
    if not path.is_file():
        raise ArcMapBridgeError("缺少安装配置：%s。请先安装 GeoPilot。" % path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArcMapBridgeError("install.json 无法读取：%s" % exc)
    if not isinstance(data, dict):
        raise ArcMapBridgeError("install.json 必须是 JSON 对象。")
    return data
