"""The one authoritative workflow-reference contract for models and clients."""

from __future__ import annotations

from typing import Dict, Any

WORKFLOW_PROTOCOL_VERSION = 1


def workflow_protocol() -> Dict[str, Any]:
    return {
        "version": WORKFLOW_PROTOCOL_VERSION,
        "layer_references": {
            "map_layer": "Use layer_ref or an exact current-map layer name.",
            "prior_output": (
                "Use from_step:<step_id> only for an earlier step that produced a loaded "
                "feature_class or raster layer."
            ),
            "file_output": "File outputs are never map layers and cannot be referenced with from_step.",
        },
        "output_name": "Use an extension-free basename with no path.",
        "where": {
            "values_operators": ["in", "between"],
            "value_operators": ["eq", "ne", "gt", "gte", "lt", "lte", "like"],
            "rule": "in and between use values; every other single-value operator uses value.",
        },
    }
