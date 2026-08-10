"""GeoPilot gateway entry point (target architecture §3, §9).

The gateway is a ``ThreadingHTTPServer`` on ``127.0.0.1:8765`` that routes
every request through ``GeoPilotHttpAdapter`` → ``GeoPilotKernel``. No legacy
``GatewayState``, ``routes/`` or ``PlanningEngine`` is reachable from here.

Static files are served from ``WEB_ROOT`` (the web console). SSE events are
projected from the ``run_events`` journal by ``JournalEventProjection``.
"""
from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from gateway_py3.api.http_adapter import GeoPilotHttpAdapter, HttpError
from gateway_py3.kernel.coordinator import GeoPilotKernel, KernelPorts
from gateway_py3.kernel.store import JournalStore
from gateway_py3.model_runtime import ModelRuntime
from gateway_py3.model_runtime.configuration import (
    ModelConfigurationStore,
    build_provider_registry,
)
from gateway_py3.model_runtime.credentials import DpapiCredentialVault
from gateway_py3.intelligence.task_compiler import TaskCompiler
from gateway_py3.intelligence.workflow_engine import WorkflowEngine
from gateway_py3.runtime.policy import PolicyGate
from gateway_py3.runtime.arcmap_runtime import ArcMapRuntime
from gateway_py3.runtime.bridge_client import RealBridgeClient
from gateway_py3.runtime.acceptance_publisher import AcceptancePublisher
from gateway_py3.streaming.event_projection import JournalEventProjection
from gateway_py3.paths import WEB_ROOT
from gateway_py3.logs import write_event
from gateway_py3.static_server import is_static_path, serve_static
from gateway_py3.release import APP_VERSION

import threading

HOST = "127.0.0.1"
PORT = 8765
# §8: fixed Origin allowlist; no wildcard CORS.
ALLOWED_ORIGINS = (
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "null",  # file:// console
)


def _resolve_deployment_hash() -> str:
    """Read the build hash from install.json (set by the installer/packager).

    Fail fast (§0): a missing/unreadable deployment hash means the Gateway
    cannot prove its own build identity against the Bridge/Py2 runtime, so
    every lease fencing check would be vacuous. Never default to ``"unknown"``.
    """
    from gateway_py3.runtime.deployment_identity import gateway_identity_path, read_identity
    return read_identity(gateway_identity_path())


def build_kernel(store: JournalStore) -> tuple[GeoPilotKernel, JournalEventProjection, RealBridgeClient]:
    """Wire the production kernel with real adapters (§6).

    The composition root installs every explicitly configured provider adapter;
    the Kernel and intelligence modules only see provider-neutral ModelRuntime.
    The ``ArcMapRuntime`` uses the real bridge
    client (C# Bridge lease protocol). ``AcceptancePublisher`` uses a staging
    directory under ``LOCALAPPDATA``.
    """
    from gateway_py3.catalog_loader import OperationCatalog
    from gateway_py3.paths import localappdata_dir
    from gateway_py3.runtime.context_provider import BridgeContextProvider
    from gateway_py3.runtime.executor_adapter import ArcMapExecutorAdapter
    from gateway_py3.runtime.capability_provider import CapabilityProvider

    projection = JournalEventProjection(store)
    bridge_client = RealBridgeClient()

    # SSE projection subscribes to journal events (§14: SSE is a projection
    # of run_events). No monkey-patching — the store owns the notification.
    store.add_event_listener(projection.notify)

    catalog = OperationCatalog()
    credential_vault = DpapiCredentialVault()
    model_configuration = ModelConfigurationStore(vault=credential_vault).load()
    provider_registry = build_provider_registry(model_configuration, credential_vault)
    model_runtime = ModelRuntime(provider_registry, model_configuration.plan, store)
    compiler = TaskCompiler(model_runtime)
    engine = WorkflowEngine(catalog, model_runtime,
                            checkpoint_path=store.path, journal=store)

    deployment_hash = _resolve_deployment_hash()

    ports = KernelPorts(
        store=store,
        context=BridgeContextProvider(store, deployment_hash),
        capabilities=CapabilityProvider(catalog),
        compiler=compiler,
        planner=engine,
        policy=PolicyGate(),
        executor=ArcMapExecutorAdapter(bridge_client, deployment_hash, store),
        acceptance=AcceptancePublisher(),
        model=model_runtime,
        bridge=bridge_client,
    )
    return GeoPilotKernel(ports), projection, bridge_client


class _ServerState:
    def __init__(self):
        self.store = JournalStore()
        self.kernel, self.projection, self.bridge_client = build_kernel(self.store)
        # §7 crash recovery: reconcile runs left active by a previous process
        # before serving traffic. Done after kernel wiring, before the adapter
        # accepts requests.
        self.kernel.recover_interrupted_runs()
        self.adapter = GeoPilotHttpAdapter(
            self.kernel, self.bridge_client, ALLOWED_ORIGINS,
        )


STATE = _ServerState()


class Handler(BaseHTTPRequestHandler):
    server_version = "GeoPilot/" + APP_VERSION

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/events":
            self._serve_sse()
            return
        if path == "/health":
            self._json({"ok": True, "app_version": APP_VERSION})
            return
        try:
            payload = STATE.adapter.handle_get(path, query=None, headers=self._headers_dict())
            if payload is not None:
                self._json(payload)
            elif self._serve_static(path):
                pass
            else:
                self._json({"error": "Not found"}, 404)
        except HttpError as exc:
            self._json({"error": str(exc)}, exc.status)
        except Exception as exc:
            write_event("http.error", {"path": path, "error": str(exc)})
            self._json({"error": "系统处理时遇到问题。"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            result = STATE.adapter.handle_post(path, payload, self._headers_dict())
            if result is None:
                self._json({"error": "Not found"}, 404)
            else:
                self._json(result)
        except HttpError as exc:
            self._json({"error": str(exc)}, exc.status)
        except Exception as exc:
            write_event("http.error", {"path": path, "error": str(exc)})
            self._json({"error": "系统处理时遇到问题。"}, 500)

    def log_message(self, fmt, *args):
        message = fmt % args
        write_event("http.access", {"message": message})

    def _headers_dict(self):
        return {key: value for key, value in self.headers.items()}

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        # Browsers send UTF-8; Windows curl/tools may send GBK. Try UTF-8
        # first (the common case), fall back to GBK for local tools.
        try:
            return json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            return json.loads(raw.decode("gbk", errors="replace"))

    def _json(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _cors_headers(self):
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session-Id, X-CSRF-Token")

    def _serve_static(self, path):
        """Serve whitelisted static files from WEB_ROOT."""
        if not is_static_path(path):
            return False
        serve_static(self, path)
        return True

    def _serve_sse(self):
        """Stream SSE events from the journal projection (§14).

        §5: sessions are isolation boundaries. ``EventSource`` cannot set
        custom headers, so the session is passed via the ``session_id`` query
        parameter; events are filtered to the caller's session.
        """
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        session_id = (qs.get("session_id") or [""])[0]
        last_event_id = 0
        raw = self.headers.get("Last-Event-ID")
        if raw:
            try:
                last_event_id = max(0, int(raw))
            except ValueError:
                pass
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self._cors_headers()
        self.end_headers()
        # Initial ready event
        self._write_sse_event("ready", {"last_event_id": last_event_id})
        while True:
            events = STATE.projection.wait_after(last_event_id, timeout=25.0,
                                                 session_id=session_id)
            if not events:
                try:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return
                continue
            for event in events:
                self._write_sse_event(event["type"], event["payload"],
                                      event_id=int(event["id"]))
                last_event_id = int(event["id"])

    def _write_sse_event(self, event_type, payload, event_id=None):
        lines = []
        if event_id is not None:
            lines.append("id: %s" % event_id)
        lines.append("event: %s" % event_type)
        lines.append("data: %s" % json.dumps(payload, ensure_ascii=False, sort_keys=True))
        lines.append("")
        try:
            self.wfile.write(("\n".join(lines) + "\n").encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            raise


def main():
    WEB_ROOT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("GeoPilot gateway listening on http://%s:%s" % (HOST, PORT))
    server.serve_forever()


if __name__ == "__main__":
    main()
