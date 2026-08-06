"""The one authoritative workflow-reference contract for models and clients."""

from __future__ import annotations

from typing import Dict, Any

WORKFLOW_PROTOCOL_VERSION = 3


def workflow_protocol() -> Dict[str, Any]:
    return {
        "version": WORKFLOW_PROTOCOL_VERSION,
        "layer_references": {
            "map_layer": "Use layer_ref or an exact current-map layer name.",
            "prior_output": (
                "Use from_step:<step_id> only for an earlier step that produced a run-scoped "
                "feature_class or raster output. It need not be visible in the map yet."
            ),
            "added_layer": (
                "Use from_step:<step_id> for a layer.add_layer step when a later step needs "
                "that newly added live map layer."
            ),
            "file_output": "File outputs are never map layers and cannot be referenced with from_step.",
            "map_structure": (
                "Run-scoped outputs are detached during computation. layer.remove_layer and "
                "layer.move_layer require live map layers, and layer.clear_layers must precede "
                "all run-scoped outputs."
            ),
        },
        "output_name": "Use an extension-free basename with no path.",
        "where": {
            "values_operators": ["in", "between"],
            "value_operators": ["eq", "ne", "gt", "gte", "lt", "lte", "like"],
            "field_comparison_operators": ["eq", "ne", "gt", "gte", "lt", "lte"],
            "rule": (
                "in and between use values; like uses value; comparison operators use exactly "
                "one of value (literal) or value_field (a field on the same layer)."
            ),
        },
    }
