from __future__ import annotations

import threading

from gateway_py3.experiments import MODES, task_semantics, workflow_draft
from gateway_py3.routes import arcmap
from gateway_py3.run_controller import RunController


def create(state, payload):
    _require_exact(
        payload,
        {
            "command",
            "mode",
            "provider",
            "model",
            "execute",
            "confirmed",
            "allow_edits",
            "source",
            "task_semantics",
            "workflow_draft",
        },
    )
    if "context" in payload:
        raise ValueError("run context is captured from the selected ArcMap window and cannot be supplied by the caller.")
    mode = payload.get("mode")
    if mode not in MODES:
        raise ValueError("mode is required.")
    if not isinstance(payload.get("command"), str) or not payload["command"].strip():
        raise ValueError("command is required.")
    artifacts = None
    if "task_semantics" in payload or "workflow_draft" in payload:
        if payload.get("mode") not in ("constrained_single", "multi_agent") or not isinstance(payload.get("source"), str) or not payload["source"].strip():
            raise ValueError("external structured submission requires source and constrained_single or multi_agent mode.")
        if not isinstance(payload.get("task_semantics"), dict) or not isinstance(payload.get("workflow_draft"), dict):
            raise ValueError("external structured submission requires task_semantics and workflow_draft.")
        artifacts = {
            "source": payload["source"].strip(),
            "task_semantics": task_semantics(payload["task_semantics"]),
            "workflow_draft": workflow_draft(payload["workflow_draft"]),
        }
    # A run is bound to a fresh snapshot of the selected ArcMap target.  It is
    # intentionally captured again inside RunController immediately before planning.
    run = state.store.create_run(str(payload.get("command") or ""), mode, "")
    if artifacts:
        payload = dict(payload)
        payload["artifacts"] = artifacts
    controller = RunController(
        state.runner, state.store,
        lambda: arcmap.sync_context(state),
        lambda request, row: arcmap.execution_permission(state, request, row),
        lambda run_id, allow_edits: _execute(state, run_id, allow_edits),
    )
    _schedule(state, controller, run["id"], payload)
    return {"ok": True, "run": state.store.get(run["id"])}


def _schedule(state, controller, run_id, payload):
    target = _run
    args = (controller, state.store, run_id, payload)
    scheduler = getattr(state, "run_scheduler", None)
    if scheduler is not None:
        scheduler(target, args)
        return
    worker = threading.Thread(
        target=target,
        args=args,
        name="geopilot-run-" + run_id,
        daemon=True,
    )
    worker.start()


def _run(controller, store, run_id, payload):
    try:
        controller.run(run_id, payload)
    except Exception as exc:
        row = store.get(run_id)
        if row["status"] != "cancelled":
            store.fail_run(run_id, "planning", exc, store.run_trace(run_id))


def _execute(state, run_id, allow_edits):
    bridge = arcmap.active_bridge(state)
    return arcmap.arcmap_bridge_client.execute_run(run_id, allow_edits=allow_edits, port=bridge["port"], hwnd=bridge.get("hwnd"))


def cancel(state, run_id):
    return {"ok": True, "run": state.store.cancel(run_id)}


def report(state, mode):
    if mode is not None and mode not in MODES:
        raise ValueError("invalid mode.")
    return state.store.export_runs(mode)


def _require_exact(payload, allowed):
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("invalid run request fields.")
