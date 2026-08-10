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

Security (§8): fixed Origin allowlist, session token header, no wildcard
CORS. Callers must present X-Session-Id (a client-generated UUID); the
adapter derives the caller identity from the session.
"""
from __future__ import annotations

import json
import hmac
import re
import secrets
import uuid
from typing import Any, Dict, Optional

from ..kernel import contracts
from ..kernel.contracts import (
    CallerIdentity, RequestEnvelope, SideEffectScope,
)
from ..kernel.coordinator import GeoPilotKernel
from ..release import APP_VERSION

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
                 bridge_client: Optional[Any] = None,
                 allowed_origins: Optional[frozenset] = None):
        self.kernel = kernel
        self.bridge_client = bridge_client
        self.allowed_origins = allowed_origins if allowed_origins is not None else ALLOWED_ORIGINS
        self._op_count = None
        self._bridge_cache = None
        self._csrf_tokens: Dict[str, str] = {}

    def _session_token(self, headers: Optional[Dict[str, str]]) -> Dict[str, str]:
        session_id = (headers or {}).get("X-Session-Id", "")
        try:
            session_id = str(uuid.UUID(session_id))
        except (ValueError, TypeError):
            raise HttpError(400, "X-Session-Id must be a canonical UUID.")
        token = self._csrf_tokens.get(session_id)
        if token is None:
            token = secrets.token_urlsafe(32)
            self._csrf_tokens[session_id] = token
        return {"session_id": session_id, "csrf_token": token}

    def _assert_web_write(self, headers: Dict[str, str]) -> None:
        session_id = headers.get("X-Session-Id", "")
        if not session_id:
            raise HttpError(400, "缺少 X-Session-Id 头。")
        token = self._csrf_tokens.get(session_id)
        origin = headers.get("Origin", "")
        if not token or origin not in self.allowed_origins or origin == "null":
            raise HttpError(403, "未知会话或跨源写请求被拒绝。")
        if not hmac.compare_digest(token, headers.get("X-CSRF-Token", "")):
            raise HttpError(403, "CSRF token 无效。")

    def _operation_count(self) -> int:
        if self._op_count is None:
            from gateway_py3.catalog_loader import OperationCatalog
            self._op_count = len(OperationCatalog().operations)
        return self._op_count

    def _assert_session_owns_run(self, run_id: str,
                                 headers: Optional[Dict[str, str]]) -> None:
        """Enforce session ownership (§5: sessions are isolation boundaries).

        A missing ``X-Session-Id`` header or a mismatch with the run's session
        is rejected with HTTP 403. Runs are append-only journal entries: there
        is no delete endpoint.
        """
        session_id = (headers or {}).get("X-Session-Id", "")
        if not session_id:
            raise HttpError(403, "缺少 X-Session-Id 头。")
        run = self.kernel.get_run(run_id)
        if run is None:
            raise HttpError(404, "运行不存在。")
        if run.get("session_id") != session_id:
            raise HttpError(403, "该运行不属于当前会话。")

    # -- request dispatch ---------------------------------------------------

    def handle_get(self, path: str, query: Optional[Dict[str, Any]] = None,
                   headers: Optional[Dict[str, str]] = None) -> Any:
        if path == API_PREFIX + "/session":
            return self._session_token(headers)
        if path == API_PREFIX + "/runs":
            session_id = (headers or {}).get("X-Session-Id", "")
            runs = [self._run_view_from_kernel(v)
                    for v in self.kernel.list_runs(session_id)]
            return {"runs": runs}
        match = _RUN_ID_RE.match(path)
        if match:
            run_id = match.group(1)
            suffix = match.group(2) or ""
            if not suffix:
                self._assert_session_owns_run(run_id, headers)
                return {"run": self._run_view(run_id)}
        if path == "/health":
            return {"ok": True, "app_version": APP_VERSION}
        if path == "/api/workbench-state":
            return self._workbench_state(headers)
        if path == "/config":
            return {"config": self._public_config()}
        if path == "/arcmap/bridges":
            import time as _time
            now = _time.time()
            if self._bridge_cache and now - self._bridge_cache[0] < 3.0:
                return {"ok": True, "bridges": self._bridge_cache[1]}
            bridges = self._bridges_from_ready_file()
            self._bridge_cache = (now, bridges)
            return {"ok": True, "bridges": bridges}
        if path == "/api/capabilities":
            return self._capabilities()
        if path == "/api/diagnostics":
            return self._diagnostics()
        return None

    def _diagnostics(self) -> Dict[str, Any]:
        checks = [
            {"id": "gateway", "label": "网关", "status": "ok",
             "detail": "GeoPilot %s 运行中。" % APP_VERSION},
            {"id": "bridge", "label": "ArcMap Bridge", "status": "ok",
             "detail": "Bridge 连接状态请看左侧状态栏。"},
        ]
        try:
            cfg = self._public_config()
            missing = [item for item in cfg["connections"]
                       if item.get("credential_required") and not item.get("has_credential")]
            if not missing:
                checks.append({"id": "model", "label": "模型配置", "status": "ok",
                               "detail": "%d 个模型连接可用。" % len(cfg["connections"])})
            else:
                checks.append({"id": "model", "label": "模型配置", "status": "warn",
                               "detail": "%d 个模型连接缺少 API Key。" % len(missing)})
        except Exception as exc:
            checks.append({"id": "model", "label": "模型配置", "status": "bad",
                           "detail": str(exc)[:80]})
        count = self._operation_count()
        checks.append({"id": "catalog", "label": "能力目录", "status": "ok",
                       "detail": "%d 个操作。" % count})
        all_ok = all(c["status"] == "ok" for c in checks)
        return {"ok": all_ok, "app_version": APP_VERSION, "checks": checks}

    def _public_config(self) -> Dict[str, Any]:
        from gateway_py3.model_runtime.configuration import ModelConfigurationStore
        return ModelConfigurationStore().public()

    def _capabilities(self) -> Dict[str, Any]:
        from gateway_py3.catalog_loader import OperationCatalog
        catalog = OperationCatalog()
        return {
            "app_version": APP_VERSION,
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

    def _bridges_from_ready_file(self) -> list:
        """Read the single Bridge instance from bridge.ready (§6.7).

        No port scanning, no auto-launch. Returns an empty list if the
        ready-file is missing or the Bridge is not responding — the operator
        must open ArcMap and load the Bridge add-in.
        """
        from ..runtime.bridge_discovery import list_bridge_targets
        return list_bridge_targets()

    def _workbench_state(self, headers=None) -> Dict[str, Any]:
        """Initial state payload for the web console.

        Bridge scanning is deferred to the /arcmap/bridges endpoint to keep
        this call fast (the front-end polls /arcmap/bridges separately).
        """
        session_id = (headers or {}).get("X-Session-Id", "")
        runs = [self._run_view_from_kernel(v)
                for v in self.kernel.list_runs(session_id)]
        op_count = self._operation_count()
        return {
            "health": {"ok": True, "app_version": APP_VERSION, "operation_count": op_count},
            "config": self._public_config(),
            "runs": runs,
            "arcmap": {"bridges": [], "error": ""},
        }

    def handle_post(self, path: str, payload: Dict[str, Any],
                    headers: Optional[Dict[str, str]] = None) -> Any:
        headers = headers or {}
        if not path.startswith("/runs/"):
            self._assert_web_write(headers)
        if path == API_PREFIX + "/runs":
            return {"run": self._submit(payload, headers)}
        match = _RUN_ID_RE.match(path)
        if match:
            run_id = match.group(1)
            suffix = match.group(2) or ""
            if suffix == "/decide":
                self._assert_session_owns_run(run_id, headers)
                from ..kernel.contracts import AuthorizationDecision, SideEffectScope
                approved = bool(payload.get("approved"))
                plan_digest = payload.get("plan_digest", "")
                scope_payload = payload.get("approved_scope")
                approved_scope = None
                if isinstance(scope_payload, dict) and approved:
                    approved_scope = SideEffectScope(
                        level=int(scope_payload.get("level", 1)),
                        input_identities=tuple(
                            (str(item["input_id"]), str(item["identity"]))
                            for item in (scope_payload.get("inputs") or ())
                            if isinstance(item, dict)
                        ),
                        output_identities=tuple(
                            (str(item["output_id"]), str(item["destination"]))
                            for item in (scope_payload.get("outputs") or ())
                            if isinstance(item, dict)
                        ),
                    )
                decision = AuthorizationDecision(
                    decision_id=payload.get("decision_id") or str(__import__("uuid").uuid4()),
                    run_id=run_id,
                    plan_digest=plan_digest,
                    approved=approved,
                    approved_scope=approved_scope,
                )
                return {"run": self._run_view_from_kernel(
                    self.kernel.decide(run_id, decision))}
            if suffix == "/resume":
                self._assert_session_owns_run(run_id, headers)
                return {"run": self._run_view_from_kernel(
                    self.kernel.resume(run_id))}
        # Bridge callbacks (lease protocol)
        bridge = self._bridge_callback(path, payload)
        if bridge is not None:
            return bridge
        # Gateway-owned configuration and local operator actions.
        if path == "/config":
            try:
                from gateway_py3.model_runtime.configuration import ModelConfigurationStore
                store = ModelConfigurationStore()
                store.save(payload)
                return {"config": store.public(), "restart_required": True}
            except Exception as exc:
                raise HttpError(400, str(exc))
        if path == "/open-path":
            import os
            target = payload.get("target")
            if target == "log_dir":
                from gateway_py3.paths import log_dir
                os.startfile(str(log_dir()))
            else:
                raise HttpError(400, "不支持的路径类型。")
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
            # No implicit default: execute=True must declare the side-effect
            # level explicitly. The level only needs to clear policy.precheck
            # so write plans reach AUTHORIZATION_REQUIRED; the real gate is the
            # human decision + its approved_scope.
            if "side_effect_level" not in payload:
                raise HttpError(400, "side_effect_level is required when execute=True.")
            level = int(payload.get("side_effect_level"))
            if level < 1 or level > 4:
                raise HttpError(400, "side_effect_level must be 1..4.")
            side_effects = SideEffectScope(
                level=level,
                input_identities=tuple(
                    (str(item["input_id"]), str(item["identity"]))
                    for item in (payload.get("input_identities") or ())
                    if isinstance(item, dict)
                ),
                output_identities=tuple(
                    (str(item["output_id"]), str(item["destination"]))
                    for item in (payload.get("outputs") or ())
                    if isinstance(item, dict)
                ),
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
            target_selector=payload.get("target_selector"),
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
        # All lease-protocol callbacks go through the kernel so the adapter
        # never touches the store directly (§3, §6.1). Fencing and state writes
        # live in the kernel callback methods.
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/receipt$", path)
        if m:
            try:
                self.kernel.receive_receipt_callback(m.group(1), payload)
            except ValueError as exc:
                raise HttpError(403, str(exc))
            return {"ok": True}
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/sample$", path)
        if m:
            try:
                self.kernel.receive_sample_callback(m.group(1), payload)
            except ValueError as exc:
                raise HttpError(403, str(exc))
            return {"ok": True}
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/acceptance-probe$", path)
        if m:
            try:
                self.kernel.receive_acceptance_probe_callback(m.group(1), payload)
            except ValueError as exc:
                raise HttpError(403, str(exc))
            return {"ok": True}
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/heartbeat$", path)
        if m:
            try:
                self.kernel.heartbeat_callback(m.group(1), payload)
            except ValueError as exc:
                raise HttpError(403, str(exc))
            return {"ok": True}
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/context$", path)
        if m:
            # Forward the full payload so the kernel can fence the lease triple
            # (lease_id + epoch + plan_hash) before accepting the context.
            try:
                self.kernel.context_callback(m.group(1), payload)
            except ValueError as exc:
                raise HttpError(400, str(exc))
            return {"ok": True}
        m = re.match(r"^/runs/([0-9a-fA-F-]{36})/lease-ack$", path)
        if m:
            try:
                return self.kernel.lease_ack_callback(m.group(1), payload)
            except ValueError as exc:
                raise HttpError(409, str(exc))
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
            "plan_digest": view.plan.digest if view.plan else "",
            "risk_level": view.plan.risk_level if view.plan else 0,
            "input_identities": list(view.plan.input_identities) if view.plan else [],
            "command": command,
            "text": command,
            "outcome": outcome,
            "result": {"ok": view.stage == "succeeded",
                       "summary": outcome.get("message", "") if outcome else ""},
            "workflow": {"action": "execute", "summary": stage_label(view.stage),
                         "steps": self._plan_steps(view)},
            "events": events,
        }

    @staticmethod
    def _plan_steps(view) -> list:
        """Project the sealed plan's workflow steps for the front-end.

        The authorization UI needs to show what the plan will modify (layers,
        datasets, output files). Empty until the plan is sealed.
        """
        plan = getattr(view, "plan", None)
        if plan is None or not getattr(plan, "workflow", None):
            return []
        steps = []
        for step in plan.workflow:
            declared = []
            for out in step.declared_outputs:
                declared.append({"output_id": out.output_id, "name": out.name,
                                 "kind": out.kind, "destination": out.destination})
            steps.append({
                "id": step.id, "operation": step.operation,
                "arguments": step.arguments, "reason": step.reason,
                "declared_outputs": declared,
            })
        return steps

    def _run_view(self, run_id: str) -> Dict[str, Any]:
        view = self.kernel.inspect(run_id)
        return self._run_view_from_kernel(view)




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
