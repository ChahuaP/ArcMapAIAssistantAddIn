"""ArcMapExecutorAdapter: adapts ArcMapRuntime to the kernel's ArcMapExecutor protocol.

Extracted from app.py build_kernel.
"""
from __future__ import annotations

import os

from ..kernel.contracts import ContextSnapshot
from .arcmap_runtime import ArcMapRuntime
from .bridge_client import RealBridgeClient
from ..kernel.store import JournalStore


class ArcMapExecutorAdapter:
    """Adapts ArcMapRuntime to the kernel's ArcMapExecutor protocol."""

    def __init__(self, bridge_client: RealBridgeClient, deployment_hash: str,
                 store: JournalStore):
        self._runtime = ArcMapRuntime(bridge_client, deployment_hash, gateway_pid=os.getpid())
        self.store = store

    def acquire(self, run_id: str, plan):
        from .bridge_discovery import discover_bridge_target
        target = discover_bridge_target()
        return self._runtime.acquire(run_id, plan, target)

    def execute(self, lease, plan, grant):
        snapshot = self.store.get_context_snapshot(lease.run_id)
        if snapshot is None:
            raise RuntimeError("上下文快照缺失，无法执行。")
        return self._runtime.execute(lease, plan, grant, snapshot)
