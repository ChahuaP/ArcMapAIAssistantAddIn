"""Proof-driven typed clarification node tests."""
from __future__ import annotations

import unittest

from gateway_py3.clarification import (
    ClarificationError, apply_patch, build_nodes, node_for, option_ids,
    validate_answer,
)


def _requirement(predicate):
    return {"requirement_id": "req:r", "predicate": predicate}


class TypedClarificationTest(unittest.TestCase):
    def test_builds_one_node_per_unresolved_location_multiple_same_kind_ok(self):
        # Two unresolved selection_type locations on different requirements:
        # both become distinct, separately-addressable nodes (no "unique
        # candidate" restriction).
        contract = {
            "input_entities": [], "outputs": [],
            "requirements": [
                _requirement({"kind": "attribute_filter", "subject": "input:a",
                              "target": "input:a", "selection_type": "unresolved"}),
                _requirement({"kind": "attribute_filter", "subject": "input:b",
                              "target": "input:b", "selection_type": "unresolved"}),
            ],
            "allowed_side_effects": ["changes_map"], "clarifications": [],
        }
        nodes, graph_digest = build_nodes(contract)
        self.assertEqual(len(nodes), 2)
        self.assertEqual({n.target_path for n in nodes},
                         {"requirements.0.predicate.selection_type",
                          "requirements.1.predicate.selection_type"})
        # Each node carries its own descriptor + digest; graph digest binds them.
        self.assertNotEqual(nodes[0].node_digest, nodes[1].node_digest)
        self.assertTrue(graph_digest)
        self.assertTrue(all(n.proof_id == "unresolved:" + n.target_path for n in nodes))

    def test_each_kind_is_resolved_to_its_real_path_with_canonical_schema(self):
        cases = {
            ("selection.state", "attribute_filter"): (
                {"kind": "attribute_filter", "subject": "input:a", "target": "input:a",
                 "selection_type": "unresolved"}, "requirements.0.predicate.selection_type"),
            ("spatial.predicate", "spatial_filter"): (
                {"kind": "spatial_filter", "subject": "output:b", "target": "input:a",
                 "selector": "input:s", "overlap_type": "unresolved",
                 "selection_type": "new_selection"}, "requirements.0.predicate.overlap_type"),
            ("quantity.unit", "buffer"): (
                {"kind": "buffer", "subject": "output:b", "source": "input:a",
                 "distance": {"value": 100, "unit": "unresolved", "dimension": "length",
                              "tolerance": 0.0, "crs": None}},
                "requirements.0.predicate.distance.unit"),
            ("field.type", "field output"): (
                None, "outputs.0.required_fields.0.type"),
        }
        for (option_id, _label), (predicate, expected_path) in cases.items():
            if predicate is not None:
                contract = {"input_entities": [], "outputs": [],
                            "requirements": [_requirement(predicate)],
                            "allowed_side_effects": ["writes_data"], "clarifications": []}
            else:
                contract = {
                    "input_entities": [],
                    "outputs": [{"output_id": "output:real", "kind": "feature_class",
                                 "name": "r", "format": "gdb", "geometry": "point",
                                 "required_fields": [{"name": "n", "type": "unresolved",
                                                      "nullable": True, "length": None,
                                                      "precision": None, "scale": None, "domain": []}],
                                 "spatial_reference": "EPSG:4326",
                                 "destination_policy": "server_derived", "evidence": "r"}],
                    "requirements": [], "allowed_side_effects": ["writes_data"],
                    "clarifications": [],
                }
            nodes, _ = build_nodes(contract)
            node = node_for(nodes, expected_path)
            self.assertIsNotNone(node, option_id)
            self.assertEqual(node.option_id, option_id)
            self.assertIn("enum", node.value_schema)
            validate_answer(node.value_schema, node.value_schema["enum"][0])
            patched = apply_patch(contract, node.target_path, node.value_schema["enum"][0])
            self.assertEqual(patched["clarifications"], [])

    def test_no_unresolved_location_yields_no_nodes(self):
        contract = {"input_entities": [], "outputs": [],
                    "requirements": [_requirement({"kind": "attribute_filter",
                            "subject": "input:a", "target": "input:a",
                            "selection_type": "new_selection"})],
                    "allowed_side_effects": ["changes_map"], "clarifications": []}
        nodes, _ = build_nodes(contract)
        self.assertEqual(nodes, [])

    def test_answer_outside_closed_enum_is_rejected(self):
        contract = {"input_entities": [], "outputs": [],
                    "requirements": [_requirement({"kind": "attribute_filter",
                            "subject": "input:a", "target": "input:a",
                            "selection_type": "unresolved"})],
                    "allowed_side_effects": ["changes_map"], "clarifications": []}
        nodes, _ = build_nodes(contract)
        schema = nodes[0].value_schema
        with self.assertRaises(ClarificationError):
            validate_answer(schema, 100)
        with self.assertRaises(ClarificationError):
            validate_answer(schema, "current_selection")  # not a canonical SELECTION_TYPE

    def test_option_ids_are_the_closed_model_vocabulary(self):
        self.assertEqual(option_ids(), ("field.type", "quantity.unit",
                                        "selection.state", "spatial.predicate"))


if __name__ == "__main__":
    unittest.main()
