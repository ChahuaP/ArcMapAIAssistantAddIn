"""Contract-level tests for the sealed AcceptanceContract (issues #1,#5,#6,#7).

Proves the unified rule ABI: every evaluator input travels inside
``rule["bindings"]`` (nothing the evaluator needs is at the rule top level),
required_fields are full 7-field FieldSpecs from the declared output, identity
fields stay per-dataset, and missing-evidence cases fail closed at seal time.
"""
from __future__ import annotations

import unittest

from gateway_py3.acceptance_contract import derive, AcceptanceContractError
from gateway_py3.kernel.contracts import (
    ContextSnapshot, DeclaredOutput, FieldColumn, LayerRef, LayerSnapshot,
    VerifiedPlan, WorkflowStep,
)

LEASE_ID = "00000000-0000-0000-0000-0000000000aa"
PLAN_ID = "00000000-0000-0000-0000-0000000000bb"


def _fs(name, ftype="string", nullable=True, length=None):
    return {"name": name, "type": ftype, "nullable": nullable, "length": length,
            "precision": None, "scale": None, "domain": []}


def _layer(name, layer_ref, identity_fields, geom_digest=None, raster_digest=None,
           content_digest=None):
    if content_digest is None:
        content_digest = "content-" + name
    return LayerSnapshot(
        identity=LayerRef(name=name, layer_ref=layer_ref,
                          data_source="C:/data/%s.shp" % name, layer_type="Feature Layer"),
        fields=(FieldColumn(name="OBJECTID", dtype="oid"),
                FieldColumn(name="TYPE", dtype="string", length=50)),
        geometry_type="Polygon", coordinate_system="EPSG:4326", selection_count=0,
        identity_fields=tuple(identity_fields), source_content_digest=content_digest,
        feature_manifest_digest=geom_digest, raster_content_digest=raster_digest,
    )


def _context(layers):
    return ContextSnapshot(
        lease_id=LEASE_ID, arcmap_pid=2000, bridge_pid=2001, bridge_port=8766,
        target_hwnd=3000, document_identity={"mxd": "Untitled.mxd"},
        layers=tuple(layers), active_data_frame="Layers", edit_session_state="none",
        captured_at=1.0, deployment_hash="deploy-v1", content_hash="content-hash",
        view_state={"active_view": "Layout", "extent": {}},
    )


def _step(operation, outputs, arguments):
    return WorkflowStep(id="step_1", operation=operation, arguments=arguments,
                        reason="produce", declared_outputs=tuple(outputs))


def _plan(step):
    return VerifiedPlan(plan_id=PLAN_ID, version=1, intent_digest="i", context_digest="c",
                        capability_digest="k", workflow=(step,),
                        validation_report={"valid": True}, model_identity="m", prompt_version="v")


class _Binding:
    def __init__(self, name, layer_ref, path):
        self.name, self.layer_ref, self.path = name, layer_ref, path


class AcceptanceContractAbiTest(unittest.TestCase):
    def _doc(self, kind, predicate, outputs, task_output, layers, bindings, operation="op"):
        step = _step(operation, outputs, {"input_layer": "layer:0", "output_name": "out"})
        plan = _plan(step)
        context = _context(layers)
        task_contract = {
            "input_entities": [],
            "outputs": [task_output] if task_output else [],
            "requirements": [{"requirement_id": "req:r", "predicate": predicate}],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }
        return derive(task_contract, plan, bindings, context)

    def test_aggregate_seals_full_fieldspec_required_fields_inside_bindings(self):
        out = DeclaredOutput(output_id="output:agg", name="agg", kind="feature_class",
                             output_format="gdb", destination_policy="server_derived")
        task_out = {"output_id": "output:agg", "kind": "feature_class", "name": "agg",
                    "format": "gdb", "geometry": "polygon", "spatial_reference": "EPSG:4326",
                    "destination_policy": "server_derived",
                    "required_fields": [_fs("TYPE"), _fs("COUNT", "integer")],
                    "evidence": "agg"}
        doc = self._doc(
            "aggregate",
            {"kind": "aggregate", "subject": "output:agg", "source": "input:roads",
             "dissolve_fields": ["TYPE"]},
            [out], task_out, [_layer("roads", "layer:0", ["OBJECTID"])],
            [_Binding("input:roads", "layer:0", "C:/data/roads.shp")])
        rule = doc["rules"][0]
        # The evaluator reads ONLY bindings — required_fields must be there.
        self.assertNotIn("required_fields", rule)  # not at rule top level
        required = rule["bindings"]["required_fields"]
        self.assertEqual([f["name"] for f in required], ["TYPE", "COUNT"])
        # Full 7-field FieldSpec, not just names.
        self.assertEqual(set(required[0]), {"name", "type", "nullable", "length",
                                            "precision", "scale", "domain"})
        self.assertEqual(rule["bindings"]["aggregate"]["dissolve_fields"], ["TYPE"])
        self.assertEqual(rule["bindings"]["lineage"]["capability_id"], "op")
        self.assertIn("source_list", rule["bindings"])

    def test_identity_fields_are_per_dataset_inside_each_binding(self):
        bindings = [_Binding("input:roads", "layer:0", "C:/data/roads.shp"),
                    _Binding("input:parcels", "layer:1", "C:/data/parcels.shp")]
        # Use an overlay so both sources are sealed distinctly.
        out = DeclaredOutput(output_id="output:o", name="o", kind="feature_class",
                             output_format="gdb", destination_policy="server_derived")
        task_out = {"output_id": "output:o", "kind": "feature_class", "name": "o",
                    "format": "gdb", "geometry": "polygon", "spatial_reference": "EPSG:4326",
                    "destination_policy": "server_derived", "required_fields": [], "evidence": "o"}
        step = _step("analysis.intersect", [out],
                     {"input_layers": ["layer:0", "layer:1"], "output_name": "o"})
        plan = _plan(step)
        context = _context([_layer("roads", "layer:0", ["OBJECTID"]),
                            _layer("parcels", "layer:1", ["PARCEL_ID"])])
        task_contract = {
            "input_entities": [], "outputs": [task_out],
            "requirements": [{"requirement_id": "req:r", "predicate": {
                "kind": "overlay", "subject": "output:o", "method": "intersect",
                "sources": ["input:roads", "input:parcels"]}}],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }
        doc = derive(task_contract, plan, bindings, context)
        sources = doc["rules"][0]["bindings"]["sources"]
        by_ns = {s.get("name"): s for s in sources}
        self.assertEqual(by_ns["roads"]["identity_fields"], ["OBJECTID"])
        self.assertEqual(by_ns["parcels"]["identity_fields"], ["PARCEL_ID"])
        # Per-source field namespace sealed for attribute lineage.
        mapping = doc["rules"][0]["bindings"]["overlay"]["field_mapping"]
        self.assertEqual(len(mapping), 2)
        self.assertNotEqual(mapping[0]["identity_fields"], mapping[1]["identity_fields"])

    def test_source_preserved_manifest_is_inside_bindings(self):
        out = DeclaredOutput(output_id="output:x", name="x", kind="feature_class",
                             output_format="gdb", destination_policy="server_derived")
        task_out = {"output_id": "output:x", "kind": "feature_class", "name": "x",
                    "format": "gdb", "geometry": "polygon", "spatial_reference": "EPSG:4326",
                    "destination_policy": "server_derived", "required_fields": [], "evidence": "x"}
        step = _step("op", [out], {"input_layer": "layer:0", "output_name": "x"})
        plan = _plan(step)
        context = _context([_layer("roads", "layer:0", ["OBJECTID"], geom_digest="geom-v1")])
        task_contract = {
            "input_entities": [], "outputs": [task_out],
            "requirements": [{"requirement_id": "req:r", "predicate": {
                "kind": "source_preserved", "subject": "input:roads"}}],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }
        doc = derive(task_contract, plan, [_Binding("input:roads", "layer:0", "C:/data/roads.shp")], context)
        rule = doc["rules"][0]
        self.assertNotIn("source_manifest", rule)  # inside bindings, not top level
        self.assertEqual(rule["bindings"]["source_manifest"]["feature_manifest_digest"], "geom-v1")

    def test_aggregate_without_output_field_specs_fails_closed(self):
        out = DeclaredOutput(output_id="output:agg", name="agg", kind="feature_class",
                             output_format="gdb", destination_policy="server_derived",
                             expected_fields=())
        task_out = {"output_id": "output:agg", "kind": "feature_class", "name": "agg",
                    "format": "gdb", "geometry": "polygon", "spatial_reference": "EPSG:4326",
                    "destination_policy": "server_derived", "required_fields": [], "evidence": "agg"}
        with self.assertRaises(AcceptanceContractError):
            self._doc(
                "aggregate",
                {"kind": "aggregate", "subject": "output:agg", "source": "input:roads",
                 "dissolve_fields": ["TYPE"]},
                [out], task_out, [_layer("roads", "layer:0", ["OBJECTID"])],
                [_Binding("input:roads", "layer:0", "C:/data/roads.shp")])

    def test_spatial_join_rejects_unsupported_match_option_pre_execution(self):
        out = DeclaredOutput(output_id="output:j", name="j", kind="feature_class",
                             output_format="gdb", destination_policy="server_derived")
        task_out = {"output_id": "output:j", "kind": "feature_class", "name": "j",
                    "format": "gdb", "geometry": "polygon", "spatial_reference": "EPSG:4326",
                    "destination_policy": "server_derived",
                    "required_fields": [_fs("Join_Count", "integer")], "evidence": "j"}
        step = _step("analysis.spatial_join", [out],
                     {"target_layer": "layer:0", "join_layer": "layer:1",
                      "output_name": "j", "match_option": "closest"})
        plan = _plan(step)
        context = _context([_layer("t", "layer:0", ["TID"]), _layer("j", "layer:1", ["JID"])])
        task_contract = {
            "input_entities": [], "outputs": [task_out],
            "requirements": [{"requirement_id": "req:r", "predicate": {
                "kind": "spatial_join", "subject": "output:j", "target": "input:t",
                "join": "input:j"}}],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }
        with self.assertRaises(AcceptanceContractError):
            derive(task_contract, plan,
                   [_Binding("input:t", "layer:0", "C:/data/t.shp"),
                    _Binding("input:j", "layer:1", "C:/data/j.shp")], context)


class BufferCrsSealTest(unittest.TestCase):
    """Buffer/spatial_filter linear Quantity must seal a CRS strategy or reject."""

    def _derive_buffer(self, layer):
        out = DeclaredOutput(output_id="output:b", name="b", kind="feature_class",
                             output_format="gdb", destination_policy="server_derived")
        step = _step("analysis.buffer", [out], {"input_layer": "layer:0",
                                                "distance": {"value": 10, "unit": "meters"},
                                                "output_name": "b"})
        plan = _plan(step)
        context = _context([layer])
        task_contract = {
            "input_entities": [], "outputs": [],
            "requirements": [{"requirement_id": "req:b", "predicate": {
                "kind": "buffer", "subject": "output:b", "source": "input:s",
                "distance": {"value": 10, "unit": "meters", "dimension": "length",
                             "tolerance": 0.0, "crs": None}}}],
            "allowed_side_effects": ["writes_data"], "clarifications": [],
        }
        return derive(task_contract, plan, [_Binding("input:s", "layer:0", "C:/data/s.shp")], context)

    def test_geographic_meters_is_rejected_at_seal(self):
        # meters on a geographic CRS is not independently provable without
        # projection; the seal requires a prior project step (no approximation,
        # no runtime Unresolved).
        geo = LayerSnapshot(
            identity=LayerRef(name="s", layer_ref="layer:0", data_source="C:/data/s.shp"),
            identity_fields=("OBJECTID",), source_content_digest="d",
            coordinate_system="EPSG:4326", crs_type="Geographic")
        with self.assertRaises(AcceptanceContractError):
            self._derive_buffer(geo)

    def test_projected_meters_seals_native_strategy_with_unit(self):
        projected = LayerSnapshot(
            identity=LayerRef(name="s", layer_ref="layer:0", data_source="C:/data/s.shp"),
            identity_fields=("OBJECTID",), source_content_digest="d",
            coordinate_system="EPSG:3395 World Mercator",
            crs_type="Projected", meters_per_unit=1.0)
        doc = self._derive_buffer(projected)
        strategy = doc["rules"][0]["bindings"]["crs_strategy"]
        self.assertEqual(strategy["mode"], "native_projected")
        self.assertEqual(strategy["buffer_value"], 10.0)  # meters / meters_per_unit(1.0)

    def test_projected_foot_seals_converted_native_strategy(self):
        # A foot-based projection: meters_per_unit=0.3048, so 10 m -> ~32.8 ft.
        foot = LayerSnapshot(
            identity=LayerRef(name="s", layer_ref="layer:0", data_source="C:/data/s.shp"),
            identity_fields=("OBJECTID",), source_content_digest="d",
            coordinate_system="NAD_1983_StatePlane", crs_type="Projected",
            meters_per_unit=0.3048)
        doc = self._derive_buffer(foot)
        strategy = doc["rules"][0]["bindings"]["crs_strategy"]
        self.assertEqual(strategy["mode"], "native_projected")
        self.assertAlmostEqual(strategy["buffer_value"], 10.0 / 0.3048, places=4)

    def test_projected_unknown_unit_is_rejected_at_seal(self):
        unknown_unit = LayerSnapshot(
            identity=LayerRef(name="s", layer_ref="layer:0", data_source="C:/data/s.shp"),
            identity_fields=("OBJECTID",), source_content_digest="d",
            coordinate_system="Mystery CRS", crs_type="Projected", meters_per_unit=None)
        with self.assertRaises(AcceptanceContractError):
            self._derive_buffer(unknown_unit)

    def test_missing_crs_type_is_rejected_at_seal_not_runtime(self):
        no_crs = LayerSnapshot(
            identity=LayerRef(name="s", layer_ref="layer:0", data_source="C:/data/s.shp"),
            identity_fields=("OBJECTID",), source_content_digest="d",
            coordinate_system="EPSG:4326", crs_type=None)
        with self.assertRaises(AcceptanceContractError):
            self._derive_buffer(no_crs)


class ContextPayloadCrsTest(unittest.TestCase):
    """The runtime-verified CRS facts must traverse the real payload → seal path."""

    def _payload_layer(self, crs_type, meters_per_unit):
        return {
            "name": "s", "layer_ref": "layer:0", "long_name": "s", "visible": True,
            "data_source": "C:/data/s.shp", "layer_type": "Feature Layer",
            "fields": [{"name": "OBJECTID", "type": "oid"}, {"name": "TYPE", "type": "String"}],
            "geometry_type": "Polygon", "spatial_reference": "EPSG:3395",
            "selected_count": 0, "selection_hash": "x",
            "crs_type": crs_type, "meters_per_unit": meters_per_unit,
        }

    def test_coordinator_callback_carries_crs_facts(self):
        from gateway_py3.kernel.coordinator import _layer_from_context_payload
        snapshot = _layer_from_context_payload(self._payload_layer("Projected", 1.0))
        self.assertEqual(snapshot.crs_type, "Projected")
        self.assertEqual(snapshot.meters_per_unit, 1.0)

    def test_context_provider_carries_crs_facts(self):
        from gateway_py3.runtime.context_provider import _field_column, _snapshot_to_context_data
        snap = LayerSnapshot(
            identity=LayerRef(name="s", layer_ref="layer:0"), coordinate_system="EPSG:3395",
            crs_type="Projected", meters_per_unit=1.0)
        data = _snapshot_to_context_data(_context_for_snapshot(snap))
        layer = data["layers"][0]
        self.assertEqual(layer["crs_type"], "Projected")
        self.assertEqual(layer["meters_per_unit"], 1.0)


class SpatialFilterDistanceSealTest(unittest.TestCase):
    """within_a_distance uses the SAME production CRS strategy as buffer."""

    def test_geographic_within_a_distance_meters_is_rejected_at_seal(self):
        out = DeclaredOutput(output_id="output:o", name="o", kind="map_state",
                             output_format="not_applicable", destination_policy="not_applicable")
        geo = LayerSnapshot(identity=LayerRef(name="s", layer_ref="layer:0", data_source="C:/data/s.shp"),
                            identity_fields=("OBJECTID",), source_content_digest="d",
                            coordinate_system="EPSG:4326", crs_type="Geographic")
        context = ContextSnapshot(lease_id=LEASE_ID, arcmap_pid=2000, bridge_pid=2001,
                                  bridge_port=8766, target_hwnd=3000,
                                  document_identity={"mxd": "m"}, layers=(geo,),
                                  active_data_frame="Layers", edit_session_state="none",
                                  captured_at=1.0, deployment_hash="dh", content_hash="ch",
                                  view_state={"active_view": "Map", "extent": {}})
        task_contract = {
            "input_entities": [], "outputs": [],
            "requirements": [{"requirement_id": "req:f", "predicate": {
                "kind": "spatial_filter", "subject": "input:s", "target": "input:s",
                "selector": "input:s", "overlap_type": "within_a_distance",
                "selection_type": "new_selection",
                "search_distance": {"value": 50, "unit": "meters", "dimension": "length",
                                    "tolerance": 0.0, "crs": None}}}],
            "allowed_side_effects": ["changes_map"], "clarifications": [],
        }
        with self.assertRaises(AcceptanceContractError):
            derive(task_contract, _plan(_step("op", [], {})),
                   [_Binding("input:s", "layer:0", "C:/data/s.shp")], context)


def _context_for_snapshot(layer):
    return ContextSnapshot(
        lease_id=LEASE_ID, arcmap_pid=2000, bridge_pid=2001, bridge_port=8766,
        target_hwnd=3000, document_identity={"mxd": "m"}, layers=(layer,),
        active_data_frame="Layers", edit_session_state="none", captured_at=1.0,
        deployment_hash="deploy-v1", content_hash="content-hash",
        view_state={"active_view": "Layout", "extent": {}},
    )


class ProducingStepFailClosedTest(unittest.TestCase):
    """Non-output state-changing effects MUST uniquely bind to a producing step
    whose real arguments reference the predicate's bound dataset entities.
    Zero or multiple candidates → AcceptanceContractError (fail closed).
    Lineage.input_ids must be non-empty and exact."""

    @classmethod
    def setUpClass(cls):
        from gateway_py3.catalog_loader import OperationCatalog
        cls.catalog = OperationCatalog()

    def _context(self):
        return _context([_layer("roads", "layer:0", ["OBJECTID"]),
                         _layer("new", "layer:1", ["OBJECTID"]),
                         _layer("other", "layer:2", ["OBJECTID"])])

    def _bindings(self):
        return [_Binding("input:roads", "layer:0", "C:/data/roads.shp"),
                _Binding("input:new", "layer:1", "C:/data/new.shp"),
                _Binding("input:other", "layer:2", "C:/data/other.shp")]

    def _task_contract(self, predicate):
        return {
            "input_entities": [
                {"entity_id": "input:roads", "role": "target", "kind": "feature_class",
                 "reference": "layer:0", "evidence": "roads"},
                {"entity_id": "input:new", "role": "source", "kind": "feature_class",
                 "reference": "layer:1", "evidence": "new"},
                {"entity_id": "input:other", "role": "context", "kind": "feature_class",
                 "reference": "layer:2", "evidence": "other"},
            ],
            "outputs": [],
            "requirements": [{"requirement_id": "req:r", "predicate": predicate}],
            "allowed_side_effects": ["edits_data"], "clarifications": [],
        }

    def _plan(self, steps):
        return VerifiedPlan(plan_id=PLAN_ID, version=1, intent_digest="i",
                            context_digest="c", capability_digest="k",
                            workflow=tuple(steps),
                            validation_report={"valid": True},
                            model_identity="m", prompt_version="v")

    # -- append: exact lineage equals source+target entity ids --------------

    def test_single_append_step_seals_exact_lineage(self):
        step = WorkflowStep(id="s0", operation="analysis.append",
                            arguments={"input_layers": ["layer:1"], "target_layer": "layer:0",
                                       "schema_type": "NO_TEST"},
                            reason="append new to roads")
        predicate = {"kind": "append", "subject": "input:roads",
                     "target": "input:roads", "sources": ["input:new"]}
        doc = derive(self._task_contract(predicate), self._plan([step]),
                     self._bindings(), self._context(), catalog=self.catalog)
        lineage = doc["rules"][0]["bindings"]["lineage"]
        self.assertEqual(lineage["capability_id"], "analysis.append")
        self.assertEqual(sorted(lineage["input_ids"]), ["input:new", "input:roads"])

    def test_append_step_with_extra_layer_does_not_pollute_lineage(self):
        # Step arguments include a reference to a third, unrelated layer —
        # lineage.input_ids must NOT include it (predicate-role-driven, not
        # argument-scanned).
        step = WorkflowStep(id="s0", operation="analysis.append",
                            arguments={"input_layers": ["layer:1"], "target_layer": "layer:0",
                                       "schema_type": "NO_TEST"},
                            reason="append")
        predicate = {"kind": "append", "subject": "input:roads",
                     "target": "input:roads", "sources": ["input:new"]}
        doc = derive(self._task_contract(predicate), self._plan([step]),
                     self._bindings(), self._context(), catalog=self.catalog)
        self.assertEqual(sorted(doc["rules"][0]["bindings"]["lineage"]["input_ids"]),
                         ["input:new", "input:roads"])

    def test_zero_append_step_is_rejected(self):
        step = WorkflowStep(id="s0", operation="analysis.buffer",
                            arguments={"input_layer": "layer:0", "distance": {"value": 10, "unit": "meters"},
                                       "output_name": "b"}, reason="buffer")
        predicate = {"kind": "append", "subject": "input:roads",
                     "target": "input:roads", "sources": ["input:new"]}
        with self.assertRaises(AcceptanceContractError):
            derive(self._task_contract(predicate), self._plan([step]),
                   self._bindings(), self._context(), catalog=self.catalog)

    def test_multiple_append_steps_same_target_is_rejected(self):
        s0 = WorkflowStep(id="s0", operation="analysis.append",
                          arguments={"input_layers": ["layer:1"], "target_layer": "layer:0",
                                     "schema_type": "NO_TEST"}, reason="r")
        s1 = WorkflowStep(id="s1", operation="analysis.append",
                          arguments={"input_layers": ["layer:1"], "target_layer": "layer:0",
                                     "schema_type": "NO_TEST"}, reason="r")
        predicate = {"kind": "append", "subject": "input:roads",
                     "target": "input:roads", "sources": ["input:new"]}
        with self.assertRaises(AcceptanceContractError):
            derive(self._task_contract(predicate), self._plan([s0, s1]),
                   self._bindings(), self._context(), catalog=self.catalog)

    def test_same_effect_different_target_unambiguous(self):
        s0 = WorkflowStep(id="s0", operation="analysis.append",
                          arguments={"input_layers": ["layer:1"], "target_layer": "layer:0",
                                     "schema_type": "NO_TEST"}, reason="r")
        s1 = WorkflowStep(id="s1", operation="analysis.append",
                          arguments={"input_layers": ["layer:1"], "target_layer": "layer:1",
                                     "schema_type": "NO_TEST"}, reason="r")
        predicate = {"kind": "append", "subject": "input:roads",
                     "target": "input:roads", "sources": ["input:new"]}
        doc = derive(self._task_contract(predicate), self._plan([s0, s1]),
                     self._bindings(), self._context(), catalog=self.catalog)
        self.assertEqual(doc["rules"][0]["bindings"]["lineage"]["capability_id"], "analysis.append")

    # -- field_update: real catalog param 'layer', exact target lineage -----

    def test_field_update_single_step_seals_lineage(self):
        step = WorkflowStep(id="s0", operation="table.update_rows",
                            arguments={"layer": "layer:0", "where": {"op": "is_not_null", "field": "TYPE"},
                                       "assignments": {"TYPE": "done"}}, reason="update")
        predicate = {"kind": "field_update", "subject": "input:roads",
                     "target": "input:roads",
                     "where": {"op": "is_not_null", "field": "TYPE"},
                     "assignments": {"TYPE": "done"}}
        doc = derive(self._task_contract(predicate), self._plan([step]),
                     self._bindings(), self._context(), catalog=self.catalog)
        lineage = doc["rules"][0]["bindings"]["lineage"]
        self.assertEqual(lineage["input_ids"], ["input:roads"])

    def test_field_update_step_with_wrong_target_is_rejected(self):
        step = WorkflowStep(id="s0", operation="table.update_rows",
                            arguments={"layer": "layer:1", "where": {}, "assignments": {}}, reason="wrong")
        predicate = {"kind": "field_update", "subject": "input:roads",
                     "target": "input:roads",
                     "where": {"op": "is_not_null", "field": "TYPE"},
                     "assignments": {"TYPE": "done"}}
        with self.assertRaises(AcceptanceContractError):
            derive(self._task_contract(predicate), self._plan([step]),
                   self._bindings(), self._context(), catalog=self.catalog)


    def test_append_step_with_extra_layer_in_input_layers_is_rejected(self):
        """Step's input_layers contains layer:2 but predicate sources only has
        input:new (→ layer:1).  Exact matching must reject the extra ref."""
        step = WorkflowStep(id="s0", operation="analysis.append",
                            arguments={"input_layers": ["layer:1", "layer:2"],
                                       "target_layer": "layer:0", "schema_type": "NO_TEST"},
                            reason="append with extra layer")
        predicate = {"kind": "append", "subject": "input:roads",
                     "target": "input:roads", "sources": ["input:new"]}
        with self.assertRaises(AcceptanceContractError):
            derive(self._task_contract(predicate), self._plan([step]),
                   self._bindings(), self._context(), catalog=self.catalog)

    def test_bijective_violation_two_entities_same_ref_is_rejected(self):
        """Two different predicate entity ids that resolve to the same layer ref
        must be rejected (not silently collapsed by set)."""
        step = WorkflowStep(id="s0", operation="analysis.append",
                            arguments={"input_layers": ["layer:1"], "target_layer": "layer:0",
                                       "schema_type": "NO_TEST"}, reason="r")
        # input:new → layer:1 AND input:dup → layer:1 (same ref)
        tc = self._task_contract({"kind": "append", "subject": "input:roads",
                                  "target": "input:roads",
                                  "sources": ["input:new", "input:dup"]})
        tc["input_entities"].append(
            {"entity_id": "input:dup", "role": "source", "kind": "feature_class",
             "reference": "layer:1", "evidence": "dup"})  # same ref as input:new
        with self.assertRaises(AcceptanceContractError):
            derive(tc, self._plan([step]),
                   self._bindings() + [_Binding("input:dup", "layer:1", "C:/data/dup.shp")],
                   self._context(), catalog=self.catalog)

    def test_duplicate_predicate_source_entity_is_rejected(self):
        """Predicate sources has a duplicate entity id → exact match must reject."""
        step = WorkflowStep(id="s0", operation="analysis.append",
                            arguments={"input_layers": ["layer:1"], "target_layer": "layer:0",
                                       "schema_type": "NO_TEST"}, reason="r")
        predicate = {"kind": "append", "subject": "input:roads",
                     "target": "input:roads", "sources": ["input:new", "input:new"]}
        with self.assertRaises(AcceptanceContractError):
            derive(self._task_contract(predicate), self._plan([step]),
                   self._bindings(), self._context(), catalog=self.catalog)

    def test_duplicate_step_array_reference_is_rejected(self):
        """Step input_layers has a duplicate reference → exact match must reject."""
        step = WorkflowStep(id="s0", operation="analysis.append",
                            arguments={"input_layers": ["layer:1", "layer:1"],
                                       "target_layer": "layer:0", "schema_type": "NO_TEST"},
                            reason="r")
        predicate = {"kind": "append", "subject": "input:roads",
                     "target": "input:roads", "sources": ["input:new"]}
        with self.assertRaises(AcceptanceContractError):
            derive(self._task_contract(predicate), self._plan([step]),
                   self._bindings(), self._context(), catalog=self.catalog)

    def test_scalar_target_given_as_list_is_rejected(self):
        """Schema/cardinality gate: target_layer is schema type 'string'; giving
        a list predicate value must not match (type mismatch)."""
        step = WorkflowStep(id="s0", operation="analysis.append",
                            arguments={"input_layers": ["layer:1"], "target_layer": "layer:0",
                                       "schema_type": "NO_TEST"}, reason="r")
        predicate = {"kind": "append", "subject": "input:roads",
                     "target": ["input:roads"],  # list where string expected
                     "sources": ["input:new"]}
        with self.assertRaises(AcceptanceContractError):
            derive(self._task_contract(predicate), self._plan([step]),
                   self._bindings(), self._context(), catalog=self.catalog)

    def test_sources_given_as_scalar_is_rejected(self):
        """Schema/cardinality gate: input_layers is schema type 'array'; giving
        a scalar predicate value must not match."""
        step = WorkflowStep(id="s0", operation="analysis.append",
                            arguments={"input_layers": ["layer:1"], "target_layer": "layer:0",
                                       "schema_type": "NO_TEST"}, reason="r")
        predicate = {"kind": "append", "subject": "input:roads",
                     "target": "input:roads",
                     "sources": "input:new"}  # scalar where array expected
        with self.assertRaises(AcceptanceContractError):
            derive(self._task_contract(predicate), self._plan([step]),
                   self._bindings(), self._context(), catalog=self.catalog)


class CatalogGateNegativeTest(unittest.TestCase):
    """Direct tests for the catalog-load consistency gate."""

    def _layer_param(self, t="string"):
        if t == "string":
            return {"type": "string", "x-geopilot-kind": "layer"}
        return {"type": "array", "items": {"type": "string"}, "minItems": 1,
                "x-geopilot-kind": "layer"}

    def test_requires_output_true_with_kind_none_is_rejected(self):
        """buffer (requires_output=True) on an in-place capability → ProfileError.
        The contract is otherwise valid (real source binding + distance schema);
        the ONLY defect is the output/kind contradiction.

        First proves the effect passes the semantic-domain ingress validator
        with a valid output kind, so the assertion cannot pass for an
        incidental reason."""
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        from gateway_py3.semantic_domain import validate_capability_effect
        effect = {
            "kind": "buffer",
            "source": {"parameter": "input_layer"},
            "distance": {"parameter": "distance"},
            "result": {"output": True},
        }
        params = {"properties": {
            "input_layer": {"type": "string", "x-geopilot-kind": "layer"},
            "distance": {"type": "object", "x-geopilot-semantic": "quantity",
                         "properties": {"value": {"type": "number"}, "unit": {"type": "string"},
                                        "dimension": {"const": "length"},
                                        "tolerance": {"type": "number"},
                                        "crs": {"type": ["string", "null"]}},
                         "required": ["value", "unit", "dimension", "tolerance", "crs"]},
        }}
        # Step 1: the effect is valid under the ingress validator with feature output.
        validate_capability_effect(effect, params, "feature_class", "test", ValueError)
        # Step 2: the same effect under outputs.kind=none → contradiction.
        bad = {
            "side_effects": "writes_data",
            "outputs": {"kind": "none"},
            "semantic_effects": [effect],
            "parameters_schema": params,
        }
        with self.assertRaisesRegex(ProfileError, "requires_output.*outputs\\.kind is none"):
            validate_capability_binding(bad)

    def test_singular_role_binds_array_schema_is_rejected(self):
        """A singular role (target) must not bind an array parameter."""
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data", "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append",
                                  "sources": [{"parameter": "input_layers"}],
                                  "target": {"parameter": "target_layers"}}],
            "parameters_schema": {"properties": {
                "input_layers": self._layer_param("array"),
                "target_layers": self._layer_param("array")}},  # array, not string
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_state_changing_without_producing_step_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "inspect"}],
            "parameters_schema": {"properties": {}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_non_canonical_sources_dict_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append", "sources": {"parameter": "input_layers"},
                                  "target": {"parameter": "target_layer"}}],
            "parameters_schema": {"properties": {
                "input_layers": self._layer_param("array"),
                "target_layer": self._layer_param()}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_nonexistent_parameter_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append",
                                  "sources": [{"parameter": "input_layers"}],
                                  "target": {"parameter": "ghost_param"}}],
            "parameters_schema": {"properties": {"input_layers": self._layer_param("array")}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_wrong_scalar_type_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append",
                                  "sources": [{"parameter": "input_layers"}],
                                  "target": {"parameter": "target_layer"}}],
            "parameters_schema": {"properties": {
                "input_layers": self._layer_param("array"),
                "target_layer": {"type": "integer"}}},  # not a string layer
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_wrong_array_items_type_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append",
                                  "sources": [{"parameter": "input_layers"}],
                                  "target": {"parameter": "target_layer"}}],
            "parameters_schema": {"properties": {
                "input_layers": {"type": "array", "items": {"type": "integer"},
                                 "x-geopilot-kind": "layer"},
                "target_layer": self._layer_param()}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_missing_layer_kind_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append",
                                  "sources": [{"parameter": "input_layers"}],
                                  "target": {"parameter": "target_layer"}}],
            "parameters_schema": {"properties": {
                "input_layers": self._layer_param("array"),
                "target_layer": {"type": "string"}}},  # missing x-geopilot-kind
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_duplicate_binding_parameter_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append",
                                  "sources": [{"parameter": "input_layers"},
                                              {"parameter": "input_layers"}],
                                  "target": {"parameter": "target_layer"}}],
            "parameters_schema": {"properties": {
                "input_layers": self._layer_param("array"),
                "target_layer": self._layer_param()}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_malformed_binding_extra_key_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data", "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append",
                                  "sources": [{"parameter": "input_layers"}],
                                  "target": {"parameter": "target_layer", "extra": "bad"}}],
            "parameters_schema": {"properties": {
                "input_layers": self._layer_param("array"),
                "target_layer": self._layer_param()}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_empty_parameter_name_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data", "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append",
                                  "sources": [{"parameter": "input_layers"}],
                                  "target": {"parameter": ""}}],
            "parameters_schema": {"properties": {
                "input_layers": self._layer_param("array"),
                "target_layer": self._layer_param()}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_absent_min_items_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data", "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append",
                                  "sources": [{"parameter": "input_layers"}],
                                  "target": {"parameter": "target_layer"}}],
            "parameters_schema": {"properties": {
                "input_layers": {"type": "array", "items": {"type": "string"},
                                 "x-geopilot-kind": "layer"},  # no minItems
                "target_layer": self._layer_param()}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)

    def test_boolean_min_items_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "edits_data", "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "append",
                                  "sources": [{"parameter": "input_layers"}],
                                  "target": {"parameter": "target_layer"}}],
            "parameters_schema": {"properties": {
                "input_layers": {"type": "array", "items": {"type": "string"},
                                 "minItems": True, "x-geopilot-kind": "layer"},
                "target_layer": self._layer_param()}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)


class SemanticDomainSourcesGateTest(unittest.TestCase):
    """validate_capability_effect rejects dict sources at the ingress (#1)."""

    def test_dict_sources_is_rejected(self):
        from gateway_py3.semantic_domain import validate_capability_effect
        effect = {"kind": "append", "sources": {"parameter": "input_layers"},
                  "target": {"parameter": "target_layer"}}
        params = {"properties": {"input_layers": {"type": "array", "items": {"type": "string"},
                                                   "minItems": 1, "x-geopilot-kind": "layer"},
                                  "target_layer": {"type": "string", "x-geopilot-kind": "layer"}}}
        with self.assertRaises(ValueError):
            validate_capability_effect(effect, params, "feature_class", "test", ValueError)


class AllSingularRolesArrayRejectionTest(unittest.TestCase):
    """Every required singular role must reject an array layer schema at catalog load."""

    def _array(self):
        return {"type": "array", "items": {"type": "string"}, "minItems": 1,
                "x-geopilot-kind": "layer"}

    def _scalar(self):
        return {"type": "string", "x-geopilot-kind": "layer"}

    def _reject(self, kind, role_to_break, break_param, valid_roles=None):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        effect = {"kind": kind, role_to_break: {"parameter": break_param}}
        props = {break_param: self._array()}
        if valid_roles:
            for r, p in valid_roles.items():
                effect[r] = {"parameter": p}
                props[p] = self._scalar() if kind != "append" or r != "sources" else \
                    {"type": "array", "items": {"type": "string"}, "minItems": 1,
                     "x-geopilot-kind": "layer"}
        output_kind = "feature_class"
        if kind in ("append", "field_update", "define_projection"):
            output_kind = "none"
        contract = {
            "side_effects": "edits_data" if output_kind == "none" else "writes_data",
            "outputs": {"kind": output_kind},
            "semantic_effects": [effect],
            "parameters_schema": {"properties": props},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(contract)

    def test_source_on_buffer(self):
        self._reject("buffer", "source", "input_layer")

    def test_target_on_append(self):
        self._reject("append", "target", "target_layer",
                     valid_roles={"sources": "input_layers"})

    def test_target_on_define_projection(self):
        self._reject("define_projection", "target", "layer")

    def test_selector_on_spatial_filter(self):
        self._reject("spatial_filter", "selector", "select_layer",
                     valid_roles={"target": "target_layer"})

    def test_join_on_spatial_join(self):
        self._reject("spatial_join", "join", "join_layer",
                     valid_roles={"target": "target_layer"})


class InspectOptionalRolesTest(unittest.TestCase):
    """Inspect has optional target; map/output variant without it is accepted;
    dataset variant with array target is rejected."""

    def _array(self):
        return {"type": "array", "items": {"type": "string"}, "minItems": 1,
                "x-geopilot-kind": "layer"}

    def test_map_output_inspect_without_target_is_accepted(self):
        from shared_runtime.acceptance_profile import validate_capability_binding
        ok = {
            "side_effects": "read_only",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "inspect"}],
            "parameters_schema": {"properties": {}},
        }
        profiles = validate_capability_binding(ok)
        self.assertEqual(profiles[0].effect, "inspect")

    def test_dataset_inspect_with_array_target_is_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "read_only",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "inspect", "target": {"parameter": "layer"}}],
            "parameters_schema": {"properties": {"layer": self._array()}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)


class DirectMatcherNegativeTest(unittest.TestCase):
    """Direct tests for _match_step_by_effect against production-like catalog stubs."""

    def _scalar_layer(self):
        return {"type": "string", "x-geopilot-kind": "layer"}

    def _array_layer(self):
        return {"type": "array", "items": {"type": "string"}, "minItems": 1,
                "x-geopilot-kind": "layer"}

    def _step(self, args):
        from gateway_py3.kernel.contracts import WorkflowStep
        return WorkflowStep(id="s0", operation="stub", arguments=args, reason="r")

    def test_singular_extra_key_binding_rejected(self):
        from gateway_py3.acceptance_contract import _match_step_by_effect
        semantic_effect = {"kind": "stub", "target": {"parameter": "layer", "extra": "bad"}}
        result = _match_step_by_effect(self._step({"layer": "layer:0"}), semantic_effect,
                                       {"target": "e:0"}, {"e:0": "layer:0"}, ("target",),
                                       {"layer": self._scalar_layer()})
        self.assertFalse(result)

    def test_singular_array_schema_rejected(self):
        from gateway_py3.acceptance_contract import _match_step_by_effect
        semantic_effect = {"kind": "stub", "target": {"parameter": "layers"}}
        result = _match_step_by_effect(self._step({"layers": ["layer:0"]}), semantic_effect,
                                       {"target": "e:0"}, {"e:0": "layer:0"}, ("target",),
                                       {"layers": self._array_layer()})
        self.assertFalse(result)

    def test_sources_invalid_layer_kind_rejected(self):
        from gateway_py3.acceptance_contract import _match_step_by_effect
        semantic_effect = {"kind": "stub", "sources": [{"parameter": "inputs"}]}
        result = _match_step_by_effect(self._step({"inputs": ["layer:0"]}), semantic_effect,
                                       {"sources": ["e:0"]}, {"e:0": "layer:0"}, ("sources",),
                                       {"inputs": {"type": "array", "items": {"type": "string"},
                                                   "minItems": 1, "x-geopilot-kind": "not_layer"}})
        self.assertFalse(result)

    def test_sources_boolean_minItems_rejected(self):
        from gateway_py3.acceptance_contract import _match_step_by_effect
        semantic_effect = {"kind": "stub", "sources": [{"parameter": "inputs"}]}
        result = _match_step_by_effect(self._step({"inputs": ["layer:0"]}), semantic_effect,
                                       {"sources": ["e:0"]}, {"e:0": "layer:0"}, ("sources",),
                                       {"inputs": {"type": "array", "items": {"type": "string"},
                                                   "minItems": True, "x-geopilot-kind": "layer"}})
        self.assertFalse(result)

    def test_sources_zero_minItems_rejected(self):
        from gateway_py3.acceptance_contract import _match_step_by_effect
        semantic_effect = {"kind": "stub", "sources": [{"parameter": "inputs"}]}
        result = _match_step_by_effect(self._step({"inputs": ["layer:0"]}), semantic_effect,
                                       {"sources": ["e:0"]}, {"e:0": "layer:0"}, ("sources",),
                                       {"inputs": {"type": "array", "items": {"type": "string"},
                                                   "minItems": 0, "x-geopilot-kind": "layer"}})
        self.assertFalse(result)

    def test_duplicate_sources_param_names_rejected(self):
        from gateway_py3.acceptance_contract import _match_step_by_effect
        semantic_effect = {"kind": "stub",
                           "sources": [{"parameter": "inputs"}, {"parameter": "inputs"}]}
        result = _match_step_by_effect(self._step({"inputs": ["layer:0"]}), semantic_effect,
                                       {"sources": ["e:0"]}, {"e:0": "layer:0"}, ("sources",),
                                       {"inputs": self._array_layer()})
        self.assertFalse(result)


class AcceptanceProfileConstructorTest(unittest.TestCase):
    """The profile constructor enforces closed, disjoint role invariants."""

    def test_valid_construction(self):
        from shared_runtime.acceptance_profile import AcceptanceProfile
        p = AcceptanceProfile("test", "test", True, roles=("source",),
                              optional_roles=("target",))
        self.assertEqual(p.roles, ("source",))
        self.assertEqual(p.optional_roles, ("target",))

    def test_unknown_role_rejected(self):
        from shared_runtime.acceptance_profile import AcceptanceProfile, ProfileError
        with self.assertRaises(ProfileError):
            AcceptanceProfile("test", "test", True, roles=("unknown_role",))

    def test_duplicate_required_role_rejected(self):
        from shared_runtime.acceptance_profile import AcceptanceProfile, ProfileError
        with self.assertRaises(ProfileError):
            AcceptanceProfile("test", "test", True, roles=("source", "source"))

    def test_duplicate_optional_role_rejected(self):
        from shared_runtime.acceptance_profile import AcceptanceProfile, ProfileError
        with self.assertRaises(ProfileError):
            AcceptanceProfile("test", "test", True, roles=("source",),
                              optional_roles=("target", "target"))

    def test_required_optional_overlap_rejected(self):
        from shared_runtime.acceptance_profile import AcceptanceProfile, ProfileError
        with self.assertRaises(ProfileError):
            AcceptanceProfile("test", "test", True, roles=("target",),
                              optional_roles=("target",))


class SourcePreservedSubjectRoleTest(unittest.TestCase):
    """source_preserved is a real profile with required singular role 'subject'.
    Array-shaped layer parameter must be rejected; scalar accepted."""

    def _array(self):
        return {"type": "array", "items": {"type": "string"}, "minItems": 1,
                "x-geopilot-kind": "layer"}

    def _scalar(self):
        return {"type": "string", "x-geopilot-kind": "layer"}

    def test_scalar_subject_accepted(self):
        from shared_runtime.acceptance_profile import validate_capability_binding
        ok = {
            "side_effects": "read_only",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "source_preserved",
                                  "subject": {"parameter": "layer"}}],
            "parameters_schema": {"properties": {"layer": self._scalar()}},
        }
        profiles = validate_capability_binding(ok)
        self.assertEqual(profiles[0].effect, "source_preserved")

    def test_array_subject_rejected(self):
        from shared_runtime.acceptance_profile import validate_capability_binding, ProfileError
        bad = {
            "side_effects": "read_only",
            "outputs": {"kind": "none"},
            "semantic_effects": [{"kind": "source_preserved",
                                  "subject": {"parameter": "layers"}}],
            "parameters_schema": {"properties": {"layers": self._array()}},
        }
        with self.assertRaises(ProfileError):
            validate_capability_binding(bad)


if __name__ == "__main__":
    unittest.main()
