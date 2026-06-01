# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os
import subprocess
import time
import urllib2


try:
    unicode
except NameError:
    unicode = str


BASE_URL = "http://127.0.0.1:8765"
EXPECTED_APP_VERSION = "0.19.0"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CREATE_NO_WINDOW = 0x08000000
PLAN_TIMEOUT_SECONDS = 360


def health():
    return _get("/health")


def save_config(config):
    return _post("/config", config)


def sync_context(context):
    return _post("/context", {"context": context})


def register_arcmap_bridge(pid, port, summary=None):
    return _post("/arcmap/register", {
        "pid": int(pid),
        "port": int(port),
        "summary": summary if isinstance(summary, dict) else {}
    })


def current_context():
    response = _get("/context", timeout=30)
    return response.get("context")


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
    log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ArcMapAIAssistant", "logs")
    if not os.path.isdir(log_dir):
        os.makedirs(log_dir)
    stdout_path = os.path.join(log_dir, "gateway_stdout.log")
    stderr_path = os.path.join(log_dir, "gateway_stderr.log")
    stdout = open(stdout_path, "ab")
    stderr = open(stderr_path, "ab")

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


def plan(command, context):
    return _post("/plan", {"command": command, "context": context}, timeout=PLAN_TIMEOUT_SECONDS)


def pending():
    return _get("/pending")


def claim(workflow_id):
    return _post("/workflows/%s/claim" % workflow_id, {})


def mark_executing(workflow_id):
    return _post("/workflows/%s/executing" % workflow_id, {})


def execution_result(workflow_id, status, result):
    return _post("/execution-result", {"workflow_id": workflow_id, "status": status, "result": result})


def _gateway_command():
    exe = os.path.join(REPO_ROOT, "gateway", "ArcMapAIAssistantGateway.exe")
    if not os.path.isfile(exe):
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


def _http_error_message(exc):
    body = exc.read()
    try:
        payload = json.loads(body.decode("utf-8"))
        if payload.get("error"):
            return payload["error"]
    except (ValueError, UnicodeDecodeError, UnicodeEncodeError, AttributeError, TypeError):
        pass
    return "HTTP %s: %s" % (exc.code, getattr(exc, "reason", "request failed"))
