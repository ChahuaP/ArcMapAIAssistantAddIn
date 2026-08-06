"""Task-bound capability scoping for model planning and artifact replay."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Sequence

from .plan_artifact import canonical_hash
from .semantic_domain import ARCMAP_SELECTION_TYPES


class CapabilitySelectionError(ValueError):
    pass


def _canonical_constraint(field: str, value: Any) -> Any:
    if field == "selection_type" and isinstance(value, str):
        normalized = value.strip()
        return ARCMAP_SELECTION_TYPES.get(normalized.upper(), normalized.lower())
    if field in {"overlap_type", "output_format"} and isinstance(value, str):
        return value.strip().lower()
    return value


def _parameter_accepts(card: Dict[str, Any], parameter: str, field: str, expected: Any) -> bool:
    schema = card.get("parameters_schema", {}).get("properties", {}).get(parameter, {})
    allowed = schema.get("enum") if isinstance(schema, dict) else None
    if not isinstance(allowed, list):
        return True
    expected = _canonical_constraint(field, expected)
    return expected in {_canonical_constraint(field, value) for value in allowed}


def effect_matches_predicate(card: Dict[str, Any], effect: Dict[str, Any],
                             predicate: Dict[str, Any]) -> bool:
    """Prove that one executable effect can realize a parsed task predicate."""
    if effect.get("kind") != predicate.get("kind") or predicate.get("kind") == "source_preserved":
        return False
    for field, expected in predicate.items():
        if field in {"kind", "subject"}:
            continue
        binding = effect.get(field)
        if binding is None:
            return False
        bindings = binding if isinstance(binding, list) else [binding]
        if field == "sources":
            if not bool(bindings) or not all(
                isinstance(item, dict) and set(item) <= {"parameter", "const", "output"}
                for item in bindings
            ):
                return False
            continue
        if len(bindings) != 1 or not isinstance(bindings[0], dict) or len(bindings[0]) != 1:
            return False
        key, value = next(iter(bindings[0].items()))
        if key == "const":
            if _canonical_constraint(field, value) != _canonical_constraint(field, expected):
                return False
        elif key == "parameter":
            if not _parameter_accepts(card, value, field, expected):
                return False
        elif key != "output":
            return False
    return True


def effect_preserves_predicate(effect: Dict[str, Any], predicate: Dict[str, Any]) -> bool:
    """Return whether a downstream effect explicitly preserves this semantic fact."""
    preserves = effect.get("preserves")
    return (
        isinstance(preserves, list)
        and predicate.get("kind") in preserves
    )


def _format_values(card: Dict[str, Any]) -> frozenset[str]:
    descriptor = card["outputs"]["format"]
    rule = descriptor["rule"]
    if rule in {"fixed", "not_applicable"}:
        return frozenset((str(descriptor["value"]).lower(),))
    if rule != "from_parameter":
        raise CapabilitySelectionError("unknown capability output-format rule: %s" % rule)
    schema = card["parameters_schema"]["properties"][descriptor["parameter"]]
    allowed = schema.get("enum")
    if isinstance(allowed, list) and allowed:
        return frozenset(str(value).lower() for value in allowed)
    return frozenset((str(descriptor["default"]).lower(),))


def _kind_matches(expected: str, actual: str) -> bool:
    return (
        expected == actual
        or (expected == "feature_layer" and actual == "feature_class")
        or (expected == "raster_layer" and actual == "raster")
    )


def _output_matches(card: Dict[str, Any], output: Dict[str, Any]) -> bool:
    actual = card["outputs"]
    if not _kind_matches(output["kind"], actual["kind"]):
        return False
    expected_format = str(output["format"]).lower()
    if expected_format not in {"", "not_applicable"} and expected_format not in _format_values(card):
        return False
    expected_geometry = output["geometry"]
    geometry = actual["geometry"]
    if expected_geometry == "not_applicable":
        return geometry["rule"] == "not_applicable"
    if geometry["rule"] == "fixed":
        return geometry["value"] in {expected_geometry, "parameter_geometry_type"}
    return geometry["rule"] in {"inherit", "lowest_dimension"}


def _compact_output(card: Dict[str, Any]) -> Dict[str, Any]:
    geometry = card["outputs"]["geometry"]
    return {
        "kind": card["outputs"]["kind"],
        "geometry": geometry["value"] if geometry["rule"] == "fixed" else geometry["rule"],
        "formats": sorted(_format_values(card)),
    }


def _compact_effect(effect: Dict[str, Any]) -> Dict[str, Any]:
    constants = {
        field: deepcopy(binding["const"])
        for field, binding in effect.items()
        if isinstance(binding, dict) and set(binding) == {"const"}
    }
    return {
        "kind": effect["kind"],
        "constants": constants,
    }


@dataclass(frozen=True)
class CapabilityClosure:
    cards: tuple[Dict[str, Any], ...]
    requirement_coverage: tuple[Dict[str, Any], ...]
    output_coverage: tuple[Dict[str, Any], ...]

    @property
    def hash(self) -> str:
        return canonical_hash(list(self.cards))

    def trace_record(self) -> Dict[str, Any]:
        return {
            "selected_ids": [card["id"] for card in self.cards],
            "selected_hash": self.hash,
            "requirement_coverage": deepcopy(list(self.requirement_coverage)),
            "output_coverage": deepcopy(list(self.output_coverage)),
        }


class CapabilityScope:
    """Owns the compact semantic index and deterministic post-contract closure."""

    def __init__(self, catalog, operations: Iterable[Dict[str, Any]] | None = None):
        self.catalog = catalog
        source = list(catalog.all_operations()) if operations is None else list(operations)
        self._operations = tuple(sorted(source, key=lambda item: item["id"]))
        self._cards = {
            operation["id"]: catalog.planning_card(operation)
            for operation in self._operations
        }
        self.catalog_hash = canonical_hash(list(catalog.all_operations()))

    def semantic_index(self) -> Dict[str, Any]:
        operations = []
        for operation in self._operations:
            card = self._cards[operation["id"]]
            operations.append({
                "id": card["id"],
                "summary": card["summary"],
                "side_effects": card["side_effects"],
                "predicates": [_compact_effect(effect) for effect in card["semantic_effects"]],
                "output": _compact_output(card),
            })
        document = {
            "schema": "geopilot-capability-index/v1",
            "catalog_hash": self.catalog_hash,
            "operations": operations,
        }
        document["index_hash"] = canonical_hash(document)
        return document

    def close(self, task_contract: Dict[str, Any],
              retained_operation_ids: Sequence[str] = ()) -> CapabilityClosure:
        allowed = set(task_contract.get("allowed_side_effects", ()))
        outputs = {
            output["output_id"]: output
            for output in task_contract.get("outputs", ())
        }
        selected = set()
        covered_outputs = set()
        requirement_coverage = []
        for requirement in task_contract.get("requirements", ()):
            predicate = requirement["predicate"]
            candidates = []
            preserving = []
            if predicate["kind"] != "source_preserved":
                for operation in self._operations:
                    card = self._cards[operation["id"]]
                    if card["side_effects"] not in allowed:
                        continue
                    if any(effect_matches_predicate(card, effect, predicate)
                           for effect in card["semantic_effects"]):
                        candidates.append(card["id"])
                    subject = predicate.get("subject")
                    if (
                        subject in outputs
                        and _output_matches(card, outputs[subject])
                        and any(effect_preserves_predicate(effect, predicate)
                                for effect in card["semantic_effects"])
                    ):
                        preserving.append(card["id"])
            selected.update(candidates)
            selected.update(preserving)
            subject = predicate.get("subject")
            if subject in outputs and any(
                _output_matches(self._cards[operation_id], outputs[subject])
                for operation_id in candidates
            ):
                covered_outputs.add(subject)
            requirement_coverage.append({
                "requirement_id": requirement["requirement_id"],
                "predicate_kind": predicate["kind"],
                "candidate_ids": sorted(candidates),
                "preserving_ids": sorted(preserving),
            })

        output_coverage = []
        for output_id, output in outputs.items():
            candidates = []
            if output_id not in covered_outputs:
                candidates = [
                    operation["id"] for operation in self._operations
                    if self._cards[operation["id"]]["side_effects"] in allowed
                    and _output_matches(self._cards[operation["id"]], output)
                ]
                selected.update(candidates)
            output_coverage.append({
                "output_id": output_id,
                "candidate_ids": sorted(candidates),
                "covered_by_requirement": output_id in covered_outputs,
            })

        for operation_id in retained_operation_ids:
            if operation_id not in self._cards:
                raise CapabilitySelectionError(
                    "workflow uses a capability outside the authoritative planning scope: %s"
                    % operation_id
                )
            selected.add(operation_id)
        if not selected:
            raise CapabilitySelectionError("task contract has no executable capability closure.")
        cards = tuple(deepcopy(self._cards[operation_id]) for operation_id in sorted(selected))
        return CapabilityClosure(cards, tuple(requirement_coverage), tuple(output_coverage))

    def validate_snapshot(self, snapshot: Sequence[Dict[str, Any]],
                          required_operation_ids: Sequence[str] = ()) -> None:
        if not isinstance(snapshot, list):
            raise CapabilitySelectionError("capability snapshot must be an array.")
        ids = [item.get("id") for item in snapshot if isinstance(item, dict)]
        if len(ids) != len(snapshot) or ids != sorted(set(ids)):
            raise CapabilitySelectionError("capability snapshot identities are invalid.")
        for item in snapshot:
            operation_id = item["id"]
            current = self._cards.get(operation_id)
            if current is None or item != current:
                raise CapabilitySelectionError(
                    "capability snapshot does not match the authoritative catalog: %s"
                    % operation_id
                )
        missing = sorted(set(required_operation_ids) - set(ids))
        if missing:
            raise CapabilitySelectionError(
                "capability snapshot does not cover workflow operations: %s"
                % ", ".join(missing)
            )
