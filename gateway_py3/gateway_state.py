from __future__ import annotations

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.event_bus import EventBus
from gateway_py3.experiments import ExperimentRunner
from gateway_py3.validators import validate_catalog
from gateway_py3.workflow_store import WorkflowStore


class GatewayState:
    def __init__(self):
        self.events = EventBus()
        self.bridge_cache = {"expires_at": 0.0, "bridges": []}
        self.catalog = OperationCatalog()
        validate_catalog(self.catalog)
        self.store = WorkflowStore()
        self._recover_runs()
        self.store.clear_state("arcmap_context")
        self.runner = ExperimentRunner(catalog=self.catalog, store=self.store)

    def _recover_runs(self):
        for row in self.store.list_recent(limit=200, include_trace=True):
            trace = row.get("agent_trace") or []
            if len(trace) != 1 or trace[0].get("type") != "run":
                continue
            if row["status"] == "executing":
                error = RuntimeError(
                    "gateway restarted while ArcMap execution was in progress; "
                    "execution was not replayed"
                )
                self.store.fail_run(
                    row["id"],
                    "recovery",
                    error,
                    trace[0]["run"],
                )
            elif row["status"] in ("running", "approved"):
                error = RuntimeError(
                    "gateway restarted before run completion; submit a new run"
                )
                self.store.fail_run(
                    row["id"],
                    "recovery",
                    error,
                    trace[0]["run"],
                )

    def reload_catalog(self):
        self.catalog = OperationCatalog()
        validate_catalog(self.catalog)
        self.runner = ExperimentRunner(catalog=self.catalog, store=self.store)
