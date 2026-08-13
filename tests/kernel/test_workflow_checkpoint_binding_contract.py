from __future__ import annotations

import unittest

from gateway_py3.intelligence.workflow_engine import WorkflowEngine


class _Snapshot:
    def __init__(self, values):
        self.values = values


class _Application:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get_state(self, config):
        return self.snapshot


class _BrokenApplication:
    def __init__(self, error):
        self.error = error

    def get_state(self, config):
        raise self.error


class WorkflowCheckpointBindingContractTest(unittest.TestCase):
    def _engine(self, snapshot):
        engine = object.__new__(WorkflowEngine)
        engine._compiled = lambda: _Application(snapshot)
        engine._config = lambda run_id: {"thread_id": run_id}
        return engine

    def test_no_checkpoint_is_distinct_from_existing_checkpoint_without_digest(self):
        self.assertIsNone(self._engine(None)._sealed_plan("run", "expected"))
        self.assertIsNone(self._engine(_Snapshot({}))._sealed_plan("run", "expected"))
        with self.assertRaisesRegex(ValueError, "missing model binding digest"):
            self._engine(_Snapshot({"plan": {}}))._sealed_plan("run", "expected")

    def test_existing_checkpoint_with_empty_digest_is_contract_failure(self):
        with self.assertRaisesRegex(ValueError, "missing model binding digest"):
            self._engine(_Snapshot({"model_plan_digest": "", "plan": {}}))._sealed_plan(
                "run", "expected")

    def test_checkpoint_read_errors_are_not_treated_as_missing_checkpoints(self):
        for error in (ValueError("undecryptable checkpoint"),
                      RuntimeError("checkpoint serializer failed")):
            engine = object.__new__(WorkflowEngine)
            engine._compiled = lambda error=error: _BrokenApplication(error)
            engine._config = lambda run_id: {"thread_id": run_id}
            with self.assertRaisesRegex(ValueError, "checkpoint could not be read"):
                engine._sealed_plan("run", "expected")

    def test_invalid_sealed_plan_is_a_checkpoint_contract_failure(self):
        with self.assertRaisesRegex(ValueError, "checkpoint plan is invalid"):
            self._engine(_Snapshot({
                "model_plan_digest": "expected",
                "plan": {"not": "a verified plan"},
            }))._sealed_plan("run", "expected")


if __name__ == "__main__":
    unittest.main()
