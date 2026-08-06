from __future__ import annotations

import threading

from gateway_py3.logs import write_event
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.event_bus import EventBus
from gateway_py3.planning_engine import PlanningEngine
from gateway_py3.run_controller import RunController
from gateway_py3.validators import validate_catalog
from gateway_py3.run_store import RunStore


class GatewayState:
    def __init__(self, store=None):
        self.events = EventBus()
        self.bridge_cache = {"expires_at": 0.0, "bridges": []}
        self.catalog = OperationCatalog()
        validate_catalog(self.catalog)
        self.store = store or RunStore()
        self._recovery_context_reader = None
        self._recovery_scheduler = None
        self._recover_runs()
        self.runner = PlanningEngine(catalog=self.catalog, store=self.store)

    def _recover_runs(self):
        self.store.recover_stale_executions(lease_seconds=30.0)
        self.store.resolve_expired_recoveries()
        self.store.reset_context_finalizers()
        for row in self.store.iter_runs(
            statuses=("running", "planned", "approved"), include_trace=True
        ):
            trace = row.get("agent_trace") or []
            if len(trace) != 1 or trace[0].get("type") != "run":
                continue
            error = RuntimeError(
                "gateway restarted before run completion; submit a new run"
            )
            self.store.fail_run(
                row["id"],
                "recovery",
                error,
                trace[0]["run"],
            )
        self.store.release_terminal_target_episodes()

    def resume_interrupted_runs(self, context_reader, scheduler):
        self.configure_recovery(context_reader, scheduler)
        controller = RunController(None, self.store, context_reader, None, None)
        for row in self.store.iter_runs(
            statuses=("executed", "executing"), include_trace=False
        ):
            if row["status"] == "executed":
                scheduler(self._resume_episode, (controller, row["id"], True))
            elif row["status"] == "executing":
                scheduler(self._resume_episode, (controller, row["id"], False))

    def configure_recovery(self, context_reader, scheduler):
        self._recovery_context_reader = context_reader
        self._recovery_scheduler = scheduler

    def schedule_executed_recovery(self, run_id):
        if self._recovery_context_reader is None or self._recovery_scheduler is None:
            return
        controller = RunController(None, self.store, self._recovery_context_reader, None, None)
        self._recovery_scheduler(self._resume_episode, (controller, run_id, True))

    def _resume_episode(self, controller, run_id, finalize_context):
        if finalize_context:
            controller.resume_executed(run_id)
            self.store.finalize_target_episode(run_id)
            return
        controller.recover_executing(run_id)

    def start_recovery_resolver(self, stop_event=None, interval_seconds=5.0):
        stop = stop_event or threading.Event()
        worker = threading.Thread(
            target=self._recovery_resolver_loop,
            args=(stop, float(interval_seconds)),
            name="geopilot-recovery-resolver",
            daemon=True,
        )
        worker.start()
        return worker

    def _recovery_resolver_loop(self, stop_event, interval_seconds):
        while not stop_event.wait(interval_seconds):
            try:
                recovered = self.store.recover_stale_executions(lease_seconds=30.0)
                resolved = self.store.resolve_expired_recoveries()
                if recovered or resolved:
                    self.events.publish("runs.changed", {"path": "/runs"})
            except Exception as exc:
                write_event("recovery.resolver_failed", {"error": str(exc)})

    def reload_catalog(self):
        self.catalog = OperationCatalog()
        validate_catalog(self.catalog)
        self.runner = PlanningEngine(catalog=self.catalog, store=self.store)
