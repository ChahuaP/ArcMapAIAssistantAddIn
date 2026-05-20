from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.deepseek_client import DeepSeekError, public_config, save_config
from gateway_py3.logs import write_event
from gateway_py3.paths import WEB_ROOT
from gateway_py3.planner import AgenticPlanner, PlannerError
from gateway_py3.validators import ValidationError, validate_catalog
from gateway_py3.workflow_store import WorkflowStore


HOST = "127.0.0.1"
PORT = 8765
APP_VERSION = "0.10.2"


class GatewayState:
    def __init__(self):
        self.catalog = OperationCatalog()
        validate_catalog(self.catalog)
        self.store = WorkflowStore()
        self.store.clear_state("arcmap_context")
        self.planner = AgenticPlanner(catalog=self.catalog, store=self.store)


STATE = GatewayState()


class Handler(BaseHTTPRequestHandler):
    server_version = "ArcMapAIAssistantGateway/0.1"

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self._json({
                    "ok": True,
                    "app_version": APP_VERSION,
                    "catalog_version": STATE.catalog.version,
                    "operation_count": len(STATE.catalog.operations)
                })
            elif path == "/config":
                self._json({"config": public_config()})
            elif path == "/context":
                self._json({"context": STATE.store.get_state("arcmap_context")})
            elif path == "/api/workflows":
                self._json({"workflows": STATE.store.list_recent()})
            elif path == "/api/capabilities":
                self._json({
                    "catalog_version": STATE.catalog.version,
                    "operation_count": len(STATE.catalog.operations),
                    "operations": [_public_operation(operation) for operation in STATE.catalog.all_operations()]
                })
            elif path == "/pending":
                self._json({"workflow": STATE.store.pending()})
            elif path == "/" or path.startswith("/web/"):
                self._static(path)
            else:
                self._json({"error": "Not found"}, 404)
        except (PlannerError, DeepSeekError, ValidationError, ValueError) as exc:
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
                context = payload.get("context")
                if context is None:
                    stored_context = STATE.store.get_state("arcmap_context")
                    if not stored_context:
                        raise ValueError("请先在 ArcGIS 工具栏点击“同步上下文”。")
                    context = stored_context["value"]
                row = STATE.planner.plan(payload["command"], context)
                self._json({"workflow": row})
            elif path == "/config":
                self._json({"config": save_config(_config_payload(payload))})
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
                self._json(STATE.store.clear_workflows())
            elif path.startswith("/workflows/") and path.endswith("/delete"):
                workflow_id = path.split("/")[2]
                self._json(STATE.store.delete(workflow_id))
            else:
                self._json({"error": "Not found"}, 404)
        except (PlannerError, DeepSeekError, ValidationError, ValueError) as exc:
            write_event("http.rejected", {"path": path, "error": str(exc)})
            self._json({"error": _public_error(exc)}, 400)
        except Exception as exc:
            write_event("http.error", {"path": path, "error": str(exc)})
            self._json({"error": "系统处理时遇到问题。请稍后重试，或查看运行日志。"}, 500)

    def log_message(self, fmt, *args):
        write_event("http.access", {"message": fmt % args})

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
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:%s" % PORT)
        self.end_headers()
        self.wfile.write(data)

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
    if payload.get("deepseek_api_key"):
        key = payload["deepseek_api_key"].strip()
        if not key.startswith("sk-"):
            raise ValueError("DeepSeek API key must start with sk-.")
        allowed["deepseek_api_key"] = key
    if payload.get("model"):
        allowed["model"] = payload["model"].strip()
    if payload.get("base_url"):
        allowed["base_url"] = payload["base_url"].strip().rstrip("/")
    return allowed


def _public_operation(operation):
    schema = operation.get("parameters_schema", {})
    properties = schema.get("properties", {})
    return {
        "id": operation["id"],
        "category": operation["category"],
        "summary": operation["summary"],
        "side_effects": operation["side_effects"],
        "required": schema.get("required", []),
        "parameters": sorted(properties.keys()),
        "example": (operation.get("examples") or [{}])[0].get("user", "")
    }


def _public_error(exc):
    if isinstance(exc, ValidationError):
        return "任务信息不完整或参数不符合要求。请换一种更明确的说法。"
    message = str(exc)
    if message.startswith("DeepSeek API key must start with sk-."):
        return "DeepSeek API Key 格式不对，请检查后重新填写。"
    return message


if __name__ == "__main__":
    main()
