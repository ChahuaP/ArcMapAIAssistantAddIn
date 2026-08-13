# -*- coding: utf-8 -*-
"""Stage A end-to-end kernel tests (§A.4, §A.5, §11).

Drives the full Run state machine through the GeoPilotKernel using Fake
adapters: read-only clarification pause, execute-with-decide success, clarification
pause, session isolation, and resume. These tests cross the deep-module
boundary only; they never touch the store, planner or ArcMap client directly.
"""
from __future__ import absolute_import

import tempfile
import unittest
import threading
import uuid
from pathlib import Path

from gateway_py3.kernel import contracts
from gateway_py3.kernel.coordinator import GeoPilotKernel, KernelPorts
from gateway_py3.kernel.store import JournalStore
from gateway_py3.kernel.contracts import (
    CallerIdentity, RequestEnvelope, SideEffectScope,
    CLARIFICATION_REQUIRED, POLICY_DENIED,
    SUCCEEDED_STAGE, AUTHORIZATION_REQUIRED,
)

from tests.kernel import fakes


def _envelope(session_id, text="select cities", execute=False, side_effects=None):
    return RequestEnvelope(
        session_id=session_id,
        request_id="00000000-0000-0000-0000-000000000002",
        text=text,
        caller=CallerIdentity(user_id="u1", tenant_id="t1", role="analyst"),
        execute=execute,
        side_effects=side_effects,
        target_selector={"bridge_pid": 2001, "bridge_port": 8766,
                         "arcmap_pid": 2000, "hwnd": 3000,
                         "deployment_hash": "a" * 64},
        model_plan=fakes.fake_agent_model_plan().model_dump(mode="json"),
        model_binding_summary=fakes.fake_model_binding_summary(),
    )


def _sid(suffix):
    return "00000000-0000-0000-0000-0000000000%s" % suffix


class _KernelTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "gp.sqlite"
        self.store = JournalStore(path=self.tmp)
        ports = fakes.build_fake_ports(self.store)
        self.kernel = GeoPilotKernel(ports)


class ReadOnlyEndToEndTest(_KernelTestBase):
    """§A.4: a plan-only (execute=False) request pauses at plan_verified.

    The kernel no longer auto-succeeds read-only runs: execute=False means the
    caller asked for a plan, not a side-effecting run, so the state machine
    stops at PLAN_VERIFIED with a recoverable ``plan_only`` pause for human
    review. Drives full chain received -> context_leased -> context_frozen ->
    intent_compiled -> plan_verified -> clarification_required.
    """

    def test_read_only_pauses_for_clarification(self):
        view = self.kernel.submit(_envelope(_sid("01")))
        view = fakes.wait_for_terminal(self.kernel, view.run_id)
        self.assertEqual(view.stage, "clarification_required")
        self.assertIsNotNone(view.outcome)
        self.assertEqual(view.outcome.kind, CLARIFICATION_REQUIRED)
        self.assertTrue(view.outcome.is_recoverable)

    def test_run_records_full_event_chain(self):
        view = self.kernel.submit(_envelope(_sid("02")))
        view = fakes.wait_for_terminal(self.kernel, view.run_id)
        kinds = [e["kind"] for e in view.events]
        self.assertIn("run_received", kinds)
        self.assertIn("context_leased", kinds)
        self.assertIn("context_frozen", kinds)
        self.assertIn("intent_compiled", kinds)
        self.assertIn("plan_verified", kinds)
        self.assertIn("clarification_required", kinds)
        self.assertLess(kinds.index("run_received"), kinds.index("context_frozen"))
        self.assertLess(kinds.index("plan_verified"), kinds.index("clarification_required"))

    def test_inspect_returns_current_state(self):
        view = self.kernel.submit(_envelope(_sid("03")))
        view = fakes.wait_for_terminal(self.kernel, view.run_id)
        inspected = self.kernel.inspect(view.run_id)
        self.assertEqual(inspected.run_id, view.run_id)
        self.assertEqual(inspected.stage, "clarification_required")


class ExecuteWithDecisionTest(_KernelTestBase):
    """§A.4: an execute request pauses at authorization_required until decide."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "gp.sqlite"
        self.store = JournalStore(path=self.tmp)
        # risk_level 2 makes the plan touch map state, so the kernel pauses at
        # authorization_required for an explicit decision instead of
        # auto-authorizing read-only plans.
        ports = fakes.build_fake_ports(self.store, risk_level=2)
        self.kernel = GeoPilotKernel(ports)

    def test_execute_paused_at_authorization(self):
        effects = SideEffectScope(level=2)
        view = self.kernel.submit(_envelope(_sid("04"), execute=True, side_effects=effects))
        view = fakes.wait_for_terminal(self.kernel, view.run_id)
        self.assertEqual(view.stage, AUTHORIZATION_REQUIRED)
        self.assertIsNone(view.outcome)

    def test_decide_approved_completes(self):
        effects = SideEffectScope(level=2)
        view = self.kernel.submit(_envelope(_sid("05"), execute=True, side_effects=effects))
        view = fakes.wait_for_terminal(self.kernel, view.run_id)
        plan_digest = view.plan.digest if view.plan else ""
        decision = contracts.AuthorizationDecision(
            decision_id=str(uuid.uuid4()), run_id=view.run_id,
            plan_digest=plan_digest, approved=True, approved_scope=effects,
        )
        final = self.kernel.decide(view.run_id, decision)
        final = fakes.wait_for_terminal(self.kernel, final.run_id)
        self.assertEqual(final.stage, SUCCEEDED_STAGE)
        self.assertTrue(final.outcome.succeeded)
        kinds = [e["kind"] for e in final.events]
        self.assertIn("authorization_approved", kinds)
        self.assertIn("runtime_acquired", kinds)
        self.assertIn("executed", kinds)
        self.assertIn("accepted", kinds)
        self.assertIn("published", kinds)
        self.assertIn("succeeded", kinds)

    def test_decide_denied_terminates_policy_denied(self):
        effects = SideEffectScope(level=2)
        view = self.kernel.submit(_envelope(_sid("06"), execute=True, side_effects=effects))
        view = fakes.wait_for_terminal(self.kernel, view.run_id)
        plan_digest = view.plan.digest if view.plan else ""
        decision = contracts.AuthorizationDecision(
            decision_id=str(uuid.uuid4()), run_id=view.run_id,
            plan_digest=plan_digest, approved=False,
        )
        final = self.kernel.decide(view.run_id, decision)
        final = fakes.wait_for_terminal(self.kernel, final.run_id)
        self.assertEqual(final.stage, "policy_denied")
        self.assertEqual(final.outcome.kind, POLICY_DENIED)
        self.assertTrue(final.outcome.is_terminal)

    def test_decide_on_non_authorized_run_rejected(self):
        view = self.kernel.submit(_envelope(_sid("07")))
        view = fakes.wait_for_terminal(self.kernel, view.run_id)
        decision = contracts.AuthorizationDecision(
            decision_id=str(uuid.uuid4()), run_id=view.run_id,
            plan_digest=view.plan.digest if view.plan else "x", approved=False,
        )
        with self.assertRaises(ValueError):
            self.kernel.decide(view.run_id, decision)


class ClarificationPauseTest(_KernelTestBase):
    """§6.2: TaskCompiler may return ClarificationRequired."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "gp.sqlite"
        self.store = JournalStore(path=self.tmp)
        adapter = fakes.FakeModelAdapter(require_clarification=True)
        ports = fakes.build_fake_ports(self.store, adapter=adapter)
        self.kernel = GeoPilotKernel(ports)

    def test_clarification_pauses(self):
        view = self.kernel.submit(_envelope(_sid("08")))
        view = fakes.wait_for_terminal(self.kernel, view.run_id)
        self.assertEqual(view.stage, "clarification_required")
        self.assertEqual(view.outcome.kind, CLARIFICATION_REQUIRED)
        self.assertTrue(view.outcome.is_recoverable)


class SessionIsolationTest(_KernelTestBase):
    """§5.1, §11 Session isolation: a new session never reads old messages."""

    def test_new_session_has_no_messages(self):
        v = self.kernel.submit(_envelope(_sid("0a"), text="first task"))
        fakes.wait_for_terminal(self.kernel, v.run_id)
        v = self.kernel.submit(_envelope(_sid("0b"), text="second task"))
        fakes.wait_for_terminal(self.kernel, v.run_id)
        first = self.store.session_messages(_sid("0a"))
        second = self.store.session_messages(_sid("0b"))
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["payload"]["text"], "first task")
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["payload"]["text"], "second task")
        self.assertNotIn("first task", [m["payload"]["text"] for m in second])

    def test_cross_session_query_returns_only_own_messages(self):
        v = self.kernel.submit(_envelope(_sid("0c"), text="alpha"))
        fakes.wait_for_terminal(self.kernel, v.run_id)
        v = self.kernel.submit(_envelope(_sid("0d"), text="beta"))
        fakes.wait_for_terminal(self.kernel, v.run_id)
        messages = self.store.session_messages(_sid("0c"))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["payload"]["text"], "alpha")


class ResumeTest(_KernelTestBase):
    """§7: resume drives a paused run forward."""

    def test_answer_clarification_is_journaled_and_recompiles(self):
        adapter = fakes.FakeModelAdapter(require_clarification=True)
        ports = fakes.build_fake_ports(self.store, adapter=adapter)
        kernel = GeoPilotKernel(ports)
        view = kernel.submit(_envelope(_sid("09")))
        view = fakes.wait_for_terminal(kernel, view.run_id)
        self.assertEqual(view.stage, "clarification_required")
        request = kernel.get_run(view.run_id)
        pending = [event for event in self.store.run_events(view.run_id)
                   if event["kind"] == "clarification_required"][-1]["payload"]["clarifications"][0]
        answer = contracts.ClarificationAnswer(
            run_id=view.run_id, session_id=request["session_id"],
            caller=contracts.CallerIdentity(user_id="u1", tenant_id="t1", role="analyst"),
            clarification_id=pending["clarification_id"], answer="new_selection",
        )
        answered = kernel.answer_clarification(view.run_id, answer)
        answered = fakes.wait_for_terminal(kernel, answered.run_id)
        self.assertEqual(answered.stage, "clarification_required")
        self.assertEqual(1, len([event for event in self.store.run_events(view.run_id)
                                 if event["kind"] == "clarification_answered"]))
        with self.assertRaises(ValueError):
            kernel.resume(view.run_id)

    def test_clarification_recompile_retains_sealed_model_digest(self):
        adapter = fakes.FakeModelAdapter(require_clarification=True)
        kernel = GeoPilotKernel(fakes.build_fake_ports(self.store, adapter=adapter))
        view = fakes.wait_for_terminal(kernel, kernel.submit(_envelope(_sid("18"))).run_id)
        initial = next(event["payload"] for event in self.store.run_events(view.run_id)
                       if event["kind"] == "run_received")
        pending = [event for event in self.store.run_events(view.run_id)
                   if event["kind"] == "clarification_required"][-1]["payload"]["clarifications"][0]
        request = kernel.get_run(view.run_id)
        kernel.answer_clarification(view.run_id, contracts.ClarificationAnswer(
            run_id=view.run_id, session_id=request["session_id"],
            caller=contracts.CallerIdentity(user_id="u1", tenant_id="t1", role="analyst"),
            clarification_id=pending["clarification_id"], answer="new_selection"))
        reconstructed = kernel._reconstruct_request(kernel.get_run(view.run_id))
        self.assertEqual(contracts.digest(initial["model_plan"]), reconstructed.model_plan_digest)
        self.assertEqual(initial["model_binding_summary"], reconstructed.model_binding_summary)

    def test_typed_clarification_rejects_unrelated_wrong_type_and_stale_context(self):
        adapter = fakes.FakeModelAdapter(require_clarification=True)
        kernel = GeoPilotKernel(fakes.build_fake_ports(self.store, adapter=adapter))
        view = fakes.wait_for_terminal(kernel, kernel.submit(_envelope(_sid("19"))).run_id)
        request = kernel.get_run(view.run_id)
        pending = [event for event in self.store.run_events(view.run_id)
                   if event["kind"] == "clarification_required"][-1]["payload"]["clarifications"][0]
        caller = contracts.CallerIdentity(user_id="u1", tenant_id="t1", role="analyst")
        with self.assertRaises(ValueError):
            kernel.answer_clarification(view.run_id, contracts.ClarificationAnswer(
                run_id=view.run_id, session_id=request["session_id"], caller=caller,
                clarification_id="clarification:unrelated", answer="new_selection"))
        with self.assertRaises(ValueError):
            kernel.answer_clarification(view.run_id, contracts.ClarificationAnswer(
                run_id=view.run_id, session_id=request["session_id"], caller=caller,
                clarification_id=pending["clarification_id"], answer=123))
        original = kernel._load_context
        kernel._load_context = lambda run_id: original(run_id).model_copy(
            update={"active_data_frame": "drifted"})
        with self.assertRaisesRegex(ValueError, "context has drifted"):
            kernel.answer_clarification(view.run_id, contracts.ClarificationAnswer(
                run_id=view.run_id, session_id=request["session_id"], caller=caller,
                clarification_id=pending["clarification_id"], answer="new_selection"))


class ModelRuntimeWiringTest(_KernelTestBase):
    """§B.5: the kernel's compiler/planner call the model through ModelRuntime;
    identical calls reuse the cache (zero adapter calls the second time)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "gp.sqlite"
        self.store = JournalStore(path=self.tmp)
        self.adapter = fakes.FakeModelAdapter()
        ports = fakes.build_fake_ports(self.store, adapter=self.adapter)
        self.kernel = GeoPilotKernel(ports)

    def test_model_calls_flow_through_model_runtime(self):
        view = self.kernel.submit(_envelope(_sid("0e")))
        view = fakes.wait_for_terminal(self.kernel, view.run_id)
        # execute=False pauses at plan_verified (plan_only); semantic + planner
        # model calls have already happened.
        self.assertEqual(view.stage, "clarification_required")
        # semantic + planner = 2 adapter calls
        self.assertEqual(self.adapter.call_count, 2)

    def test_identical_request_reuses_cache_across_runs(self):
        view = self.kernel.submit(_envelope(_sid("0f"), text="select cities"))
        fakes.wait_for_terminal(self.kernel, view.run_id)
        before = self.adapter.call_count
        view = self.kernel.submit(_envelope(_sid("10"), text="select cities"))
        fakes.wait_for_terminal(self.kernel, view.run_id)
        # second identical request hits the ModelRuntime cache: zero new calls
        self.assertEqual(self.adapter.call_count, before)


class ConcurrentResumeTest(unittest.TestCase):
    def test_two_resume_calls_commit_once(self):
        from gateway_py3.kernel.coordinator import GeoPilotKernel
        from gateway_py3.kernel.store import JournalStore
        from tests.kernel import fakes
        store = JournalStore(path=__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "gp.sqlite")
        kernel = GeoPilotKernel(fakes.build_fake_ports(store))
        state = {"stage": "execution_indeterminate", "outcome_kind": "ExecutionIndeterminate"}
        calls = []
        barrier = threading.Barrier(2)
        kernel.ports.store.get_run = lambda run_id: dict(state)
        kernel._view = lambda run_id: dict(state)
        def reconcile(run_id):
            calls.append(run_id)
            state.update(stage="succeeded", outcome_kind="Succeeded")
            return dict(state)
        kernel._reconcile_runtime_run = reconcile
        results = []
        def worker():
            barrier.wait()
            results.append(kernel.resume("run"))
        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        [thread.start() for thread in threads]
        [thread.join() for thread in threads]
        self.assertEqual(calls, ["run"])
        self.assertEqual(len(results), 2)


if __name__ == "__main__":
    unittest.main()
