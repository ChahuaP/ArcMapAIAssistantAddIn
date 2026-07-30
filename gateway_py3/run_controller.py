from __future__ import annotations

import time
import uuid

from .experiments import digest
from .validators import context_hash


class RunController:
    """Owns planning, permission, ArcMap execution and next-context capture."""

    def __init__(self, runner, store, context_reader, permission, executor):
        self.runner = runner
        self.store = store
        self.context_reader = context_reader
        self.permission = permission
        self.executor = executor

    def run(self, run_id, request):
        request = dict(request)
        request["allow_edits"] = bool(request.get("allow_edits", False))
        capture = self._capture_initial_context(run_id)
        if capture is None:
            return self.store.get(run_id)
        request["bridge_target"] = capture["bridge"]
        row = self._plan(run_id, request, capture["context"])
        if row["status"] != "planned" or not request.get("execute"):
            return row

        if self.store.is_cancel_requested(run_id):
            return self.store.get(run_id)

        self.store.update_run(run_id, "approved")
        row = self.store.get(run_id)
        allow_edits = self._permission(run_id, request, row)
        if allow_edits is None:
            return self.store.get(run_id)

        if self.store.is_cancel_requested(run_id):
            return self.store.get(run_id)

        return self._execute(run_id, allow_edits, request["bridge_target"])

    def _capture_initial_context(self, run_id):
        trace, stage = self._start_stage(run_id, "context_before_planning")
        try:
            capture = self.context_reader(run_id, None, "before_planning", None)
            context = capture.get("context") if isinstance(capture, dict) else None
            if not isinstance(context, dict):
                raise ValueError("ArcMap context capture is invalid.")
            capture = dict(capture)
            actual_hash = context_hash(context)
            if capture.get("context_hash") != actual_hash:
                raise ValueError("ArcMap context capture hash does not match its execution fingerprint.")
            capture["snapshot_hash"] = digest(context)
            self.store.bind_context(run_id, capture)
        except Exception as exc:
            trace = self._finish_stage(run_id, trace, stage, "failed")
            self.store.fail_run(run_id, "context_before_planning", exc, trace)
            return None
        self._finish_stage(run_id, trace, stage, "succeeded")
        return capture

    def _plan(self, run_id, request, context):
        trace, stage = self._start_stage(run_id, "planning")
        try:
            row = self.runner.plan(
                    run_id,
                    request["command"],
                    context,
                    request["mode"],
                    request.get("provider", ""),
                    request.get("model", ""),
            )
        except Exception as exc:
            trace = self._finish_stage(run_id, trace, stage, "failed")
            return self.store.fail_run(run_id, "planning", exc, trace)

        self._finish_stage(run_id, trace, stage, "succeeded")
        return row

    def _permission(self, run_id, request, row):
        trace, stage = self._start_stage(run_id, "permission")
        try:
            allow_edits = self.permission(request, row)
        except Exception as exc:
            trace = self._finish_stage(run_id, trace, stage, "failed")
            self.store.fail_run(run_id, "permission", exc, trace)
            return None

        self._finish_stage(run_id, trace, stage, "succeeded")
        return bool(allow_edits)

    def _execute(self, run_id, allow_edits, bridge_target):
        trace, stage = self._start_stage(run_id, "execution")
        transport_error = None
        try:
            self.executor(run_id, allow_edits, bridge_target)
        except Exception as exc:
            transport_error = exc
        row = self.store.get(run_id)
        if row["status"] == "executing":
            row = self._wait_for_runtime_terminal(run_id)
        if row["status"] == "executed":
            trace = self._finish_stage(run_id, trace, stage, "succeeded")
            if transport_error is not None:
                trace["execution_transport_warning"] = self._error_fingerprint(transport_error)
                self.store.update_run(run_id, "executed", trace=trace)
            return self._sync_context(run_id)
        if row["status"] == "failed":
            self._finish_stage(run_id, trace, stage, "failed")
            return self.store.get(run_id)
        if row["status"] == "recovery_required":
            self._finish_stage(run_id, trace, stage, "recovery_required")
            return self.store.get(run_id)
        if row["status"] != "executed":
            trace = self._finish_stage(run_id, trace, stage, "failed")
            error = transport_error or RuntimeError("ArcMap runtime did not persist an execution result.")
            return self.store.fail_run(run_id, "execution", error, trace)

    def _sync_context(self, run_id):
        owner_id = str(uuid.uuid4())
        fence = self.store.claim_context_finalization(run_id, owner_id)
        if not fence:
            return self._wait_for_context_terminal(run_id)
        trace, stage = self._start_stage(run_id, "context_after_execution", fence)
        try:
            target = self.store.run_trace(run_id)["context"]["window"]
            capture = self.context_reader(run_id, target, "after_execution", fence)
            next_context = capture.get("context") if isinstance(capture, dict) else None
            if not isinstance(next_context, dict):
                raise ValueError("ArcMap context capture is invalid.")
            next_hash = context_hash(next_context)
            if capture.get("context_hash") != next_hash:
                raise ValueError("post-execution context hash does not match its execution fingerprint.")
        except Exception as exc:
            trace = self._finish_stage(run_id, trace, stage, "failed", fence)
            return self.store.fail_context_sync(run_id, fence, exc, trace)

        self._finish_stage(run_id, trace, stage, "succeeded", fence)
        trace = self.store.run_trace(run_id)
        summary = self._context_summary(next_context)
        trace["execution"] = {
            "ok": True,
            "result_hash": digest(self.store.get(run_id)["result"]),
            "context_next_hash": next_hash,
            "context_next_snapshot_hash": digest(next_context),
            "context_next_summary": summary,
            "context_next_summary_hash": digest(summary),
            "context_next_captured_at": capture.get("captured_at", time.time()),
            "context_next_window": capture.get("bridge", {}),
        }
        return self.store.complete_context_sync(run_id, fence, trace)

    def resume_executed(self, run_id):
        row = self.store.get(run_id)
        if row["status"] == "executed":
            return self._sync_context(run_id)
        if row["status"] in ("succeeded", "context_failed"):
            return row
        raise ValueError("run is not ready for post-execution recovery.")

    def recover_executing(self, run_id):
        row = self.store.get(run_id)
        if row["status"] == "executing":
            return self._wait_for_runtime_terminal(run_id)
        return row

    def _wait_for_runtime_terminal(self, run_id):
        while True:
            row = self.store.get(run_id)
            if row["status"] != "executing":
                return row
            recovered = self.store.recover_stale_executions(lease_seconds=30.0)
            if run_id in recovered:
                return self.store.get(run_id)
            time.sleep(0.2)

    def _wait_for_context_terminal(self, run_id):
        while True:
            row = self.store.get(run_id)
            if row["status"] != "executed":
                return row
            if self.store.context_finalizer_expired(run_id):
                return self._sync_context(run_id)
            time.sleep(0.2)

    @staticmethod
    def _error_fingerprint(exc):
        return {
            "type": type(exc).__name__,
            "hash": digest({"type": type(exc).__name__, "message": str(exc)}),
        }

    def _start_stage(self, run_id, name, fence=None):
        trace = self.store.run_trace(run_id)
        stage = {
            "name": name,
            "started_at": time.time(),
            "status": "running",
        }
        trace["stages"].append(stage)
        if fence is None:
            self.store.update_run(run_id, self.store.get(run_id)["status"], trace=trace)
        else:
            self.store.update_context_finalization(run_id, fence, trace)
        return trace, stage

    def _finish_stage(self, run_id, trace, stage, status, fence=None):
        current_trace = self.store.run_trace(run_id)
        current_stage = next(
            item
            for item in current_trace["stages"]
            if item["name"] == stage["name"]
            and item["started_at"] == stage["started_at"]
        )
        current_stage["status"] = status
        current_stage["finished_at"] = time.time()
        if fence is None:
            self.store.update_run(
                run_id,
                self.store.get(run_id)["status"],
                trace=current_trace,
            )
        else:
            self.store.update_context_finalization(run_id, fence, current_trace)
        return current_trace

    @staticmethod
    def _context_summary(context):
        layers = context.get("layers", [])
        return {
            "layers": [
                {
                    "name": layer.get("name"),
                    "layer_ref": layer.get("layer_ref"),
                }
                for layer in layers
            ],
            "is_saved": bool(context.get("is_saved")),
        }
