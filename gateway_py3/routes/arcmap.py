from __future__ import annotations

import socket
import secrets
import time

from gateway_py3 import arcmap_bridge_client
from gateway_py3.validators import context_hash


BRIDGE_CACHE_SECONDS = 2.0


def sync_context(state, run_id, phase, bridge=None, port_checker=None, finalizer=None):
    if phase not in ("before_planning", "after_execution"):
        raise ValueError("invalid ArcMap context sync phase.")
    bridge = bridge or active_bridge(state, port_checker=port_checker)
    target = _target_identity(bridge)
    status = state.store.get(run_id)["status"]
    expected_status = "running" if phase == "before_planning" else "executed"
    if status != expected_status:
        raise ValueError("run is not ready for %s context sync." % phase)
    if phase == "after_execution" and not isinstance(finalizer, dict):
        raise ValueError("post-execution context sync requires a finalizer fence.")
    sync_token = secrets.token_urlsafe(32)
    key = "arcmap_context_sync:" + sync_token
    state.store.set_state(key, {
        "run_id": run_id,
        "phase": phase,
        "bridge": target,
        "finalizer": finalizer,
        "context": None,
        "consumed": False,
    })
    try:
        result = arcmap_bridge_client.sync_context_target(
            run_id, sync_token, phase, port=target["bridge_port"], hwnd=target["hwnd"]
        )
    except Exception:
        state.store.delete_state(key)
        raise
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    deadline = time.time() + 10
    while not context and time.time() < deadline:
        stored = state.store.get_state(key)
        value = stored.get("value") if stored else None
        if isinstance(value, dict) and isinstance(value.get("context"), dict):
            context = value["context"]
            break
        time.sleep(0.2)
    state.store.delete_state(key)
    if not context:
        raise arcmap_bridge_client.ArcMapBridgeError("ArcMap Bridge 同步后没有返回有效 context。")
    return {
        "ok": True,
        "bridge": target,
        "context_hash": context_hash(context),
        "captured_at": time.time(),
        "context": context
    }


def health(state, port_checker=None):
    bridge = active_bridge(state, port_checker=port_checker)
    result = arcmap_bridge_client.health(port=bridge["bridge_port"])
    result["registered_bridge"] = bridge
    return result


def receive_run_context(state, run_id, payload):
    context = payload.get("context")
    sync_token = payload.get("sync_token")
    phase = payload.get("phase")
    target = _target_identity(payload.get("target"))
    if not isinstance(context, dict) or not isinstance(sync_token, str) or not sync_token:
        raise ValueError("run context requires context and sync_token.")
    state.store.consume_context_sync(sync_token, run_id, phase, target, context)
    return {"ok": True}


def _target_identity(value):
    if not isinstance(value, dict):
        raise ValueError("ArcMap target identity is required.")
    target = {name: int(value.get(name) or 0) for name in ("bridge_pid", "bridge_port", "arcmap_pid", "hwnd")}
    if any(item <= 0 for item in target.values()):
        raise ValueError("ArcMap target identity requires bridge_pid, bridge_port, arcmap_pid and hwnd.")
    return target


def register(state, payload):
    bridge_pid = int(payload.get("bridge_pid") or 0)
    bridge_port = int(payload.get("bridge_port") or 0)
    if bridge_pid <= 0 or bridge_port <= 0:
        raise ValueError("bridge_pid and bridge_port are required.")
    bridge = {
        "bridge_pid": bridge_pid,
        "bridge_port": bridge_port,
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
    }
    state.store.set_state("arcmap_bridge:%s" % bridge_pid, bridge)
    invalidate_bridge_cache(state)
    return {"ok": True, "bridge": bridge}


def set_active(state, payload, port_checker=None):
    bridge_port = int(payload.get("bridge_port") or 0)
    bridge_pid = int(payload.get("bridge_pid") or 0)
    hwnd = int(payload.get("hwnd") or 0)
    if bridge_port <= 0 and bridge_pid <= 0 and hwnd <= 0:
        raise ValueError("bridge_pid, bridge_port or hwnd is required.")
    matches = []
    for bridge in bridges(state, port_checker=port_checker, force=True):
        if hwnd > 0 and bridge.get("hwnd") == hwnd:
            matches.append(bridge)
        elif bridge_port > 0 and bridge.get("bridge_port") == bridge_port and (hwnd <= 0 or bridge.get("hwnd") == hwnd):
            matches.append(bridge)
        elif bridge_pid > 0 and bridge.get("bridge_pid") == bridge_pid and (hwnd <= 0 or bridge.get("hwnd") == hwnd):
            matches.append(bridge)
    if not matches:
        raise ValueError("没有找到匹配的 ArcMap Bridge。")
    if len(matches) > 1:
        raise ValueError("匹配到多个 ArcMap，请用 hwnd 精确选择。")
    state.store.set_state("arcmap_active_bridge", matches[0])
    invalidate_bridge_cache(state)
    return {"ok": True, "bridge": matches[0]}


def set_permission(state, payload):
    permission = {
        "auto_execute": bool(payload.get("auto_execute")),
        "allow_edits": bool(payload.get("allow_edits")),
    }
    state.store.set_state("arcmap_permission", permission)
    return {"ok": True, "permission": permission}


def execution_permission(state, payload, row):
    permission = stored_permission(state)
    user_confirmed = bool(payload.get("confirmed"))
    auto_execute = bool(permission.get("auto_execute"))
    if not user_confirmed and not auto_execute:
        raise ValueError("执行前需要用户在 Codex 对话中确认，或先设置 arcmap permission auto_execute=true。")

    workflow = row.get("workflow") or {}
    has_edits = workflow_has_side_effect(state, workflow, "edits_data")
    allow_edits = bool(payload.get("allow_edits")) or bool(permission.get("allow_edits"))
    if has_edits and not allow_edits:
        raise ValueError("该 workflow 会直接修改原始数据。需要用户明确设置 allow_edits=true。")
    return allow_edits


def stored_permission(state):
    stored = state.store.get_state("arcmap_permission")
    if not stored:
        return {}
    value = stored.get("value")
    return value if isinstance(value, dict) else {}


def workflow_has_side_effect(state, workflow, side_effect):
    for step in workflow.get("steps") or []:
        operation_id = step.get("operation")
        if operation_id in state.catalog.operations and state.catalog.operations[operation_id].get("side_effects") == side_effect:
            return True
    return False


def active_bridge(state, port_checker=None):
    stored = state.store.get_state("arcmap_active_bridge")
    if stored and isinstance(stored.get("value"), dict):
        bridge = stored["value"]
        try:
            target = _target_identity(bridge)
            return dict(bridge, **target)
        except (KeyError, TypeError, ValueError):
            state.store.delete_state("arcmap_active_bridge")
    return scan_bridge(state, port_checker=port_checker)


def bridges(state, port_checker=None, force=False):
    cached = getattr(state, "bridge_cache", None)
    if not force and isinstance(cached, dict) and time.time() < float(cached.get("expires_at") or 0):
        return list(cached.get("bridges") or [])
    check_port = port_checker or is_local_port_open
    arcmap_bridge_client.ensure_running()
    candidates = []
    seen_ports = set()
    for item in state.store.list_state("arcmap_bridge:"):
        value = item.get("value")
        if not isinstance(value, dict):
            continue
        port = int(value.get("bridge_port") or 0)
        if port <= 0 or port in seen_ports:
            continue
        seen_ports.add(port)
        candidates.append(value)
    for port in [8766] + list(range(8767, 8790)):
        if port not in seen_ports:
            candidates.append({"bridge_pid": 0, "bridge_port": port})
            seen_ports.add(port)

    live = []
    for candidate in candidates:
        port = int(candidate.get("bridge_port") or 0)
        if port <= 0:
            continue
        if not check_port(port):
            continue
        try:
            health_result = arcmap_bridge_client.health(port=port)
        except arcmap_bridge_client.ArcMapBridgeError:
            continue
        bridge_pid = int(health_result.get("bridge_pid") or candidate.get("bridge_pid") or 0)
        bridge_port = int(health_result.get("bridge_port") or port)
        summary = health_result.get("summary") if isinstance(health_result.get("summary"), dict) else candidate.get("summary", {})
        targets = summary.get("targets") if isinstance(summary, dict) else None
        if isinstance(targets, list) and targets:
            for target in targets:
                if not isinstance(target, dict):
                    continue
                hwnd = int(target.get("hwnd") or 0)
                bridge = {
                    "bridge_pid": bridge_pid,
                    "bridge_port": bridge_port,
                    "arcmap_pid": int(target.get("arcmap_pid") or 0),
                    "hwnd": hwnd,
                    "summary": {
                        "bridge": summary.get("bridge", "external"),
                        "source_sha256": summary.get("source_sha256", ""),
                        "title": target.get("title") or "",
                        "name": target.get("name") or "",
                    },
                }
                state.store.set_state("arcmap_bridge:%s:%s" % (bridge_pid, hwnd), bridge)
                live.append(bridge)
        else:
            bridge = {
                "bridge_pid": bridge_pid,
                "bridge_port": bridge_port,
                "summary": summary,
            }
            state.store.set_state("arcmap_bridge:%s" % bridge["bridge_pid"], bridge)
            live.append(bridge)
    mark_active_bridge(state, live)
    state.bridge_cache = {"expires_at": time.time() + BRIDGE_CACHE_SECONDS, "bridges": live}
    return live


def is_local_port_open(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.05)
    try:
        return sock.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        sock.close()


def scan_bridge(state, port_checker=None):
    live_bridges = bridges(state, port_checker=port_checker, force=True)
    if len(live_bridges) == 1:
        state.store.set_state("arcmap_active_bridge", live_bridges[0])
        mark_active_bridge(state, live_bridges)
        state.bridge_cache = {"expires_at": time.time() + BRIDGE_CACHE_SECONDS, "bridges": live_bridges}
        return live_bridges[0]
    if len(live_bridges) > 1:
        raise arcmap_bridge_client.ArcMapBridgeError("检测到多个 ArcMap 窗口。当前 Web 控制台无法可靠判断要操作哪一个；请只保留一个 ArcMap 窗口后重试，或用外部 agent 指定 hwnd。")
    raise arcmap_bridge_client.ArcMapBridgeError("ArcMap Bridge 未连接。")


def invalidate_bridge_cache(state):
    state.bridge_cache = {"expires_at": 0.0, "bridges": []}


def mark_active_bridge(state, live_bridges):
    stored = state.store.get_state("arcmap_active_bridge")
    active = stored.get("value") if stored and isinstance(stored.get("value"), dict) else None
    if not active:
        return
    active_hwnd = int(active.get("hwnd") or 0)
    active_arcmap_pid = int(active.get("arcmap_pid") or 0)
    for bridge in live_bridges:
        if (active_hwnd > 0 and active_arcmap_pid > 0
                and int(bridge.get("hwnd") or 0) == active_hwnd
                and int(bridge.get("arcmap_pid") or 0) == active_arcmap_pid):
            bridge["active"] = True
            state.store.set_state("arcmap_active_bridge", bridge)
            return
