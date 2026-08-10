"""ArcMapExecutorAdapter: adapts ArcMapRuntime to the kernel's ArcMapExecutor protocol.

Extracted from app.py build_kernel.
"""
from __future__ import annotations

import os

from ..kernel.contracts import ContextSnapshot, TargetSelector
from .arcmap_runtime import ArcMapRuntime
from .bridge_client import RealBridgeClient
from ..kernel.store import JournalStore


class ArcMapExecutorAdapter:
    """Adapts ArcMapRuntime to the kernel's ArcMapExecutor protocol."""

    def __init__(self, bridge_client: RealBridgeClient, deployment_hash: str,
                 store: JournalStore):
        self._runtime = ArcMapRuntime(bridge_client, deployment_hash, gateway_pid=os.getpid())
        self.store = store

    def acquire_lease(self, run_id: str, target_selector: TargetSelector):
        """Acquire a context lease (pre-planning, no plan bound)."""
        return self._runtime.acquire_lease(run_id, target_selector)

    def execute(self, lease, plan, grant):
        snapshot = self.store.get_planning_context_snapshot(lease.run_id)
        if snapshot is None:
            raise RuntimeError("上下文快照缺失，无法执行。")
        return self._runtime.execute(lease, plan, grant, snapshot)

    def reconcile(self, lease, run_id):
        """Post-dispatch recovery (§7): ask the Bridge whether execution happened."""
        return self._runtime.reconcile(lease, run_id)
