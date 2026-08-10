"""ArcMapRuntime: lease acquisition, context capture, execution (§6.7).

The runtime is the single owner of:
- precise binding of ArcMap / Bridge / HWND / deployment hash (§6.7)
- single-writer lease with fencing token (lease_id + epoch)
- pre-execution context re-check (reject stale plans on context drift)
- outbox / receipt reconcile; ``ExecutionIndeterminate`` when unprovable
- staged outputs

It never scans ports, never silently switches to "the first healthy bridge",
never auto-restarts a dead bridge. Execution proceeds only when the callback
carries the exact lease_id + epoch + plan_hash (§2).

The bridge is injected through a Protocol so offline tests use a Fake bridge
and production uses the real Bridge client. Context capture is incremental
(§4.2): the structural layer is always captured; value summaries are sampled
lazily only when a capability asks for them.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from ..kernel import contracts
from ..kernel.contracts import (
    AuthorizationGrant, CapabilitySnapshot, ContextSnapshot, IntentSpec,
    Outcome, RuntimeLease, VerifiedPlan, outcome_succeeded, outcome_failed,
    INFRASTRUCTURE_FAILED, EXECUTION_INDETERMINATE, CAPABILITY_FAILED,
)


@runtime_checkable
class BridgeClient(Protocol):
    """The ArcMap execution bridge (§6.7).

    Production implementation talks to the C# Bridge inside ArcMap; the Fake
    bridge simulates dispatch + callback for offline tests.
    """
    def dispatch(self, lease: RuntimeLease, plan: VerifiedPlan,
                 grant: AuthorizationGrant,
                 context_snapshot: ContextSnapshot) -> str:
        """Dispatch one verified plan for execution. Returns a receipt token."""
        ...

    def wait_for_receipt(self, receipt_token: str, timeout: float) -> Optional[Dict[str, Any]]:
        """Block until the runtime callback arrives. Returns None on timeout."""
        ...


class ArcMapRuntime:
    """§6.7 ArcMapRuntime: acquire / capture / execute / reconcile.

    ``deployment_hash`` is the exact build identity of the ArcMap-side runtime;
    any mismatch rejects the lease. ``epoch`` starts at 1 and increments on
    every re-acquire; stale callbacks carry an old epoch and are rejected.
    """

    def __init__(self, bridge: BridgeClient, deployment_hash: str,
                 gateway_pid: int):
        self.bridge = bridge
        self.deployment_hash = str(deployment_hash)
        self.gateway_pid = int(gateway_pid)

    # -- §6.7 acquire --------------------------------------------------------

    def acquire_lease(self, run_id: str, target_selector: contracts.TargetSelector) -> RuntimeLease:
        """Acquire a context lease binding the ArcMap target before planning.

        No plan is bound yet; plan_digest uses a non-empty sentinel so the
        Bridge lease fencing (which requires a non-empty plan_hash) and the
        Py2 context callback (which rejects empty plan_hash) both accept it.
        The real plan binding is enforced via AuthorizationGrant at execution.
        """
        from .bridge_discovery import discover_bridge_target
        target = discover_bridge_target(target_selector)
        if target.get("source_sha256") != self.deployment_hash:
            raise RuntimeError("Bridge deployment identity does not match the installed Gateway manifest.")
        identity = self._target_identity(target)
        now = time.time()
        return RuntimeLease(
            lease_id=str(uuid.uuid4()),
            run_id=run_id,
            plan_digest="context-lease",
            gateway_pid=self.gateway_pid,
            arcmap_pid=identity["arcmap_pid"],
            bridge_pid=identity["bridge_pid"],
            bridge_port=identity["bridge_port"],
            target_hwnd=identity["hwnd"],
            deployment_hash=self.deployment_hash,
            epoch=1,
            acquired_at=now,
            last_heartbeat=now,
        )

    # -- §6.7 capture (incremental, §4.2) -----------------------------------

    def capture(self, run_id: str, lease: RuntimeLease,
                document_identity: Dict[str, Any],
                structural_layers: List[Dict[str, Any]],
                captured_at: Optional[float] = None,
                content_hash: Optional[str] = None) -> ContextSnapshot:
        """Freeze a ContextSnapshot from the structural layer.

        ``structural_layers`` carries the full structural capture (layer refs,
        names, data sources, fields, coordinate systems, geometry types,
        selection counts) — always captured (§4.2). Value summaries are NOT
        sampled here; callers request them lazily via ``sample_values`` only
        when a capability needs them.
        """
        layers = tuple(
            contracts.LayerSnapshot(
                identity=contracts.LayerRef(
                    name=layer.get("name", ""),
                    layer_ref=layer.get("layer_ref", ""),
                    data_source=layer.get("data_source"),
                    layer_type=layer.get("layer_type"),
                ),
                fields=tuple(
                    contracts.FieldColumn(name=f.get("name", ""), dtype=f.get("dtype"))
                    for f in layer.get("fields", [])
                ),
                coordinate_system=layer.get("coordinate_system"),
                geometry_type=layer.get("geometry_type"),
                selection_count=int(layer.get("selection_count", 0) or 0),
                long_name=layer.get("long_name"),
                visible=bool(layer.get("visible", False)),
                selection_hash=layer.get("selection_hash"),
                value_summary=None,
            )
            for layer in structural_layers
        )
        return ContextSnapshot(
            lease_id=lease.lease_id,
            arcmap_pid=lease.arcmap_pid,
            bridge_pid=lease.bridge_pid,
            bridge_port=lease.bridge_port,
            target_hwnd=lease.target_hwnd,
            document_identity=dict(document_identity),
            layers=layers,
            active_data_frame=document_identity.get("active_data_frame"),
            edit_session_state=str(document_identity.get("edit_session_state", "unknown")),
            view_state=document_identity.get("view_state"),
            captured_at=float(captured_at if captured_at is not None else time.time()),
            deployment_hash=lease.deployment_hash,
            content_hash=str(content_hash or ""),
        )

    def sample_values(self, lease: RuntimeLease, context: ContextSnapshot,
                      layer_refs: List[str],
                      field_names: List[str],
                      max_rows: int = 250, max_samples: int = 20) -> ContextSnapshot:
        """Lazily attach value summaries for requested layers/fields (§4.2).

        Only the named layers and fields are sampled; nothing else is touched.
        The returned snapshot has a new digest because value_summary is part
        of the projection, but ``content_hash`` stays the structural hash.
        """
        if not layer_refs or not field_names:
            return context
        wanted = set(layer_refs)
        wanted_fields = set(field_names)
        layers = []
        for layer in context.layers:
            if layer.identity.layer_ref not in wanted:
                layers.append(layer)
                continue
            samples = self.bridge.sample_values(
                lease, layer.identity.layer_ref, sorted(wanted_fields), max_rows, max_samples
            )
            if not isinstance(samples, dict) or not isinstance(samples.get("values"), dict):
                raise RuntimeError("Bridge returned no authoritative lazy samples.")
            layers.append(layer.model_copy(update={"value_summary": samples["values"]}))
        return context.model_copy(update={"layers": tuple(layers)})

    # -- §6.7 execute --------------------------------------------------------

    def execute(self, lease: RuntimeLease, plan: VerifiedPlan,
                grant: AuthorizationGrant,
                context_snapshot: ContextSnapshot) -> Outcome:
        """Dispatch one plan; validate the runtime receipt (fencing §2).

        Returns a succeeded Outcome with the receipt when the runtime
        confirmed execution, or a terminal Outcome (CapabilityFailed /
        InfrastructureFailed / ExecutionIndeterminate).
        """
        if lease.plan_digest != plan.digest:
            return outcome_failed(
                contracts.CONTRACT_FAILED, "execution",
                "lease_plan_mismatch", "租约绑定的计划与待执行计划不一致。",
            )
        if grant.lease_id != lease.lease_id or grant.lease_epoch != lease.epoch:
            return outcome_failed(
                contracts.POLICY_DENIED, "execution", "grant_lease_mismatch",
                "授权与租约不匹配，拒绝执行。",
            )
        if context_snapshot.deployment_hash != lease.deployment_hash:
            return outcome_failed(
                INFRASTRUCTURE_FAILED, "execution", "deployment_mismatch",
                "部署哈希不匹配，拒绝执行。",
            )
        # Bind the planning-time ArcMap identity to the execution-time lease
        # (§6.7): the context was captured before the lease was signed, so the
        # two must describe the same ArcMap window. Any drift means the user
        # switched windows between planning and execution — execution must not
        # proceed against a different instance.
        for field in ("arcmap_pid", "bridge_pid", "bridge_port", "target_hwnd"):
            lease_val = getattr(lease, field)
            snap_val = getattr(context_snapshot, field)
            if lease_val != snap_val:
                return outcome_failed(
                    INFRASTRUCTURE_FAILED, "execution", "pre_dispatch_identity_drift",
                    "规划与执行的 ArcMap 身份不一致（%s: 规划=%s 执行=%s）；"
                    "禁止跨实例执行。" % (field, snap_val, lease_val),
                )
        # Map content binding: the planning-time content_hash must be present
        # and is forwarded to the Bridge in the dispatch payload. The Bridge
        # (which has ArcMap access) must reject execution if the current map
        # content no longer matches — Python cannot re-capture it (no arcpy).
        if not context_snapshot.content_hash:
            return outcome_failed(
                INFRASTRUCTURE_FAILED, "execution", "missing_content_hash",
                "上下文缺少 content_hash，无法绑定地图内容。",
            )
        receipt_token = self.bridge.dispatch(lease, plan, grant, context_snapshot)
        receipt = self.bridge.wait_for_receipt(receipt_token, timeout=600.0)
        if receipt is None:
            # Execution was dispatched but cannot be proven; this is a paused,
            # recoverable state — never auto-replay (§2, §7).
            return contracts.outcome_paused(
                EXECUTION_INDETERMINATE, "execution", "receipt_unavailable",
                "执行已分发但无法确认是否发生；禁止自动重放。",
            )
        return self._validate_receipt(lease, plan, receipt)

    def _validate_receipt(self, lease: RuntimeLease, plan: VerifiedPlan,
                          receipt: Dict[str, Any]) -> Outcome:
        """Fencing: the callback must carry the exact lease/epoch/plan_hash.

        Duplicate and stale callbacks are rejected (§2).
        """
        if receipt.get("lease_id") != lease.lease_id:
            return outcome_failed(
                contracts.POLICY_DENIED, "execution", "receipt_lease_mismatch",
                "回执携带的 lease_id 与当前租约不匹配。",
            )
        if int(receipt.get("epoch", 0)) != lease.epoch:
            return outcome_failed(
                contracts.POLICY_DENIED, "execution", "receipt_stale_epoch",
                "回执携带的 epoch 已过期。",
            )
        if receipt.get("plan_hash") != plan.digest:
            return outcome_failed(
                contracts.POLICY_DENIED, "execution", "receipt_plan_mismatch",
                "回执携带的计划哈希与当前计划不匹配。",
            )
        status = receipt.get("status", "")
        if status == "executed":
            return outcome_succeeded(
                "execution", "ArcMap 执行完成。",
                details={"receipt": receipt},
            )
        message = receipt.get("message") or "ArcMap 运行时报告执行失败。"
        return outcome_failed(
            CAPABILITY_FAILED, "execution", "runtime_failed",
            message,
            details={"receipt": receipt},
        )

    # -- §6.7 reconcile ------------------------------------------------------

    def reconcile(self, lease: RuntimeLease, run_id: str) -> Outcome:
        """Reconcile after dispatch interruption (§7).

        Asks the bridge for the authoritative receipt. If the bridge cannot
        prove whether execution happened, the outcome is
        ``ExecutionIndeterminate`` — never auto-replay.
        """
        if lease.run_id != run_id:
            return outcome_failed(
                contracts.CONTRACT_FAILED, "reconcile", "run_mismatch",
                "租约与 run 不匹配。",
            )
        receipt = self.bridge.reconcile(lease, run_id)
        if receipt is None:
            # Cannot prove whether execution happened: paused, never replay.
            return contracts.outcome_paused(
                EXECUTION_INDETERMINATE, "reconcile", "unprovable",
                "无法确定执行是否发生；禁止自动重放。",
            )
        # Fencing: receipt must carry the exact lease/epoch/plan_hash. The
        # plan digest comes from the lease binding, not a reconstructed plan.
        if receipt.get("lease_id") != lease.lease_id:
            return outcome_failed(
                contracts.POLICY_DENIED, "reconcile", "receipt_lease_mismatch",
                "回执携带的 lease_id 与当前租约不匹配。",
            )
        if int(receipt.get("epoch", 0)) != lease.epoch:
            return outcome_failed(
                contracts.POLICY_DENIED, "reconcile", "receipt_stale_epoch",
                "回执携带的 epoch 已过期。",
            )
        if receipt.get("plan_hash") != lease.plan_digest:
            return outcome_failed(
                contracts.POLICY_DENIED, "reconcile", "receipt_plan_mismatch",
                "回执携带的计划哈希与租约绑定不一致。",
            )
        if receipt.get("status") == "executed":
            return outcome_succeeded(
                "reconcile", "执行已确认。",
                details={"receipt": receipt},
            )
        return outcome_failed(
            CAPABILITY_FAILED, "reconcile", "runtime_failed",
            receipt.get("message", "ArcMap 运行时报告执行失败。"),
            details={"receipt": receipt},
        )

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _target_identity(target: Dict[str, int]) -> Dict[str, int]:
        if not isinstance(target, dict):
            raise ValueError("ArcMap target identity is required.")
        identity = {
            name: int(target.get(name) or 0)
            for name in ("bridge_pid", "bridge_port", "arcmap_pid", "hwnd")
        }
        if any(value <= 0 for value in identity.values()):
            raise ValueError(
                "ArcMap target identity requires bridge_pid, bridge_port, "
                "arcmap_pid and hwnd."
            )
        return identity
