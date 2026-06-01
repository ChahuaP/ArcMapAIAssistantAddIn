from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from gateway_py3 import arcmap_bridge_client
from gateway_py3.event_bus import serve_event_stream
from gateway_py3.folder_dialog import FolderDialogError
from gateway_py3.gateway_state import GatewayState
from gateway_py3.llm_providers import ProviderError
from gateway_py3.logs import write_event
from gateway_py3.paths import WEB_ROOT
from gateway_py3.planner import PlannerError
from gateway_py3.routes import arcmap as arcmap_routes
from gateway_py3.routes import common as route_common
from gateway_py3.routes import external_agent as external_agent_routes
from gateway_py3.routes import handle_get, handle_post
from gateway_py3.routes import planner as planner_routes
from gateway_py3.static_server import is_static_path, serve_static
from gateway_py3.tool_builder import ToolBuilderError
from gateway_py3.validators import ValidationError


HOST = "127.0.0.1"
PORT = 8765
APP_VERSION = "0.19.0"
STATE = GatewayState()
REJECTED_ERRORS = (
    KeyError,
    FolderDialogError,
    PlannerError,
    ProviderError,
    ToolBuilderError,
    ValidationError,
    ValueError,
    arcmap_bridge_client.ArcMapBridgeError,
)


class Handler(BaseHTTPRequestHandler):
    server_version = "ArcMapAIAssistantGateway/0.1"

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/events":
            try:
                serve_event_stream(self, STATE.events)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            return
        try:
            payload = handle_get(STATE, path, APP_VERSION, parse_qs(parsed.query))
            if payload is not None:
                self._json(payload)
            elif is_static_path(path):
                serve_static(self, path)
            else:
                self._json({"error": "Not found"}, 404)
        except REJECTED_ERRORS as exc:
            write_event("http.rejected", {"path": path, "error": str(exc)})
            self._json({"error": route_common.public_error(exc)}, 400)
        except Exception as exc:
            write_event("http.error", {"path": path, "error": str(exc)})
            self._json({"error": "系统处理时遇到问题。请稍后重试，或查看运行日志。"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = handle_post(STATE, path, self._read_json())
            if payload is None:
                self._json({"error": "Not found"}, 404)
            else:
                self._json(payload)
        except REJECTED_ERRORS as exc:
            write_event("http.rejected", {"path": path, "error": str(exc)})
            self._json({"error": route_common.public_error(exc)}, 400)
        except Exception as exc:
            write_event("http.error", {"path": path, "error": str(exc)})
            self._json({"error": "系统处理时遇到问题。请稍后重试，或查看运行日志。"}, 500)

    def log_message(self, fmt, *args):
        message = fmt % args
        if route_common.is_quiet_access_message(message):
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


def main():
    WEB_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("ArcMap AI Assistant Gateway listening on http://%s:%s" % (HOST, PORT))
    server.serve_forever()


def _plan_request(payload):
    return planner_routes.plan_request(STATE, payload, port_checker=_is_local_port_open)


def _external_agent_validate(payload):
    return external_agent_routes.validate_workflow(STATE, payload)


def _external_agent_propose(payload):
    return external_agent_routes.propose_workflow(STATE, payload)


def _arcmap_sync():
    return arcmap_routes.sync_context(STATE, port_checker=_is_local_port_open)


def _arcmap_health():
    return arcmap_routes.health(STATE, port_checker=_is_local_port_open)


def _arcmap_register(payload):
    return arcmap_routes.register(STATE, payload)


def _arcmap_set_active(payload):
    return arcmap_routes.set_active(STATE, payload, port_checker=_is_local_port_open)


def _arcmap_set_permission(payload):
    return arcmap_routes.set_permission(STATE, payload)


def _arcmap_execute_approved(payload):
    return arcmap_routes.execute_approved(STATE, payload, port_checker=_is_local_port_open)


def _arcmap_execute_workflow(payload):
    return arcmap_routes.execute_workflow(STATE, payload, port_checker=_is_local_port_open)


def _arcmap_bridges():
    return arcmap_routes.bridges(STATE, port_checker=_is_local_port_open)


def _active_arcmap_bridge():
    return arcmap_routes.active_bridge(STATE, port_checker=_is_local_port_open)


def _is_local_port_open(port):
    return arcmap_routes.is_local_port_open(port)


def _public_operation(operation):
    return route_common.public_operation(operation, detail=True)


def _public_error(exc):
    return route_common.public_error(exc)


if __name__ == "__main__":
    main()
