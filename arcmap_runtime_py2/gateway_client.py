# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os
import subprocess
import time
import urllib2

try:
    import path_utils
except ImportError:
    from . import path_utils


try:
    unicode
except NameError:
    unicode = str


BASE_URL = "http://127.0.0.1:8765"
EXPECTED_APP_VERSION = "1.0.2"
REPO_ROOT = path_utils.abspath(path_utils.join_path(os.path.dirname(__file__), ".."))
CREATE_NO_WINDOW = 0x08000000


def health():
    return _get("/health")


def save_config(config):
    return _post("/config", config)


def sync_run_context(run_id, context, sync_token, phase, target):
    if not run_id or not sync_token or phase not in ("before_planning", "after_execution") or not isinstance(target, dict):
        raise RuntimeError(u"ArcMap context callback requires run_id, sync_token, phase and target.")
    return _post("/runs/%s/context" % run_id, {
        "context": context,
        "sync_token": sync_token,
        "phase": phase,
        "target": target,
    })


def register_arcmap_bridge(bridge_pid, bridge_port, summary=None):
    return _post("/arcmap/register", {
        "bridge_pid": int(bridge_pid),
        "bridge_port": int(bridge_port),
        "summary": summary if isinstance(summary, dict) else {}
    })


def ensure_running():
    payload = _health_payload(timeout=2)
    if _is_expected_version(payload):
        return
    if payload:
        stop_gateway()
    start_gateway()
    deadline = time.time() + 15
    while time.time() < deadline:
        if _is_expected_version(_health_payload(timeout=2)):
            return
        time.sleep(0.5)
    payload = _health_payload(timeout=2)
    if payload and not _is_expected_version(payload):
        raise RuntimeError(u"本地网关版本不匹配：当前 %s，需要 %s。请重新安装最新版。" % (payload.get("app_version", u"未知"), EXPECTED_APP_VERSION))
    raise RuntimeError(u"本地网关启动失败。请双击 StartGateway.cmd 查看错误。")


def is_running(timeout=2):
    return _health_payload(timeout=timeout) is not None


def is_expected_version(timeout=2):
    return _is_expected_version(_health_payload(timeout=timeout))


def stop_gateway():
    if os.name != "nt":
        return False
    try:
        output = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            creationflags=CREATE_NO_WINDOW
        )
        if not isinstance(output, unicode):
            output = output.decode("mbcs", "replace")
    except (subprocess.CalledProcessError, OSError):
        return False
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(":8765") and parts[3].upper() == "LISTENING":
            pid = parts[4]
            if pid.isdigit() and int(pid) != os.getpid():
                subprocess.call(
                    ["taskkill", "/PID", pid, "/F"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=CREATE_NO_WINDOW
                )
                return True
    return False


def start_gateway():
    log_dir = path_utils.join_path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ArcMapAIAssistant", "logs")
    if not path_utils.isdir(log_dir):
        path_utils.makedirs(log_dir)
    stdout_path = path_utils.join_path(log_dir, "gateway_stdout.log")
    stderr_path = path_utils.join_path(log_dir, "gateway_stderr.log")
    stdout = path_utils.open_binary(stdout_path, "ab")
    stderr = path_utils.open_binary(stderr_path, "ab")

    command = _gateway_command()
    try:
        subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=stdout,
            stderr=stderr,
            creationflags=CREATE_NO_WINDOW
        )
    except OSError as exc:
        raise RuntimeError(u"无法启动本地网关：%s" % exc)


def claim_run(run_id, target, owner_id):
    return _post("/runs/%s/claim" % run_id, {"target": target, "owner_id": owner_id})


def heartbeat_run(run_id, owner_id):
    return _post("/runs/%s/heartbeat" % run_id, {"owner_id": owner_id}, timeout=10)


def complete_run(run_id, status, result, owner_id, result_hash, target):
    return _post("/runs/%s/complete" % run_id, {
        "status": status,
        "result": result,
        "owner_id": owner_id,
        "result_hash": result_hash,
        "target": target,
    }, timeout=10)


def _gateway_command():
    exe = path_utils.join_path(REPO_ROOT, "gateway", "ArcMapAIAssistantGateway.exe")
    if not path_utils.isfile(exe):
        raise RuntimeError(u"缺少本地网关 EXE：%s。请重新安装 GeoPilot。" % exe)
    return [exe]


def _get(path, timeout=30):
    request = urllib2.Request(BASE_URL + path)
    return _request_json(request, timeout)


def _health_payload(timeout):
    try:
        return _get("/health", timeout=timeout)
    except (RuntimeError, ValueError, urllib2.URLError):
        return None


def _is_expected_version(payload):
    return bool(payload and payload.get("app_version") == EXPECTED_APP_VERSION)


def _post(path, payload, timeout=120):
    data = json.dumps(payload, ensure_ascii=True)
    if not isinstance(data, bytes):
        data = data.encode("ascii")
    request = urllib2.Request(BASE_URL + path, data=data, headers={"Content-Type": "application/json; charset=utf-8"})
    return _request_json(request, timeout)


def _request_json(request, timeout):
    try:
        response = urllib2.urlopen(request, timeout=timeout)
        return json.loads(response.read().decode("utf-8"))
    except urllib2.HTTPError as exc:
        message = _http_error_message(exc)
        raise RuntimeError(message)
    except urllib2.URLError as exc:
        raise RuntimeError(_url_error_message(exc))


def _http_error_message(exc):
    body = exc.read()
    try:
        payload = json.loads(body.decode("utf-8"))
        if payload.get("error"):
            return payload["error"]
    except (ValueError, UnicodeDecodeError, UnicodeEncodeError, AttributeError, TypeError):
        pass
    return "HTTP %s: %s" % (exc.code, getattr(exc, "reason", "request failed"))


def _url_error_message(exc):
    reason = getattr(exc, "reason", exc)
    errno = getattr(reason, "errno", None)
    text = _unicode_text(reason).lower()
    if errno == 10061 or u"connection refused" in text:
        return u"本地网关未连接：127.0.0.1:8765 拒绝连接。请重新点击“启动控制台”。"
    if errno == 10060 or u"timed out" in text or u"timeout" in text:
        return u"本地网关响应超时。请确认 GeoPilot 网关正在运行。"
    if errno == 11001 or u"getaddrinfo" in text:
        return u"本机地址解析失败，无法连接 GeoPilot 网关。请检查本机网络配置。"
    return u"无法连接 GeoPilot 本地网关。请重新点击“启动控制台”。"


def _unicode_text(value):
    if isinstance(value, unicode):
        return value
    try:
        return unicode(value)
    except (UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError):
        try:
            return str(value).decode("utf-8", "replace")
        except (UnicodeDecodeError, UnicodeEncodeError, TypeError, AttributeError):
            return u""
