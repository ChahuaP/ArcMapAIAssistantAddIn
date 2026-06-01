import unittest

from gateway_py3.event_bus import EventBus
from gateway_py3.routes.event_topics import mutation_events


class EventStreamTests(unittest.TestCase):
    def test_event_bus_returns_events_after_seen_id(self):
        bus = EventBus()
        first = bus.publish("workflows.changed", {"path": "/plan"})
        second = bus.publish("context.changed", {"path": "/context"})

        events = bus.wait_after(first["id"], timeout=0)

        self.assertEqual([item["id"] for item in events], [second["id"]])
        self.assertEqual(events[0]["type"], "context.changed")

    def test_mutation_routes_publish_targeted_events(self):
        self.assertEqual(mutation_events("/agent/workflows/propose", {"ok": True}), ["workflows.changed"])
        self.assertEqual(mutation_events("/context", {"context": {}}), ["context.changed"])
        self.assertEqual(mutation_events("/tools/abc/enable", {"tool": {}}), ["tools.changed", "catalog.changed"])
        self.assertEqual(mutation_events("/agent/workflows/validate", {"ok": True}), [])


if __name__ == "__main__":
    unittest.main()
