import unittest

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.validators import ValidationError, prepare_workflow
from gateway_py3.workflow_protocol import workflow_protocol


class ConditionFieldComparisonTests(unittest.TestCase):
    def setUp(self):
        self.catalog = OperationCatalog()
        self.context = {
            "is_saved": True,
            "layers": [
                {
                    "layer_ref": "layer:parcels",
                    "name": "parcels",
                    "longName": "parcels",
                    "fields": [
                        {"name": "PLAN_USE", "type": "String"},
                        {"name": "ACT_USE", "type": "String"},
                    ],
                }
            ],
        }

    def test_prepare_workflow_accepts_same_layer_field_comparison(self):
        workflow = {
            "action": "execute",
            "summary": "select land-use mismatches",
            "steps": [
                {
                    "id": "select_mismatch",
                    "operation": "selection.select_by_attribute",
                    "arguments": {
                        "layer": "layer:parcels",
                        "where": {
                            "field": "PLAN_USE",
                            "op": "ne",
                            "value_field": "ACT_USE",
                        },
                    },
                    "reason": "planned and actual land use must differ",
                }
            ],
        }

        prepared = prepare_workflow(workflow, self.catalog, self.context)

        self.assertEqual(
            prepared["steps"][0]["arguments"]["where"],
            {"field": "PLAN_USE", "op": "ne", "value_field": "ACT_USE"},
        )

    def test_model_contract_exposes_the_same_field_comparison_vocabulary(self):
        operation = self.catalog.get("selection.select_by_attribute")
        protocol = workflow_protocol()["where"]

        self.assertIn("where", operation["parameters_schema"]["properties"])
        self.assertEqual(
            [item["parameter"] for item in operation["capability_contract"]["inputs"]],
            ["layer"],
        )
        self.assertEqual(
            protocol["field_comparison_operators"],
            ["eq", "ne", "gt", "gte", "lt", "lte"],
        )
        self.assertIn("value_field", protocol["rule"])

    def test_legacy_condition_operator_fields_and_aliases_are_rejected(self):
        base_step = {
            "id": "select_mismatch",
            "operation": "selection.select_by_attribute",
            "reason": "select mismatches",
        }
        for where in (
            {"field": "PLAN_USE", "operator": "ne", "value_field": "ACT_USE"},
            {"field": "PLAN_USE", "op": "!=", "value_field": "ACT_USE"},
            {"and": [
                {"field": "PLAN_USE", "op": "ne", "value_field": "ACT_USE"},
                {"field": "PLAN_USE", "op": "is_not_null"},
            ]},
        ):
            step = dict(base_step)
            step["arguments"] = {"layer": "layer:parcels", "where": where}
            workflow = {
                "action": "execute",
                "summary": "reject legacy condition",
                "steps": [step],
            }
            with self.subTest(where=where):
                with self.assertRaises(ValidationError):
                    prepare_workflow(workflow, self.catalog, self.context)


if __name__ == "__main__":
    unittest.main()
