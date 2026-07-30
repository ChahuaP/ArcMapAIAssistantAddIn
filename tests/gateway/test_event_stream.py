import unittest
from pathlib import Path

from gateway_py3.event_bus import EventBus
from gateway_py3.routes.event_topics import mutation_events


class EventStreamTests(unittest.TestCase):
    def test_event_bus_returns_events_after_seen_id(self):
        bus = EventBus()
        first = bus.publish("runs.changed", {"path": "/runs"})
        second = bus.publish("runs.changed", {"path": "/runs/1/cancel"})

        events = bus.wait_after(first["id"], timeout=0)

        self.assertEqual([item["id"] for item in events], [second["id"]])
        self.assertEqual(events[0]["type"], "runs.changed")

    def test_mutation_routes_publish_targeted_events(self):
        self.assertEqual(mutation_events("/runs", {"ok": True}), ["runs.changed"])
        self.assertEqual(mutation_events("/arcmap/active", {"ok": True}), ["arcmap.changed"])
        self.assertEqual(mutation_events("/config", {"config": {}}), ["config.changed"])
        self.assertEqual(mutation_events("/tools/abc/enable", {"tool": {}}), ["tools.changed", "catalog.changed"])
        self.assertEqual(mutation_events("/runs/1/context", {"ok": True}), ["context.changed"])
        self.assertEqual(mutation_events("/runs/1/cancel", {"ok": True}), ["runs.changed"])

    def test_web_consumes_the_same_event_domains(self):
        text = (Path(__file__).parents[2] / "gateway_py3" / "web" / "app_events.js").read_text(encoding="utf-8")
        for name in ("runs", "context", "arcmap", "config", "tools", "catalog"):
            self.assertIn("'%s.changed'" % name, text)
        self.assertIn("types.has('runs')", text)
        self.assertNotIn("workflows.changed", text)
        self.assertNotIn("types.has('workflows')", text)


if __name__ == "__main__":
    unittest.main()
