import pathlib
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.routes import arcmap
from gateway_py3.routes import handle_get
from gateway_py3.workflow_store import WorkflowStore


class WorkbenchStateTests(unittest.TestCase):
    def test_workbench_state_returns_first_screen_payload_without_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            store.set_state("arcmap_context", {"layers": []})
            store.create_draft("刷新地图", "ctx", _workflow("view.refresh_view"), [{"token": "trace"}])
            state = SimpleNamespace(catalog=OperationCatalog(), store=store)

            with patch("gateway_py3.routes.arcmap.bridges", return_value=[{"pid": 1, "port": 8766, "hwnd": 2}]):
                result = handle_get(state, "/api/workbench-state", "0.21.1")

        self.assertEqual(result["health"]["app_version"], "0.21.1")
        self.assertIn("config", result)
        self.assertIn("context", result)
        self.assertEqual(result["arcmap"]["bridges"][0]["hwnd"], 2)
        self.assertNotIn("agent_trace", result["workflows"][0])

    def test_workflow_list_filters_and_detail_lazy_load(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            first = store.create_draft("半代理", "ctx", _workflow("view.refresh_view"), [{"a": 1}], mode="semi_agent")
            time.sleep(0.01)
            second = store.create_draft("全代理", "ctx", _workflow("view.refresh_view"), [{"b": 2}], mode="full_agent")
            state = SimpleNamespace(catalog=OperationCatalog(), store=store)

            listed = handle_get(state, "/api/workflows", "0.21.1", {
                "mode": ["full_agent"],
                "limit": ["10"],
                "include_trace": ["false"],
            })
            detail = handle_get(state, "/workflows/%s" % second["id"], "0.21.1")
            since = handle_get(state, "/api/workflows", "0.21.1", {"since": [str(first["updated_at"])], "limit": ["10"]})

        self.assertEqual([item["id"] for item in listed["workflows"]], [second["id"]])
        self.assertNotIn("agent_trace", listed["workflows"][0])
        self.assertEqual(detail["workflow"]["agent_trace"], [{"b": 2}])
        self.assertEqual([item["id"] for item in since["workflows"]], [second["id"]])

    def test_bridge_scan_uses_short_ttl_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            store.set_state("arcmap_bridge:1", {"pid": 1, "port": 8771})
            state = SimpleNamespace(store=store, bridge_cache={"expires_at": 0.0, "bridges": []})
            calls = []

            def fake_health(port=None):
                calls.append(port)
                return {"ok": True, "pid": 1, "port": 8771, "summary": {}}

            with patch("gateway_py3.arcmap_bridge_client.ensure_running", return_value=None):
                with patch("gateway_py3.arcmap_bridge_client.health", side_effect=fake_health):
                    first = arcmap.bridges(state, port_checker=lambda port: port == 8771)
                    second = arcmap.bridges(state, port_checker=lambda port: port == 8771)

        self.assertEqual(first, second)
        self.assertEqual(calls, [8771])


def _workflow(operation):
    return {
        "action": "execute",
        "summary": "执行任务。",
        "steps": [
            {"id": "step_1", "operation": operation, "arguments": {}, "reason": "执行任务。"}
        ],
    }


if __name__ == "__main__":
    unittest.main()
