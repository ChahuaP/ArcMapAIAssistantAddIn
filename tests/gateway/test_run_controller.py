import tempfile
import unittest
from pathlib import Path

from arcmap_runtime_py2.execution_outbox import result_hash
from gateway_py3.run_controller import RunController
from gateway_py3.validators import context_hash
from gateway_py3.run_store import RunStore


class Runner:
    def __init__(self, store): self.store = store
    def plan(self, run_id, command, context, mode, provider, model):
        return self.store.update_run(run_id, "planned", workflow={"action": "execute", "summary": command, "steps": []})


class RunControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self.temp.name) / "runs.sqlite")

    def tearDown(self): self.temp.cleanup()

    def controller(self, reader, executor):
        return RunController(Runner(self.store), self.store, reader, lambda request, row: False, executor)

    def capture(self, layers=None):
        context = {"layers": layers or []}
        return {"context": context, "context_hash": context_hash(context), "bridge": {"bridge_pid": 2, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 1}, "captured_at": 1}

    def test_run_uses_fresh_context_not_request_payload(self):
        run = self.store.create_run("x", "context_single")
        seen = []
        capture = self.capture([{"name": "fresh"}])
        controller = self.controller(lambda run_id, target, phase, fence: capture, lambda run_id, edits, target: None)
        controller.runner.plan = lambda run_id, command, context, mode, provider, model: (seen.append(context), self.store.update_run(run_id, "planned", workflow={"action": "execute", "summary": "x", "steps": []}))[1]
        controller.run(run["id"], {"command": "x", "mode": "context_single", "execute": False})
        self.assertEqual(seen[0]["layers"][0]["name"], "fresh")
        self.assertEqual(self.store.run_trace(run["id"])["context_captures"][0]["window"]["hwnd"], 1)

    def test_execution_reaches_success_only_after_arcmap_claim(self):
        run = self.store.create_run("x", "context_single")
        def execute(run_id, edits, target):
            self.store.claim_for_execution(run_id, target, "owner")
            result = {"ok": True}
            self.store.complete_execution(run_id, "executed", result, "owner", result_hash(result), target)
            return {"ok": True}
        capture = self.capture()
        controller = self.controller(lambda run_id, target, phase, fence: capture, execute)
        controller.run(run["id"], {"command": "x", "mode": "context_single", "execute": True})
        self.assertEqual(self.store.get(run["id"])["status"], "succeeded")

    def test_claim_is_atomic_and_bound_to_id(self):
        first = self.store.create_run("a", "direct_single")
        second = self.store.create_run("b", "direct_single")
        target = self.capture()["bridge"]
        for row in (first, second): self.store.bind_context(row["id"], self.capture())
        for row in (first, second): self.store.update_run(row["id"], "planned"); self.store.update_run(row["id"], "approved")
        self.store.claim_for_execution(second["id"], target, "owner")
        self.assertEqual(self.store.get(first["id"])["status"], "approved")
        self.assertEqual(self.store.get(second["id"])["status"], "executing")
        with self.assertRaises(ValueError): self.store.claim_for_execution(second["id"], target, "owner-2")

    def test_context_bind_is_atomic_and_execution_result_is_not_overwritten(self):
        run = self.store.create_run("x", "context_single")
        capture = self.capture()
        def execute(run_id, edits, target):
            self.store.claim_for_execution(run_id, target, "owner")
            result = {"ok": True, "summary": "ArcPy authoritative"}
            self.store.complete_execution(run_id, "executed", result, "owner", result_hash(result), target)
            return {"ok": True, "summary": "bridge placeholder"}
        controller = self.controller(lambda run_id, target, phase, fence: capture, execute)
        controller.run(run["id"], {"command": "x", "mode": "context_single", "execute": True})
        row = self.store.get(run["id"])
        self.assertTrue(row["context_hash"])
        self.assertEqual(row["result"]["summary"], "ArcPy authoritative")
        with self.assertRaises(ValueError): self.store.bind_context(run["id"], dict(capture, context_hash="different"))

    def test_executing_run_cannot_be_cancelled(self):
        run = self.store.create_run("x", "direct_single")
        capture = self.capture()
        self.store.bind_context(run["id"], capture)
        self.store.update_run(run["id"], "planned")
        self.store.update_run(run["id"], "approved")
        self.store.claim_for_execution(run["id"], capture["bridge"], "owner")
        with self.assertRaises(ValueError): self.store.cancel(run["id"])

    def test_runtime_failure_result_is_preserved(self):
        run = self.store.create_run("x", "context_single")
        capture = self.capture()
        def execute(run_id, edits, target):
            self.store.claim_for_execution(run_id, target, "owner")
            result = {"ok": False, "error": "ArcPy failure", "traceback": "trace"}
            self.store.complete_execution(run_id, "failed", result, "owner", result_hash(result), target)
            raise RuntimeError("bridge reports runtime failure")
        controller = self.controller(lambda run_id, target, phase, fence: capture, execute)
        controller.run(run["id"], {"command": "x", "mode": "context_single", "execute": True})
        row = self.store.get(run["id"])
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["result"]["traceback"], "trace")
        self.assertEqual(row["agent_trace"][0]["run"]["stages"][-1]["status"], "failed")

    def test_swallowed_addin_failure_uses_authoritative_runtime_failure_audit(self):
        run = self.store.create_run("x", "context_single")
        capture = self.capture()

        def addin_onclick(run_id, edits, target):
            try:
                self.store.claim_for_execution(run_id, target, "owner")
                result = {"ok": False, "error": "ArcPy failure", "traceback": "ArcPy traceback"}
                self.store.complete_execution(run_id, "failed", result, "owner", result_hash(result), target)
                raise RuntimeError("ArcPy failure")
            except RuntimeError:
                return {"ok": True, "bridge": "Execute returned normally"}

        row = self.controller(lambda run_id, target, phase, fence: capture, addin_onclick).run(
            run["id"], {"command": "x", "mode": "context_single", "execute": True}
        )
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["agent_trace"][0]["run"]["stages"][-1]["status"], "failed")
        self.assertEqual(row["result"]["traceback"], "ArcPy traceback")

    def test_bridge_transport_error_after_executed_still_syncs_context(self):
        run = self.store.create_run("x", "context_single")
        capture = self.capture()
        def execute(run_id, edits, target):
            self.store.claim_for_execution(run_id, target, "owner")
            result = {"ok": True, "summary": "ArcPy authoritative"}
            self.store.complete_execution(run_id, "executed", result, "owner", result_hash(result), target)
            raise RuntimeError("bridge response was lost")
        controller = self.controller(lambda run_id, target, phase, fence: capture, execute)
        row = controller.run(run["id"], {"command": "x", "mode": "context_single", "execute": True})
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["result"]["summary"], "ArcPy authoritative")
