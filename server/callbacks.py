"""The Py2/Bridge callback HTTP surface of the boundary server.

The ArcMap Python 2 runtime and the C# Bridge post their callbacks to
127.0.0.1:8765 exactly as they posted to the old gateway; this module
implements that surface and routes fenced callbacks into the BridgeSession.
There is no public web API here — the web console is dsh's, not ours.
"""
from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .identity import APP_VERSION
from .paths import localappdata_dir


def _access_log(line: str) -> None:
    try:
        directory = localappdata_dir() / "boundary"
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / "callbacks.log", "a", encoding="utf-8") as stream:
            stream.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), line))
    except OSError:
        pass

CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 8765
_RUN_PATH = re.compile(r"^/runs/([0-9a-fA-F-]{36})/(receipt|context|lease-ack|heartbeat|sample|acceptance-probe)$")


class _CallbackHTTPServer(ThreadingHTTPServer):
    # Exactly one boundary server owns 8765. On Windows SO_REUSEADDR would
    # silently allow a second bind and steal callbacks, so refuse it.
    allow_reuse_address = False
    daemon_threads = True


class CallbackServer:
    """ThreadingHTTPServer bound to the callback port; fails loud on conflict."""

    def __init__(self, session, host: str = CALLBACK_HOST, port: int = CALLBACK_PORT):
        self.session = session
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002 - stdlib signature
                pass  # keep the stdio MCP channel free of HTTP noise

            def _send(self, status: int, document: Dict[str, Any]) -> None:
                _access_log("%s %s -> %s %s" % (self.command, self.path, status,
                                                document.get("error", "")))
                body = json.dumps(document, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                # The dsh web console (browser) polls /status across ports.
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):  # noqa: N802 - stdlib naming
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):  # noqa: N802 - stdlib naming
                if self.path == "/health":
                    self._send(200, {"ok": True, "app_version": APP_VERSION,
                                     "service": "arcmap-harness-boundary"})
                    return
                if self.path == "/status":
                    self._send(200, server.session.status())
                    return
                if self.path == "/arcmap/bridges":
                    try:
                        targets = server.session.targets()
                    except Exception as exc:
                        self._send(503, {"error": str(exc)})
                        return
                    self._send(200, {"ok": True, "bridges": [
                        {"bridge_pid": target["bridge_pid"],
                         "bridge_port": target["bridge_port"],
                         "arcmap_pid": target["arcmap_pid"],
                         "hwnd": target["hwnd"]}
                        for target in targets]})
                    return
                self._send(404, {"error": "unknown path: %s" % self.path})

            def do_POST(self):  # noqa: N802 - stdlib naming
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    payload = json.loads(self.rfile.read(length).decode("utf-8")) \
                        if length else {}
                except ValueError:
                    self._send(400, {"error": "callback body is not valid JSON"})
                    return
                if not isinstance(payload, dict):
                    self._send(400, {"error": "callback body must be an object"})
                    return
                if self.path == "/arcmap/register":
                    self._send(200, {"ok": True})
                    return
                match = _RUN_PATH.match(self.path)
                if match is None:
                    self._send(404, {"error": "unknown path: %s" % self.path})
                    return
                run_id, action = match.group(1), match.group(2)
                try:
                    self._send(200, server.dispatch(action, run_id, payload))
                except ValueError as exc:  # fencing and contract failures -> 409
                    self._send(409, {"error": str(exc)})
                except Exception as exc:  # noqa: BLE001 - surface as 503
                    self._send(503, {"error": "%s: %s" % (type(exc).__name__, exc)})

        self._httpd = _CallbackHTTPServer((host, port), Handler)

    def dispatch(self, action: str, run_id: str,
                 payload: Dict[str, Any]) -> Dict[str, Any]:
        session = self.session
        if action == "receipt":
            session.receive_receipt(run_id, payload)
            return {"ok": True}
        if action == "context":
            session.receive_context(run_id, payload)
            return {"ok": True}
        if action == "lease-ack":
            return session.lease_ack(run_id, payload)
        if action == "heartbeat":
            session.heartbeat(run_id, payload)
            return {"ok": True}
        if action == "sample":
            session.receive_sample(run_id, payload)
            return {"ok": True}
        if action == "acceptance-probe":
            session.receive_probe(run_id, payload)
            return {"ok": True}
        raise ValueError("unknown callback action: %s" % action)

    def start(self) -> None:
        thread = threading.Thread(target=self._httpd.serve_forever,
                                  name="boundary-callbacks", daemon=True)
        thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def start_callback_server(session, port: int = CALLBACK_PORT) -> CallbackServer:
    """Bind and start; OSError here means the old gateway still owns 8765."""
    server = CallbackServer(session, port=port)
    server.start()
    return server
