from __future__ import annotations

from typing import Any, Dict, List

from arcmap_runtime_py2.context_fingerprint import context_hash

from .catalog_loader import OperationCatalog


class ValidationError(Exception):
    pass


def validate_catalog(catalog: OperationCatalog) -> None:
    required = [
        "id",
        "version",
        "category",
        "summary",
        "model_card",
        "parameters_schema",
        "context_requirements",
        "side_effects",
        "output_policy",
        "executor",
        "examples"
    ]
    for operation in catalog.all_operations():
        missing = [key for key in required if key not in operation]
        if missing:
            raise ValidationError(f"{operation.get('id', '<unknown>')} missing fields: {missing}")
        if operation["side_effects"] not in ("read_only", "changes_map", "writes_data", "edits_data"):
            raise ValidationError(f"{operation['id']} has invalid side_effects")


def validate_workflow(workflow: Dict[str, Any], catalog: OperationCatalog) -> None:
    if not isinstance(workflow, dict):
        raise ValidationError("Workflow must be an object.")
    if not isinstance(workflow.get("summary"), str) or not workflow["summary"].strip():
        raise ValidationError("Workflow summary is required.")
    action = workflow.get("action", "execute")
    if action not in ("execute", "clarify", "unsupported"):
        raise ValidationError("Workflow action must be execute, clarify, or unsupported.")
    steps = workflow.get("steps")
    if not isinstance(steps, list):
        raise ValidationError("Workflow steps must be an array.")
    if action == "execute" and not steps:
        raise ValidationError("Executable workflow must contain at least one step.")
    if action in ("clarify", "unsupported") and steps:
        raise ValidationError("Clarify and unsupported workflows must not contain executable steps.")

    seen_step_ids = set()
    for step in steps:
        _validate_step(step, catalog, seen_step_ids)
        seen_step_ids.add(step["id"])


def _validate_step(step: Dict[str, Any], catalog: OperationCatalog, seen_step_ids: set[str]) -> None:
    for key in ("id", "operation", "arguments", "reason"):
        if key not in step:
            raise ValidationError(f"Step missing field: {key}")
    if step["id"] in seen_step_ids:
        raise ValidationError(f"Duplicate step id: {step['id']}")
    operation = catalog.get(step["operation"])
    arguments = step["arguments"]
    if not isinstance(arguments, dict):
        raise ValidationError(f"{step['id']} arguments must be an object.")
    _validate_arguments(step["id"], arguments, operation["parameters_schema"])


def _validate_arguments(step_id: str, arguments: Dict[str, Any], schema: Dict[str, Any]) -> None:
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    additional = schema.get("additionalProperties", True)

    for name in required:
        if name not in arguments:
            raise ValidationError(f"{step_id} missing required argument: {name}")
    if additional is False:
        extra = sorted(set(arguments) - set(properties))
        if extra:
            raise ValidationError(f"{step_id} has unknown arguments: {extra}")

    for name, value in arguments.items():
        if name in properties:
            _validate_type(step_id, name, value, properties[name])


def _validate_type(step_id: str, name: str, value: Any, schema: Dict[str, Any]) -> None:
    expected = schema.get("type")
    if expected == "string" and not isinstance(value, str):
        raise ValidationError(f"{step_id}.{name} must be string.")
    if expected == "boolean" and not isinstance(value, bool):
        raise ValidationError(f"{step_id}.{name} must be boolean.")
    if expected == "integer" and not isinstance(value, int):
        raise ValidationError(f"{step_id}.{name} must be integer.")
    if expected == "array":
        if not isinstance(value, list):
            raise ValidationError(f"{step_id}.{name} must be array.")
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < min_items:
            raise ValidationError(f"{step_id}.{name} must contain at least {min_items} items.")
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            _validate_type(step_id, f"{name}[{index}]", item, item_schema)
    if expected == "object" and not isinstance(value, dict):
        raise ValidationError(f"{step_id}.{name} must be object.")
    enum = schema.get("enum")
    if enum and value not in enum:
        raise ValidationError(f"{step_id}.{name} must be one of {enum}.")
