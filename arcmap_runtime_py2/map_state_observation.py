# -*- coding: utf-8 -*-
"""Independent postcondition observations for ArcMap map-state capabilities."""
from __future__ import absolute_import

import arcpy

try:
    import arcmap_desktop_selection
    import context_reader
    from operations import common
except ImportError:
    from . import arcmap_desktop_selection
    from . import context_reader
    from .operations import common


SUPPORTED_POSTCONDITIONS = frozenset((
    "active_view_matches_request",
    "all_layers_removed",
    "extent_matches_layer",
    "extent_matches_selection",
    "field_inventory_matches_live_layer",
    "layer_description_matches_live_map",
    "layer_added_from_path",
    "layer_inventory_matches_live_map",
    "layout_text_matches_request",
    "layout_elements_match_live_layout",
    "layer_position_matches_request",
    "layer_removed_from_map",
    "layer_visibility_matches_request",
    "map_extent_matches_live_view",
    "selection_count_matches_live_layer",
    "selection_count_matches_result",
    "spatial_reference_matches_live_frame",
))

LAYOUT_ELEMENT_TYPES = (
    "TEXT_ELEMENT",
    "LEGEND_ELEMENT",
    "MAPSURROUND_ELEMENT",
    "PICTURE_ELEMENT",
    "DATAFRAME_ELEMENT",
)

# ArcMap preserves the viewport aspect ratio and may add a small display-edge
# margin after assigning DataFrame.extent.  A fit remains tight when at least
# one non-degenerate axis stays within one percent of the target span.
MAX_VIEWPORT_FIT_PADDING_FRACTION = 0.01


def supports(postcondition_kind):
    return postcondition_kind in SUPPORTED_POSTCONDITIONS


def capture_before(operation, arguments, context, step_outputs):
    """Capture JSON-safe state required to verify relative map mutations."""
    kinds = [
        condition.get("kind")
        for condition in ((operation.get("capability_contract") or {}).get("postconditions") or [])
    ]
    map_kinds = set(kinds).intersection((
        "all_layers_removed",
        "layer_added_from_path",
        "layer_position_matches_request",
        "layer_removed_from_map",
    ))
    if not map_kinds:
        return None
    if len(map_kinds) != 1:
        raise ValueError("one map membership postcondition is required")
    kind = list(map_kinds)[0]
    layers = _current_layers()
    snapshot = {
        "kind": kind,
        "layer_count": len(layers),
        "layer_identities": [_layer_identity(layer) for layer in layers],
    }
    if kind == "layer_added_from_path":
        snapshot["path"] = common._text(arguments["path"])
        return snapshot
    if kind == "all_layers_removed":
        return snapshot
    target = common.find_layer(context, arguments["layer"], step_outputs)
    snapshot["target_index"] = _layer_index(layers, target)
    snapshot["target_identity"] = _layer_identity(target)
    if kind == "layer_removed_from_map":
        return snapshot
    position = arguments["position"].upper()
    snapshot["position"] = position
    if position in ("BEFORE", "AFTER"):
        reference = common.find_layer(context, arguments.get("reference_layer"), step_outputs)
        _layer_index(layers, reference)
        snapshot["reference_identity"] = _layer_identity(reference)
    return snapshot


def observe(operation, postcondition, arguments, result, context, step_outputs, input_snapshot=None):
    kind = postcondition.get("kind")
    if kind == "active_view_matches_request":
        return _observe_active_view(arguments)
    if kind == "all_layers_removed":
        return _observe_all_layers_removed(result, input_snapshot)
    if kind == "extent_matches_layer":
        return _observe_extent_matches_layer(arguments, context, step_outputs)
    if kind == "extent_matches_selection":
        return _observe_extent_matches_selection(arguments, context, step_outputs)
    if kind == "field_inventory_matches_live_layer":
        return _observe_field_inventory(arguments, result, context, step_outputs)
    if kind == "layer_description_matches_live_map":
        return _observe_layer_description(arguments, result, context, step_outputs)
    if kind == "layer_added_from_path":
        return _observe_layer_added(arguments, result, input_snapshot)
    if kind == "layer_inventory_matches_live_map":
        return _observe_layer_inventory(result)
    if kind == "layout_text_matches_request":
        return _observe_layout_text(arguments)
    if kind == "layout_elements_match_live_layout":
        return _observe_layout_elements(arguments, result)
    if kind == "layer_position_matches_request":
        return _observe_layer_position(arguments, context, step_outputs, input_snapshot)
    if kind == "layer_removed_from_map":
        return _observe_layer_removed(result, input_snapshot)
    if kind == "layer_visibility_matches_request":
        return _observe_layer_visibility(arguments, context, step_outputs)
    if kind == "map_extent_matches_live_view":
        return _observe_map_extent(result)
    if kind == "selection_count_matches_live_layer":
        return _observe_selection_count(arguments, result, context, step_outputs)
    if kind == "selection_count_matches_result":
        return _observe_selection_result(arguments, result, context, step_outputs)
    if kind == "spatial_reference_matches_live_frame":
        return _observe_spatial_reference(result)
    raise ValueError("unsupported map-state postcondition: %s" % kind)


def _observe_extent_matches_layer(arguments, context, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    mxd = common.current_mxd()
    data_frame = common.active_data_frame(mxd)
    actual = _extent(getattr(data_frame, "extent", None))
    expected = _extent(layer.getExtent())
    matched = _extent_is_fitted(actual, expected)
    return _base_observation(
        "extent_matches_layer",
        matched,
        expected,
        actual,
    )


def _observe_active_view(arguments):
    mxd = common.current_mxd()
    expected_mode = arguments["view_mode"]
    if expected_mode == "PAGE_LAYOUT":
        expected = "PAGE_LAYOUT"
    elif expected_mode == "DATA_VIEW":
        expected = common.active_data_frame(mxd).name
    else:
        raise ValueError("unsupported active view mode: %s" % expected_mode)
    actual = getattr(mxd, "activeView", None)
    return _base_observation(
        "active_view_matches_request",
        actual == expected,
        expected,
        actual,
    )


def _observe_extent_matches_selection(arguments, context, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    mxd = common.current_mxd()
    data_frame = common.active_data_frame(mxd)
    actual = _extent(getattr(data_frame, "extent", None))
    expected = _extent(layer.getSelectedExtent())
    return _base_observation(
        "extent_matches_selection",
        _extent_is_fitted(actual, expected),
        expected,
        actual,
    )


def _observe_layer_visibility(arguments, context, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    expected = bool(arguments["visible"])
    actual = bool(getattr(layer, "visible", not expected))
    return _base_observation(
        "layer_visibility_matches_request",
        actual == expected,
        expected,
        actual,
    )


def _observe_layer_inventory(result):
    live_layers = [
        {
            "layer_ref": layer.get("layer_ref"),
            "name": layer.get("name"),
            "visible": bool(layer.get("visible")),
        }
        for layer in context_reader.read_context().get("layers", [])
    ]
    actual = result.get("layers") if isinstance(result, dict) else None
    return _base_observation(
        "layer_inventory_matches_live_map",
        actual == live_layers,
        live_layers,
        actual,
    )


def _observe_layer_description(arguments, result, context, step_outputs):
    expected = _live_layer_info(arguments, context, step_outputs)
    actual = _json_snapshot(result)
    return _base_observation(
        "layer_description_matches_live_map",
        actual == expected,
        expected,
        actual,
    )


def _observe_field_inventory(arguments, result, context, step_outputs):
    layer_info = _live_layer_info(arguments, context, step_outputs)
    expected = {"layer": layer_info.get("name"), "fields": layer_info.get("fields", [])}
    actual = _json_snapshot(result)
    return _base_observation(
        "field_inventory_matches_live_layer",
        actual == expected,
        expected,
        actual,
    )


def _observe_selection_count(arguments, result, context, step_outputs):
    layer_info = _live_layer_info(arguments, context, step_outputs)
    expected = {
        "layer": layer_info.get("name"),
        "selected_count": layer_info.get("selected_count", 0),
    }
    actual = _json_snapshot(result)
    return _base_observation(
        "selection_count_matches_live_layer",
        actual == expected,
        expected,
        actual,
    )


def _observe_selection_result(arguments, result, context, step_outputs):
    parameter = "layer" if arguments.get("layer") else "target_layer"
    layer = common.find_layer(context, arguments[parameter], step_outputs)
    live_count = len(arcmap_desktop_selection.capture_oids(layer))
    reported_count = result.get("selected_count") if isinstance(result, dict) else None
    cleared = bool(result.get("cleared")) if isinstance(result, dict) else False
    matched = reported_count == live_count and (not cleared or live_count == 0)
    observation = _base_observation(
        "selection_count_matches_result",
        matched,
        {"selected_count": live_count, "cleared_count": 0 if cleared else "not_applicable"},
        {"selected_count": reported_count, "cleared": cleared},
    )
    observation["selection_count"] = live_count
    return observation


def _live_layer_info(arguments, context, step_outputs):
    layer = common.find_layer(context, arguments["layer"], step_outputs)
    live_layers = context_reader.read_context().get("layers", [])
    matches = [
        item for item in live_layers
        if item.get("dataSource") == getattr(layer, "dataSource", None)
    ]
    if len(matches) != 1:
        matches = [
            item for item in live_layers
            if item.get("longName") == getattr(layer, "longName", getattr(layer, "name", None))
        ]
    if len(matches) != 1:
        raise ValueError("live layer metadata is missing or ambiguous")
    return matches[0]


def _observe_map_extent(result):
    expected = context_reader.read_context().get("extent")
    actual = result.get("extent") if isinstance(result, dict) else None
    return _base_observation(
        "map_extent_matches_live_view",
        actual == expected,
        expected,
        actual,
    )


def _observe_spatial_reference(result):
    expected = context_reader.read_context().get("spatial_reference")
    actual = result.get("spatial_reference") if isinstance(result, dict) else None
    return _base_observation(
        "spatial_reference_matches_live_frame",
        actual == expected,
        expected,
        actual,
    )


def _observe_layout_elements(arguments, result):
    mxd = common.current_mxd()
    requested_type = arguments.get("element_type") or "ALL"
    element_types = LAYOUT_ELEMENT_TYPES if requested_type == "ALL" else (requested_type,)
    expected = []
    for element_type in element_types:
        for element in arcpy.mapping.ListLayoutElements(mxd, element_type):
            expected.append(_layout_element_item(element, element_type))
    actual = result.get("elements") if isinstance(result, dict) else None
    matched = (
        actual == expected
        and isinstance(result, dict)
        and result.get("count") == len(expected)
    )
    return _base_observation(
        "layout_elements_match_live_layout",
        matched,
        {"elements": expected, "count": len(expected)},
        {"elements": actual, "count": result.get("count") if isinstance(result, dict) else None},
    )


def _layout_element_item(element, element_type):
    item = {
        "type": element_type,
        "name": common._text(getattr(element, "name", "")),
        "element_position_x": _number_or_none(getattr(element, "elementPositionX", None)),
        "element_position_y": _number_or_none(getattr(element, "elementPositionY", None)),
        "element_width": _number_or_none(getattr(element, "elementWidth", None)),
        "element_height": _number_or_none(getattr(element, "elementHeight", None)),
    }
    if element_type == "TEXT_ELEMENT":
        item["text"] = common._text(getattr(element, "text", ""))
    return item


def _number_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_snapshot(value):
    if isinstance(value, dict):
        return dict((key, _json_snapshot(item)) for key, item in value.items())
    if isinstance(value, list):
        return [_json_snapshot(item) for item in value]
    if isinstance(value, tuple):
        return [_json_snapshot(item) for item in value]
    return value


def _observe_layer_position(arguments, context, step_outputs, input_snapshot):
    before = (input_snapshot or {}).get("map_state_before")
    if not isinstance(before, dict) or before.get("kind") != "layer_position_matches_request":
        raise ValueError("layer position verification requires a pre-state snapshot")
    target = common.find_layer(context, arguments["layer"], step_outputs)
    layers = _current_layers()
    target_index = _layer_index(layers, target)
    position = arguments["position"].upper()
    reference_index = None
    if position in ("BEFORE", "AFTER"):
        reference = common.find_layer(context, arguments.get("reference_layer"), step_outputs)
        reference_index = _layer_index(layers, reference)

    same_members = (
        len(layers) == before.get("layer_count")
        and sorted(_identity_key(_layer_identity(layer)) for layer in layers)
        == sorted(_identity_key(identity) for identity in before.get("layer_identities", []))
    )
    if position == "TOP":
        requested_position = target_index == 0
        expected_index = 0
    elif position == "BOTTOM":
        expected_index = len(layers) - 1
        requested_position = target_index == expected_index
    elif position == "UP":
        expected_index = max(0, before["target_index"] - 1)
        requested_position = target_index == expected_index
    elif position == "DOWN":
        expected_index = min(before["layer_count"] - 1, before["target_index"] + 1)
        requested_position = target_index == expected_index
    elif position == "BEFORE":
        expected_index = reference_index - 1
        requested_position = target_index == expected_index
    elif position == "AFTER":
        expected_index = reference_index + 1
        requested_position = target_index == expected_index
    else:
        raise ValueError("unsupported layer move position: %s" % position)

    expected = {
        "position": position,
        "target_index": expected_index,
        "members_preserved": True,
    }
    actual = {
        "position": position,
        "target_index": target_index,
        "reference_index": reference_index,
        "members_preserved": same_members,
    }
    return _base_observation(
        "layer_position_matches_request",
        same_members and requested_position,
        expected,
        actual,
    )


def _observe_layer_added(arguments, result, input_snapshot):
    before = _required_map_snapshot(input_snapshot, "layer_added_from_path")
    layers = _current_layers()
    expected_path = common._normalize_path(common._text(arguments["path"]))
    matching = [
        layer for layer in layers
        if common._normalize_path(common._text(getattr(layer, "dataSource", ""))) == expected_path
    ]
    before_keys = sorted(_identity_key(identity) for identity in before["layer_identities"])
    after_keys = sorted(_identity_key(_layer_identity(layer)) for layer in layers)
    added_layer = matching[-1] if matching else None
    remaining = _without_one(after_keys, _identity_key(_layer_identity(added_layer))) if added_layer else None
    reported_path = result.get("layer_path") if isinstance(result, dict) else None
    reported_name = result.get("layer_name") if isinstance(result, dict) else None
    matched = (
        len(layers) == before["layer_count"] + 1
        and added_layer is not None
        and remaining == before_keys
        and common._normalize_path(common._text(reported_path or "")) == expected_path
        and reported_name == getattr(added_layer, "name", None)
    )
    return _base_observation(
        "layer_added_from_path",
        matched,
        {"path": common._text(arguments["path"]), "layer_count": before["layer_count"] + 1},
        {"path": reported_path, "layer_name": reported_name, "layer_count": len(layers)},
    )


def _observe_layer_removed(result, input_snapshot):
    before = _required_map_snapshot(input_snapshot, "layer_removed_from_map")
    layers = _current_layers()
    expected_keys = [_identity_key(identity) for identity in before["layer_identities"]]
    expected_keys = _without_one(expected_keys, _identity_key(before["target_identity"]))
    after_keys = sorted(_identity_key(_layer_identity(layer)) for layer in layers)
    removed_name = result.get("removed_layer") if isinstance(result, dict) else None
    expected_name = before["target_identity"].get("name")
    matched = (
        len(layers) == before["layer_count"] - 1
        and sorted(expected_keys or []) == after_keys
        and removed_name == expected_name
    )
    return _base_observation(
        "layer_removed_from_map",
        matched,
        {"removed_layer": expected_name, "layer_count": before["layer_count"] - 1},
        {"removed_layer": removed_name, "layer_count": len(layers)},
    )


def _observe_all_layers_removed(result, input_snapshot):
    before = _required_map_snapshot(input_snapshot, "all_layers_removed")
    layers = _current_layers()
    reported_count = result.get("removed_count") if isinstance(result, dict) else None
    matched = not layers and reported_count == before["layer_count"]
    return _base_observation(
        "all_layers_removed",
        matched,
        {"removed_count": before["layer_count"], "layer_count": 0},
        {"removed_count": reported_count, "layer_count": len(layers)},
    )


def _observe_layout_text(arguments):
    mxd = common.current_mxd()
    name = common._text(arguments["element_name"])
    match = arguments.get("match") or "EXACT"
    if match == "EXACT":
        elements = [
            element for element in arcpy.mapping.ListLayoutElements(mxd, "TEXT_ELEMENT")
            if common._text(getattr(element, "name", "")) == name
        ]
    elif match == "WILDCARD":
        elements = list(arcpy.mapping.ListLayoutElements(mxd, "TEXT_ELEMENT", name))
    else:
        raise ValueError("unsupported text element match mode: %s" % match)
    expected = common._text(arguments["text"])
    actual = common._text(getattr(elements[0], "text", "")) if len(elements) == 1 else None
    return _base_observation(
        "layout_text_matches_request",
        len(elements) == 1 and actual == expected,
        {"element_name": name, "text": expected},
        {"match_count": len(elements), "text": actual},
    )


def _base_observation(postcondition_kind, matched, expected, actual):
    return {
        "path": None,
        "kind": "map_state",
        "exists": True,
        "geometry": "not_applicable",
        "fields": [],
        "feature_count": None,
        "spatial_reference": None,
        "selection_count": None,
        "map_state_check": {
            "kind": postcondition_kind,
            "expected": expected,
            "actual": actual,
            "verdict": "passed" if matched else "failed",
        },
    }


def _current_layers():
    mxd = common.current_mxd()
    data_frame = common.active_data_frame(mxd)
    return list(arcpy.mapping.ListLayers(mxd, "", data_frame))


def _layer_index(layers, target):
    for index, layer in enumerate(layers):
        if layer is target or layer == target:
            return index
    raise ValueError("target layer is absent from the active data frame")


def _layer_identity(layer):
    return {
        "name": common._text(getattr(layer, "name", "")),
        "long_name": common._text(getattr(layer, "longName", getattr(layer, "name", ""))),
        "data_source": common._text(getattr(layer, "dataSource", "")),
    }


def _identity_key(identity):
    return (
        common._normalize_path(common._text(identity.get("data_source", ""))),
        common._text(identity.get("long_name", "")),
        common._text(identity.get("name", "")),
    )


def _required_map_snapshot(input_snapshot, kind):
    before = (input_snapshot or {}).get("map_state_before")
    if not isinstance(before, dict) or before.get("kind") != kind:
        raise ValueError("%s verification requires a pre-state snapshot" % kind)
    return before


def _without_one(values, target):
    remaining = list(values)
    try:
        remaining.remove(target)
    except ValueError:
        return None
    return sorted(remaining)


def _extent(value):
    if value is None:
        return None
    names = ("XMin", "YMin", "XMax", "YMax")
    try:
        return dict((name, float(getattr(value, name))) for name in names)
    except (AttributeError, TypeError, ValueError):
        return None


def _extent_is_fitted(actual, expected):
    if actual is None or expected is None:
        return False
    tolerance = _extent_tolerance(actual, expected)
    actual_center = (
        (actual["XMin"] + actual["XMax"]) / 2.0,
        (actual["YMin"] + actual["YMax"]) / 2.0,
    )
    expected_center = (
        (expected["XMin"] + expected["XMax"]) / 2.0,
        (expected["YMin"] + expected["YMax"]) / 2.0,
    )
    centered = (
        abs(actual_center[0] - expected_center[0]) <= tolerance
        and abs(actual_center[1] - expected_center[1]) <= tolerance
    )
    covers = (
        actual["XMin"] <= expected["XMin"] + tolerance
        and actual["YMin"] <= expected["YMin"] + tolerance
        and actual["XMax"] >= expected["XMax"] - tolerance
        and actual["YMax"] >= expected["YMax"] - tolerance
    )
    actual_spans = (
        actual["XMax"] - actual["XMin"],
        actual["YMax"] - actual["YMin"],
    )
    expected_spans = (
        expected["XMax"] - expected["XMin"],
        expected["YMax"] - expected["YMin"],
    )
    non_degenerate_axes = [
        (actual_span, expected_span)
        for actual_span, expected_span in zip(actual_spans, expected_spans)
        if expected_span > tolerance
    ]
    tightly_fitted = not non_degenerate_axes or any(
        actual_span <= (
            expected_span * (1.0 + MAX_VIEWPORT_FIT_PADDING_FRACTION)
            + tolerance
        )
        for actual_span, expected_span in non_degenerate_axes
    )
    return centered and covers and tightly_fitted


def _extent_tolerance(actual, expected):
    values = list(actual.values()) + list(expected.values())
    scale = max([abs(value) for value in values] + [1.0])
    return scale * 1e-9


def _close(left, right, tolerance):
    return abs(left - right) <= tolerance
