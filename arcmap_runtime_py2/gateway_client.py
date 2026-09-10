# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os
import time
import urllib2

try:
    import deployment_identity
    import release
except ImportError:
    from . import deployment_identity
    from . import release


try:
    unicode
except NameError:
    unicode = str


BASE_URL = "http://127.0.0.1:8765"


def health():
    return _get("/health")


def save_config(config):
    return _post("/config", config)


def sync_run_context(run_id, context, lease_id, epoch, plan_hash, phase, target):
    if not run_id or not lease_id or epoch <= 0 or not plan_hash or phase not in ("before_planning", "after_execution") or not isinstance(target, dict):
        raise RuntimeError(u"ArcMap context callback requires run_id, lease_id, epoch, plan_hash, phase and target.")
    return _post("/runs/%s/context" % run_id, {
        "context": context,
        "lease_id": lease_id,
        "epoch": int(epoch),
        "plan_hash": plan_hash,
        "phase": phase,
        "target": target,
        "deployment_hash": deployment_identity.deployment_hash(),
    })


def register_arcmap_bridge(bridge_pid, bridge_port, summary=None):
    return _post("/arcmap/register", {
        "bridge_pid": int(bridge_pid),
        "bridge_port": int(bridge_port),
        "summary": summary if isinstance(summary, dict) else {}
    })


def ensure_running(timeout=15.0):
    """Wait for the boundary server's callback surface (8765) to be healthy.

    The boundary server is started by the dsh console (``server/main.py`` ->
    ``start_callback_server``); the Py2 runtime never launches it. This only
    verifies the connection so the Add-in can report a clear error instead of
    hanging.
    """
    deadline = time.time() + timeout
    while True:
        payload = _health_payload(timeout=2)
        if _is_expected_version(payload):
            return
        if payload:
            raise RuntimeError(u"边界服务器版本不匹配：当前 %s，需要 %s。请重新安装最新版。" % (payload.get("app_version", u"未知"), release.APP_VERSION))
        if time.time() >= deadline:
            break
        time.sleep(0.5)
    raise RuntimeError(u"边界服务器未连接：127.0.0.1:8765。请先打开 ArcMap 并点击 Add-in 的 ArcMap Harness 按钮。")



def is_running(timeout=2):
    return _health_payload(timeout=timeout) is not None


def is_expected_version(timeout=2):
    return _is_expected_version(_health_payload(timeout=timeout))


def acknowledge_lease(run_id, lease_id, epoch, plan_hash, target):
    """Confirm lease binding for a run before execution (replaces claim)."""
    if not run_id or not lease_id or epoch <= 0 or not plan_hash or not isinstance(target, dict):
        raise RuntimeError(u"lease acknowledgement requires run_id, lease_id, epoch, plan_hash and target.")
    return _post("/runs/%s/lease-ack" % run_id, {
        "lease_id": lease_id,
        "epoch": int(epoch),
        "plan_hash": plan_hash,
        "target": target,
        "deployment_hash": deployment_identity.deployment_hash(),
    })


def heartbeat_run(run_id, lease_id, epoch, plan_hash):
    return _post("/runs/%s/heartbeat" % run_id, {
        "lease_id": lease_id,
        "epoch": int(epoch),
        "plan_hash": plan_hash,
    }, timeout=10)


def post_execution_receipt(run_id, status, result, lease_id, epoch, plan_hash, result_hash, target):
    return _post("/runs/%s/receipt" % run_id, {
        "status": status,
        "result": result,
        "lease_id": lease_id,
        "epoch": int(epoch),
        "plan_hash": plan_hash,
        "result_hash": result_hash,
        "target": target,
        "deployment_hash": deployment_identity.deployment_hash(),
    }, timeout=10)


def complete_sample(run_id, layer_ref, values, lease_id, epoch, plan_hash):
    return _post("/runs/%s/sample" % run_id, {
        "layer_ref": layer_ref, "values": values, "lease_id": lease_id,
        "epoch": int(epoch), "plan_hash": plan_hash,
        "deployment_hash": deployment_identity.deployment_hash(),
    }, timeout=30)


def complete_acceptance_probe(run_id, document, lease_id, epoch, plan_hash, deployment_hash):
    actual = deployment_identity.deployment_hash()
    if deployment_hash != actual:
        raise RuntimeError(u"Boundary/Bridge deployment identity does not match Py2 runtime.")
    return _post("/runs/%s/acceptance-probe" % run_id, {
        "document": document,
        "lease_id": lease_id,
        "epoch": int(epoch),
        "plan_hash": plan_hash,
        "deployment_hash": actual,
    }, timeout=30)


def current_target():
    payload = _get("/arcmap/bridges", timeout=10)
    bridges = payload.get("bridges") if isinstance(payload, dict) else None
    if not isinstance(bridges, list):
        raise RuntimeError(u"本地网关没有返回 ArcMap 目标列表。")
    matches = [
        bridge for bridge in bridges
        if isinstance(bridge, dict) and int(bridge.get("arcmap_pid") or 0) == os.getpid()
    ]
    if len(matches) != 1:
        raise RuntimeError(u"当前 ArcMap 必须且只能对应一个 Bridge 目标。")
    return dict(
        (name, int(matches[0].get(name) or 0))
        for name in ("bridge_pid", "bridge_port", "arcmap_pid", "hwnd")
    )


def _get(path, timeout=30):
    request = urllib2.Request(BASE_URL + path)
    return _request_json(request, timeout)


def _health_payload(timeout):
    try:
        return _get("/health", timeout=timeout)
    except (RuntimeError, ValueError, urllib2.URLError):
        return None


def _is_expected_version(payload):
    return bool(payload and payload.get("app_version") == release.APP_VERSION)


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
        return u"边界服务器未连接：127.0.0.1:8765 拒绝连接。请先打开 ArcMap 并点击 Add-in 的 ArcMap Harness 按钮。"
    if errno == 10060 or u"timed out" in text or u"timeout" in text:
        return u"边界服务器响应超时。请确认 ArcMap Harness 控制台正在运行。"
    if errno == 11001 or u"getaddrinfo" in text:
        return u"本机地址解析失败，无法连接边界服务器。请检查本机网络配置。"
    return u"无法连接 ArcMap Harness 边界服务器。请先打开 ArcMap 并点击 Add-in 的 ArcMap Harness 按钮。"


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
