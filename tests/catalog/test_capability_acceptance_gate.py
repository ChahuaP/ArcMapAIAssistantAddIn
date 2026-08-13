"""Production capability coverage gate (no hand-written test-class map).

Iterates the REAL catalog: every capability's EVERY declared semantic effect
must bind to a production AcceptanceProfile (the single fact source consumed by
catalog load, Py3 seal and Py2 dispatcher), and every output-producing
capability must derive a valid AcceptanceContract from its real operation
schema/contract.  No test-side string map stands in for production evidence.
"""
from __future__ import annotations

import unittest

from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.acceptance_contract import derive, AcceptanceContractError
from gateway_py3.kernel.contracts import (
    ContextSnapshot, DeclaredOutput, FieldColumn, LayerRef, LayerSnapshot,
    VerifiedPlan, WorkflowStep,
)
from shared_runtime.acceptance_profile import PROFILES, get_profile, ProfileError

LEASE_ID = "00000000-0000-0000-0000-0000000000aa"
PLAN_ID = "00000000-0000-0000-0000-0000000000bb"


def _fs(name):
    return {"name": name, "type": "string", "nullable": True, "length": None,
            "precision": None, "scale": None, "domain": []}


class _Binding:
    def __init__(self, name, layer_ref="layer:0", path="C:/data/s.shp"):
        self.name, self.layer_ref, self.path = name, layer_ref, path


class CapabilityAcceptanceGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = OperationCatalog()

    # -- every effect of every capability binds to a production profile ------

    def test_every_declared_effect_of_every_capability_has_a_profile(self):
        unbound = []
        for operation_id, operation in self.catalog.operations.items():
            effects = (operation.get("capability_contract") or {}).get("semantic_effects") or []
            for index, effect in enumerate(effects):
                kind = effect.get("kind") if isinstance(effect, dict) else None
                if kind not in PROFILES:
                    unbound.append("%s.effects[%d]=%s" % (operation_id, index, kind))
        self.assertEqual(unbound, [], "effects with no production profile: %s" % unbound)

    def test_every_capability_validates_via_the_production_binder(self):
        """The production validator binds EVERY effect of EVERY capability that
        declares semantic_effects (the catalog load already enforces this; this
        re-checks it through the shared production function on the real catalog)."""
        from shared_runtime.acceptance_profile import validate_capability_binding, bound_effect_kinds
        checked = 0
        for operation_id, operation in self.catalog.operations.items():
            contract = operation.get("capability_contract")
            effects = (contract or {}).get("semantic_effects") or []
            if not effects:
                continue  # utility op with no acceptance-bound effect
            profiles = validate_capability_binding(contract)
            declared = tuple(e["kind"] for e in effects if isinstance(e, dict))
            self.assertEqual(bound_effect_kinds(profiles), declared,
                             "%s bound profiles differ from declared effects" % operation_id)
            checked += 1
        self.assertGreater(checked, 0, "no capability declared semantic_effects")

    # -- every output-producing capability derives evidence from real schema --

    def _inputs(self, operation):
        return [i["parameter"] for i in (operation.get("capability_contract") or {}).get("inputs") or []
                if isinstance(i, dict) and i.get("parameter")]

    def _output_fields(self, operation):
        fields = (((operation.get("capability_contract") or {}).get("outputs") or {}).get("fields") or {})
        names = []
        for spec in (fields.get("static_fields") or []):
            name = spec.get("name") if isinstance(spec, dict) else spec
            if isinstance(name, str) and name:
                names.append(name)
        return names or ["TYPE"]

    def _predicate_for(self, operation, kind):
        if kind in ("copy", "project", "aggregate", "repair", "add_xy", "field_update"):
            return {"kind": kind, "subject": "output:o", "source": "input:a"}
        if kind == "buffer":
            return {"kind": kind, "subject": "output:o", "source": "input:a",
                    "distance": {"value": 10, "unit": "meters", "dimension": "length",
                                 "tolerance": 1.0, "crs": None}}
        if kind in ("merge",):
            return {"kind": kind, "subject": "output:o", "sources": ["input:a", "input:b"]}
        if kind == "spatial_join":
            return {"kind": kind, "subject": "output:o", "target": "input:a", "join": "input:b"}
        if kind == "overlay":
            method = next((e.get("method", {}).get("const") for e in
                           (operation.get("capability_contract") or {}).get("semantic_effects") or []
                           if isinstance(e, dict) and e.get("kind") == "overlay"), "intersect")
            return {"kind": kind, "subject": "output:o", "method": method,
                    "sources": ["input:a", "input:b"]}
        if kind in ("feature_create",):
            return {"kind": kind, "subject": "output:o", "action": "create"}
        if kind == "artifact_export":
            return {"kind": kind, "subject": "output:o", "action": "export_table",
                    "selected_only": False, "output_format": "csv"}
        if kind in ("define_projection",):
            return {"kind": kind, "subject": "output:o", "target": "input:a", "spatial_reference": "EPSG:4326"}
        return {"kind": kind, "subject": "output:o", "source": "input:a"}

    def test_every_output_producing_capability_derives_evidence(self):
        output_kinds = {"copy", "merge", "aggregate", "spatial_join", "overlay", "buffer",
                        "project", "feature_create", "artifact_export", "define_projection"}
        failures = []
        for operation_id, operation in self.catalog.operations.items():
            effects = (operation.get("capability_contract") or {}).get("semantic_effects") or []
            primary = effects[0]["kind"] if effects and isinstance(effects[0], dict) else None
            if primary not in output_kinds:
                continue
            step = WorkflowStep(id="s", operation=operation_id, arguments={}, reason="r",
                                declared_outputs=(DeclaredOutput(
                                    output_id="output:o", name="o", kind="feature_class",
                                    output_format="gdb", destination_policy="server_derived"),))
            plan = VerifiedPlan(plan_id=PLAN_ID, version=1, intent_digest="i", context_digest="c",
                                capability_digest="k", workflow=(step,),
                                validation_report={"valid": True}, model_identity="m", prompt_version="v")
            inputs = self._inputs(operation)
            bindings = [_Binding("input:a", "layer:0"), _Binding("input:b", "layer:1")]
            # A projected (meter) source so a meters Quantity is natively provable.
            context = ContextSnapshot(
                lease_id=LEASE_ID, arcmap_pid=2000, bridge_pid=2001, bridge_port=8766,
                target_hwnd=3000, document_identity={"mxd": "m"},
                layers=(LayerSnapshot(identity=LayerRef(name="a", layer_ref="layer:0"),
                                      identity_fields=("OBJECTID",), source_content_digest="d",
                                      coordinate_system="EPSG:3395",
                                      crs_type="Projected", meters_per_unit=1.0),
                        LayerSnapshot(identity=LayerRef(name="b", layer_ref="layer:1"),
                                      identity_fields=("OBJECTID",), source_content_digest="d2",
                                      coordinate_system="EPSG:3395",
                                      crs_type="Projected", meters_per_unit=1.0)),
                active_data_frame="Layers", edit_session_state="none", captured_at=1.0,
                deployment_hash="dh", content_hash="ch", view_state={"active_view": "Map", "extent": {}})
            predicate = self._predicate_for(operation, primary)
            required = [_fs(n) for n in self._output_fields(operation)] if primary in ("aggregate", "spatial_join") else []
            task_contract = {
                "input_entities": [], "allowed_side_effects": ["writes_data"], "clarifications": [],
                "outputs": [{"output_id": "output:o", "kind": "feature_class", "name": "o", "format": "gdb",
                             "geometry": "polygon", "spatial_reference": "EPSG:4326",
                             "destination_policy": "server_derived", "required_fields": required, "evidence": "o"}],
                "requirements": [{"requirement_id": "req:r", "predicate": predicate}],
            }
            try:
                derive(task_contract, plan, bindings, context, catalog=self.catalog)
            except AcceptanceContractError as exc:
                failures.append("%s (%s): %s" % (operation_id, primary, exc))
        self.assertEqual(failures, [], "output producers whose evidence is not derivable:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
