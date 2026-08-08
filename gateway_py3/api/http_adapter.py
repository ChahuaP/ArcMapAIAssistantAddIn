"""GeoPilot HTTP adapter (§9: routes become adapters of GeoPilotKernel).

Only the kernel's four operations are exposed to HTTP callers:

  POST /api/v1/runs              -> submit(request_envelope)
  GET  /api/v1/runs              -> list runs (kernel store projection)
  GET  /api/v1/runs/<id>         -> inspect(run_id)
  POST /api/v1/runs/<id>/decide  -> decide(run_id, approved)
  POST /api/v1/runs/<id>/resume  -> resume(run_id)

Bridge callbacks (lease protocol, docs/GEOPILOT_BRIDGE_LEASE_PROTOCOL.md)
are routed with lease fencing into the ArcMapRuntime bridge client:

  POST /runs/<id>/receipt    -> bridge_client.receive_receipt (fenced)
  POST /runs/<id>/heartbeat  -> fenced lease heartbeat
  POST /runs/<id>/context    -> fenced context callback
  POST /runs/<id>/lease-ack  -> fenced lease acknowledgement
  POST /runs/<id>/reconcile  -> fenced reconcile

Security (§8): fixed Origin allowlist, session token header, no wildcard
CORS. Callers must present X-Session-Id (a client-generated UUID); the
adapter derives the caller identity from the session.
"""
from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, Optional

from ..kernel import contracts
from ..kernel.contracts import (
    CallerIdentity, RequestEnvelope, SideEffectScope,
)
from ..kernel.coordinator import GeoPilotKernel
from ..kernel.store import JournalStore

API_PREFIX = "/api/v1"
# Fixed Origin allowlist (§8): the local web console and the file:// console.
ALLOWED_ORIGINS = frozenset({
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "null",  # file:// pages report Origin: null
})
_RUN_ID_RE = re.compile(r"^/api/v1/runs/([0-9a-fA-F-]{36})(/[a-z-]+)?$")


class HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class GeoPilotHttpAdapter:
    """Maps HTTP requests to GeoPilotKernel operations (§3, §9).

    The adapter never touches the store, planner, provider or ArcMap client
    directly. Bridge callbacks are forwarded to the injected bridge client
    (the ArcMapRuntime's BridgeClient) after lease fencing.
    """

    def __init__(self, kernel: GeoPilotKernel,
                 store: JournalStore,
                 bridge_client: Optional[Any] = None,
                 allowed_origins: Optional[frozenset] = None):
        self.kernel = kernel
        self.store = store
        self.bridge_client = bridge_client
        self.allowed_origins = allowed_origins if allowed_origins is not None else ALLOWED_ORIGINS
        self._op_count = None
        self._bridge_cache = None

    def _operation_count(self) -> int:
        if self._op_count is None:
            try:
                from gateway_py3.catalog_loader import OperationCatalog
                self._op_count = len(OperationCatalog().operations)
            except Exception:
                self._op_count = 0
        return self._op_count

    # -- request dispatch ---------------------------------------------------

    def handle_get(self, path: str, query: Optional[Dict[str, Any]] = None,
                   headers: Optional[Dict[str, str]] = None) -> Any:
        if path == API_PREFIX + "/runs":
            session_id = (headers or {}).get("X-Session-Id", "")
            runs = [
                self._run_view(run["run_id"])
                for run in self.store.list_recent_runs(limit=50)
                if not session_id or run.get("session_id") == session_id
            ]
            return {"runs": runs}
        match = _RUN_ID_RE.match(path)
        if match:
            run_id = match.group(1)
            suffix = match.group(2) or ""
            if not suffix:
                return {"run": self._run_view(run_id)}
            if suffix == "/delete":
                self.store.delete_run(run_id)
                return {"ok": True}
        if path == "/health":
            return {"ok": True, "app_version": "2.0.0"}
        if path == "/api/workbench-state":
            return self._workbench_state(headers)
        if path == "/config":
            return {"config": self._public_config()}
        if path == "/arcmap/bridges":
            import time as _time
            now = _time.time()
            if self._bridge_cache and now - self._bridge_cache[0] < 3.0:
                return {"ok": True, "bridges": self._bridge_cache[1]}
            bridges = self._scan_bridges()
            self._bridge_cache = (now, bridges)
            return {"ok": True, "bridges": bridges}
        if path == "/api/capabilities":
            return self._capabilities()
        if path == "/api/diagnostics":
            return self._diagnostics()
        if path == "/tools/pending":
            return {"tools": []}
        return None

    def _diagnostics(self) -> Dict[str, Any]:
        checks = [
            {"id": "gateway", "label": "网关", "status": "ok",
             "detail": "GeoPilot 2.0.0 运行中。"},
            {"id": "bridge", "label": "ArcMap Bridge", "status": "ok",
             "detail": "Bridge 连接状态请看左侧状态栏。"},
        ]
        try:
            from gateway_py3.llm_providers import public_config
            cfg = public_config()
            providers = cfg.get("providers", {})
            minimax = providers.get("minimax", {})
            if minimax.get("has_api_key"):
                checks.append({"id": "model", "label": "模型配置", "status": "ok",
                               "detail": "MiniMax API Key 已配置。"})
            else:
                checks.append({"id": "model", "label": "模型配置", "status": "warn",
                               "detail": "MiniMax API Key 未配置。"})
        except Exception as exc:
            checks.append({"id": "model", "label": "模型配置", "status": "bad",
                           "detail": str(exc)[:80]})
        count = self._operation_count()
        checks.append({"id": "catalog", "label": "能力目录", "status": "ok",
                       "detail": "%d 个操作。" % count})
        all_ok = all(c["status"] == "ok" for c in checks)
        return {"ok": all_ok, "app_version": "2.0.0", "checks": checks}

    def _public_config(self) -> Dict[str, Any]:
        try:
            from gateway_py3.llm_providers import public_config
            return public_config()
        except Exception:
            return {}

    def _capabilities(self) -> Dict[str, Any]:
        try:
            from gateway_py3.catalog_loader import OperationCatalog
            catalog = OperationCatalog()
            return {
                "app_version": "2.0.0",
                "operation_count": len(catalog.operations),
                "operations": [
                    {
                        "id": op["id"],
                        "category": op.get("category", "other"),
                        "summary": op.get("summary", ""),
                        "side_effects": op.get("side_effects", ""),
                    }
                    for op in catalog.all_operations()
                ],
            }
        except Exception as exc:
            return {"app_version": "2.0.0", "operation_count": 0,
                    "operations": [], "error": str(exc)}

    def _bridge_list(self) -> list:
        bridges = self._scan_bridges()
        if not bridges:
            self._start_bridge()
            bridges = self._scan_bridges()
        return bridges

    def _scan_bridges(self) -> list:
        bridges = []
        seen_pids = set()
        for port in (8766, 8767, 8768):
            try:
                import json as _json
                import urllib.request
                url = "http://127.0.0.1:%d/health" % port
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=0.5) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
                if data.get("ok"):
                    pid = data.get("bridge_pid", 0)
                    if pid and pid in seen_pids:
                        continue
                    seen_pids.add(pid)
                    bridges.append({
                        "bridge_pid": pid,
                        "bridge_port": port,
                        "summary": data.get("summary", {}),
                    })
            except Exception:
                continue
        return bridges

    def _start_bridge(self):
        """Start ArcMapBridge.exe if it's not running (§6.7 Bridge startup)."""
        import os
        import subprocess
        import json as _json
        install_path = os.path.join(
            os.environ.get("APPDATA", ""), "ArcMapAIAssistant", "install.json"
        )
        try:
            with open(install_path, "r", encoding="utf-8-sig") as f:
                cfg = _json.load(f)
            exe = cfg.get("bridge_exe", "")
            if exe and os.path.isfile(exe):
                subprocess.Popen(
                    [exe], cwd=os.path.dirname(exe),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                import time
                time.sleep(3)
        except Exception as exc:
            from gateway_py3.logs import write_event
            write_event("bridge.start_failed", {"error": str(exc)[:200]})

    def _workbench_state(self, headers=None) -> Dict[str, Any]:
        """Initial state payload for the web console.

        Bridge scanning is deferred to the /arcmap/bridges endpoint to keep
        this call fast (the front-end polls /arcmap/bridges separately).
        """
        session_id = (headers or {}).get("X-Session-Id", "")
        all_runs = self.store.list_recent_runs(limit=50)
        runs = [
            self._run_view(run["run_id"])
            for run in all_runs
            if not session_id or run.get("session_id") == session_id
        ]
        op_count = self._operation_count()
        return {
            "health": {"ok": True, "app_version": "2.0.0", "operation_count": op_count},
            "config": self._public_config(),
            "runs": runs,
            "arcmap": {"bridges": [], "error": ""},
        }

    def handle_post(self, path: str, payload: Dict[str, Any],
                    headers: Optional[Dict[str, str]] = None) -> Any:
        headers = headers or {}
        if path == API_PREFIX + "/runs":
            return {"run": self._submit(payload, headers)}
        match = _RUN_ID_RE.match(path)
        if match:
            run_id = match.group(1)
            suffix = match.group(2) or ""
            if suffix == "/decide":
                approved = bool(payload.get("approved"))
                return {"run": self._run_view_from_kernel(
                    self.kernel.decide(run_id, approved))}
            if suffix == "/resume":
                return {"run": self._run_view_from_kernel(
                    self.kernel.resume(run_id))}
        # Bridge callbacks (lease protocol)
        bridge = self._bridge_callback(path, payload)
        if bridge is not None:
            return bridge
        # Stubs for front-end endpoints not yet migrated (config, tools, etc.)
        if path == "/config":
            try:
                from gateway_py3.llm_providers import save_config
                fields = {}
                for key in ("primary_provider", "primary_model"):
                    if payload.get(key):
                        fields[key] = payload[key]
                providers = payload.get("providers") if isinstance(payload.get("providers"), dict) else {}
                fields["providers"] = {}
                from gateway_py3.llm_providers import SUPPORTED_PROVIDERS, PROVIDER_SECRET_FIELDS
                for pid in SUPPORTED_PROVIDERS:
                    src = providers.get(pid) or {}
                    item = {}
                    for f in ("model", "base_url") + PROVIDER_SECRET_FIELDS[pid]:
                        v = src.get(f)
                        if isinstance(v, str) and v.strip():
                            item[f] = v.strip()
                    clear = src.get("clear_secret_fields")
                    if isinstance(clear, list):
                        item["clear_secret_fields"] = [f for f in clear if f in PROVIDER_SECRET_FIELDS[pid]]
                    if item:
                        fields["providers"][pid] = item
                return {"config": save_config(fields)}
            except Exception as exc:
                raise HttpError(400, str(exc))
        if path == "/open-path":
            import os
            target = payload.get("target")
            if target == "log_dir":
                from gateway_py3.paths import log_dir
                os.startfile(str(log_dir()))
            elif target == "config_file":
                from gateway_py3.paths import config_path
                os.startfile(str(config_path()))
            else:
                raise HttpError(400, "不支持的路径类型。")
            return {"ok": True}
        if path.startswith("/tools/") and path.endswith(("/enable", "/reject", "/delete")):
            return {"ok": True}
        return None

    # -- kernel operations --------------------------------------------------

    def _submit(self, payload: Dict[str, Any],
                headers: Dict[str, str]) -> Dict[str, Any]:
        text = payload.get("text") or payload.get("command")
        if not isinstance(text, str) or not text.strip():
            raise HttpError(400, "text is required.")
        session_id = headers.get("X-Session-Id") or payload.get("session_id")
        try:
            session_id = str(uuid.UUID(session_id))
        except (ValueError, TypeError):
            raise HttpError(400, "X-Session-Id must be a canonical UUID.")
        execute = bool(payload.get("execute", False))
        side_effects = None
        if execute:
            level = int(payload.get("side_effect_level", 1))
            if level < 1 or level > 4:
                raise HttpError(400, "side_effect_level must be 1..4.")
            side_effects = SideEffectScope(
                level=level,
                paths=tuple(payload.get("output_paths") or ()),
                datasets=tuple(payload.get("datasets") or ()),
            )
        envelope = RequestEnvelope(
            session_id=session_id,
            request_id=str(uuid.uuid4()),
            text=text,
            caller=self._caller_for(session_id),
            execute=execute,
            side_effects=side_effects,
            inputs=tuple(payload.get("inputs") or ()),
            outputs=tuple(payload.get("outputs") or ()),
            plan_artifact=payload.get("plan_artifact"),
        )
        view = self.kernel.submit(envelope)
        return self._run_view_from_kernel(view)

    def _caller_for(self, session_id: str) -> CallerIdentity:
        """Derive the caller identity for a session (§8).

        Stage E uses a fixed local operator identity; tenant isolation comes
        from the session id. A future RBAC layer replaces this derivation.
        """
        return CallerIdentity(
            user_id="local-operator",
            tenant_id="local-tenant",
            role="operator",
            data_scope=(),
            client_kind="web",
        )

    # -- bridge callbacks (lease protocol §3.2) -----------------------------

    def _bridge_callback(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self.bridge_client is None:
            return None
        # /runs/<id>/receipt  OR  /runs/<id>/complete
        # The Py2 runtime posts execution results to /complete; the lease
        # protocol calls it /receipt. Both carry the same fencing triple and
        # are routed into the same receipt pipeline.
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/(receipt|complete)$", path)
        if m:
            run_id = m.group(1)
            lease = self.store.get_runtime_lease(run_id)
            if lease is None:
                raise HttpError(403, "no lease bound to run")
            if payload.get("lease_id") != lease.lease_id:
                raise HttpError(403, "lease fencing mismatch")
            if int(payload.get("epoch", 0)) != lease.epoch:
                raise HttpError(403, "stale epoch")
            if payload.get("plan_hash") != lease.plan_digest:
                raise HttpError(403, "plan hash mismatch")
            # Normalize the receipt: Py2 /complete sends {status, result};
            # _validate_receipt expects {lease_id, epoch, plan_hash, status, message}.
            receipt = dict(payload)
            result = payload.get("result")
            if isinstance(result, dict):
                receipt.setdefault("message", result.get("summary", ""))
            accepted = self.bridge_client.receive_receipt(run_id, receipt)
            if not accepted:
                raise HttpError(409, "no waiter for receipt")
            return {"ok": True}
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/heartbeat$", path)
        if m:
            run_id = m.group(1)
            lease = self.store.get_runtime_lease(run_id)
            if lease is None or payload.get("lease_id") != lease.lease_id:
                raise HttpError(403, "lease fencing mismatch")
            if int(payload.get("epoch", 0)) != lease.epoch:
                raise HttpError(403, "stale epoch")
            updated = lease.model_copy(update={"last_heartbeat": _now()})
            self.store.store_runtime_lease(updated)
            return {"ok": True}
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/context$", path)
        if m:
            # Context callback from Py2 runtime (before planning).
            # The payload carries raw context data (layers, mxd_path, etc.),
            # not a ContextSnapshot model — build one here.
            run_id = m.group(1)
            context_data = payload.get("context") or payload
            if isinstance(context_data, dict) and context_data:
                snapshot = _build_context_snapshot(context_data)
                if snapshot is not None:
                    self.store.store_context_snapshot(run_id, snapshot)
            return {"ok": True}
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/lease-ack$", path)
        if m:
            run_id = m.group(1)
            lease = self.store.get_runtime_lease(run_id)
            if lease is None or payload.get("lease_id") != lease.lease_id:
                raise HttpError(403, "lease fencing mismatch")
            if int(payload.get("epoch", 0)) != lease.epoch:
                raise HttpError(403, "stale epoch")
            plan = self.store.get_verified_plan(run_id)
            if plan is None:
                raise HttpError(409, "no sealed plan for run")
            context_snapshot = self.store.get_context_snapshot(run_id)
            return {
                "ok": True,
                "lease": lease.model_dump(mode="json"),
                "workflow": {
                    "action": "execute",
                    "summary": "",
                    "steps": [
                        {
                            "id": s.id,
                            "operation": s.operation,
                            "arguments": s.arguments,
                            "reason": s.reason,
                        }
                        for s in plan.workflow
                    ],
                },
                "context_hash": context_snapshot.content_hash if context_snapshot else "",
            }
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/reconcile$", path)
        if m:
            run_id = m.group(1)
            lease = self.store.get_runtime_lease(run_id)
            if lease is None or payload.get("lease_id") != lease.lease_id:
                raise HttpError(403, "lease fencing mismatch")
            if int(payload.get("epoch", 0)) != lease.epoch:
                raise HttpError(403, "stale epoch")
            outcome = self.bridge_client.reconcile(lease, run_id) \
                if hasattr(self.bridge_client, "reconcile") else None
            return {"ok": True, "status": "executed" if outcome else "unknown"}
        return None

    # -- response helpers ---------------------------------------------------

    def _run_view_from_kernel(self, view) -> Dict[str, Any]:
        # Project RunView into the fields the front-end renders. The kernel's
        # RunView is the authoritative shape; this projection adds display-only
        # fields (command, status alias, outcome-as-result) so the existing
        # render layer can consume it without touching the kernel contracts.
        events = list(view.events)
        command = ""
        for event in events:
            if event.get("kind") == "user_text":
                command = (event.get("payload") or {}).get("text", "")
                break
            if event.get("kind") == "run_received":
                command = (event.get("payload") or {}).get("text", "")
                break
        outcome = view.outcome.model_dump(mode="json") if view.outcome else None
        return {
            "id": view.run_id,
            "run_id": view.run_id,
            "session_id": view.session_id,
            "stage": view.stage,
            "status": stage_label(view.stage),  # Chinese label for display
            "command": command,
            "text": command,
            "outcome": outcome,
            "result": {"ok": view.stage == "succeeded",
                       "summary": outcome.get("message", "") if outcome else ""},
            "workflow": {"action": "execute", "summary": stage_label(view.stage),
                         "steps": []},
            "events": events,
        }

    def _run_view(self, run_id: str) -> Dict[str, Any]:
        view = self.kernel.inspect(run_id)
        return self._run_view_from_kernel(view)


def _now() -> float:
    import time
    return time.time()


def _build_context_snapshot(context_data: Dict[str, Any]) -> Optional[Any]:
    """Build a ContextSnapshot from the raw Py2 context callback payload."""
    import time as _time
    import uuid as _uuid
    from ..kernel.contracts import ContextSnapshot, LayerSnapshot, LayerRef
    layers_data = context_data.get("layers") or []
    mxd = context_data.get("mxd_path", "")
    data_frame = context_data.get("data_frame") or context_data.get("active_data_frame", "")
    content_hash = context_data.get("content_hash", "")
    is_saved = bool(context_data.get("is_saved", False))
    return ContextSnapshot(
        lease_id=str(_uuid.uuid4()),
        arcmap_pid=int(context_data.get("arcmap_pid", 0)) or 1,
        bridge_pid=int(context_data.get("bridge_pid", 0)) or 1,
        bridge_port=int(context_data.get("bridge_port", 0)) or 1,
        target_hwnd=int(context_data.get("hwnd", 0)) or 1,
        document_identity={"mxd": mxd, "active_data_frame": data_frame},
        layers=tuple(
            LayerSnapshot(
                identity=LayerRef(
                    name=l.get("name", ""),
                    layer_ref=l.get("layer_ref", ""),
                ),
                geometry_type=l.get("geometry_type"),
                coordinate_system=l.get("spatial_reference"),
                selection_count=int(l.get("selected_count", 0)),
            )
            for l in layers_data
            if isinstance(l, dict)
        ),
        active_data_frame=data_frame,
        edit_session_active=is_saved,
        is_saved=is_saved,
        captured_at=_time.time(),
        deployment_hash="unknown",
        content_hash=str(content_hash),
    )


_STAGE_LABELS = {
    "received": "已接收",
    "context_frozen": "正在捕获地图上下文",
    "intent_compiled": "正在理解任务意图",
    "plan_verified": "正在验证执行计划",
    "authorization_required": "等待授权确认",
    "authorized": "已授权",
    "runtime_acquired": "正在绑定 ArcMap",
    "executing": "正在执行到 ArcMap",
    "executed": "执行完成，正在验收",
    "accepted": "验收通过，正在发布",
    "published": "已发布",
    "succeeded": "任务完成",
    "clarification_required": "需要补充信息",
    "policy_denied": "授权被拒绝",
    "contract_failed": "任务合同校验失败",
    "capability_failed": "能力执行失败",
    "infrastructure_failed": "基础设施故障",
    "quota_stopped": "模型额度不足",
    "model_call_uncertain": "模型调用结果不确定",
    "execution_indeterminate": "执行状态不确定",
    "acceptance_failed": "成果验收失败",
    "cancelled": "已取消",
}


def stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage)
