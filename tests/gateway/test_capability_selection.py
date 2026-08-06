import copy
import json
import unittest

from gateway_py3.capability_selection import (
    CapabilityScope,
    CapabilitySelectionError,
    effect_matches_predicate,
)
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.semantic_domain import KINDS


def output(output_id, kind, name, output_format, geometry):
    return {
        "output_id": output_id,
        "kind": kind,
        "name": name,
        "format": output_format,
        "geometry": geometry,
        "required_fields": [],
        "spatial_reference": "not_applicable",
        "destination": "not_applicable" if kind == "map_state" else "default",
        "evidence": "request",
    }


class CapabilitySelectionPublicTests(unittest.TestCase):
    def setUp(self):
        self.catalog = OperationCatalog()
        self.scope = CapabilityScope(self.catalog)

    def test_semantic_index_is_compact_and_contains_no_executable_schemas(self):
        index = self.scope.semantic_index()
        encoded = json.dumps(index, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.assertLess(len(encoded), 19_000)
        self.assertEqual(len(list(self.catalog.all_operations())), len(index["operations"]))
        self.assertTrue(all("parameters_schema" not in item for item in index["operations"]))
        self.assertEqual(
            KINDS - {"source_preserved"},
            {
                predicate["kind"]
                for item in index["operations"]
                for predicate in item["predicates"]
            },
        )

    def test_flood_export_contract_closes_to_only_four_executable_cards(self):
        task = {
            "input_entities": [],
            "outputs": [
                output("output:shp", "feature_class", "priority_shelters", "shp", "point"),
                output("output:csv", "file", "priority_shelters", "csv", "not_applicable"),
                output("output:png", "file", "flood_response_map", "png", "not_applicable"),
            ],
            "requirements": [
                {
                    "requirement_id": "filter",
                    "predicate": {
                        "kind": "attribute_filter", "subject": "input:shelters",
                        "target": "input:shelters", "where": {"field": "CAPACITY", "op": "gte", "value": 1000},
                        "selection_type": "new_selection",
                    },
                },
                {
                    "requirement_id": "shp",
                    "predicate": {
                        "kind": "artifact_export", "subject": "output:shp",
                        "target": "input:shelters", "action": "export_selected_features",
                        "selected_only": True, "output_format": "shp",
                    },
                },
                {
                    "requirement_id": "csv",
                    "predicate": {
                        "kind": "artifact_export", "subject": "output:csv",
                        "target": "output:shp", "action": "table_csv",
                        "selected_only": False, "output_format": "csv",
                    },
                },
                {
                    "requirement_id": "png",
                    "predicate": {
                        "kind": "artifact_export", "subject": "output:png",
                        "action": "map_png", "output_format": "png",
                    },
                },
            ],
            "allowed_side_effects": ["changes_map", "writes_data"],
            "clarifications": [],
        }

        closure = self.scope.close(task)

        self.assertEqual(
            [
                "export.map_png",
                "export.table_csv",
                "selection.export_selected_features",
                "selection.select_by_attribute",
            ],
            [card["id"] for card in closure.cards],
        )
        self.assertTrue(all(item["candidate_ids"] for item in closure.requirement_coverage))
        self.assertTrue(all(item["covered_by_requirement"] for item in closure.output_coverage))

    def test_every_registered_effect_matches_its_own_closed_predicate(self):
        covered_kinds = set()
        for operation in self.catalog.all_operations():
            card = self.catalog.planning_card(operation)
            for effect in card["semantic_effects"]:
                if effect["kind"] == "inspect":
                    covered_kinds.add(effect["kind"])
                    continue
                predicate = {"kind": effect["kind"], "subject": "output:x"}
                for field, binding in effect.items():
                    if field in {"kind", "result", "preserves"}:
                        continue
                    bindings = binding if isinstance(binding, list) else [binding]
                    if field == "sources":
                        predicate[field] = ["input:x"]
                        continue
                    key, value = next(iter(bindings[0].items()))
                    if key == "const":
                        predicate[field] = copy.deepcopy(value)
                    elif field in {"source", "target", "selector", "join"}:
                        predicate[field] = "input:x"
                    else:
                        schema = card["parameters_schema"]["properties"].get(value, {})
                        allowed = schema.get("enum")
                        predicate[field] = allowed[0] if isinstance(allowed, list) else "value"
                self.assertTrue(
                    effect_matches_predicate(card, effect, predicate),
                    "%s did not match %s" % (card["id"], predicate),
                )
                covered_kinds.add(effect["kind"])
        self.assertEqual(KINDS - {"source_preserved"}, covered_kinds)

    def test_snapshot_validation_rejects_stale_cards_and_missing_workflow_operations(self):
        card = self.catalog.planning_card(self.catalog.get("layer.clear_layers"))
        self.scope.validate_snapshot([card], ["layer.clear_layers"])
        stale = copy.deepcopy(card)
        stale["summary"] = "stale"
        with self.assertRaisesRegex(CapabilitySelectionError, "authoritative catalog"):
            self.scope.validate_snapshot([stale], ["layer.clear_layers"])
        with self.assertRaisesRegex(CapabilitySelectionError, "does not cover"):
            self.scope.validate_snapshot([card], ["export.map_png"])


if __name__ == "__main__":
    unittest.main()
