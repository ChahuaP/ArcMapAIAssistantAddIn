from __future__ import annotations

import unittest

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.task_contract import parse_task_contract
from gateway_py3.workflow_verifier import WorkflowVerifier


def _layer_context():
    return {
        "layers": [{
            "layer_ref": "layer:roads",
            "name": "roads",
            "geometry_type": "polyline",
            "fields": [
                {"name": "OBJECTID", "type": "OID"},
                {"name": "NAME", "type": "String"},
            ],
            "spatial_reference": "EPSG:3857",
            "selected_count": 0,
        }],
    }


class ArtifactExportCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.verifier = WorkflowVerifier(OperationCatalog())

    def test_generic_table_csv_contract_compiles_and_verifies(self):
        request = "把 roads 属性表导出为 roads.csv"
        task = parse_task_contract({
            "input_entities": [{
                "entity_id": "input:roads", "role": "source table",
                "kind": "feature_layer", "reference": "layer:roads", "evidence": "roads",
            }],
            "outputs": [{
                "output_id": "output:roads_csv", "kind": "file", "name": "roads.csv",
                "format": "csv", "geometry": "not_applicable", "required_fields": [],
                "spatial_reference": "not_applicable", "destination_policy": "server_derived",
                "evidence": "roads.csv",
            }],
            "requirements": [{
                "requirement_id": "requirement:csv",
                "predicate": {
                    "kind": "artifact_export", "subject": "output:roads_csv",
                    "target": "input:roads", "action": "export_table", "selected_only": False,
                },
                "evidence": "导出",
            }],
            "allowed_side_effects": ["writes_data"],
            "clarifications": [],
        }, request, _layer_context())

        report = self.verifier.verify({
            "summary": "Export table as CSV", "action": "execute",
            "steps": [{
                "id": "export_csv", "operation": "artifact.export_table_csv", "reason": "Requested table export",
                "arguments": {"layer": "input:roads", "output_name": "roads.csv"},
            }],
        }, _layer_context(), task)

        self.assertTrue(report["ok"], msg=str(report["hard_violations"]))
        self.assertTrue(report["output_results"][0]["satisfied"])
        self.assertTrue(report["requirements"][0]["satisfied"])
        self.assertEqual("file", report["facts"][0]["output"]["kind"])
        self.assertEqual("csv", report["facts"][0]["output"]["format"])
        self.assertEqual("server_derived", report["facts"][0]["output"]["destination_policy"])

    def test_generic_current_map_png_contract_compiles_and_verifies(self):
        request = "导出当前地图为 overview.png"
        task = parse_task_contract({
            "input_entities": [],
            "outputs": [{
                "output_id": "output:map_png", "kind": "file", "name": "overview.png",
                "format": "png", "geometry": "not_applicable", "required_fields": [],
                "spatial_reference": "not_applicable", "destination_policy": "server_derived",
                "evidence": "overview.png",
            }],
            "requirements": [{
                "requirement_id": "requirement:png",
                "predicate": {
                    "kind": "artifact_export", "subject": "output:map_png",
                    "action": "export_map", "selected_only": False,
                },
                "evidence": "导出当前地图",
            }],
            "allowed_side_effects": ["writes_data"],
            "clarifications": [],
        }, request, {"layers": []})

        report = self.verifier.verify({
            "summary": "Export current map as PNG", "action": "execute",
            "steps": [{
                "id": "export_png", "operation": "artifact.export_map_png", "reason": "Requested map export",
                "arguments": {"output_name": "overview.png"},
            }],
        }, {"layers": []}, task)

        self.assertTrue(report["ok"], msg=str(report["hard_violations"]))
        self.assertTrue(report["output_results"][0]["satisfied"])
        self.assertTrue(report["requirements"][0]["satisfied"])
        proof = report["requirements"][0]["proof"]["semantic_fact"]
        self.assertEqual("export_map", proof["action"])
        self.assertEqual("current_map", proof["target"])

    def test_wrong_file_identity_cannot_satisfy_declared_output(self):
        request = "导出当前地图为 overview.png"
        task = parse_task_contract({
            "input_entities": [],
            "outputs": [{
                "output_id": "output:map_png", "kind": "file", "name": "overview.png",
                "format": "png", "geometry": "not_applicable", "required_fields": [],
                "spatial_reference": "not_applicable", "destination_policy": "server_derived",
                "evidence": "overview.png",
            }],
            "requirements": [{
                "requirement_id": "requirement:png",
                "predicate": {
                    "kind": "artifact_export", "subject": "output:map_png",
                    "action": "export_map", "selected_only": False,
                },
                "evidence": "导出当前地图",
            }],
            "allowed_side_effects": ["writes_data"],
            "clarifications": [],
        }, request, {"layers": []})
        report = self.verifier.verify({
            "summary": "Wrong output identity", "action": "execute",
            "steps": [{
                "id": "wrong_export", "operation": "artifact.export_map_png", "reason": "Intentionally wrong output name",
                "arguments": {"output_name": "different.png"},
            }],
        }, {"layers": []}, task)

        self.assertFalse(report["ok"])
        self.assertFalse(report["output_results"][0]["satisfied"])


if __name__ == "__main__":
    unittest.main()
