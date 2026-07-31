from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .paths import CATALOG_ROOT
from .tool_builder import enabled_operation_specs
from .output_policy import canonical_output_policy


class CatalogError(Exception):
    pass


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


class OperationCatalog:
    def __init__(self, root: Path | None = None):
        self.root = root or CATALOG_ROOT
        self.catalog = _load_json(self.root / "catalog.json")
        self.operations: Dict[str, Dict[str, Any]] = {}
        self.packs: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        for rel_path in self.catalog["packs"]:
            pack = _load_json(self.root / rel_path)
            self.packs.append(pack)
            for operation in pack["operations"]:
                self._register_operation(operation)
        for operation in enabled_operation_specs():
            self._register_operation(operation)

    def _register_operation(self, operation: Dict[str, Any]) -> None:
        operation_id = operation["id"]
        if operation_id in self.operations:
            raise CatalogError(f"Duplicate operation id: {operation_id}")
        self.operations[operation_id] = operation

    def get(self, operation_id: str) -> Dict[str, Any]:
        if operation_id not in self.operations:
            raise CatalogError(f"Unknown operation: {operation_id}")
        return self.operations[operation_id]

    def all_operations(self) -> Iterable[Dict[str, Any]]:
        return self.operations.values()

    def model_card(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        schema = operation["parameters_schema"]
        return {
            "id": operation["id"],
            "summary": operation["summary"],
            "model_card": operation["model_card"],
            "parameters_schema": schema,
            "context_requirements": operation.get("context_requirements", {}),
            "side_effects": operation["side_effects"],
            "output_policy": canonical_output_policy(
                operation.get("output_policy"),
                operation.get("side_effects", ""),
            ),
            "examples": operation.get("examples", [])[:2],
        }
