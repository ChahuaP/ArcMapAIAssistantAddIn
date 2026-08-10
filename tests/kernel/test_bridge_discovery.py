import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gateway_py3.kernel.contracts import TargetSelector
from gateway_py3.runtime.bridge_discovery import discover_bridge_target


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class BridgeDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.ready = self.directory / "bridge.ready"
        self.ready.write_text(json.dumps({"port": 8766, "pid": 41}), encoding="utf-8")

    def _discover(self, targets, selector):
        health = {"ok": True, "bridge_pid": 41, "bridge_port": 8766,
                  "deployment_hash": "a" * 64}

        def respond(request, timeout):
            if request.full_url.endswith("/health"):
                return _Response(health)
            if request.full_url.endswith("/targets"):
                return _Response({"ok": True, "targets": targets})
            raise AssertionError("unexpected Bridge endpoint: %s" % request.full_url)

        with patch("gateway_py3.runtime.bridge_discovery._ready_file_path", return_value=str(self.ready)), \
             patch("gateway_py3.runtime.bridge_discovery.urllib.request.urlopen",
                   side_effect=respond):
            return discover_bridge_target(selector)

    def test_zero_targets_fails_closed(self):
        selector = TargetSelector(bridge_pid=41, bridge_port=8766, arcmap_pid=81,
                                  hwnd=91, deployment_hash="a" * 64)
        with self.assertRaisesRegex(RuntimeError, "resolved 0 targets"):
            self._discover([], selector)

    def test_one_target_is_exactly_bound(self):
        selector = TargetSelector(bridge_pid=41, bridge_port=8766, arcmap_pid=81,
                                  hwnd=91, deployment_hash="a" * 64)
        target = self._discover([{"arcmap_pid": 81, "hwnd": 91, "active": True}], selector)
        self.assertEqual(target, {"bridge_pid": 41, "bridge_port": 8766,
                                  "arcmap_pid": 81, "hwnd": 91,
                                  "source_sha256": "a" * 64})

    def test_multiple_targets_resolve_only_explicit_selection(self):
        selector = TargetSelector(bridge_pid=41, bridge_port=8766, arcmap_pid=82,
                                  hwnd=92, deployment_hash="a" * 64)
        target = self._discover([{"arcmap_pid": 81, "hwnd": 91, "active": True},
                                 {"arcmap_pid": 82, "hwnd": 92, "active": False}], selector)
        self.assertEqual(target["arcmap_pid"], 82)

    def test_target_without_boolean_active_is_rejected(self):
        selector = TargetSelector(bridge_pid=41, bridge_port=8766, arcmap_pid=81,
                                  hwnd=91, deployment_hash="a" * 64)
        with self.assertRaisesRegex(RuntimeError, "boolean active"):
            self._discover([{"arcmap_pid": 81, "hwnd": 91}], selector)
