import pathlib
import tempfile
import unittest
from types import SimpleNamespace

from gateway_py3 import app
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.routes import handle_get
from gateway_py3.workflow_store import WorkflowStore

try:
    from gateway.planner_test_utils import context as _context
    from gateway.planner_test_utils import step as _step
except ImportError:
    from tests.gateway.planner_test_utils import context as _context
    from tests.gateway.planner_test_utils import step as _step


class ExternalAgentApiTests(unittest.TestCase):
    def setUp(self):
        self.old_state = app.STATE

    def tearDown(self):
        app.STATE = self.old_state

    def test_validate_prepares_workflow_without_planner(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            workflow = {
                "action": "execute",
                "summary": "将对 nanjing 生成缓冲区。",
                "steps": [
                    _step("step_1", "analysis.buffer", {
                        "input_layer": "nanjing",
                        "distance": "10 Meters",
                        "output_name": "nanjing_buffer"
                    }, "生成 10 米缓冲区")
                ]
            }

            result = app._external_agent_validate({
                "context": _context(is_saved=True),
                "workflow": workflow
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["workflow"]["steps"][0]["operation"], "analysis.buffer")
        self.assertTrue(result["context_hash"])

    def test_propose_stores_external_agent_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            workflow = {
                "action": "execute",
                "summary": "刷新当前地图。",
                "steps": [
                    _step("step_1", "view.refresh_view", {}, "刷新视图")
                ]
            }

            result = app._external_agent_propose({
                "command": "刷新地图",
                "context": _context(is_saved=True),
                "workflow": workflow,
                "source": "codex"
            })

            row = result["workflow"]
            stored = store.get(row["id"])

        self.assertTrue(result["ok"])
        self.assertEqual(row["mode"], "external_agent")
        self.assertEqual(stored["command"], "刷新地图")
        self.assertEqual(stored["agent_trace"][0]["source"], "codex")

    def test_validate_uses_synced_context_when_payload_omits_context(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            store.set_state("arcmap_context", _context(is_saved=True))
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            workflow = {
                "action": "execute",
                "summary": "刷新当前地图。",
                "steps": [
                    _step("step_1", "view.refresh_view", {}, "刷新视图")
                ]
            }

            result = app._external_agent_validate({"workflow": workflow})

        self.assertTrue(result["ok"])

    def test_validate_rejects_runtime_output_path(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            workflow = {
                "action": "execute",
                "summary": "将对 nanjing 生成缓冲区。",
                "steps": [
                    _step("step_1", "analysis.buffer", {
                        "input_layer": "nanjing",
                        "distance": "10 Meters",
                        "output_name": "nanjing_buffer",
                        "output_path": r"D:\Data\nanjing_buffer.shp"
                    }, "生成 10 米缓冲区")
                ]
            }

            with self.assertRaisesRegex(Exception, "output_path"):
                app._external_agent_validate({
                    "context": _context(is_saved=True),
                    "workflow": workflow
                })

    def test_public_capability_includes_schema_for_external_agents(self):
        operation = OperationCatalog().get("analysis.buffer")

        public = app._public_operation(operation)

        self.assertIn("parameters_schema", public)
        self.assertIn("output_policy", public)
        self.assertEqual(public["parameters_schema"]["properties"]["output_format"]["enum"], ["gdb", "shp"])

    def test_agent_diagnostics_reports_external_agent_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            store.set_state("arcmap_context", _context(is_saved=True))
            store.set_state("arcmap_permission", {"auto_execute": True, "allow_edits": False})
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )

            result = handle_get(app.STATE, "/agent/diagnostics", app.APP_VERSION)

        self.assertEqual(result["app_version"], app.APP_VERSION)
        self.assertIn("edit_geometry", result["categories"])
        self.assertTrue(result["context"]["synced"])
        self.assertIn("arcmap-sync", result["first_run_steps"])

    def test_validate_accepts_geometry_creation_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            workflow = {
                "action": "execute",
                "summary": "创建五角星面要素。",
                "steps": [
                    _step("step_1", "edit.create_star_polygon", {
                        "center_x": 118.78,
                        "center_y": 32.04,
                        "outer_radius": 0.01,
                        "outer_radius_unit": "degrees",
                        "point_count": 5,
                        "wkid": 4326,
                        "output_name": "star_feature"
                    }, "按中心点和半径创建五角星面。")
                ]
            }

            result = app._external_agent_validate({
                "context": _context(is_saved=True),
                "workflow": workflow
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["workflow"]["steps"][0]["operation"], "edit.create_star_polygon")

    def test_validate_accepts_batch_geometry_creation_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            workflow = {
                "action": "execute",
                "summary": "创建多个五角星到同一个图层。",
                "steps": [
                    _step("step_1", "edit.create_star_polygon", {
                        "features": [
                            {"center_x": 118.78, "center_y": 32.04, "name": "star_1"},
                            {"center_x": 118.79, "center_y": 32.05, "name": "star_2"}
                        ],
                        "outer_radius": 0.01,
                        "outer_radius_unit": "degrees",
                        "point_count": 5,
                        "wkid": 4326,
                        "output_name": "stars"
                    }, "把多个五角星写入同一个新面图层。")
                ]
            }

            result = app._external_agent_validate({
                "context": _context(is_saved=True),
                "workflow": workflow
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["workflow"]["steps"][0]["arguments"]["output_name"], "stars")

    def test_validate_accepts_append_geometry_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            workflow = {
                "action": "execute",
                "summary": "往已有面图层追加两个五角星。",
                "steps": [
                    _step("step_1", "edit.append_star_polygons", {
                        "target_layer": "nanjing",
                        "features": [
                            {"center_x": 118.78, "center_y": 32.04, "name": "star_1"},
                            {"center_x": 118.79, "center_y": 32.05, "name": "star_2"}
                        ],
                        "outer_radius": 0.01,
                        "outer_radius_unit": "degrees",
                        "point_count": 5
                    }, "用户明确要求写入已有图层。")
                ]
            }

            result = app._external_agent_validate({
                "context": _context(is_saved=True),
                "workflow": workflow
            })

        self.assertTrue(result["ok"])
        self.assertEqual(result["workflow"]["steps"][0]["arguments"]["target_layer"], "layer:nanjing")

    def test_arcmap_execute_workflow_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            workflow = {
                "action": "execute",
                "summary": "刷新当前地图。",
                "steps": [
                    _step("step_1", "view.refresh_view", {}, "刷新视图")
                ]
            }

            with self.assertRaisesRegex(ValueError, "确认"):
                app._arcmap_execute_workflow({
                    "command": "刷新地图",
                    "context": _context(is_saved=True),
                    "workflow": workflow
                })

    def test_arcmap_execute_workflow_runs_after_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            workflow = {
                "action": "execute",
                "summary": "刷新当前地图。",
                "steps": [
                    _step("step_1", "view.refresh_view", {}, "刷新视图")
                ]
            }
            with _BridgePatch() as bridge:
                result = app._arcmap_execute_workflow({
                    "command": "刷新地图",
                    "context": _context(is_saved=True),
                    "workflow": workflow,
                    "confirmed": True
                })

            row = result["workflow"]

        self.assertTrue(result["ok"])
        self.assertEqual(bridge.calls, [False])
        self.assertEqual(row["status"], "approved_for_arcmap")

    def test_arcmap_execute_workflow_rejects_edits_without_edit_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            workflow = {
                "action": "execute",
                "summary": "添加字段。",
                "steps": [
                    _step("step_1", "table.add_field", {
                        "layer": "nanjing",
                        "field_name": "TEST_FIELD",
                        "field_type": "TEXT"
                    }, "测试直接编辑权限")
                ]
            }

            with self.assertRaisesRegex(ValueError, "allow_edits"):
                app._arcmap_execute_workflow({
                    "command": "添加字段",
                    "context": _context(is_saved=True),
                    "workflow": workflow,
                    "confirmed": True
                })

    def test_arcmap_permission_allows_auto_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            app._arcmap_set_permission({"auto_execute": True})
            workflow = {
                "action": "execute",
                "summary": "刷新当前地图。",
                "steps": [
                    _step("step_1", "view.refresh_view", {}, "刷新视图")
                ]
            }
            with _BridgePatch() as bridge:
                result = app._arcmap_execute_workflow({
                    "command": "刷新地图",
                    "context": _context(is_saved=True),
                    "workflow": workflow
                })

        self.assertTrue(result["ok"])
        self.assertEqual(bridge.calls, [False])

    def test_full_agent_plan_syncs_and_executes_without_manual_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_DraftPlanner(store),
                reload_catalog=lambda: None
            )
            with _BridgePatch(sync_context=_context(is_saved=True)) as bridge:
                result = app._plan_request({
                    "command": "刷新地图",
                    "mode": "full_agent",
                    "project_id": "project-1"
                })

            row = result["workflow"]

        self.assertIn("execution", result)
        self.assertEqual(row["status"], "approved_for_arcmap")
        self.assertEqual(bridge.sync_calls, 1)
        self.assertEqual(bridge.calls, [True])
        self.assertEqual(app.STATE.planner.calls[0]["context"]["layers"][0]["name"], "nanjing")

    def test_arcmap_register_selects_active_bridge(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            app._arcmap_register({"pid": 123, "port": 8770})
            with _BridgePatch(sync_context=_context(is_saved=True), health_port=8770, health_pid=123) as bridge:
                result = app._arcmap_sync()

        self.assertTrue(result["ok"])
        self.assertEqual(result["bridge"]["port"], 8770)
        self.assertEqual(bridge.health_ports[0], 8770)

    def test_arcmap_bridges_lists_multiple_live_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            app._arcmap_register({"pid": 111, "port": 8771, "summary": {"mxd_path": "a.mxd"}})
            app._arcmap_register({"pid": 222, "port": 8772, "summary": {"mxd_path": "b.mxd"}})
            with _MultiBridgePatch({8771: 111, 8772: 222}):
                bridges = app._arcmap_bridges()

        ports = sorted(bridge["port"] for bridge in bridges)
        self.assertEqual(ports, [8771, 8772])

    def test_active_bridge_replaces_stale_arcmap_window_handle(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WorkflowStore(pathlib.Path(directory) / "workflows.sqlite")
            store.set_state("arcmap_active_bridge", {
                "pid": 1,
                "port": 8766,
                "hwnd": 18155486,
                "summary": {"title": "old"}
            })
            app.STATE = SimpleNamespace(
                catalog=OperationCatalog(),
                store=store,
                planner=_PlannerThatMustNotRun()
            )
            with _TargetBridgePatch(8766, 1, [{"hwnd": 222, "title": "current.mxd", "name": "ArcMap"}]):
                bridge = app._active_arcmap_bridge()

        self.assertEqual(bridge["hwnd"], 222)
        self.assertEqual(bridge["summary"]["title"], "current.mxd")


class _PlannerThatMustNotRun:
    def plan(self, *args, **kwargs):
        raise AssertionError("external agent endpoints must not call planner")


class _DraftPlanner:
    def __init__(self, store):
        self.store = store
        self.calls = []

    def plan(self, command, context, mode="semi_agent", project_id=None):
        self.calls.append({
            "command": command,
            "context": context,
            "mode": mode,
            "project_id": project_id
        })
        return self.store.create_draft(
            command,
            "ctx",
            {
                "action": "execute",
                "summary": "刷新当前地图。",
                "steps": [
                    _step("step_1", "view.refresh_view", {}, "刷新视图")
                ]
            },
            [],
            mode=mode,
            project_id=project_id or ""
        )


class _BridgePatch:
    def __init__(self, sync_context=None, health_port=8766, health_pid=1):
        self.sync_context = sync_context
        self.health_port = health_port
        self.health_pid = health_pid

    def __enter__(self):
        self.old_execute = app.arcmap_bridge_client.execute_approved
        self.old_sync_target = app.arcmap_bridge_client.sync_context_target
        self.old_health = app.arcmap_bridge_client.health
        self.old_ensure_running = app.arcmap_bridge_client.ensure_running
        self.old_is_local_port_open = app._is_local_port_open
        self.calls = []
        self.sync_calls = 0
        self.health_ports = []

        def fake_execute(allow_edits=False, port=None, hwnd=None):
            self.calls.append(allow_edits)
            return {"ok": True, "result": {"ok": True}}

        def fake_sync(port=None, hwnd=None):
            self.sync_calls += 1
            return {"ok": True, "context": self.sync_context or _context(is_saved=True)}

        def fake_health(port=None):
            self.health_ports.append(port)
            if port != self.health_port:
                raise app.arcmap_bridge_client.ArcMapBridgeError("missing")
            return {"ok": True, "pid": self.health_pid, "port": self.health_port}

        app.arcmap_bridge_client.execute_approved = fake_execute
        app.arcmap_bridge_client.sync_context_target = fake_sync
        app.arcmap_bridge_client.health = fake_health
        app.arcmap_bridge_client.ensure_running = lambda: True
        app._is_local_port_open = lambda port: True
        return self

    def __exit__(self, exc_type, exc, tb):
        app.arcmap_bridge_client.execute_approved = self.old_execute
        app.arcmap_bridge_client.sync_context_target = self.old_sync_target
        app.arcmap_bridge_client.health = self.old_health
        app.arcmap_bridge_client.ensure_running = self.old_ensure_running
        app._is_local_port_open = self.old_is_local_port_open


class _MultiBridgePatch:
    def __init__(self, ports):
        self.ports = ports

    def __enter__(self):
        self.old_health = app.arcmap_bridge_client.health
        self.old_ensure_running = app.arcmap_bridge_client.ensure_running
        self.old_is_local_port_open = app._is_local_port_open

        def fake_health(port=None):
            if port not in self.ports:
                raise app.arcmap_bridge_client.ArcMapBridgeError("missing")
            return {
                "ok": True,
                "pid": self.ports[port],
                "port": port,
                "summary": {"mxd_path": "%s.mxd" % self.ports[port]}
            }

        app.arcmap_bridge_client.health = fake_health
        app.arcmap_bridge_client.ensure_running = lambda: True
        app._is_local_port_open = lambda port: True
        return self

    def __exit__(self, exc_type, exc, tb):
        app.arcmap_bridge_client.health = self.old_health
        app.arcmap_bridge_client.ensure_running = self.old_ensure_running
        app._is_local_port_open = self.old_is_local_port_open


class _TargetBridgePatch:
    def __init__(self, port, pid, targets):
        self.port = port
        self.pid = pid
        self.targets = targets

    def __enter__(self):
        self.old_health = app.arcmap_bridge_client.health
        self.old_ensure_running = app.arcmap_bridge_client.ensure_running
        self.old_is_local_port_open = app._is_local_port_open

        def fake_health(port=None):
            if port != self.port:
                raise app.arcmap_bridge_client.ArcMapBridgeError("missing")
            return {
                "ok": True,
                "pid": self.pid,
                "port": self.port,
                "summary": {
                    "bridge": "external",
                    "arcmap_count": len(self.targets),
                    "targets": self.targets
                }
            }

        app.arcmap_bridge_client.health = fake_health
        app.arcmap_bridge_client.ensure_running = lambda: True
        app._is_local_port_open = lambda port: True
        return self

    def __exit__(self, exc_type, exc, tb):
        app.arcmap_bridge_client.health = self.old_health
        app.arcmap_bridge_client.ensure_running = self.old_ensure_running
        app._is_local_port_open = self.old_is_local_port_open


if __name__ == "__main__":
    unittest.main()
