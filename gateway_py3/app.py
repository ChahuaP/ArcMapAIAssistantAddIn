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
from gateway_py3.routes import arcmap as arcmap_routes
from gateway_py3.routes import common as route_common
from gateway_py3.routes import handle_get, handle_post
from gateway_py3.static_server import is_static_path, serve_static
from gateway_py3.tool_builder import ToolBuilderError
from gateway_py3.validators import ValidationError


HOST = "127.0.0.1"
PORT = 8765
APP_VERSION = "1.1.4"
STATE = GatewayState()
REJECTED_ERRORS = (
    KeyError,
    FolderDialogError,
    ProviderError,
    ToolBuilderError,
    ValidationError,
    ValueError,
    arcmap_bridge_client.ArcMapBridgeError,
)


class Handler(BaseHTTPRequestHandler):
    server_version = "ArcMapAIAssistantGateway/1.0"

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
    STATE.start_recovery_resolver()
    STATE.resume_interrupted_runs(
        lambda run_id, target, phase, fence: arcmap_routes.sync_context(
            STATE, run_id, phase, bridge=target, finalizer=fence
        ),
        lambda target, args: __import__("threading").Thread(target=target, args=args, daemon=True).start(),
    )
    print("ArcMap AI Assistant Gateway listening on http://%s:%s" % (HOST, PORT))
    server.serve_forever()


if __name__ == "__main__":
    main()
