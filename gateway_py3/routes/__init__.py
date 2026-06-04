from __future__ import annotations

from gateway_py3 import arcmap_bridge_client
from gateway_py3.diagnostics import collect_agent_diagnostics, collect_diagnostics
from gateway_py3.folder_dialog import select_folder
from gateway_py3.llm_providers import public_config, save_config
from gateway_py3.routes import common, external_agent, arcmap, planner, tools, voice
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
    if path == "/context":
        return {"context": state.store.get_state("arcmap_context")}
    if path == "/api/workflows":
        return {"workflows": state.store.list_recent(
            limit=common.int_query(query, "limit", 50),
            mode=common.optional_query(query, "mode"),
            since=common.float_query(query, "since"),
            include_trace=common.bool_query(query, "include_trace", False),
        )}
    if path.startswith("/workflows/") and path.count("/") == 2:
        return {"workflow": state.store.get(path.split("/")[2])}
    if path == "/api/workbench-state":
        return workbench_state(state, app_version)
    if path == "/tools/pending":
        return {"tools": state.store.list_pending_tools()}
    if path == "/api/capabilities":
        detail = common.bool_query(query, "detail", False)
        return {
            "app_version": app_version,
            "operation_count": len(state.catalog.operations),
            "operations": [common.public_operation(operation, detail=detail) for operation in state.catalog.all_operations()]
        }
    if path == "/api/diagnostics":
        return collect_diagnostics(app_version, len(state.catalog.operations))
    if path == "/agent/diagnostics":
        return collect_agent_diagnostics(app_version, len(state.catalog.operations), state)
    if path == "/pending":
        return {"workflow": state.store.pending()}
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
        "context": state.store.get_state("arcmap_context"),
        "workflows": state.store.list_recent(include_trace=False),
        "arcmap": arcmap_payload,
    }


def handle_post(state, path, payload):
    result = _handle_post(state, path, payload)
    publish_mutation_events(state, path, result)
    return result


def _handle_post(state, path, payload):
    if path == "/plan":
        return planner.plan_request(state, payload)
    if path == "/voice/transcribe":
        return voice.transcribe(state, payload)
    if path == "/voice/correct":
        return voice.correct(state, payload)
    if path == "/agent/workflows/validate":
        return external_agent.validate_workflow(state, payload)
    if path == "/agent/workflows/propose":
        return external_agent.propose_workflow(state, payload)
    if path == "/arcmap/sync":
        return arcmap.sync_context(state)
    if path == "/arcmap/register":
        return arcmap.register(state, payload)
    if path == "/arcmap/active":
        return arcmap.set_active(state, payload)
    if path == "/arcmap/permission":
        return arcmap.set_permission(state, payload)
    if path == "/arcmap/execute-approved":
        return arcmap.execute_approved(state, payload)
    if path == "/arcmap/execute-workflow":
        return arcmap.execute_workflow(state, payload)
    if path == "/config":
        return {"config": save_config(common.config_payload(payload))}
    if path == "/dialog/select-folder":
        return {"folder": select_folder(str(payload.get("title") or "选择文件夹"))}
    if path == "/context":
        context = payload.get("context")
        if not isinstance(context, dict):
            raise ValueError("context must be an object.")
        return {"context": state.store.set_state("arcmap_context", context)}
    if path.startswith("/workflows/") and path.endswith("/approve"):
        return {"workflow": state.store.approve(path.split("/")[2])}
    if path.startswith("/workflows/") and path.endswith("/claim"):
        return {"workflow": state.store.claim(path.split("/")[2])}
    if path.startswith("/workflows/") and path.endswith("/executing"):
        return {"workflow": state.store.mark_executing(path.split("/")[2])}
    if path == "/execution-result":
        return {"workflow": state.store.finish(payload["workflow_id"], payload["status"], payload.get("result", {}))}
    if path == "/workflows/clear":
        return state.store.clear_workflows(payload.get("mode"))
    if path.startswith("/workflows/") and path.endswith("/repair-custom-tool"):
        return planner.repair_custom_tool_workflow(state, path.split("/")[2], payload)
    if path.startswith("/workflows/") and path.endswith("/delete"):
        return state.store.delete(path.split("/")[2])
    if path.startswith("/tools/") and path.endswith("/enable"):
        return tools.enable_pending_tool(state, path.split("/")[2])
    if path.startswith("/tools/") and path.endswith("/reject"):
        return tools.reject_pending_tool(state, path.split("/")[2])
    if path.startswith("/tools/") and path.endswith("/delete"):
        return tools.delete_pending_tool(state, path.split("/")[2])
    return None
