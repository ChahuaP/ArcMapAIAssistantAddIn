"""Server-owned typed clarification, resolved one-to-one per unresolved proof.

A clarification is never a free-form patch.  The model selects a closed
``option_id`` (a semantic *kind*); the server resolves the single real contract
path and the real value schema for the unique unresolved location of that kind
in the actual sealed task contract.  The option, its sealed proof id, its
target path and its schema are bound one-to-one and deterministically.

There is no hardcoded ``requirements.0`` index, no string-token guessing, and
no fail-open fallback that binds an answer to every unresolved proof: when the
unresolved location cannot be uniquely resolved the clarification is rejected
(fail closed), and on answer the server re-resolves against the current
contract to detect drift, staleness or an irrelevant answer.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from shared_runtime.semantic_abi import (
    ANGLE_UNITS, AREA_UNITS, FIELD_TYPES, LENGTH_UNITS, SELECTION_TYPES,
    SPATIAL_PREDICATES,
)


class ClarificationError(ValueError):
    pass


class ClarificationKind(str, Enum):
    QUANTITY_UNIT = "quantity_unit"
    SPATIAL_PREDICATE = "spatial_predicate"
    FIELD_TYPE = "field_type"
    SELECTION_STATE = "selection_state"


# The closed set of model-selectable option ids and the kind each resolves.
OPTION_KINDS: Dict[str, ClarificationKind] = {
    "quantity.unit": ClarificationKind.QUANTITY_UNIT,
    "spatial.predicate": ClarificationKind.SPATIAL_PREDICATE,
    "field.type": ClarificationKind.FIELD_TYPE,
    "selection.state": ClarificationKind.SELECTION_STATE,
}


@dataclass(frozen=True)
class ResolvedOption:
    """One clarification resolved to its real, single contract location."""
    option_id: str
    kind: ClarificationKind
    target_path: str
    value_schema: Dict[str, Any]
    proof_id: str


def option_ids() -> Tuple[str, ...]:
    return tuple(sorted(OPTION_KINDS))


def kind_for(option_id: str) -> ClarificationKind:
    try:
        return OPTION_KINDS[option_id]
    except KeyError:
        raise ClarificationError("unknown clarification option_id")


def _schema_for(kind: ClarificationKind) -> Dict[str, Any]:
    if kind is ClarificationKind.QUANTITY_UNIT:
        return {"type": "string", "enum": sorted(LENGTH_UNITS | AREA_UNITS | ANGLE_UNITS)}
    if kind is ClarificationKind.SPATIAL_PREDICATE:
        return {"type": "string", "enum": sorted(SPATIAL_PREDICATES)}
    if kind is ClarificationKind.FIELD_TYPE:
        return {"type": "string", "enum": sorted(FIELD_TYPES)}
    if kind is ClarificationKind.SELECTION_STATE:
        return {"type": "string", "enum": sorted(SELECTION_TYPES)}
    raise ClarificationError("unknown clarification kind")


def _is_unresolved(value: Any, allowed: Tuple[str, ...]) -> bool:
    # A field is unresolved when it carries a non-empty value outside the
    # closed canonical enum (the model's placeholder, never a valid answer).
    return isinstance(value, str) and bool(value) and value not in allowed


def _scan_quantity_units(requirement: Dict[str, Any], index: int) -> Optional[str]:
    predicate = requirement.get("predicate") if isinstance(requirement, dict) else None
    if not isinstance(predicate, dict):
        return None
    for field in ("distance", "search_distance"):
        quantity = predicate.get(field)
        if isinstance(quantity, dict) and _is_unresolved(quantity.get("unit"), LENGTH_UNITS):
            return "requirements.%d.predicate.%s.unit" % (index, field)
    return None


def _scan_selection(requirement: Dict[str, Any], index: int) -> Optional[str]:
    predicate = requirement.get("predicate") if isinstance(requirement, dict) else None
    if not isinstance(predicate, dict):
        return None
    # The canonical selection field is ``selection_type`` (never ``selection``).
    if _is_unresolved(predicate.get("selection_type"), SELECTION_TYPES):
        return "requirements.%d.predicate.selection_type" % index
    return None


def _scan_spatial_predicate(requirement: Dict[str, Any], index: int) -> Optional[str]:
    predicate = requirement.get("predicate") if isinstance(requirement, dict) else None
    if not isinstance(predicate, dict):
        return None
    if _is_unresolved(predicate.get("overlap_type"), SPATIAL_PREDICATES):
        return "requirements.%d.predicate.overlap_type" % index
    return None


def _scan_field_type(output: Dict[str, Any], index: int) -> Optional[str]:
    fields = output.get("required_fields") if isinstance(output, dict) else None
    if not isinstance(fields, list):
        return None
    for field_index, field in enumerate(fields):
        if isinstance(field, dict) and _is_unresolved(field.get("type"), FIELD_TYPES):
            return "outputs.%d.required_fields.%d.type" % (index, field_index)
    return None


_SCANNERS = {
    ClarificationKind.QUANTITY_UNIT: lambda req, i: (_scan_quantity_units(req, i),),
    ClarificationKind.SELECTION_STATE: lambda req, i: (_scan_selection(req, i),),
    ClarificationKind.SPATIAL_PREDICATE: lambda req, i: (_scan_spatial_predicate(req, i),),
}


def resolve_option(option_id: str, task_contract: Dict[str, Any]) -> ResolvedOption:
    """Resolve one option to its single real contract path and schema.

    Raises ``ClarificationError`` when the kind has no unresolved location or
    more than one (ambiguous).  The caller must never fall back to binding an
    unrelated or all-unresolved proof.
    """
    kind = kind_for(option_id)
    schema = _schema_for(kind)
    if not isinstance(task_contract, dict):
        raise ClarificationError("clarification requires a sealed task contract")

    if kind is ClarificationKind.FIELD_TYPE:
        outputs = task_contract.get("outputs")
        if not isinstance(outputs, list):
            raise ClarificationError("field.type clarification has no output schema")
        candidates = [path for path in (_scan_field_type(output, i) for i, output in enumerate(outputs)) if path]
    else:
        requirements = task_contract.get("requirements")
        if not isinstance(requirements, list):
            raise ClarificationError("clarification has no sealed requirements")
        scanner = _SCANNERS[kind][0]
        candidates = [path for path in (scanner(requirement, i) for i, requirement in enumerate(requirements)) if path]

    if not candidates:
        raise ClarificationError("option %s has no unresolved location in this contract" % option_id)
    if len(candidates) > 1:
        raise ClarificationError("option %s is ambiguous across %d locations" % (option_id, len(candidates)))
    target_path = candidates[0]
    proof_id = "unresolved:%s:%s" % (option_id, target_path)
    return ResolvedOption(option_id=option_id, kind=kind, target_path=target_path,
                          value_schema=schema, proof_id=proof_id)


def validate_answer(schema: Dict[str, Any], value: Any) -> None:
    expected = schema.get("type")
    if expected == "string" and not isinstance(value, str):
        raise ClarificationError("clarification answer must be a string")
    if expected == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise ClarificationError("clarification answer must be a number")
    if "enum" in schema and value not in schema["enum"]:
        raise ClarificationError("clarification answer is outside the closed enum")
    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        raise ClarificationError("clarification answer is below minimum")
    if "maximum" in schema and isinstance(value, (int, float)) and value > schema["maximum"]:
        raise ClarificationError("clarification answer is above maximum")


def apply_patch(document: Dict[str, Any], target_path: str, value: Any) -> Dict[str, Any]:
    result = deepcopy(document)
    parts = target_path.split(".")
    cursor: Any = result
    for part in parts[:-1]:
        if isinstance(cursor, list):
            index = int(part)
            if index >= len(cursor):
                raise ClarificationError("clarification target path is absent")
            cursor = cursor[index]
        elif isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            raise ClarificationError("clarification target path is absent")
    leaf = parts[-1]
    if not isinstance(cursor, dict) or leaf not in cursor:
        raise ClarificationError("clarification target path is absent")
    cursor[leaf] = deepcopy(value)
    # Clearing the clarifications block confirms the draft no longer asks.
    if isinstance(result.get("clarifications"), list):
        result["clarifications"] = []
    return result
