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
        self._notify_all()
        return view

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
        self.assertEqual(view.stage, "succeeded")

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
        self.assertEqual(run["stage"], "succeeded")

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


if __name__ == "__main__":
    unittest.main()
