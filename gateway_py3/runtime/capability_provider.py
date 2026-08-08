"""CapabilityProvider: provides capabilities from the operation catalog.

Extracted from app.py build_kernel.
"""
from __future__ import annotations

from typing import Any

from ..kernel.contracts import CapabilitySnapshot, CapabilitySpec


class CapabilityProvider:
    """Provides capabilities from the operation catalog."""

    def __init__(self, catalog: Any):
        self.catalog = catalog

    def snapshot(self, run_id: str) -> CapabilitySnapshot:
        cards = []
        for op in self.catalog.all_operations():
            cards.append(CapabilitySpec(
                operation_id=op["id"],
                business_semantic=op.get("summary", ""),
                parameters_schema=op.get("parameters_schema", {"type": "object"}),
                risk_level={"read_only": 1, "changes_map": 2, "writes_data": 3, "edits_data": 4}.get(op.get("side_effects", "read_only"), 1),
            ))
        return CapabilitySnapshot(
            operation_cards=tuple(cards),
            domain_rule_hash="rules-v1",
            registry_version="catalog-v1",
        )
