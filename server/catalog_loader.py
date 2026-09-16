from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List
from jsonschema import Draft202012Validator

from .paths import CATALOG_ROOT
from .capability_registry import CapabilityRegistry
from shared_runtime.operation_schema import OperationSchemaError, validate_parameter_schema
from shared_runtime.output_contract import OutputContractError, validate_output_policy


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
        self.capabilities = CapabilityRegistry(self.operations.values())

    def _load(self) -> None:
        for rel_path in self.catalog["packs"]:
            pack = _load_json(self.root / rel_path)
            self.packs.append(pack)
            for operation in pack["operations"]:
                self._register_operation(operation)

    def _register_operation(self, operation: Dict[str, Any]) -> None:
        operation_id = operation.get("id")
        if not isinstance(operation_id, str) or not operation_id:
            raise CatalogError("Operation id must be a non-empty string")
        if operation_id in self.operations:
            raise CatalogError(f"Duplicate operation id: {operation_id}")
        try:
            Draft202012Validator.check_schema(operation.get('parameters_schema'))
            validate_parameter_schema(operation.get("parameters_schema"))
            validate_output_policy(
                operation.get("output_policy"), operation.get("side_effects")
            )
        except (OperationSchemaError, OutputContractError) as exc:
            raise CatalogError(f"{operation_id} has an invalid executable contract: {exc}") from exc
        self.operations[operation_id] = operation

    def get(self, operation_id: str) -> Dict[str, Any]:
        if operation_id not in self.operations:
            raise CatalogError(f"Unknown operation: {operation_id}")
        return self.operations[operation_id]

    def all_operations(self) -> Iterable[Dict[str, Any]]:
        return self.operations.values()

    def planning_card(self, operation: Dict[str, Any]) -> Dict[str, Any]:
        return self.capabilities.planning_card(operation)
