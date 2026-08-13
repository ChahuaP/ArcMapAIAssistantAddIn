"""Server-generated, proof-driven typed clarification.

The server is the sole authority over what may be clarified.  It scans the
sealed task-contract draft once and emits one ``ProofNode`` per genuinely
unresolved location — each carrying its closed descriptor (kind, real target
path, value schema) and a content digest.  Multiple locations of the same kind
produce multiple, separately-addressable nodes.

A ``ClarificationRequest`` is derived one-to-one from a sealed node: the model
never supplies a path, only selects a server-issued instance option id bound to
exactly one node.  On answer, the server rebuilds the nodes from the current
contract and verifies the node still exists with the same digest (drift, a
resolved location, or an irrelevant answer is rejected); there is no scanner at
answer time, no string-token guessing, and no fail-open rebinding.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

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
# The same kind may resolve to several distinct unresolved locations; each
# becomes its own server-issued instance (see build_nodes).
OPTION_KINDS: Dict[str, ClarificationKind] = {
    "quantity.unit": ClarificationKind.QUANTITY_UNIT,
    "spatial.predicate": ClarificationKind.SPATIAL_PREDICATE,
    "field.type": ClarificationKind.FIELD_TYPE,
    "selection.state": ClarificationKind.SELECTION_STATE,
}


def option_ids() -> Tuple[str, ...]:
    return tuple(sorted(OPTION_KINDS))


def kind_for(option_id: str) -> ClarificationKind:
    try:
        return OPTION_KINDS[option_id]
    except KeyError:
        raise ClarificationError("unknown clarification option_id")


@dataclass(frozen=True)
class ProofNode:
    """One server-generated unresolved clarification location."""
    proof_id: str
    option_id: str
    kind: ClarificationKind
    target_path: str
    value_schema: Dict[str, Any]
    node_digest: str

    def as_dict(self) -> Dict[str, Any]:
        return {"proof_id": self.proof_id, "option_id": self.option_id,
                "kind": self.kind.value, "target_path": self.target_path,
                "value_schema": self.value_schema, "node_digest": self.node_digest}


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
    return isinstance(value, str) and bool(value) and value not in allowed


def _node(option_id: str, target_path: str) -> ProofNode:
    kind = kind_for(option_id)
    schema = _schema_for(kind)
    digest = hashlib.sha256(json.dumps(
        {"option_id": option_id, "target_path": target_path,
         "value_schema": schema}, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return ProofNode(proof_id="unresolved:%s" % target_path, option_id=option_id,
                     kind=kind, target_path=target_path, value_schema=schema, node_digest=digest)


def _scan_requirement(requirement: Dict[str, Any], index: int) -> List[ProofNode]:
    predicate = requirement.get("predicate") if isinstance(requirement, dict) else None
    if not isinstance(predicate, dict):
        return []
    nodes: List[ProofNode] = []
    for field in ("distance", "search_distance"):
        quantity = predicate.get(field)
        if isinstance(quantity, dict) and _is_unresolved(quantity.get("unit"), LENGTH_UNITS):
            nodes.append(_node("quantity.unit", "requirements.%d.predicate.%s.unit" % (index, field)))
    if _is_unresolved(predicate.get("selection_type"), SELECTION_TYPES):
        nodes.append(_node("selection.state", "requirements.%d.predicate.selection_type" % index))
    if _is_unresolved(predicate.get("overlap_type"), SPATIAL_PREDICATES):
        nodes.append(_node("spatial.predicate", "requirements.%d.predicate.overlap_type" % index))
    return nodes


def _scan_output(output: Dict[str, Any], index: int) -> List[ProofNode]:
    fields = output.get("required_fields") if isinstance(output, dict) else None
    if not isinstance(fields, list):
        return []
    nodes: List[ProofNode] = []
    for field_index, field in enumerate(fields):
        if isinstance(field, dict) and _is_unresolved(field.get("type"), FIELD_TYPES):
            nodes.append(_node("field.type", "outputs.%d.required_fields.%d.type" % (index, field_index)))
    return nodes


def build_nodes(task_contract: Dict[str, Any]) -> Tuple[List[ProofNode], str]:
    """Build every unresolved clarification node from a sealed draft.

    Returns the node list plus a graph digest binding them.  Each unresolved
    location becomes its own node (multiple same-kind locations are kept
    distinct by target path); the graph digest lets an answer verify the whole
    sealed set has not drifted.
    """
    if not isinstance(task_contract, dict):
        raise ClarificationError("clarification requires a sealed task contract")
    nodes: List[ProofNode] = []
    for index, requirement in enumerate(task_contract.get("requirements", []) or []):
        nodes.extend(_scan_requirement(requirement, index))
    for index, output in enumerate(task_contract.get("outputs", []) or []):
        nodes.extend(_scan_output(output, index))
    # Deterministic order by target path; stable graph digest.
    nodes.sort(key=lambda n: n.target_path)
    payload = json.dumps([n.as_dict() for n in nodes], sort_keys=True, separators=(",", ":"))
    graph_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return nodes, graph_digest


def node_for(nodes: List[ProofNode], target_path: str) -> Optional[ProofNode]:
    return next((n for n in nodes if n.target_path == target_path), None)


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
    result = json.loads(json.dumps(document))  # deep copy via JSON (Py2/Py3 safe)
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
    cursor[leaf] = json.loads(json.dumps(value))
    if isinstance(result.get("clarifications"), list):
        result["clarifications"] = []
    return result
