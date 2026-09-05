"""Validated operation catalog with model-facing projections.

The operation_catalog packs are the single source of capability truth; this
module validates them through CapabilityRegistry at startup (fail loud) and
projects tool-facing metadata. No capability may reach the model without
passing registry closure.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .capability_registry import CapabilityRegistry
from .catalog_loader import OperationCatalog

_EFFECT_TO_LEVEL = {
    "read_only": 1,
    "changes_map": 2,
    "writes_data": 3,
    "edits_data": 4,
}


class Catalog:
    """Validated operation catalog with model-facing projections."""

    def __init__(self) -> None:
        loader = OperationCatalog()
        operations = list(loader.all_operations())
        # Registry closure is the review gate: an operation that fails the
        # capability contract must never be exposed as a tool.
        self._registry = CapabilityRegistry(operations)
        self._cards: Dict[str, Dict[str, Any]] = {
            operation["id"]: operation for operation in operations
        }

    def operation_ids(self) -> List[str]:
        return sorted(self._cards)

    def get(self, operation_id: str) -> Dict[str, Any]:
        try:
            return self._cards[operation_id]
        except KeyError:
            raise KeyError("未知能力：%s" % operation_id)

    def summary(self) -> List[Dict[str, Any]]:
        result = []
        for operation_id in self.operation_ids():
            card = self._cards[operation_id]
            result.append({
                "operation": operation_id,
                "summary": card.get("summary", ""),
                "side_effect": self.side_effects(card),
                "side_effect_level": self.side_effect_level(card),
                "required_parameters": self.required_parameters(card),
            })
        return result

    def contract(self, operation_id: str) -> Dict[str, Any]:
        card = self.get(operation_id)
        contract = card.get("capability_contract", {}) or {}
        return {
            "operation": operation_id,
            "summary": card.get("summary", ""),
            "parameters_schema": card.get("parameters_schema", {}),
            "inputs": contract.get("inputs", []),
            "outputs": contract.get("outputs", {}),
            "side_effects": self.side_effects(card),
            "authorization": contract.get("authorization", {}),
            "postconditions": contract.get("postconditions", []),
        }

    @staticmethod
    def side_effects(card: Dict[str, Any]) -> str:
        effects = card.get("side_effects", "read_only")
        if isinstance(effects, list):
            effects = effects[0] if effects else "read_only"
        return str(effects)

    @classmethod
    def side_effect_level(cls, card: Dict[str, Any]) -> int:
        return _EFFECT_TO_LEVEL.get(cls.side_effects(card), 1)

    @staticmethod
    def required_parameters(card: Dict[str, Any]) -> List[str]:
        schema = card.get("parameters_schema", {})
        return list(schema.get("required", []))
