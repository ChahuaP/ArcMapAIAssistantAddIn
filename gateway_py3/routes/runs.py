from __future__ import annotations

import threading
import time

from gateway_py3.experiments import MODES
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
        },
    )
    if "context" in payload:
        raise ValueError("run context is captured from the selected ArcMap window and cannot be supplied by the caller.")
    mode = payload.get("mode")
    if mode not in MODES:
        raise ValueError("mode is required.")
    if not isinstance(payload.get("command"), str) or not payload["command"].strip():
        raise ValueError("command is required.")
    # Reserve the selected ArcMap target before the first context capture.  The
    # durable FIFO reservation is the episode boundary: C_t through C_t+1.
    target = arcmap.active_bridge(state)
    run = state.store.create_run_for_target(str(payload.get("command") or ""), mode, target)
    controller = RunController(
        state.runner, state.store,
        lambda run_id, bridge_target, phase, fence: arcmap.sync_context(
            state, run_id, phase, bridge=bridge_target or target, finalizer=fence
        ),
        lambda request, row: arcmap.execution_permission(state, request, row),
        lambda run_id, allow_edits, bridge_target: _execute(state, run_id, allow_edits, bridge_target),
    )
    _schedule(state, controller, run["id"], payload)
    return {"ok": True, "run": state.store.get(run["id"])}


def _schedule(state, controller, run_id, payload):
    args = (controller, state, run_id, payload)
    scheduler = getattr(state, "run_scheduler", None)
    if scheduler is not None:
        scheduler(_run, args)
        return
    worker = threading.Thread(
        target=_run,
        args=args,
        name="geopilot-run-" + run_id,
        daemon=True,
    )
    worker.start()


def _run(controller, state, run_id, payload):
    try:
        while not state.store.claim_target_episode(run_id):
            time.sleep(0.05)
        controller.run(run_id, payload)
    except Exception as exc:
        row = state.store.get(run_id)
        if row["status"] in ("running", "planned", "approved"):
            state.store.fail_run(run_id, "controller", exc, state.store.run_trace(run_id))
        elif row["status"] == "executing":
            state.store.require_execution_recovery(
                run_id, "run controller stopped before authoritative ArcMap result acknowledgement"
            )
    finally:
        state.store.finalize_target_episode(run_id)
        state.events.publish("runs.changed", {"path": "/runs/%s" % run_id})


def _execute(state, run_id, allow_edits, bridge):
    return arcmap.arcmap_bridge_client.execute_run(run_id, allow_edits=allow_edits, port=bridge["bridge_port"], hwnd=bridge.get("hwnd"))


def cancel(state, run_id):
    return {"ok": True, "run": state.store.cancel(run_id)}


def report(state, mode):
    if mode is not None and mode not in MODES:
        raise ValueError("invalid mode.")
    return state.store.export_runs(mode)


def _require_exact(payload, allowed):
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("invalid run request fields.")
