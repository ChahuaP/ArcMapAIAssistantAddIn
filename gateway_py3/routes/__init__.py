from __future__ import annotations

import os
import uuid

from gateway_py3 import arcmap_bridge_client
from gateway_py3.diagnostics import collect_agent_diagnostics, collect_diagnostics
from gateway_py3.folder_dialog import select_folder
from gateway_py3.llm_providers import public_config, save_config
from gateway_py3.workflow_protocol import workflow_protocol
from gateway_py3.experiments import planning_policy
from gateway_py3.paths import config_path, log_dir
from gateway_py3.routes import common, runs, arcmap, tools, voice
from gateway_py3.routes.event_topics import publish_mutation_events


def handle_get(state, path, app_version, query=None):
    if path == "/health":
        return {
            "ok": True,
            "app_version": app_version,
            "operation_count": len(state.catalog.operations)
        }
    if path == "/config":
        return {"config": public_config()}
    if path == "/api/runs":
        return {"runs": state.store.list_recent(
            limit=common.int_query(query, "limit", 50),
            mode=common.optional_query(query, "mode"),
            since=common.float_query(query, "since"),
            include_trace=common.bool_query(query, "include_trace", False),
        )}
    if path == "/api/workbench-state":
        return workbench_state(state, app_version)
    if path == "/tools/pending":
        return {"tools": state.store.list_pending_tools()}
    if path == "/api/capabilities":
        detail = common.bool_query(query, "detail", False)
        protocol = workflow_protocol()
        return {
            "app_version": app_version,
            "operation_count": len(state.catalog.operations),
            "operations": [
                common.public_operation(operation, detail=detail)
                for operation in state.catalog.all_operations()
            ],
            "workflow_protocol": protocol,
            "planning_policy": planning_policy(state.catalog, protocol),
        }
    if path == "/api/diagnostics":
        return collect_diagnostics(app_version, len(state.catalog.operations))
    if path == "/agent/diagnostics":
        return collect_agent_diagnostics(app_version, len(state.catalog.operations), state)
    if path == "/runs/report":
        return runs.report(state, common.optional_query(query, "mode"))
    if path.startswith("/runs/") and path.count("/") == 2:
        return {"run": state.store.get(_run_id(path.rsplit("/", 1)[1]))}
    if path == "/arcmap/health":
        return arcmap.health(state)
    if path == "/arcmap/bridges":
        return {"ok": True, "bridges": arcmap.bridges(state)}
    return None


def workbench_state(state, app_version):
    arcmap_payload = {"bridges": [], "error": ""}
    try:
        arcmap_payload["bridges"] = arcmap.bridges(state)
    except arcmap_bridge_client.ArcMapBridgeError as exc:
        arcmap_payload["error"] = str(exc)
    config = public_config()
    return {
        "health": {
            "ok": True,
            "app_version": app_version,
            "operation_count": len(state.catalog.operations),
        },
        "config": config,
        "runs": state.store.list_recent(include_trace=False),
        "arcmap": arcmap_payload,
    }


def handle_post(state, path, payload):
    result = _handle_post(state, path, payload)
    publish_mutation_events(state, path, result)
    return result


def _handle_post(state, path, payload):
    if path == "/voice/transcribe":
        return voice.transcribe(state, payload)
    if path == "/voice/correct":
        return voice.correct(state, payload)
    if path == "/runs":
        return runs.create(state, payload)
    if path.startswith("/runs/") and path.endswith("/cancel") and path.count("/") == 3:
        return runs.cancel(state, _run_id(path.split("/")[2]))
    if path == "/arcmap/register":
        return arcmap.register(state, payload)
    if path == "/arcmap/active":
        return arcmap.set_active(state, payload)
    if path == "/arcmap/permission":
        return arcmap.set_permission(state, payload)
    if path.startswith("/runs/") and path.endswith("/claim") and path.count("/") == 3:
        return {"run": state.store.claim_for_execution(
            _run_id(path.split("/")[2]), payload.get("target"), payload.get("owner_id"))}
    if path.startswith("/runs/") and path.endswith("/heartbeat") and path.count("/") == 3:
        return {"run": state.store.heartbeat_execution(
            _run_id(path.split("/")[2]), payload.get("owner_id"))}
    if path.startswith("/runs/") and path.endswith("/complete") and path.count("/") == 3:
        run_id = _run_id(path.split("/")[2])
        row = state.store.complete_execution(
            run_id,
            payload["status"],
            payload.get("result", {}),
            payload.get("owner_id"),
            payload.get("result_hash"),
            payload.get("target"),
        )
        if row["status"] == "executed":
            state.schedule_executed_recovery(run_id) if hasattr(state, "schedule_executed_recovery") else None
        return {"run": row}
    if path == "/config":
        return {"config": save_config(common.config_payload(payload))}
    if path == "/dialog/select-folder":
        return {"folder": select_folder(str(payload.get("title") or "选择文件夹"))}
    if path == "/open-path":
        target = payload.get("target")
        if target == "log_dir":
            resolved = log_dir()
        elif target == "config_file":
            resolved = config_path()
        else:
            raise ValueError("不支持的路径类型。")
        os.startfile(str(resolved))
        return {"ok": True, "path": str(resolved)}
    if path.startswith("/runs/") and path.endswith("/context") and path.count("/") == 3:
        return arcmap.receive_run_context(state, _run_id(path.split("/")[2]), payload)
    if path == "/runs/clear":
        return state.store.clear_runs(payload.get("mode"))
    if path.startswith("/runs/") and path.endswith("/delete"):
        return state.store.delete(path.split("/")[2])
    if path.startswith("/tools/") and path.endswith("/enable"):
        return tools.enable_pending_tool(state, path.split("/")[2])
    if path.startswith("/tools/") and path.endswith("/reject"):
        return tools.reject_pending_tool(state, path.split("/")[2])
    if path.startswith("/tools/") and path.endswith("/delete"):
        return tools.delete_pending_tool(state, path.split("/")[2])
    return None


def _run_id(value):
    parsed = uuid.UUID(value)
    if str(parsed) != value:
        raise ValueError("run id must be a canonical UUID.")
    return value
