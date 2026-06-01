from __future__ import annotations

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.event_bus import EventBus
from gateway_py3.planner import AgenticPlanner
from gateway_py3.validators import validate_catalog
from gateway_py3.workflow_store import WorkflowStore


class GatewayState:
    def __init__(self):
        self.events = EventBus()
        self.bridge_cache = {"expires_at": 0.0, "bridges": []}
        self.catalog = OperationCatalog()
        validate_catalog(self.catalog)
        self.store = WorkflowStore()
        self.store.clear_state("arcmap_context")
        self.planner = AgenticPlanner(catalog=self.catalog, store=self.store, event_bus=self.events)

    def reload_catalog(self):
        self.catalog = OperationCatalog()
        validate_catalog(self.catalog)
        self.planner = AgenticPlanner(catalog=self.catalog, store=self.store, event_bus=self.events)
