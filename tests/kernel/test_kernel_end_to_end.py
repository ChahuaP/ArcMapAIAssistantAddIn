# -*- coding: utf-8 -*-
"""Stage A end-to-end kernel tests (§A.4, §A.5, §11).

Drives the full Run state machine through the GeoPilotKernel using Fake
adapters: read-only success, execute-with-decide success, clarification
pause, session isolation, and resume. These tests cross the deep-module
boundary only; they never touch the store, planner or ArcMap client directly.
"""
from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

from gateway_py3.kernel import contracts
from gateway_py3.kernel.coordinator import GeoPilotKernel, KernelPorts
from gateway_py3.kernel.store import JournalStore
from gateway_py3.kernel.contracts import (
    CallerIdentity, RequestEnvelope, SideEffectScope,
    SUCCEEDED, CLARIFICATION_REQUIRED, POLICY_DENIED,
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
    """§A.4: a read-only request runs straight to Succeeded without pausing."""

    def test_read_only_runs_to_succeeded(self):
        view = self.kernel.submit(_envelope(_sid("01")))
        self.assertEqual(view.stage, SUCCEEDED_STAGE)
        self.assertIsNotNone(view.outcome)
        self.assertTrue(view.outcome.succeeded)
        self.assertEqual(view.outcome.kind, SUCCEEDED)

    def test_run_records_full_event_chain(self):
        view = self.kernel.submit(_envelope(_sid("02")))
        kinds = [e["kind"] for e in view.events]
        self.assertIn("run_received", kinds)
        self.assertIn("context_frozen", kinds)
        self.assertIn("intent_compiled", kinds)
        self.assertIn("plan_verified", kinds)
        self.assertIn("authorization_skipped", kinds)
        self.assertIn("succeeded", kinds)
        self.assertLess(kinds.index("run_received"), kinds.index("context_frozen"))
        self.assertLess(kinds.index("plan_verified"), kinds.index("authorization_skipped"))

    def test_inspect_returns_current_state(self):
        view = self.kernel.submit(_envelope(_sid("03")))
        inspected = self.kernel.inspect(view.run_id)
        self.assertEqual(inspected.run_id, view.run_id)
        self.assertEqual(inspected.stage, SUCCEEDED_STAGE)


class ExecuteWithDecisionTest(_KernelTestBase):
    """§A.4: an execute request pauses at authorization_required until decide."""

    def test_execute_paused_at_authorization(self):
        effects = SideEffectScope(level=1, paths=("C:/out",), datasets=())
        view = self.kernel.submit(_envelope(_sid("04"), execute=True, side_effects=effects))
        self.assertEqual(view.stage, AUTHORIZATION_REQUIRED)
        self.assertIsNone(view.outcome)

    def test_decide_approved_completes(self):
        effects = SideEffectScope(level=1, paths=("C:/out",), datasets=())
        view = self.kernel.submit(_envelope(_sid("05"), execute=True, side_effects=effects))
        final = self.kernel.decide(view.run_id, approved=True)
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
        effects = SideEffectScope(level=1, paths=("C:/out",), datasets=())
        view = self.kernel.submit(_envelope(_sid("06"), execute=True, side_effects=effects))
        final = self.kernel.decide(view.run_id, approved=False)
        self.assertEqual(final.stage, "policy_denied")
        self.assertEqual(final.outcome.kind, POLICY_DENIED)
        self.assertTrue(final.outcome.is_terminal)

    def test_decide_on_non_authorized_run_rejected(self):
        view = self.kernel.submit(_envelope(_sid("07")))
        with self.assertRaises(ValueError):
            self.kernel.decide(view.run_id, approved=True)


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
        self.assertEqual(view.stage, "clarification_required")
        self.assertEqual(view.outcome.kind, CLARIFICATION_REQUIRED)
        self.assertTrue(view.outcome.is_recoverable)


class SessionIsolationTest(_KernelTestBase):
    """§5.1, §11 Session isolation: a new session never reads old messages."""

    def test_new_session_has_no_messages(self):
        self.kernel.submit(_envelope(_sid("0a"), text="first task"))
        self.kernel.submit(_envelope(_sid("0b"), text="second task"))
        first = self.store.session_messages(_sid("0a"))
        second = self.store.session_messages(_sid("0b"))
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["payload"]["text"], "first task")
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["payload"]["text"], "second task")
        self.assertNotIn("first task", [m["payload"]["text"] for m in second])

    def test_cross_session_query_returns_only_own_messages(self):
        self.kernel.submit(_envelope(_sid("0c"), text="alpha"))
        self.kernel.submit(_envelope(_sid("0d"), text="beta"))
        messages = self.store.session_messages(_sid("0c"))
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["payload"]["text"], "alpha")


class ResumeTest(_KernelTestBase):
    """§7: resume drives a paused run forward."""

    def test_resume_after_clarification_not_implemented_yet(self):
        # A clarification pause is recoverable; resume should attempt to
        # re-advance. With the fake model still requiring clarification,
        # it pauses again at the same stage.
        adapter = fakes.FakeModelAdapter(require_clarification=True)
        ports = fakes.build_fake_ports(self.store, adapter=adapter)
        kernel = GeoPilotKernel(ports)
        view = kernel.submit(_envelope(_sid("09")))
        self.assertEqual(view.stage, "clarification_required")
        resumed = kernel.resume(view.run_id)
        self.assertEqual(resumed.stage, "clarification_required")


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
        self.assertEqual(view.stage, SUCCEEDED_STAGE)
        # semantic + planner = 2 adapter calls
        self.assertEqual(self.adapter.call_count, 2)

    def test_identical_request_reuses_cache_across_runs(self):
        self.kernel.submit(_envelope(_sid("0f"), text="select cities"))
        before = self.adapter.call_count
        self.kernel.submit(_envelope(_sid("10"), text="select cities"))
        # second identical request hits the ModelRuntime cache: zero new calls
        self.assertEqual(self.adapter.call_count, before)


if __name__ == "__main__":
    unittest.main()
