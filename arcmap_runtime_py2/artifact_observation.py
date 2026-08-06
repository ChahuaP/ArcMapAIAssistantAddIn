# -*- coding: utf-8 -*-
"""Measured ArcMap artifacts and CapabilityContract postconditions."""
from __future__ import absolute_import

import csv

import arcpy

try:
    import arcmap_desktop_selection
    import capability_contract_protocol
    import execution_session
    import map_state_observation
    import path_utils
except ImportError:
    from . import arcmap_desktop_selection
    from . import capability_contract_protocol
    from . import execution_session
    from . import map_state_observation
    from . import path_utils

try:
    unicode
except NameError:
    unicode = str


class ArtifactVerificationError(Exception):
    def __init__(self, contract_path, expected, actual):
        self.contract_path = contract_path
        self.expected = expected
        self.actual = actual
        Exception.__init__(self, u"后置条件不满足：%s" % contract_path)


def observe_and_verify(operation, arguments, result, context, step_outputs, publication_state,
                       input_snapshot=None, verifier_proof=None):
    """Return JSON-safe measured state; raise on every measurable mismatch."""
    contract = operation.get("capability_contract") or {}
    outputs = contract.get("outputs") or {}
    if not contract.get("postconditions"):
        return {"path": None, "kind": "none", "geometry": "not_applicable", "fields": [],
                "feature_count": None, "spatial_reference": None, "selection_count": None,
                "map_publication": publication_state or "none",
                "input_snapshot": input_snapshot or {"inputs": {}},
                "contract": {"verdict": "not_applicable", "checks": []}}
    postconditions = contract.get("postconditions") or []
    map_conditions = [
        condition for condition in postconditions
        if map_state_observation.supports(condition.get("kind"))
    ]
    if outputs.get("kind") == "map_state" and not map_conditions:
        actual_kind = postconditions[0].get("kind") if postconditions else None
        raise ArtifactVerificationError(
            "capability_contract.postconditions[0].kind",
            "supported map-state postcondition",
            actual_kind,
        )
    if map_conditions:
        if len(postconditions) != 1 or len(map_conditions) != 1:
            raise ArtifactVerificationError(
                "capability_contract.postconditions", "one map-state postcondition", postconditions,
            )
        observation = map_state_observation.observe(
            operation, map_conditions[0], arguments, result, context, step_outputs, input_snapshot,
        )
        semantic_check = observation["map_state_check"]
        if semantic_check["verdict"] == "failed":
            raise ArtifactVerificationError(
                "capability_contract.postconditions[0].kind",
                semantic_check["expected"],
                semantic_check["actual"],
            )
    else:
        target = _target(operation, arguments, result, context, step_outputs)
        observation = _observe(target, outputs.get("kind", "none"))
    observation["map_publication"] = publication_state or "none"
    observation["input_snapshot"] = input_snapshot or {"inputs": {}}
    observation["contract"] = {"verdict": "passed", "checks": []}
    for index, postcondition in enumerate(postconditions):
        expectation = postcondition.get("expectation") or {}
        for name in ("kind", "geometry", "fields", "spatial_reference", "cardinality", "selection_state", "map_publication"):
            expected = _expected(outputs, expectation.get(name), name)
            if name == "cardinality":
                try:
                    expected = capability_contract_protocol.resolve_output_cardinality(
                        expected, arguments, contract.get("parameters_schema") or {},
                    )
                except ValueError as exc:
                    raise ArtifactVerificationError(
                        "capability_contract.outputs.cardinality", expected, unicode(exc),
                    )
            check = _check(name, expected, observation, arguments, verifier_proof)
            observation["contract"]["checks"].append(check)
            if check["verdict"] == "failed":
                observation["contract"]["verdict"] = "failed"
                raise ArtifactVerificationError("capability_contract.postconditions[%d].expectation.%s" % (index, name), expected, check["actual"])
    return observation


def _target(operation, arguments, result, context, step_outputs):
    if isinstance(result, dict):
        if result.get("output"):
            return result["output"]
        if result.get("outputs"):
            return result["outputs"]
    fields = ((operation.get("capability_contract") or {}).get("outputs") or {}).get("fields") or {}
    field_target = fields.get("target")
    if field_target and field_target != "not_applicable":
        return _find_layer(context, arguments.get(field_target), step_outputs)
    for field_source in fields.get("sources") or []:
        value = arguments.get(field_source)
        if isinstance(value, list):
            value = value[0] if value else None
        if value:
            return _find_layer(context, value, step_outputs)
    for key in ("layer", "target_layer", "input_layer"):
        if arguments.get(key):
            return _find_layer(context, arguments[key], step_outputs)
    return None


def _find_layer(context, value, step_outputs):
    if value is None or not isinstance(value, (str, unicode)):
        return value
    try:
        from operations import common
    except ImportError:
        from .operations import common
    return common.find_layer(context, value, step_outputs or {})


def _observe(target, expected_kind):
    observation = {"path": None, "kind": "none", "geometry": "not_applicable", "fields": [],
                   "feature_count": None, "spatial_reference": None, "selection_count": None}
    if target is None:
        return observation
    if isinstance(target, list):
        paths = [_path(item) for item in target]
        observation.update({"path": paths, "kind": "file_collection", "exists": bool(paths) and all(path and path_utils.isfile(path) for path in paths), "extensions": [path_utils.splitext(path)[1].lower() for path in paths]})
        return observation
    observation["path"] = _path(target)
    if expected_kind in ("file", "file_collection"):
        extension = path_utils.splitext(observation["path"])[1].lower() if observation["path"] else ""
        observation.update({"kind": expected_kind, "exists": bool(observation["path"] and path_utils.isfile(observation["path"])), "extension": extension})
        if expected_kind == "file" and observation["exists"] and extension == ".csv":
            observation["fields"] = _csv_header(observation["path"])
        return observation
    dataset_target = _dataset_target(target)
    # In-place edits have output kind none but still require full dataset observation.
    if not _exists(dataset_target):
        observation.update({"kind": "missing", "exists": False})
        return observation
    description = arcpy.Describe(dataset_target)
    observed_kind = _kind(getattr(description, "dataType", ""), expected_kind)
    observed_fields = list(arcpy.ListFields(dataset_target))
    observation.update({"kind": expected_kind if expected_kind == "none" else observed_kind,
                        "dataset_kind": observed_kind, "exists": True,
                        "geometry": getattr(description, "shapeType", "not_applicable") or "not_applicable",
                        "fields": [field.name for field in observed_fields],
                        "field_types": dict((field.name, getattr(field, "type", None)) for field in observed_fields),
                        "spatial_reference": _spatial_reference(getattr(description, "spatialReference", None))})
    if observation["kind"] in ("feature_class", "table") or expected_kind == "none":
        try:
            observation["feature_count"] = int(arcpy.GetCount_management(dataset_target).getOutput(0))
        except (RuntimeError, AttributeError, TypeError, ValueError):
            pass
    # Selection is ArcMap layer state, never dataset state.  Querying FIDSet on
    # a physical output path is both meaningless and unsafe in ArcMap 10.2;
    # empty shapefiles can crash inside AfCore.dll instead of raising Python.
    if bool(getattr(target, "isFeatureLayer", False)):
        try:
            observation["selection_count"] = len(arcmap_desktop_selection.capture_oids(target))
        except (RuntimeError, AttributeError, TypeError):
            pass
    return observation


def _dataset_target(target):
    """Use the physical dataset for data-plane reads of session layers.

    ArcMap 10.2 invalidates a MakeFeatureLayer result when that transient
    layer itself is passed to another geoprocessing tool such as GetCount.
    Selection state still belongs to the layer handle, while schema, count,
    geometry and spatial reference belong to its registered dataset.
    """
    session = execution_session.current()
    if session is None:
        return target
    path = session.registered_path_for_detached_layer(target)
    return path if path is not None else target


def _check(name, expected, observation, arguments, verifier_proof):
    actual = observation.get(name)
    check = {"name": name, "expected": expected, "actual": actual, "verdict": "passed"}
    before = (observation.get("input_snapshot") or {}).get("inputs") or {}
    if expected in (None, "not_applicable", "none"):
        return check
    if name == "kind":
        check["actual"] = {"kind": actual, "exists": observation.get("exists")}
        check["verdict"] = "passed" if actual == expected and (expected not in ("file", "file_collection") or observation.get("exists")) else "failed"
    elif name in ("geometry", "spatial_reference"):
        rule = expected.get("rule") if isinstance(expected, dict) else None
        if rule == "inherit":
            source = _input(before, expected.get("value") or expected.get("input"))
            key = "geometry" if name == "geometry" else "spatial_reference"
            check["actual"] = {"output": actual, "input": source.get(key) if source else None}
            check["verdict"] = "passed" if source and actual == source.get(key) else "failed"
        elif name == "geometry" and rule == "lowest_dimension":
            sources = _inputs(before, expected.get("value"))
            input_geometries = [source.get("geometry") for source in sources]
            try:
                resolved = capability_contract_protocol.resolve_lowest_dimension_geometry(input_geometries)
            except ValueError as exc:
                check["actual"] = {
                    "output": actual,
                    "inputs": input_geometries,
                    "error": unicode(exc),
                }
                check["verdict"] = "failed"
            else:
                check["actual"] = {
                    "output": actual,
                    "inputs": input_geometries,
                    "resolved": resolved,
                }
                check["verdict"] = (
                    "passed" if unicode(actual).strip().lower() == resolved else "failed"
                )
        elif rule not in (None, "not_applicable") and expected.get("value"):
            check["verdict"] = "passed" if unicode(actual).lower() == unicode(expected["value"]).lower() else "failed"
    elif name == "fields":
        effect = expected.get("effect") if isinstance(expected, dict) else ""
        target = _input(before, expected.get("target")) if isinstance(expected, dict) else None
        targets = []
        if isinstance(expected, dict):
            for source in expected.get("sources") or [expected.get("target")]:
                targets.extend(_inputs(before, source))
        required = list(expected.get("static_fields") or []) if isinstance(expected, dict) else []
        parameter = arguments.get(expected.get("parameter_field")) if isinstance(expected, dict) else None
        if effect == "add_parameter_field": required.append(parameter)
        if effect == "delete_parameter_field":
            check["actual"] = {"before": target.get("fields") if target else None, "after": observation.get("fields")}
            check["verdict"] = "passed" if target and parameter in target.get("fields", []) and parameter not in observation.get("fields", []) else "failed"
        elif effect in ("inherit_input", "inherit_tabular_fields", "inherit_target_merge_join"):
            inherited = target.get("fields", []) if target else []
            if effect == "inherit_tabular_fields" and target:
                types = target.get("field_types") or {}
                inherited = [field for field in inherited if unicode(types.get(field) or "").lower() not in ("geometry", "raster", "blob")]
            required.extend(inherited)
            check["actual"] = {"required": required, "after": observation.get("fields")}
            check["verdict"] = "passed" if target and all(field in observation.get("fields", []) for field in required) else "failed"
        elif effect == "aggregate_by_parameter_fields":
            required.extend(arguments.get("dissolve_fields") or [])
            check["actual"] = {"required": required, "after": observation.get("fields")}
            check["verdict"] = "passed" if all(field in observation.get("fields", []) for field in required) else "failed"
        elif effect == "merge_inputs":
            for source in targets:
                for field in source.get("fields", []):
                    if field not in required:
                        required.append(field)
            check["actual"] = {"required": required, "after": observation.get("fields")}
            check["verdict"] = "passed" if targets and all(
                field in observation.get("fields", []) for field in required) else "failed"
        elif required:
            check["verdict"] = "passed" if all(field in observation.get("fields", []) for field in required) else "failed"
    elif name == "cardinality":
        if expected in ("one_per_input_feature", "one_per_target_feature"):
            parameter = "input_layer" if expected == "one_per_input_feature" else "target_layer"
            source = _input(before, parameter)
            check["actual"] = {"output": observation.get("feature_count"), parameter: source.get("feature_count") if source else None}
            check["verdict"] = "passed" if source and observation.get("feature_count") == source.get("feature_count") else "failed"
        elif expected == "selected_feature_count":
            source = _input(before, "layer") or _input(before, "target_layer")
            selected_count = source.get("selection_count") if source else None
            check["actual"] = {"output": observation.get("feature_count"), "selected_input": selected_count}
            check["verdict"] = "passed" if selected_count is not None and observation.get("feature_count") == selected_count else "failed"
        elif expected not in ("one", "in_place", "one_snapshot"):
            _symbolic_or_fail(check, verifier_proof, expected)
    elif name == "selection_state" and expected in ("applied", "cleared"):
        source = _input(before, "layer") or _input(before, "target_layer")
        check["actual"] = {"before": source.get("selection_count") if source else None, "after": observation.get("selection_count"), "selection_type": arguments.get("selection_type", "NEW_SELECTION")}
        if expected == "cleared":
            check["verdict"] = "passed" if observation.get("selection_count") == 0 else "failed"
        else:
            check["verdict"] = "passed" if source and observation.get("selection_count") is not None else "failed"
    elif name == "map_publication":
        if expected == "published" and actual == "scheduled":
            check["verdict"] = "scheduled"
        elif expected in ("published", "map_state_updated"):
            check["verdict"] = "passed" if actual == expected else "failed"
    return check


def _symbolic_or_fail(check, proof, expected):
    if (isinstance(proof, dict)
            and proof.get("proof_id")
            and proof.get("proof_kind") == "validated_capability_output"
            and proof.get("contract_path") == "outputs.cardinality"
            and proof.get("expected") == expected):
        check["verdict"] = "symbolically_verified"
        check["proof"] = proof
    else:
        check["verdict"] = "failed"
        check["actual"] = {"unverifiable": True, "required_plan_artifact_proof": expected}


def _input(inputs, name):
    values = _inputs(inputs, name)
    return values[0] if values else None


def _inputs(inputs, name):
    if not name:
        return []
    value = inputs.get(name)
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return [value] if isinstance(value, dict) else []


def _expected(outputs, reference, name):
    return outputs.get(name) if isinstance(reference, dict) and reference.get("ref") == "outputs." + name else reference


def _exists(value):
    exists = getattr(arcpy, "Exists", None)
    return bool(exists(value)) if exists is not None else True


def _path(value):
    try:
        return path_utils.to_unicode_path(getattr(value, "dataSource", value))
    except (TypeError, ValueError):
        return None


def _csv_header(path):
    with path_utils.open_binary(path, "rb") as stream:
        rows = csv.reader(stream)
        for row in rows:
            return [_decode_csv_name(value) for value in row]
    return []


def _decode_csv_name(value):
    if isinstance(value, unicode):
        return value.lstrip(u"\ufeff")
    return value.decode("utf-8-sig")


def _kind(data_type, declared_kind):
    value = unicode(data_type).lower()
    if "raster" in value:
        return "raster"
    if "table" in value:
        return "table"
    if "feature" in value or declared_kind == "feature_class":
        return "feature_class"
    return declared_kind


def _spatial_reference(value):
    if value is None:
        return None
    return getattr(value, "name", None) or getattr(value, "factoryCode", None)
