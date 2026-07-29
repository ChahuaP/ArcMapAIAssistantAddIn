from __future__ import annotations

import time

from .experiments import digest


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
        capture = self._capture_context(run_id, "before_planning")
        if capture is None:
            return self.store.get(run_id)
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

        return self._execute(run_id, allow_edits)

    def _capture_context(self, run_id, phase):
        trace, stage = self._start_stage(run_id, "context_" + phase)
        try:
            capture = self.context_reader()
            context = capture.get("context") if isinstance(capture, dict) else None
            if not isinstance(context, dict):
                raise ValueError("ArcMap context capture is invalid.")
            record = {
                "phase": phase,
                "hash": digest(context),
                "captured_at": capture.get("captured_at", time.time()),
                "window": capture.get("bridge", {}),
            }
            trace.setdefault("context_captures", []).append(record)
            self.store.update_run(run_id, self.store.get(run_id)["status"], trace=trace)
        except Exception as exc:
            trace = self._finish_stage(run_id, trace, stage, "failed")
            self.store.fail_run(run_id, "context_" + phase, exc, trace)
            return None
        self._finish_stage(run_id, trace, stage, "succeeded")
        return capture

    def _plan(self, run_id, request, context):
        trace, stage = self._start_stage(run_id, "planning")
        try:
            artifacts = request.get("artifacts")
            if artifacts:
                row = self.runner.plan_from_artifacts(
                    run_id,
                    request["mode"],
                    context,
                    artifacts,
                    request.get("provider", ""),
                    request.get("model", ""),
                )
            else:
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

    def _execute(self, run_id, allow_edits):
        trace, stage = self._start_stage(run_id, "execution")
        try:
            result = self.executor(run_id, allow_edits)
        except Exception as exc:
            trace = self._finish_stage(run_id, trace, stage, "failed")
            return self.store.fail_run(run_id, "execution", exc, trace)

        self._finish_stage(run_id, trace, stage, "succeeded")
        if self.store.is_cancel_requested(run_id):
            return self.store.get(run_id)
        return self._sync_context(run_id, result)

    def _sync_context(self, run_id, result):
        trace, stage = self._start_stage(run_id, "context_sync")
        try:
            capture = self.context_reader()
            next_context = capture.get("context") if isinstance(capture, dict) else None
            if not isinstance(next_context, dict):
                raise ValueError("ArcMap context capture is invalid.")
        except Exception as exc:
            trace = self._finish_stage(run_id, trace, stage, "failed")
            return self.store.fail_run(run_id, "context_sync", exc, trace)

        self._finish_stage(run_id, trace, stage, "succeeded")
        if self.store.is_cancel_requested(run_id):
            return self.store.get(run_id)
        trace = self.store.run_trace(run_id)
        summary = self._context_summary(next_context)
        trace["execution"] = {
            "ok": True,
            "result_hash": digest(result),
            "context_next_hash": digest(next_context),
            "context_next_summary": summary,
            "context_next_summary_hash": digest(summary),
            "context_next_captured_at": capture.get("captured_at", time.time()),
            "context_next_window": capture.get("bridge", {}),
        }
        row = self.store.get(run_id)
        if row["status"] != "succeeded":
            raise RuntimeError("ArcMap did not complete the claimed run successfully.")
        return self.store.update_run(
            run_id,
            "succeeded",
            trace=trace,
            result={
                "execution": result,
                "context_next_hash": digest(next_context),
            },
        )

    def _start_stage(self, run_id, name):
        trace = self.store.run_trace(run_id)
        stage = {
            "name": name,
            "started_at": time.time(),
            "status": "running",
        }
        trace["stages"].append(stage)
        self.store.update_run(run_id, self.store.get(run_id)["status"], trace=trace)
        return trace, stage

    def _finish_stage(self, run_id, trace, stage, status):
        current_trace = self.store.run_trace(run_id)
        current_stage = next(
            item
            for item in current_trace["stages"]
            if item["name"] == stage["name"]
            and item["started_at"] == stage["started_at"]
        )
        current_stage["status"] = status
        current_stage["finished_at"] = time.time()
        self.store.update_run(
            run_id,
            self.store.get(run_id)["status"],
            trace=current_trace,
        )
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
