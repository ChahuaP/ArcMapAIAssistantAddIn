"""Closed, evidence-bound task interpretation used by G2 and G3."""
from __future__ import annotations

from copy import deepcopy
import json
import ntpath
from typing import Any, Dict

from .artifact_identity import artifact_filename_is_mentioned, artifact_format_is_mentioned
from .llm_providers import StructuredOutputContract
from .plan_artifact import canonical_hash
from .semantic_domain import (
    bind_condition_field_types,
    parse_task_predicate,
)
from arcmap_runtime_py2.condition_protocol import (
    LEAF_CONDITION_OPERATORS, LOGICAL_CONDITION_OPERATORS, VALUE_CONDITION_OPERATORS,
    FIELD_COMPARISON_OPERATORS, normalize_condition_tree, canonical_operator,
    validate_condition_tree,
)


class TaskContractError(ValueError):
    pass


_ROOT = {"input_entities", "outputs", "requirements", "allowed_side_effects", "clarifications"}
_INPUT = {"entity_id", "role", "kind", "reference", "evidence"}
_OUTPUT = {
    "output_id", "kind", "name", "format", "geometry",
    "required_fields", "spatial_reference", "destination", "evidence",
}
_REQUIREMENT = {"requirement_id", "predicate", "evidence"}
_CLARIFICATION = {"clarification_id", "question", "evidence"}
_MODEL_INPUT = _INPUT - {"evidence", "kind"}
_MODEL_CLARIFICATION = _CLARIFICATION - {"evidence"}
_MODEL_REQUIREMENT = {"requirement_id", "predicate_json"}
_KINDS = {"feature_layer", "raster_layer", "table", "file", "map_state", "feature_class", "raster"}
_GEOMETRY = {"point", "polyline", "polygon", "raster", "not_applicable"}
_EFFECTS = {"read_only", "changes_map", "writes_data", "edits_data"}
_FORMATS = {"not_applicable", "map", "gdb", "shp", "tif", "tiff", "csv", "kmz", "png", "pdf", "file"}
_NON_SPATIAL_OUTPUT_KINDS = {"file", "table", "map_state"}
_OUTPUT_PRODUCER_KINDS = {
    "buffer", "overlay", "spatial_join", "aggregate", "project", "merge", "append",
    "feature_create", "feature_append", "copy", "repair", "define_projection", "add_xy",
}
_EVIDENCE_DESCRIPTION = (
    "One contiguous substring copied verbatim from the user request; return only "
    "the copied text with no prefix, suffix, explanation, translation, or inferred fact."
)


def _exact(value: Any, fields: set[str], path: str) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise TaskContractError(path + " has invalid fields.")
    return value


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskContractError(path + " must be non-empty.")
    return value


def _evidence(value: Any, request: str, path: str) -> str:
    value = _text(value, path)
    if value not in request:
        raise TaskContractError(path + " must be an exact user-request substring.")
    return value


def _raise_task_contract_violations(violations):
    if not violations:
        return
    if len(violations) == 1:
        raise TaskContractError(violations[0])
    raise TaskContractError(
        "task_contract has %d independent violations: %s"
        % (len(violations), " | ".join(
            "[%d] %s" % (index + 1, message)
            for index, message in enumerate(violations)
        ))
    )


def _parse_requirement(
    item, index, request, entity_ids, input_ids, output_ids,
    outputs_by_id, input_context, selected_entities,
):
    path = "requirements[%d]" % index
    _exact(item, _REQUIREMENT, path)
    _text(item["requirement_id"], path + ".requirement_id")
    predicate_path = path + ".predicate"
    if isinstance(item["predicate"], dict) and "where" in item["predicate"]:
        item["predicate"]["where"] = normalize_condition_tree(item["predicate"]["where"])
        try:
            item["predicate"]["where"] = validate_condition_tree(
                item["predicate"]["where"], TaskContractError,
            )
        except TaskContractError as exc:
            raise TaskContractError(predicate_path + ".where is invalid: " + str(exc))
        target_id = item["predicate"].get("target", item["predicate"].get("subject"))
        target_context = input_context.get(target_id)
        if target_context is not None:
            item["predicate"]["where"] = bind_condition_field_types(
                item["predicate"]["where"], target_context.get("fields", []),
                predicate_path + ".where", TaskContractError,
            )
    if (
        isinstance(item["predicate"], dict)
        and item["predicate"].get("kind") == "buffer"
        and item["predicate"].get("subject") not in entity_ids
    ):
        raise TaskContractError(
            predicate_path
            + ".subject is undeclared: an intermediate buffer belongs to Workflow, not TaskContract. "
            + "For a distance filter, use spatial_filter within_a_distance with search_distance; "
            + "declare a buffer output only when the user requests that buffer as a deliverable."
        )
    item["predicate"] = parse_task_predicate(
        item["predicate"], entity_ids, predicate_path, TaskContractError,
    )
    predicate = item["predicate"]
    if predicate["kind"] in {"attribute_filter", "spatial_filter"}:
        target = predicate.get("target", predicate["subject"])
        subject_output = outputs_by_id.get(predicate["subject"])
        if predicate["subject"] in input_ids and target != predicate["subject"]:
            raise TaskContractError(
                predicate_path + ".%s target must equal subject when filtering an existing input entity."
                % predicate["kind"]
            )
        if target not in input_ids or (
            predicate["subject"] not in input_ids
            and (subject_output is None or subject_output["kind"] != "map_state")
        ):
            raise TaskContractError(
                predicate_path + ".%s must bind the existing input entity filtered before output creation."
                % predicate["kind"]
            )
        if predicate["selection_type"] == "select_subset" and target not in selected_entities:
            raise TaskContractError(
                predicate_path
                + ".selection_type select_subset requires a selection established by current context "
                + "or an earlier filter requirement on the same target."
            )
        selected_entities.add(target)
    if predicate["kind"] == "artifact_export" and predicate["subject"] not in output_ids:
        raise TaskContractError(predicate_path + ".subject must be the declared exported output entity.")
    if predicate["kind"] == "artifact_export":
        output_format = outputs_by_id[predicate["subject"]]["format"]
        fixed_formats = {
            "map_png": "png", "map_pdf": "pdf", "export_png": "png",
            "export_pdf": "pdf", "table_csv": "csv", "layer_kml": "kmz",
        }
        expected_format = fixed_formats.get(predicate["action"])
        if expected_format is not None and output_format != expected_format:
            raise TaskContractError(
                predicate_path + ".action requires output format " + expected_format + "."
            )
        if predicate["action"] in {"export_selected_features", "split_by_field"} and output_format not in {"gdb", "shp"}:
            raise TaskContractError(
                predicate_path + ".action requires a gdb or shp output."
            )
        predicate["output_format"] = output_format
    if predicate["kind"] == "source_preserved" and predicate["subject"] not in input_ids:
        raise TaskContractError(predicate_path + ".source_preserved must bind an input entity, never an output.")
    if predicate["kind"] == "artifact_export" and predicate.get("target") == predicate["subject"]:
        raise TaskContractError(predicate_path + ".target must be the exported source entity, not the output itself.")
    _evidence(item["evidence"], request, path + ".evidence")
    return item


def parse_task_contract(value: Dict[str, Any], request: str, context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    _text(request, "request")
    value = _exact(value, _ROOT, "task_contract")
    result = deepcopy(value)
    ids = set()
    context_by_reference = {}
    if context is not None:
        for layer in context.get("layers", []) or []:
            for key in ("layer_ref", "name", "longName"):
                reference = layer.get(key)
                if reference:
                    context_by_reference[str(reference)] = layer
    input_context = {}
    input_references = set()
    for index, item in enumerate(result["input_entities"]):
        _exact(item, _INPUT, "input_entities[%d]" % index)
        for field in ("entity_id", "role", "reference"):
            _text(item[field], "input_entities[%d].%s" % (index, field))
        if item["kind"] not in _KINDS:
            raise TaskContractError("input entity kind is invalid.")
        _evidence(item["evidence"], request, "input_entities[%d].evidence" % index)
        if context is not None:
            layer = context_by_reference.get(item["reference"])
            if layer is None:
                raise TaskContractError("input entity reference is not present in current context.")
            input_context[item["entity_id"]] = layer
        if item["entity_id"] in ids:
            raise TaskContractError("task-contract ids must be unique.")
        if item["reference"] in input_references:
            raise TaskContractError("input entity references must be unique.")
        ids.add(item["entity_id"])
        input_references.add(item["reference"])
    output_ids = set()
    declaration_violations = []
    for index, item in enumerate(result["outputs"]):
        _exact(item, _OUTPUT, "outputs[%d]" % index)
        for field in ("output_id", "name", "spatial_reference", "destination"):
            _text(item[field], "outputs[%d].%s" % (index, field))
        if item["format"] not in _FORMATS:
            raise TaskContractError("output format is invalid.")
        if item["kind"] not in _KINDS or item["geometry"] not in _GEOMETRY:
            raise TaskContractError("output kind or geometry is invalid.")
        item["evidence"] = _evidence(item["evidence"], request, "outputs[%d].evidence" % index)
        destination = item["destination"]
        if item["kind"] == "map_state" and destination != "not_applicable":
            raise TaskContractError(
                "outputs[%d].destination must be not_applicable for map_state." % index
            )
        if item["kind"] != "map_state" and destination == "not_applicable":
            raise TaskContractError(
                "outputs[%d].destination cannot be not_applicable for a persisted output." % index
            )
        if destination not in {"default", "not_applicable"}:
            if not _is_fully_qualified_windows_path(destination):
                raise TaskContractError("outputs[%d].destination must be an absolute Windows path." % index)
            if destination not in request:
                raise TaskContractError(
                    "outputs[%d].destination must be copied exactly from the user request." % index
                )
        if (
            artifact_format_is_mentioned(item["format"], item["evidence"])
            and not artifact_filename_is_mentioned(item["name"], item["format"], item["evidence"])
        ):
            raise TaskContractError(
                "outputs[%d].name must exactly match the explicit filename in outputs[%d].evidence."
                % (index, index)
            )
        if item["kind"] in _NON_SPATIAL_OUTPUT_KINDS:
            # Geometry and CRS are type-derived for non-spatial artifacts.  A
            # model-provided map CRS must not become an impossible requirement
            # on CSV/PDF/PNG/KMZ files or map-state acknowledgements.
            item["geometry"] = "not_applicable"
            item["spatial_reference"] = "not_applicable"
        if not isinstance(item["required_fields"], list) or any(not isinstance(name, str) or not name for name in item["required_fields"]):
            declaration_violations.append("outputs[%d].output required_fields is invalid." % index)
        if item["output_id"] in ids:
            raise TaskContractError("input and output ids must be disjoint.")
        if item["output_id"] in output_ids:
            raise TaskContractError("output ids must be unique.")
        output_ids.add(item["output_id"])
    entity_ids = ids | output_ids
    selected_entities = {
        entity_id for entity_id, layer in input_context.items()
        if int(layer.get("selected_count") or 0) > 0
    }
    outputs_by_id = {item["output_id"]: item for item in result["outputs"]}
    output_indexes = {item["output_id"]: index for index, item in enumerate(result["outputs"])}
    if not result["requirements"]:
        raise TaskContractError("task_contract requires requirements.")
    requirement_ids = set()
    requirement_violations = list(declaration_violations)
    for index, item in enumerate(result["requirements"]):
        try:
            _parse_requirement(
                item, index, request, entity_ids, ids, output_ids,
                outputs_by_id, input_context, selected_entities,
            )
        except TaskContractError as exc:
            requirement_violations.append(str(exc))
            continue
        if item["requirement_id"] in requirement_ids:
            requirement_violations.append("requirement ids must be unique.")
        requirement_ids.add(item["requirement_id"])
    _raise_task_contract_violations(requirement_violations)
    predicate_hashes = set()
    for index, item in enumerate(result["requirements"]):
        predicate_hash = canonical_hash(item["predicate"])
        if predicate_hash in predicate_hashes:
            raise TaskContractError("requirements[%d].predicate duplicates an existing semantic obligation." % index)
        predicate_hashes.add(predicate_hash)
    buffer_outputs = {
        item["predicate"]["subject"]
        for item in result["requirements"]
        if item["predicate"]["kind"] == "buffer"
    }
    for index, item in enumerate(result["requirements"]):
        predicate = item["predicate"]
        if predicate["kind"] != "spatial_filter" or predicate["selector"] not in buffer_outputs:
            continue
        if predicate["overlap_type"] != "intersect" or "search_distance" in predicate:
            raise TaskContractError(
                "requirements[%d].predicate using a buffer output selector must use intersect "
                "without search_distance; the buffer requirement already owns the distance."
                % index
            )
    for item in result["requirements"]:
        predicate = item["predicate"]
        if predicate["kind"] != "artifact_export":
            continue
        output = outputs_by_id[predicate["subject"]]
        source_id = predicate.get("target")
        if source_id is None:
            if output["required_fields"]:
                raise TaskContractError(
                    "outputs[%d].required_fields must be empty for a targetless artifact export."
                    % output_indexes[predicate["subject"]]
                )
            continue
        source_fields = None
        if source_id in input_context:
            source_fields = {
                str(field["name"]).casefold()
                for field in input_context[source_id].get("fields", [])
                if isinstance(field, dict) and field.get("name")
            }
        elif source_id in outputs_by_id:
            source_fields = {
                str(field).casefold()
                for field in outputs_by_id[source_id]["required_fields"]
            }
        if source_fields is None:
            continue
        unavailable = [
            field for field in output["required_fields"]
            if field.casefold() not in source_fields
        ]
        if unavailable:
            raise TaskContractError(
                "outputs[%d].required_fields contains fields the passthrough export source cannot supply: %s."
                % (output_indexes[predicate["subject"]], ", ".join(unavailable))
            )
    for item in result["requirements"]:
        predicate = item["predicate"]
        if predicate["kind"] != "artifact_export" or predicate["action"] not in {"table_csv", "layer_kml"}:
            continue
        output = outputs_by_id[predicate["subject"]]
        source_output = outputs_by_id.get(predicate.get("target"))
        if source_output is not None and output["name"] not in request:
            output["name"] = source_output["name"]
    produced_outputs = {
        item["predicate"]["subject"]
        for item in result["requirements"]
        if item["predicate"]["kind"] in _OUTPUT_PRODUCER_KINDS
    }
    for index, item in enumerate(result["requirements"]):
        predicate = item["predicate"]
        if predicate["kind"] == "artifact_export" and predicate["subject"] in produced_outputs:
            raise TaskContractError(
                "requirements[%d].predicate redundantly exports an output already created by its GIS operation."
                % index
            )
    if not isinstance(result["allowed_side_effects"], list) or set(result["allowed_side_effects"]) - _EFFECTS:
        raise TaskContractError("allowed_side_effects is invalid.")
    # Selection is a transient execution mechanism, not a user-facing
    # permission vocabulary.  Its map-state effect is derived from the closed
    # semantic predicates so users never need to name it in their request.
    if any(item["predicate"]["kind"] in {"attribute_filter", "spatial_filter"}
           for item in result["requirements"]):
        result["allowed_side_effects"] = sorted(set(result["allowed_side_effects"]) | {"changes_map"})
    clarification_ids = set()
    for index, item in enumerate(result["clarifications"]):
        _exact(item, _CLARIFICATION, "clarifications[%d]" % index)
        for field in ("clarification_id", "question"):
            _text(item[field], "clarifications[%d].%s" % (index, field))
        _evidence(item["evidence"], request, "clarifications[%d].evidence" % index)
        if item["clarification_id"] in clarification_ids:
            raise TaskContractError("clarification ids must be unique.")
        clarification_ids.add(item["clarification_id"])
    if (
        isinstance(context, dict)
        and context.get("is_saved") is False
        and any(
            output["kind"] != "map_state" and output["destination"] == "default"
            for output in result["outputs"]
        )
        and not result["clarifications"]
    ):
        raise TaskContractError(
            "An unsaved ArcMap document with persisted outputs requires an output-location clarification "
            "unless every persisted output has an explicit request-bound destination."
        )
    return result


def _is_fully_qualified_windows_path(value: str) -> bool:
    drive, tail = ntpath.splitdrive(value)
    if drive.startswith("\\\\"):
        parts = [part for part in drive[2:].split("\\") if part]
        return len(parts) >= 2
    return bool(drive and tail.startswith(("\\", "/")))


TASK_CONTRACT = StructuredOutputContract(
    name="submit_task_contract_v10",
    description=(
        "Submit the closed GeoPilot task contract. The server binds authoritative input kinds "
        "from live ArcMap references and immutable request evidence."
    ),
    schema={
        "type": "object",
        "properties": {
            "task_contract": {
                "type": "object",
                "properties": {
                    "input_entities": {
                        "type": "array", "items": {
                            "type": "object", "properties": {
                                "entity_id": {"type": "string", "minLength": 1},
                                "role": {"type": "string", "minLength": 1},
                                "reference": {"type": "string", "minLength": 1},
                            }, "required": sorted(_MODEL_INPUT), "additionalProperties": False,
                        },
                    },
                    "outputs": {
                        "type": "array", "items": {
                            "type": "object", "properties": {
                                "output_id": {"type": "string", "minLength": 1},
                                "kind": {"type": "string", "enum": sorted(_KINDS)},
                                "name": {"type": "string", "minLength": 1},
                                "format": {"type": "string", "enum": sorted(_FORMATS)},
                                "geometry": {"type": "string", "enum": sorted(_GEOMETRY)},
                                "required_fields": {
                                    "type": "array", "items": {"type": "string", "minLength": 1},
                                    "description": (
                                        "Only fields explicitly required on this output and supplied by its "
                                        "source or producer; never inventory or invent an output schema."
                                    ),
                                },
                                "spatial_reference": {"type": "string", "minLength": 1},
                                "destination": {
                                    "type": "string", "minLength": 1,
                                    "description": (
                                        "Use the exact absolute Windows folder or geodatabase path requested "
                                        "for this output. Use default only when the request does not specify "
                                        "a destination, and not_applicable only for a non-file map-state output."
                                    ),
                                },
                                "evidence": {
                                    "type": "string", "minLength": 1,
                                    "description": (
                                        "The shortest contiguous user-request substring that names or "
                                        "demands this output; include the exact filename when present."
                                    ),
                                },
                            }, "required": sorted(_OUTPUT), "additionalProperties": False,
                        },
                    },
                    "requirements": {
                        "type": "array", "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "requirement_id": {"type": "string", "minLength": 1},
                                "predicate_json": {
                                    "type": "string", "minLength": 2,
                                    "description": (
                                        "A JSON string that decodes to exactly one object matching one variant "
                                        "from the closed task_predicate_catalog in the system instruction."
                                    ),
                                },
                            },
                            "required": sorted(_MODEL_REQUIREMENT),
                            "additionalProperties": False,
                        },
                    },
                    "allowed_side_effects": {"type": "array", "items": {"type": "string", "enum": sorted(_EFFECTS)}},
                    "clarifications": {
                        "type": "array", "items": {
                            "type": "object", "properties": {
                                "clarification_id": {"type": "string", "minLength": 1},
                                "question": {"type": "string", "minLength": 1},
                            }, "required": sorted(_MODEL_CLARIFICATION), "additionalProperties": False,
                        },
                    },
                }, "required": sorted(_ROOT), "additionalProperties": False,
            },
        }, "required": ["task_contract"], "additionalProperties": False,
    },
)


def task_contract_for_context(context: Dict[str, Any], request: str = "") -> StructuredOutputContract:
    """Close model-selectable identities to the current ArcMap context."""
    layers = [layer for layer in (context.get("layers", []) or []) if isinstance(layer, dict)]
    references = sorted({
        str(layer["layer_ref"])
        for layer in layers
        if isinstance(layer.get("layer_ref"), str) and layer["layer_ref"]
    })
    if not references and not request:
        return TASK_CONTRACT
    contract = deepcopy(TASK_CONTRACT)
    task_properties = contract.schema["properties"]["task_contract"]["properties"]
    input_properties = task_properties["input_entities"]["items"]["properties"]
    if references:
        input_properties["reference"] = {"type": "string", "enum": references}
    input_properties["entity_id"] = {
        "type": "string", "pattern": "^input:.+$",
        "description": "A unique stable input id beginning with input:.",
    }
    task_properties["outputs"]["items"]["properties"]["output_id"] = {
        "type": "string", "pattern": "^output:.+$",
        "description": "A unique stable output id beginning with output:.",
    }
    return contract


def bind_model_task_contract(
    value: Dict[str, Any], request: str, context: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Create the authoritative internal contract from the strict model view.

    Input kinds belong to the live ArcMap boundary, and evidence belongs to the
    immutable request boundary.  Neither is a semantic model choice.
    """
    _text(request, "request")
    value = _exact(value, _ROOT, "task_contract")
    result = deepcopy(value)
    _bind_section_evidence(result, "input_entities", _MODEL_INPUT, request)
    _bind_input_entity_kinds(result, context)
    _bind_model_requirements(result, request)
    _bind_section_evidence(result, "clarifications", _MODEL_CLARIFICATION, request)
    return result


def _bind_section_evidence(result, section, fields, request):
    items = result.get(section)
    if not isinstance(items, list):
        raise TaskContractError(section + " must be an array.")
    for index, item in enumerate(items):
        _exact(item, fields, "%s[%d]" % (section, index))
        item["evidence"] = request


def _bind_model_requirements(result, request):
    items = result.get("requirements")
    if not isinstance(items, list):
        raise TaskContractError("requirements must be an array.")
    bound = []
    for index, item in enumerate(items):
        path = "requirements[%d]" % index
        _exact(item, _MODEL_REQUIREMENT, path)
        requirement_id = _text(item["requirement_id"], path + ".requirement_id")
        raw_predicate = _text(item["predicate_json"], path + ".predicate_json")
        try:
            predicate = json.loads(raw_predicate)
        except (TypeError, ValueError) as exc:
            raise TaskContractError(path + ".predicate_json must contain valid JSON.") from exc
        if not isinstance(predicate, dict):
            raise TaskContractError(path + ".predicate_json must decode to an object.")
        bound.append({
            "requirement_id": requirement_id,
            "predicate": predicate,
            "evidence": request,
        })
    result["requirements"] = bound


def _bind_input_entity_kinds(result, context):
    items = result["input_entities"]
    if not items:
        return
    if not isinstance(context, dict):
        raise TaskContractError("live context is required to bind input entity kinds.")
    bindings_by_reference = {}
    for layer in context.get("layers", []) or []:
        if not isinstance(layer, dict):
            continue
        kind = _context_layer_kind(layer)
        identity = layer.get("layer_ref")
        if kind is None or not identity:
            continue
        for key in ("layer_ref", "name", "longName"):
            reference = layer.get(key)
            if reference:
                bindings_by_reference[str(reference)] = (kind, str(identity))
    identities = set()
    for index, item in enumerate(items):
        reference = item["reference"]
        binding = bindings_by_reference.get(reference)
        if binding is None:
            raise TaskContractError(
                "input_entities[%d].reference is not a bindable live ArcMap layer." % index
            )
        kind, identity = binding
        if identity in identities:
            raise TaskContractError("input entity references must be unique.")
        item["kind"] = kind
        identities.add(identity)


def _context_layer_kind(layer):
    if layer.get("isFeatureLayer") or layer.get("geometry_type"):
        return "feature_layer"
    if layer.get("dataSource"):
        return "raster_layer"
    return None


def task_contract_model_view(value: Dict[str, Any]) -> Dict[str, Any]:
    """Project an internal TaskContract to the sole model-visible shape."""
    value = _exact(value, _ROOT, "task_contract")
    result = deepcopy(value)
    for section, fields in (
        ("input_entities", _INPUT),
        ("clarifications", _CLARIFICATION),
    ):
        items = result.get(section)
        if not isinstance(items, list):
            raise TaskContractError(section + " must be an array.")
        for index, item in enumerate(items):
            _exact(item, fields, "%s[%d]" % (section, index))
            del item["evidence"]
            if section == "input_entities":
                del item["kind"]
    model_requirements = []
    for index, item in enumerate(result.get("requirements", [])):
        _exact(item, _REQUIREMENT, "requirements[%d]" % index)
        if not isinstance(item["predicate"], dict):
            raise TaskContractError("requirements[%d].predicate must be an object." % index)
        model_requirements.append({
            "requirement_id": item["requirement_id"],
            "predicate_json": json.dumps(
                {key: value for key, value in item["predicate"].items() if key != "output_format"},
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ),
        })
    result["requirements"] = model_requirements
    return result



