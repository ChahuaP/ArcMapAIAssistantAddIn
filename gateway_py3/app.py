from __future__ import annotations

import json
import mimetypes
import socket
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from gateway_py3 import arcmap_bridge_client
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.diagnostics import collect_diagnostics
from gateway_py3.folder_dialog import FolderDialogError, select_folder
from gateway_py3.llm_providers import FULL_AGENT_MODE, ProviderError, public_config, save_config
from gateway_py3.logs import write_event
from gateway_py3.paths import WEB_ROOT
from gateway_py3.planner import AgenticPlanner, PlannerError
from gateway_py3.tool_builder import ToolBuilderError, delete_tool, enable_tool, reject_tool
from gateway_py3.validators import ValidationError, context_hash, prepare_workflow, validate_catalog
from gateway_py3.workflow_store import WorkflowStore


HOST = "127.0.0.1"
PORT = 8765
APP_VERSION = "0.14.0"
POLL_ACCESS_PATHS = (
    "/api/workflows",
    "/config",
    "/context",
    "/health",
    "/projects",
)


class GatewayState:
    def __init__(self):
        self.catalog = OperationCatalog()
        validate_catalog(self.catalog)
        self.store = WorkflowStore()
        self.store.clear_state("arcmap_context")
        self.planner = AgenticPlanner(catalog=self.catalog, store=self.store)

    def reload_catalog(self):
        self.catalog = OperationCatalog()
        validate_catalog(self.catalog)
        self.planner = AgenticPlanner(catalog=self.catalog, store=self.store)


STATE = GatewayState()


class Handler(BaseHTTPRequestHandler):
    server_version = "ArcMapAIAssistantGateway/0.1"

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._json({
                    "ok": True,
                    "app_version": APP_VERSION,
                    "operation_count": len(STATE.catalog.operations)
                })
            elif path == "/config":
                self._json({"config": public_config()})
            elif path == "/context":
                self._json({"context": STATE.store.get_state("arcmap_context")})
            elif path == "/api/workflows":
                self._json({"workflows": STATE.store.list_recent()})
            elif path == "/projects":
                self._json({"projects": STATE.store.list_projects(), "active_project": STATE.store.get_active_project()})
            elif path == "/projects/active":
                self._json({"project": STATE.store.get_active_project()})
            elif path.startswith("/projects/") and path.endswith("/memory"):
                project_id = path.split("/")[2]
                self._json({"memories": STATE.store.list_project_memories(project_id)})
            elif path.startswith("/projects/") and path.endswith("/events"):
                project_id = path.split("/")[2]
                self._json({"events": STATE.store.list_project_events(project_id)})
            elif path == "/tools/pending":
                self._json({"tools": STATE.store.list_pending_tools()})
            elif path == "/api/capabilities":
                self._json({
                    "app_version": APP_VERSION,
                    "operation_count": len(STATE.catalog.operations),
                    "operations": [_public_operation(operation) for operation in STATE.catalog.all_operations()]
                })
            elif path == "/api/diagnostics":
                self._json(collect_diagnostics(
                    APP_VERSION,
                    len(STATE.catalog.operations)
                ))
            elif path == "/pending":
                self._json({"workflow": STATE.store.pending()})
            elif path == "/arcmap/health":
                self._json(_arcmap_health())
            elif path == "/arcmap/bridges":
                self._json({"ok": True, "bridges": _arcmap_bridges()})
            elif path == "/" or path.startswith("/web/"):
                self._static(path)
            else:
                self._json({"error": "Not found"}, 404)
        except (KeyError, PlannerError, ProviderError, ToolBuilderError, ValidationError, ValueError, arcmap_bridge_client.ArcMapBridgeError) as exc:
            write_event("http.rejected", {"path": path, "error": str(exc)})
            self._json({"error": _public_error(exc)}, 400)
        except Exception as exc:
            write_event("http.error", {"path": path, "error": str(exc)})
            self._json({"error": "系统处理时遇到问题。请稍后重试，或查看运行日志。"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/plan":
                self._json(_plan_request(payload))
            elif path == "/agent/workflows/validate":
                self._json(_external_agent_validate(payload))
            elif path == "/agent/workflows/propose":
                self._json(_external_agent_propose(payload))
            elif path == "/arcmap/sync":
                self._json(_arcmap_sync())
            elif path == "/arcmap/register":
                self._json(_arcmap_register(payload))
            elif path == "/arcmap/active":
                self._json(_arcmap_set_active(payload))
            elif path == "/arcmap/permission":
                self._json(_arcmap_set_permission(payload))
            elif path == "/arcmap/execute-approved":
                self._json(_arcmap_execute_approved(payload))
            elif path == "/arcmap/execute-workflow":
                self._json(_arcmap_execute_workflow(payload))
            elif path == "/config":
                self._json({"config": save_config(_config_payload(payload))})
            elif path == "/dialog/select-folder":
                self._json({"folder": select_folder(str(payload.get("title") or "选择 GeoPilot 项目工作目录"))})
            elif path == "/projects":
                name = payload.get("name") or ""
                workdir = payload.get("workdir") or ""
                self._json({"project": STATE.store.create_project(name, workdir)})
            elif path == "/projects/active":
                project_id = payload.get("project_id") or ""
                self._json({"project": STATE.store.set_active_project(project_id)})
            elif path.startswith("/projects/") and path.endswith("/delete"):
                project_id = path.split("/")[2]
                self._json(STATE.store.delete_project(project_id))
            elif path == "/context":
                context = payload.get("context")
                if not isinstance(context, dict):
                    raise ValueError("context must be an object.")
                self._json({"context": STATE.store.set_state("arcmap_context", context)})
            elif path.startswith("/workflows/") and path.endswith("/approve"):
                workflow_id = path.split("/")[2]
                self._json({"workflow": STATE.store.approve(workflow_id)})
            elif path.startswith("/workflows/") and path.endswith("/claim"):
                workflow_id = path.split("/")[2]
                self._json({"workflow": STATE.store.claim(workflow_id)})
            elif path.startswith("/workflows/") and path.endswith("/executing"):
                workflow_id = path.split("/")[2]
                self._json({"workflow": STATE.store.mark_executing(workflow_id)})
            elif path == "/execution-result":
                workflow_id = payload["workflow_id"]
                status = payload["status"]
                result = payload.get("result", {})
                self._json({"workflow": STATE.store.finish(workflow_id, status, result)})
            elif path == "/workflows/clear":
                self._json(STATE.store.clear_workflows(payload.get("project_id"), payload.get("mode")))
            elif path.startswith("/workflows/") and path.endswith("/repair-custom-tool"):
                workflow_id = path.split("/")[2]
                self._json(_repair_custom_tool_workflow(workflow_id, payload))
            elif path.startswith("/workflows/") and path.endswith("/delete"):
                workflow_id = path.split("/")[2]
                self._json(STATE.store.delete(workflow_id))
            elif path.startswith("/tools/") and path.endswith("/enable"):
                tool_id = path.split("/")[2]
                tool = enable_tool(STATE.store, tool_id)
                STATE.reload_catalog()
                self._json({"tool": tool, "operation_count": len(STATE.catalog.operations)})
            elif path.startswith("/tools/") and path.endswith("/reject"):
                tool_id = path.split("/")[2]
                self._json({"tool": reject_tool(STATE.store, tool_id)})
            elif path.startswith("/tools/") and path.endswith("/delete"):
                tool_id = path.split("/")[2]
                tool = delete_tool(STATE.store, tool_id)
                STATE.reload_catalog()
                self._json({"tool": tool, "operation_count": len(STATE.catalog.operations)})
            else:
                self._json({"error": "Not found"}, 404)
        except (KeyError, FolderDialogError, PlannerError, ProviderError, ToolBuilderError, ValidationError, ValueError, arcmap_bridge_client.ArcMapBridgeError) as exc:
            write_event("http.rejected", {"path": path, "error": str(exc)})
            self._json({"error": _public_error(exc)}, 400)
        except Exception as exc:
            write_event("http.error", {"path": path, "error": str(exc)})
            self._json({"error": "系统处理时遇到问题。请稍后重试，或查看运行日志。"}, 500)

    def log_message(self, fmt, *args):
        message = fmt % args
        if _is_poll_access_message(message):
            return
        write_event("http.access", {"message": message})

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _static(self, path):
        rel = "index.html" if path == "/" else path[len("/web/"):]
        target = (WEB_ROOT / rel).resolve()
        if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.exists():
            self._json({"error": "Not found"}, 404)
            return
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main():
    WEB_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("ArcMap AI Assistant Gateway listening on http://%s:%s" % (HOST, PORT))
    server.serve_forever()


def _config_payload(payload):
    allowed = {}
    for key in ("default_mode", "semi_agent_provider", "semi_agent_model", "full_agent_provider", "full_agent_model"):
        if payload.get(key):
            allowed[key] = payload[key]
    providers = payload.get("providers") if isinstance(payload.get("providers"), dict) else {}
    allowed_providers = {}
    for provider_id in ("deepseek", "minimax", "zhipu"):
        source = providers.get(provider_id) or {}
        item = {}
        for field in ("api_key", "model", "base_url"):
            if isinstance(source.get(field), str) and source[field].strip():
                item[field] = source[field].strip()
        if provider_id == "deepseek" and item.get("api_key") and not item["api_key"].startswith("sk-"):
            raise ValueError("DeepSeek API key must start with sk-.")
        if item:
            allowed_providers[provider_id] = item
    if allowed_providers:
        allowed["providers"] = allowed_providers
    return allowed


def _plan_request(payload):
    mode = payload.get("mode") or public_config()["default_mode"]
    context = payload.get("context")
    if mode == FULL_AGENT_MODE:
        context = _arcmap_sync()["context"]
    elif context is None:
        stored_context = STATE.store.get_state("arcmap_context")
        if not stored_context:
            raise ValueError("请先在 ArcGIS 工具栏点击“同步上下文”。")
        context = stored_context["value"]

    project_id = payload.get("project_id") or ""
    if mode == FULL_AGENT_MODE and not project_id:
        active_project = STATE.store.get_active_project()
        project_id = active_project["id"] if active_project else ""
    row = STATE.planner.plan(payload["command"], context, mode=mode, project_id=project_id)
    STATE.reload_catalog()
    response = {"workflow": row}
    if mode == FULL_AGENT_MODE and (row.get("workflow") or {}).get("action") == "execute":
        STATE.store.approve(row["id"])
        bridge = _active_arcmap_bridge()
        response["execution"] = arcmap_bridge_client.execute_approved(
            allow_edits=True,
            port=bridge["port"],
            hwnd=bridge.get("hwnd")
        )
        response["workflow"] = STATE.store.get(row["id"])
    return response


def _external_agent_validate(payload):
    context = _external_agent_context(payload)
    workflow = payload.get("workflow")
    if not isinstance(workflow, dict):
        raise ValueError("workflow must be an object.")
    prepared = prepare_workflow(workflow, STATE.catalog, context)
    return {
        "ok": True,
        "context_hash": context_hash(context),
        "workflow": prepared
    }


def _external_agent_propose(payload):
    result = _external_agent_validate(payload)
    workflow = result["workflow"]
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        command = workflow.get("summary") or "External agent workflow"
    row = STATE.store.create_draft(
        command=command.strip(),
        context_hash=result["context_hash"],
        workflow=workflow,
        agent_trace=[{
            "type": "external_agent",
            "source": str(payload.get("source") or "external_agent")
        }],
        mode="external_agent",
        project_id=str(payload.get("project_id") or "")
    )
    return {"ok": True, "workflow": row}


def _external_agent_context(payload):
    context = payload.get("context")
    if isinstance(context, dict):
        return context
    stored_context = STATE.store.get_state("arcmap_context")
    if not stored_context:
        raise ValueError("请先在 ArcGIS 工具栏点击“同步上下文”。")
    return stored_context["value"]


def _arcmap_sync():
    bridge = _active_arcmap_bridge()
    before = STATE.store.get_state("arcmap_context")
    before_value = before.get("value") if before else None
    result = arcmap_bridge_client.sync_context_target(port=bridge["port"], hwnd=bridge.get("hwnd"))
    context = result.get("context") if isinstance(result.get("context"), dict) else {}
    deadline = time.time() + 10
    while not context and time.time() < deadline:
        stored = STATE.store.get_state("arcmap_context")
        if stored and isinstance(stored.get("value"), dict) and stored.get("value") is not before_value:
            context = stored["value"]
            break
        time.sleep(0.2)
    if not context:
        stored = STATE.store.get_state("arcmap_context")
        context = stored.get("value") if stored and isinstance(stored.get("value"), dict) else {}
    if not context:
        raise arcmap_bridge_client.ArcMapBridgeError("ArcMap Bridge 同步后没有返回有效 context。")
    return {
        "ok": True,
        "bridge": bridge,
        "context_hash": context_hash(context),
        "context": context
    }


def _arcmap_health():
    bridge = _active_arcmap_bridge()
    result = arcmap_bridge_client.health(port=bridge["port"])
    result["registered_bridge"] = bridge
    return result


def _arcmap_register(payload):
    pid = int(payload.get("pid") or 0)
    port = int(payload.get("port") or 0)
    if pid <= 0 or port <= 0:
        raise ValueError("pid and port are required.")
    bridge = {
        "pid": pid,
        "port": port,
        "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else {},
    }
    STATE.store.set_state("arcmap_bridge:%s" % pid, bridge)
    return {"ok": True, "bridge": bridge}


def _arcmap_set_active(payload):
    port = int(payload.get("port") or 0)
    pid = int(payload.get("pid") or 0)
    hwnd = int(payload.get("hwnd") or 0)
    if port <= 0 and pid <= 0 and hwnd <= 0:
        raise ValueError("pid, port or hwnd is required.")
    matches = []
    for bridge in _arcmap_bridges():
        if hwnd > 0 and bridge.get("hwnd") == hwnd:
            matches.append(bridge)
        elif port > 0 and bridge.get("port") == port and (hwnd <= 0 or bridge.get("hwnd") == hwnd):
            matches.append(bridge)
        elif pid > 0 and bridge.get("pid") == pid and (hwnd <= 0 or bridge.get("hwnd") == hwnd):
            matches.append(bridge)
    if not matches:
        raise ValueError("没有找到匹配的 ArcMap Bridge。")
    if len(matches) > 1:
        raise ValueError("匹配到多个 ArcMap，请用 hwnd 精确选择。")
    STATE.store.set_state("arcmap_active_bridge", matches[0])
    return {"ok": True, "bridge": matches[0]}


def _arcmap_set_permission(payload):
    permission = {
        "auto_execute": bool(payload.get("auto_execute")),
        "allow_edits": bool(payload.get("allow_edits")),
    }
    STATE.store.set_state("arcmap_permission", permission)
    return {"ok": True, "permission": permission}


def _arcmap_execute_approved(payload):
    row = STATE.store.pending()
    if not row:
        raise ValueError("没有已审批的工作流。")
    allow_edits = _arcmap_execution_permission(payload, row)
    bridge = _active_arcmap_bridge()
    result = arcmap_bridge_client.execute_approved(allow_edits=allow_edits, port=bridge["port"], hwnd=bridge.get("hwnd"))
    result["bridge"] = bridge
    return result


def _arcmap_execute_workflow(payload):
    proposed = _external_agent_propose(payload)
    row = proposed["workflow"]
    STATE.store.approve(row["id"])
    allow_edits = _arcmap_execution_permission(payload, row)
    bridge = _active_arcmap_bridge()
    result = arcmap_bridge_client.execute_approved(allow_edits=allow_edits, port=bridge["port"], hwnd=bridge.get("hwnd"))
    result["bridge"] = bridge
    return {
        "ok": True,
        "workflow": STATE.store.get(row["id"]),
        "execution": result
    }


def _arcmap_execution_permission(payload, row):
    permission = _stored_arcmap_permission()
    user_confirmed = bool(payload.get("confirmed"))
    auto_execute = bool(permission.get("auto_execute"))
    if not user_confirmed and not auto_execute:
        raise ValueError("执行前需要用户在 Codex 对话中确认，或先设置 arcmap permission auto_execute=true。")

    workflow = row.get("workflow") or {}
    has_edits = _workflow_has_side_effect(workflow, "edits_data")
    allow_edits = bool(payload.get("allow_edits")) or bool(permission.get("allow_edits"))
    if has_edits and not allow_edits:
        raise ValueError("该 workflow 会直接修改原始数据。需要用户明确设置 allow_edits=true。")
    return allow_edits


def _stored_arcmap_permission():
    stored = STATE.store.get_state("arcmap_permission")
    if not stored:
        return {}
    value = stored.get("value")
    return value if isinstance(value, dict) else {}


def _workflow_has_side_effect(workflow, side_effect):
    for step in workflow.get("steps") or []:
        operation_id = step.get("operation")
        if operation_id in STATE.catalog.operations and STATE.catalog.operations[operation_id].get("side_effects") == side_effect:
            return True
    return False


def _active_arcmap_bridge():
    stored = STATE.store.get_state("arcmap_active_bridge")
    if stored and isinstance(stored.get("value"), dict):
        bridge = stored["value"]
        try:
            health = arcmap_bridge_client.health(port=int(bridge["port"]))
            hwnd = int(bridge.get("hwnd") or 0)
            if hwnd <= 0:
                return _scan_arcmap_bridge()
            return {
                "pid": int(health.get("pid") or bridge.get("pid") or 0),
                "port": int(health.get("port") or bridge["port"]),
                "hwnd": hwnd,
                "summary": bridge.get("summary", {}),
            }
        except Exception:
            pass
    return _scan_arcmap_bridge()


def _arcmap_bridges():
    arcmap_bridge_client.ensure_running()
    candidates = []
    seen_ports = set()
    for item in STATE.store.list_state("arcmap_bridge:"):
        value = item.get("value")
        if not isinstance(value, dict):
            continue
        port = int(value.get("port") or 0)
        if port <= 0 or port in seen_ports:
            continue
        seen_ports.add(port)
        candidates.append(value)
    for port in [8766] + list(range(8767, 8790)):
        if port not in seen_ports:
            candidates.append({"pid": 0, "port": port})
            seen_ports.add(port)

    live = []
    for candidate in candidates:
        port = int(candidate.get("port") or 0)
        if port <= 0:
            continue
        if not _is_local_port_open(port):
            continue
        try:
            health = arcmap_bridge_client.health(port=port)
        except Exception:
            continue
        bridge_pid = int(health.get("pid") or candidate.get("pid") or 0)
        bridge_port = int(health.get("port") or port)
        summary = health.get("summary") if isinstance(health.get("summary"), dict) else candidate.get("summary", {})
        targets = summary.get("targets") if isinstance(summary, dict) else None
        if isinstance(targets, list) and targets:
            for target in targets:
                if not isinstance(target, dict):
                    continue
                hwnd = int(target.get("hwnd") or 0)
                bridge = {
                    "pid": bridge_pid,
                    "port": bridge_port,
                    "hwnd": hwnd,
                    "summary": {
                        "bridge": summary.get("bridge", "external"),
                        "title": target.get("title") or "",
                        "name": target.get("name") or "",
                    },
                }
                STATE.store.set_state("arcmap_bridge:%s:%s" % (bridge_pid, hwnd), bridge)
                live.append(bridge)
        else:
            bridge = {
                "pid": bridge_pid,
                "port": bridge_port,
                "summary": summary,
            }
            STATE.store.set_state("arcmap_bridge:%s" % bridge["pid"], bridge)
            live.append(bridge)
    return live


def _is_local_port_open(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.05)
    try:
        return sock.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        sock.close()


def _scan_arcmap_bridge():
    bridges = _arcmap_bridges()
    if len(bridges) == 1:
        STATE.store.set_state("arcmap_active_bridge", bridges[0])
        return bridges[0]
    if len(bridges) > 1:
        raise arcmap_bridge_client.ArcMapBridgeError("检测到多个 ArcMap，请先选择目标窗口。")
    raise arcmap_bridge_client.ArcMapBridgeError("ArcMap Bridge 未连接。")


def _public_operation(operation):
    schema = operation.get("parameters_schema", {})
    properties = schema.get("properties", {})
    return {
        "id": operation["id"],
        "category": operation["category"],
        "summary": operation["summary"],
        "model_card": operation.get("model_card", ""),
        "side_effects": operation["side_effects"],
        "required": schema.get("required", []),
        "parameters": sorted(properties.keys()),
        "parameters_schema": schema,
        "context_requirements": operation.get("context_requirements", {}),
        "output_policy": operation.get("output_policy", {}),
        "example": (operation.get("examples") or [{}])[0].get("user", "")
    }


def _is_poll_access_message(message):
    if " 200 " not in message:
        return False
    return any('"GET %s HTTP/' % path in message for path in POLL_ACCESS_PATHS)


def _repair_custom_tool_workflow(workflow_id, payload):
    source = STATE.store.get(workflow_id)
    if source.get("status") != "failed":
        raise ValueError("只有执行失败的任务可以一键迭代自建工具。")
    operation_ids = _custom_operation_ids(source.get("workflow") or {})
    if not operation_ids:
        raise ValueError("这个失败任务没有使用自建工具，不能进入自建工具迭代。")
    context = payload.get("context")
    if context is None:
        stored_context = STATE.store.get_state("arcmap_context")
        if not stored_context:
            raise ValueError("请先在 ArcGIS 工具栏点击“同步上下文”。")
        context = stored_context["value"]
    if not isinstance(context, dict):
        raise ValueError("context must be an object.")
    mode = source.get("mode") or public_config()["default_mode"]
    project_id = source.get("project_id") or ""
    command = _custom_tool_repair_command(source, operation_ids, payload.get("feedback") or "")
    row = STATE.planner.plan(command, context, mode=mode, project_id=project_id)
    STATE.reload_catalog()
    return {"workflow": row}


def _custom_operation_ids(workflow):
    result = []
    for step in workflow.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation_id = step.get("operation")
        if isinstance(operation_id, str) and operation_id.startswith("custom.") and operation_id not in result:
            result.append(operation_id)
    return result


def _custom_tool_repair_command(source, operation_ids, feedback):
    result = source.get("result") or {}
    error = result.get("error") if isinstance(result, dict) else ""
    traceback_text = result.get("traceback") if isinstance(result, dict) else ""
    extra = ""
    if isinstance(error, str) and "000840" in error and "空间参考" in error:
        extra = (
            "\n这个错误通常表示 CreateFeatureclass_management 的 spatial_reference 参数不是 ArcPy SpatialReference。"
            "修复时必须从输入图层读取：spatial_reference = arcpy.Describe(input_layer).spatialReference，"
            "不要传 context['spatial_reference']、spatialReference.name、factoryCode、字符串或 layer.spatialReference。"
        )
    if isinstance(feedback, str) and feedback.strip():
        feedback_text = "\n用户补充意见：%s" % feedback.strip()
    else:
        feedback_text = ""
    return (
        "进入自定义工具开发修复流程。上一次执行自建工具失败，请根据失败结果修订原工具。"
        "必须先调用 toolbuilder_get_draft 读取原工具，再调用 toolbuilder_revise_draft 修订同一个 tool_id；"
        "不要创建新工具，不要要求用户提供 executor 代码。"
        "\n涉及的自建 operation_id：%s"
        "\n原始用户请求：%s"
        "\n失败错误：%s"
        "%s"
        "%s"
        "\n失败工作流：%s"
        "\n失败 traceback：%s"
    ) % (
        "、".join(operation_ids),
        source.get("command") or "",
        error or "",
        extra,
        feedback_text,
        json.dumps(source.get("workflow") or {}, ensure_ascii=False, sort_keys=True),
        traceback_text or "",
    )


def _public_error(exc):
    if isinstance(exc, ValidationError):
        return "任务信息不完整或参数不符合要求。请换一种更明确的说法。"
    if isinstance(exc, KeyError):
        return "没有找到对应记录，请刷新页面后再试。"
    message = str(exc)
    if message.startswith("DeepSeek API key must start with sk-."):
        return "DeepSeek API Key 格式不对，请检查后重新填写。"
    return message


if __name__ == "__main__":
    main()
