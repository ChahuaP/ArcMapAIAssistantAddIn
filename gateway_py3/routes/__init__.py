from __future__ import annotations

from gateway_py3.diagnostics import collect_agent_diagnostics, collect_diagnostics
from gateway_py3.folder_dialog import select_folder
from gateway_py3.llm_providers import public_config, save_config
from gateway_py3.routes import common, external_agent, arcmap, planner, tools


def handle_get(state, path, app_version):
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
        return {"workflows": state.store.list_recent()}
    if path == "/projects":
        return {"projects": state.store.list_projects(), "active_project": state.store.get_active_project()}
    if path == "/projects/active":
        return {"project": state.store.get_active_project()}
    if path.startswith("/projects/") and path.endswith("/memory"):
        return {"memories": state.store.list_project_memories(path.split("/")[2])}
    if path.startswith("/projects/") and path.endswith("/events"):
        return {"events": state.store.list_project_events(path.split("/")[2])}
    if path == "/tools/pending":
        return {"tools": state.store.list_pending_tools()}
    if path == "/api/capabilities":
        return {
            "app_version": app_version,
            "operation_count": len(state.catalog.operations),
            "operations": [common.public_operation(operation) for operation in state.catalog.all_operations()]
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


def handle_post(state, path, payload):
    if path == "/plan":
        return planner.plan_request(state, payload)
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
        return {"folder": select_folder(str(payload.get("title") or "选择 GeoPilot 项目工作目录"))}
    if path == "/projects":
        return {"project": state.store.create_project(payload.get("name") or "", payload.get("workdir") or "")}
    if path == "/projects/active":
        return {"project": state.store.set_active_project(payload.get("project_id") or "")}
    if path.startswith("/projects/") and path.endswith("/delete"):
        return state.store.delete_project(path.split("/")[2])
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
        return state.store.clear_workflows(payload.get("project_id"), payload.get("mode"))
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
