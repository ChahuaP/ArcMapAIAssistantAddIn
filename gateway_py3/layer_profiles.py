from __future__ import annotations

from typing import Any, Dict, List


MAX_FIELD_VALUE_SAMPLES = 20


def matching_layers_exact(value: str, layers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    raw = value[1:] if value.startswith("@") else value
    matches = []
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        if raw in (
            layer.get("layer_ref"),
            layer.get("name"),
            layer.get("longName"),
            layer.get("dataSource"),
        ):
            matches.append(layer)
    return matches


def layer_value_profile(layer: Dict[str, Any], only_fields_with_samples: bool = False) -> Dict[str, Any]:
    return {
        "layer_ref": layer.get("layer_ref"),
        "name": layer.get("name"),
        "longName": layer.get("longName"),
        "geometry_type": layer.get("geometry_type"),
        "selected_count": layer.get("selected_count"),
        "fields": profile_fields(layer.get("fields", []) or [], only_fields_with_samples=only_fields_with_samples),
        "value_profile_truncated": bool(layer.get("value_profile_truncated")),
    }


def profile_fields(fields: List[Dict[str, Any]], only_fields_with_samples: bool = False) -> List[Dict[str, Any]]:
    result = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        value_samples = field.get("value_samples", [])
        if not isinstance(value_samples, list):
            value_samples = []
        value_samples = value_samples[:MAX_FIELD_VALUE_SAMPLES]
        if only_fields_with_samples and not value_samples:
            continue
        result.append({
            "name": field.get("name"),
            "type": field.get("type"),
            "value_samples": value_samples,
        })
    return result
