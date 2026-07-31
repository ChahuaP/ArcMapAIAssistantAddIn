import pathlib
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.routes import arcmap
from gateway_py3.routes import handle_get
from gateway_py3.run_store import RunStore


class WorkbenchStateTests(unittest.TestCase):
    def test_workbench_state_returns_first_screen_payload_without_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(pathlib.Path(directory) / "runs.sqlite")
            _planned(store, "刷新地图", "context_single")
            state = SimpleNamespace(catalog=OperationCatalog(), store=store)

            with patch("gateway_py3.routes.arcmap.bridges", return_value=[{"bridge_pid": 1, "bridge_port": 8766, "arcmap_pid": 10, "hwnd": 2}]):
                result = handle_get(state, "/api/workbench-state", "0.21.2")

        self.assertEqual(result["health"]["app_version"], "0.21.2")
        self.assertIn("config", result)
        self.assertNotIn("context", result)
        self.assertEqual(result["arcmap"]["bridges"][0]["hwnd"], 2)
        self.assertNotIn("agent_trace", result["runs"][0])

    def test_run_list_filters_and_detail_lazy_load(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(pathlib.Path(directory) / "runs.sqlite")
            first = _planned(store, "上下文单模型", "context_single")
            time.sleep(0.01)
            second = _planned(store, "多智能体", "multi_agent")
            state = SimpleNamespace(catalog=OperationCatalog(), store=store)

            listed = handle_get(state, "/api/runs", "0.21.2", {
                "mode": ["multi_agent"],
                "limit": ["10"],
                "include_trace": ["false"],
            })
            since = handle_get(state, "/api/runs", "0.21.2", {"since": [str(first["updated_at"])], "limit": ["10"]})

        self.assertEqual([item["id"] for item in listed["runs"]], [second["id"]])
        self.assertNotIn("agent_trace", listed["runs"][0])
        self.assertEqual([item["id"] for item in since["runs"]], [second["id"]])

    def test_bridge_scan_uses_short_ttl_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(pathlib.Path(directory) / "runs.sqlite")
            store.set_state("arcmap_bridge:1", {"bridge_pid": 1, "bridge_port": 8771})
            state = SimpleNamespace(store=store, bridge_cache={"expires_at": 0.0, "bridges": []})
            calls = []

            def fake_health(port=None):
                calls.append(port)
                return {"ok": True, "bridge_pid": 1, "bridge_port": 8771, "summary": {}}

            with patch("gateway_py3.arcmap_bridge_client.ensure_running", return_value=None):
                with patch("gateway_py3.arcmap_bridge_client.health", side_effect=fake_health):
                    first = arcmap.bridges(state, port_checker=lambda port: port == 8771)
                    second = arcmap.bridges(state, port_checker=lambda port: port == 8771)

        self.assertEqual(first, second)
        self.assertEqual(calls, [8771])

    def test_bridge_restart_refreshes_transport_for_same_arcmap_identity(self):
        stored = {"bridge_pid": 1, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 2}
        refreshed = arcmap.target_bridge_from_health({
            "bridge_pid": 9, "bridge_port": 8770,
            "summary": {"targets": [{"arcmap_pid": 20, "hwnd": 2}]},
        }, stored, 2)
        self.assertEqual(refreshed["bridge_pid"], 9)
        self.assertEqual(refreshed["bridge_port"], 8770)
        self.assertEqual(refreshed["arcmap_pid"], 20)

    def test_arcmap_restart_reusing_hwnd_does_not_inherit_active_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RunStore(pathlib.Path(directory) / "runs.sqlite")
            state = SimpleNamespace(store=store)
            store.set_state("arcmap_active_bridge", {"bridge_pid": 1, "bridge_port": 8766, "arcmap_pid": 20, "hwnd": 2})
            live = [{"bridge_pid": 9, "bridge_port": 8770, "arcmap_pid": 21, "hwnd": 2}]
            arcmap.mark_active_bridge(state, live)
        self.assertNotIn("active", live[0])


def _workflow(operation):
    return {
        "action": "execute",
        "summary": "执行任务。",
        "steps": [
            {"id": "step_1", "operation": operation, "arguments": {}, "reason": "执行任务。"}
        ],
    }


def _planned(store, command, mode):
    row = store.create_run(command, mode)
    return store.update_run(row["id"], "planned", workflow=_workflow("context.list_layers"))


if __name__ == "__main__":
    unittest.main()
