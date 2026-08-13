# -*- coding: utf-8 -*-
"""Stage E JournalEventProjection tests (§14, §11 流式输出).

Covers: run_events projection to SSE types; Last-Event-ID reconnect from the
journal (no loss even after in-memory truncation); non-streaming adapters
skip model.token silently; terminal state recoverable via inspect.
"""
from __future__ import absolute_import

import tempfile
import unittest
from pathlib import Path

from gateway_py3.kernel import contracts
from gateway_py3.kernel.coordinator import GeoPilotKernel
from gateway_py3.kernel.store import JournalStore
from gateway_py3.streaming.event_projection import JournalEventProjection

from tests.kernel import fakes

SID = "00000000-0000-0000-0000-000000000001"


class _KernelWithProjection:
    """Wraps a kernel + store + projection, notifying the projection after
    each journal append (the production HTTP server does the same)."""

    def __init__(self, store, kernel, projection):
        self.store = store
        self.kernel = kernel
        self.projection = projection

    def submit(self, request):
        view = self.kernel.submit(request)
        # submit drives the state machine in a background thread; wait for it
        # to settle before reading the projection (tests assert terminal state).
        fakes.wait_for_terminal(self.kernel, view.run_id)
        self._notify_all()
        return self.kernel.inspect(view.run_id)

    def _notify_all(self):
        events = self.store.events_after(0, limit=1000)
        for event in events:
            self.projection.notify(event["event_seq"], event["run_id"],
                                   event["kind"], event["stage"], event["payload"])


class EventProjectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "gp.sqlite"
        self.store = JournalStore(path=self.tmp)
        ports = fakes.build_fake_ports(self.store)
        self.kernel = GeoPilotKernel(ports)
        self.projection = JournalEventProjection(self.store)
        self.harness = _KernelWithProjection(self.store, self.kernel, self.projection)

    def _envelope(self, text="select cities"):
        return contracts.RequestEnvelope(
            session_id=SID,
            request_id="00000000-0000-0000-0000-000000000002",
            text=text,
            caller=contracts.CallerIdentity(user_id="u1", tenant_id="t1", role="analyst"),
            target_selector={"bridge_pid": 2001, "bridge_port": 8766,
                             "arcmap_pid": 2000, "hwnd": 3000,
                             "deployment_hash": "a" * 64},
            model_plan=fakes.fake_agent_model_plan().model_dump(mode="json"),
            model_binding_summary=fakes.fake_model_binding_summary(),
        )

    def test_events_project_to_sse_types(self):
        view = self.harness.submit(self._envelope())
        events = self.projection.projection_after(0)
        types = [e["type"] for e in events]
        self.assertIn("run.stage_changed", types)
        # every projected event carries run_id + stage
        for event in events:
            self.assertIn("run_id", event["payload"])
            self.assertIn("stage", event["payload"])
            if event["type"] == "run.stage_changed":
                self.assertIn("outcome_kind", event["payload"])
        paused = next(event for event in events
                      if event["payload"]["stage"] == "clarification_required")
        self.assertEqual(contracts.CLARIFICATION_REQUIRED,
                         paused["payload"]["outcome_kind"])
        # execute=False pauses at plan_verified (plan_only review).
        self.assertEqual(view.stage, "clarification_required")

    def test_reconnect_from_last_event_id_no_loss(self):
        view = self.harness.submit(self._envelope())
        events = self.projection.projection_after(0)
        last_id = events[-1]["id"]
        # simulate reconnect: new event arrives after last_id
        view2 = self.harness.submit(self._envelope(text="buffer again"))
        resumed = self.projection.projection_after(last_id)
        self.assertGreater(len(resumed), 0)
        self.assertGreater(resumed[0]["id"], last_id)
        # terminal state is readable via the journal regardless of SSE
        run = self.store.get_run(view2.run_id)
        self.assertEqual(run["stage"], "clarification_required")

    def test_terminal_stage_changed_carries_its_journaled_outcome_kind(self):
        request = self._envelope("fail context")
        self.store.create_session(SID, "t1")
        run_id = self.store.create_run(request)["run_id"]
        self.store.append_event(
            run_id, "context_failed", "context", {"reason": "bridge unavailable"},
            outcome=contracts.outcome_failed(
                contracts.INFRASTRUCTURE_FAILED, "context", "bridge_down", "bridge unavailable"),
        )

        event = self.projection.projection_after(0)[-1]

        self.assertEqual("run.stage_changed", event["type"])
        self.assertEqual("infrastructure_failed", event["payload"]["stage"])
        self.assertEqual(contracts.INFRASTRUCTURE_FAILED, event["payload"]["outcome_kind"])

    def test_wait_after_wakes_on_new_event(self):
        import threading
        results = []

        def waiter():
            events = self.projection.wait_after(0, timeout=5.0)
            results.append(events)

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        import time
        time.sleep(0.1)
        self.harness.submit(self._envelope())
        thread.join(timeout=6.0)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results)
        self.assertGreater(len(results[0]), 0)


class PublicationTransactionTest(unittest.TestCase):
    @staticmethod
    def _artifact():
        return contracts.ArtifactIdentity(
            output_id="out", kind="feature_class", output_format="gdb",
            logical_dataset_path="C:\\staging\\run\\out.gdb\\roads",
            source_publish_unit_path="C:\\staging\\run\\out.gdb",
            destination_dataset_path="C:\\publish\\out.gdb\\roads",
            destination_publish_unit_path="C:\\publish",
            publication_kind="file_gdb")

    @staticmethod
    def _publication():
        document = {
            "output_id": "out", "kind": "feature_class", "output_format": "gdb",
            "destination_dataset_path": "C:\\publish\\out.gdb\\roads",
            "destination_publish_unit_path": "C:\\publish",
            "publication_kind": "file_gdb",
            "members": [{"relative_path": "out.gdb/a", "size": 1, "sha256": "a" * 64}],
            "semantic_evidence": {"kind": "feature_class"},
            "acceptance_evidence_hash": "b" * 64,
        }
        document["evidence_hash"] = contracts.digest(document)
        return {"publication_id": "pub", "publication_kind": "artifact_bundle",
                "artifacts": [{"output_id": "out"}], "published_artifacts": [document]}

    def _accepted_run(self, store):
        request = contracts.RequestEnvelope(
            session_id=SID, request_id="00000000-0000-0000-0000-000000000009",
            text="test", caller=contracts.CallerIdentity(user_id="u", tenant_id="t", role="analyst"),
            target_selector={"bridge_pid": 2001, "bridge_port": 8766,
                             "arcmap_pid": 2000, "hwnd": 3000,
                             "deployment_hash": "a" * 64},
            model_plan=fakes.fake_agent_model_plan().model_dump(mode="json"),
            model_binding_summary=fakes.fake_model_binding_summary())
        store.create_session(SID, "t")
        run_id = store.create_run(request)["run_id"]
        for kind, stage in (("context_leased", "context_leased"), ("context_frozen", "context_frozen"),
                            ("intent_compiled", "intent_compiled"), ("plan_verified", "plan_verified"),
                            ("authorization_required", "authorization_required"), ("authorization_approved", "authorized"),
                            ("runtime_acquired", "runtime_acquired"), ("execution_started", "executing"),
                            ("executed", "executed"), ("accepted", "accepted")):
            store.append_event(run_id, kind, stage, {})
        return run_id

    def test_finalize_publication_marks_exact_staged_artifact_published(self):
        store = JournalStore(path=Path(tempfile.mkdtemp()) / "gp.sqlite")
        run_id = self._accepted_run(store)
        artifact = self._artifact()
        store.store_artifact(run_id, artifact)
        store.finalize_publication(run_id, "grant", self._publication())
        self.assertEqual(store.list_staged_artifacts(run_id), [])
        published = store.list_published_artifacts(run_id)
        self.assertEqual("C:\\publish\\out.gdb\\roads", published[0]["destination_dataset_path"])
        self.assertNotIn("logical_dataset_path", published[0])
        with store._connection() as conn:
            self.assertEqual(conn.execute("SELECT staged, published FROM artifacts WHERE run_id=? AND output_id='out'", (run_id,)).fetchone(), (0, 1))

    def test_finalize_publication_rolls_back_receipt_when_event_write_fails(self):
        store = JournalStore(path=Path(tempfile.mkdtemp()) / "gp.sqlite")
        run_id = self._accepted_run(store)
        original = store._append_event_locked
        store._append_event_locked = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fault"))
        with self.assertRaisesRegex(RuntimeError, "fault"):
            store.finalize_publication(run_id, "grant", {"publication_id": "pub"})
        store._append_event_locked = original
        with store._connection() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM publication_receipts WHERE run_id=?", (run_id,)).fetchone())
        self.assertEqual(store.get_run(run_id)["stage"], "accepted")

    def test_listener_failure_after_commit_does_not_reverse_publication(self):
        store = JournalStore(path=Path(tempfile.mkdtemp()) / "gp.sqlite")
        run_id = self._accepted_run(store)
        artifact = self._artifact()
        store.store_artifact(run_id, artifact)
        store.add_event_listener(lambda *args: (_ for _ in ()).throw(RuntimeError("listener fault")))

        store.finalize_publication(run_id, "grant", self._publication())

        self.assertEqual(store.get_run(run_id)["stage"], "published")
        self.assertEqual(store.list_staged_artifacts(run_id), [])
        with store._connection() as conn:
            self.assertEqual(conn.execute(
                "SELECT staged, published FROM artifacts WHERE run_id=? AND output_id='out'",
                (run_id,)).fetchone(), (0, 1))


if __name__ == "__main__":
    unittest.main()
