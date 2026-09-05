"""Boundary server unit tests: catalog closure, three-state pre-checks,
journal round-trips and callback fencing. No bridge, no model, no ArcMap."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pathlib import Path

from server.catalog import Catalog
from server import codegen
from server.journal import OpJournal
from server.precheck import check
from server.precheck import coerce_arguments as precheck_coerce
from server.session import BridgeUnavailable, BridgeSession, LeaseFenceError


def _layer_op_card():
    catalog = Catalog()
    for operation_id in catalog.operation_ids():
        card = catalog.get(operation_id)
        properties = card.get("parameters_schema", {}).get("properties", {})
        if (any((p or {}).get("x-geopilot-kind") == "layer" for p in properties.values())
                and catalog.required_parameters(card)):
            return card
    raise AssertionError("catalog has no layer-parameter operation")


class CatalogTests(unittest.TestCase):
    def test_registry_closure_over_all_packs(self):
        catalog = Catalog()
        self.assertEqual(len(catalog.operation_ids()), 55)  # export_map_png removed

    def test_contract_projection_carries_closed_contract(self):
        catalog = Catalog()
        contract = catalog.contract("analysis.buffer")
        self.assertIn("parameters_schema", contract)
        self.assertIn("postconditions", contract)
        self.assertTrue(contract["postconditions"], "buffer must carry postconditions")

    def test_side_effect_levels_span_the_map(self):
        catalog = Catalog()
        levels = {catalog.side_effect_level(catalog.get(oid))
                  for oid in catalog.operation_ids()}
        self.assertEqual(levels, {1, 2, 3, 4})


class PrecheckTests(unittest.TestCase):
    def setUp(self):
        self.card = _layer_op_card()
        self.context = {"layers": [{"layer_ref": "layer:0", "name": "建筑物"}]}

    def test_missing_required_is_unresolved_with_askable_obligations(self):
        verdict = check(self.card, {}, self.context)
        self.assertEqual(verdict["status"], "unresolved")
        self.assertTrue(verdict["obligations"])
        for obligation in verdict["obligations"]:
            self.assertIn("question", obligation)

    def test_foreign_layer_reference_is_violated(self):
        arguments = self._filled_arguments(layer_override="不存在的图层")
        verdict = check(self.card, arguments, self.context)
        self.assertEqual(verdict["status"], "violated")

    def test_wrong_type_is_violated(self):
        arguments = self._filled_arguments(type_override=True)
        verdict = check(self.card, arguments, self.context)
        self.assertEqual(verdict["status"], "violated")

    def _filled_arguments(self, layer_override=None, type_override=False):
        schema = self.card["parameters_schema"]
        arguments = {}
        for name in schema.get("required", []):
            property_schema = schema["properties"][name]
            if property_schema.get("x-geopilot-kind") == "layer":
                arguments[name] = (layer_override if isinstance(layer_override, str)
                                   else "建筑物")
            elif type_override:
                arguments[name] = "不是该类型"
            else:
                arguments[name] = {"number": 10, "integer": 1, "boolean": True,
                                   "string": "值", "array": [], "object": {}}.get(
                                       property_schema.get("type", "string"), "值")
        return arguments


class JournalTests(unittest.TestCase):
    def test_op_lifecycle_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = OpJournal(Path(tmp) / "j.db")
            op_id = journal.start_op("run_operation", "analysis.buffer",
                                     {"distance": 10}, {"status": "proven"})
            journal.bind_run(op_id, "run-1", "digest", "lease-1", 2, "hash-1")
            journal.finish_op(op_id, "executed", {"summary": "ok"})
            journal.event(op_id, "verify", {"passed": True})
            row = journal.get_op(op_id)
            self.assertEqual(row["receipt_status"], "executed")
            self.assertEqual(row["run_id"], "run-1")
            self.assertEqual(row["arguments"], {"distance": 10})
            self.assertEqual(journal.recent(5)[0]["op_id"], op_id)
            journal.close()


class SessionFencingTests(unittest.TestCase):
    """Fencing without a bridge: callbacks route through waiters only when
    the lease triple matches exactly."""

    def _record_with_run(self):
        session = BridgeSession()
        run_id = "11111111-1111-1111-1111-111111111111"
        record = type("Record", (), {})()
        record.run_id = run_id
        record.op_id = "op-x"
        record.operation = "analysis.buffer"
        record.lease_id = "lease-1"
        record.epoch = 2
        record.plan_hash = "hash-1"
        record.step = {"id": "step_1", "operation": "analysis.buffer",
                       "arguments": {}, "reason": ""}
        record.staging_root = "staging"
        record.content_hash = "chash"
        record.target = {"arcmap_pid": 1, "bridge_pid": 2, "bridge_port": 3,
                         "hwnd": 4}
        record.receipt = None
        session._runs[run_id] = record
        return session, record

    def test_lease_ack_returns_workflow_row_after_fencing(self):
        session, record = self._record_with_run()
        payload = {"lease_id": "lease-1", "epoch": 2, "plan_hash": "hash-1"}
        row = session.lease_ack(record.run_id, payload)
        self.assertEqual(row["workflow"]["steps"][0]["operation"], "analysis.buffer")
        self.assertEqual(row["content_hash"], "chash")
        self.assertIn("staging_root", row)

    def test_stale_epoch_is_rejected(self):
        session, record = self._record_with_run()
        payload = {"lease_id": "lease-1", "epoch": 1, "plan_hash": "hash-1"}
        with self.assertRaises(LeaseFenceError):
            session.lease_ack(record.run_id, payload)

    def test_unknown_run_is_rejected(self):
        session, _ = self._record_with_run()
        with self.assertRaises(LeaseFenceError):
            session.lease_ack("22222222-2222-2222-2222-222222222222",
                              {"lease_id": "x", "epoch": 1, "plan_hash": "y"})

    def test_receipt_waits_and_fences(self):
        session, record = self._record_with_run()
        receipt = {"status": "executed", "result": {"summary": "ok"},
                   "lease_id": "lease-1", "epoch": 2, "plan_hash": "hash-1"}
        # register the waiter the way execute() does, then deliver
        waiter = session._register(session._receipt_waiters, record.run_id)
        session.receive_receipt(record.run_id, receipt)
        self.assertTrue(waiter.event.is_set())
        self.assertEqual(waiter.document["status"], "executed")
        bad = dict(receipt, epoch=1)
        waiter2 = session._register(session._receipt_waiters, record.run_id)
        with self.assertRaises(LeaseFenceError):
            session.receive_receipt(record.run_id, bad)
        self.assertFalse(waiter2.event.is_set())


class TargetLivenessTests(unittest.TestCase):
    """Bridge may outlive ArcMap; discovery must drop dead-PID targets."""

    def test_pid_alive_probe_on_this_process(self):
        import os
        self.assertTrue(BridgeSession._pid_alive(os.getpid()))

    def test_pid_alive_probe_on_invalid_pid(self):
        self.assertFalse(BridgeSession._pid_alive(0))
        self.assertFalse(BridgeSession._pid_alive(-5))

    def test_dead_arcmap_target_is_dropped(self):
        live = lambda pid: pid != 999
        targets = [
            {"arcmap_pid": 111, "bridge_pid": 222, "bridge_port": 8766,
             "hwnd": 1, "active": True},
            {"arcmap_pid": 999, "bridge_pid": 222, "bridge_port": 8766,
             "hwnd": 2, "active": True},
        ]
        result = BridgeSession.filter_live_targets(targets, live)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["arcmap_pid"], 111)

    def test_all_dead_yields_empty(self):
        result = BridgeSession.filter_live_targets(
            [{"arcmap_pid": 1, "bridge_pid": 2}], lambda pid: False)
        self.assertEqual(result, [])

    def test_status_distinguishes_bridge_residue_from_arcmap(self):
        session = BridgeSession()
        session._read_ready_file = lambda: {"pid": 123, "port": 8766}
        session._pid_alive = lambda pid: pid == 123  # bridge alive, arcmap not
        session._health = lambda port: True
        def raise_offline():
            raise BridgeUnavailable("ArcMap 已关闭")
        session.targets = raise_offline
        document = session.status()
        self.assertTrue(document["bridge"]["process_alive"])
        self.assertTrue(document["bridge"]["health_ok"])
        self.assertEqual(document["arcmap"]["alive_count"], 0)
        self.assertIn("offline_reason", document["arcmap"])


class CodegenTests(unittest.TestCase):
    """B structure: one native tool per catalog operation."""

    def test_every_operation_maps_to_a_unique_tool_name(self):
        catalog = Catalog()
        names = [codegen.tool_name(oid) for oid in catalog.operation_ids()]
        self.assertEqual(len(names), 55)  # export_map_png removed
        self.assertEqual(len(set(names)), 55, "tool names must be unique")
        self.assertEqual(codegen.tool_name("layer.add_layer"), "layer__add_layer")

    def test_tool_names_stay_in_the_dsh_function_name_contract(self):
        import re
        catalog = Catalog()
        for oid in catalog.operation_ids():
            name = codegen.tool_name(oid)
            self.assertLessEqual(len(name), 64)
            self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]+", name), name)

    def test_arguments_model_fields_are_optional(self):
        catalog = Catalog()
        card = catalog.get("layer.add_layer")
        model, required = codegen.build_arguments_model("layer.add_layer", card)
        self.assertIsNotNone(model)
        self.assertTrue(required, "add_layer must declare required params")
        for name, field in model.model_fields.items():
            self.assertIsNone(field.default,
                              "all fields optional so pre-check owns requiredness")

    def test_tool_description_carries_summary_and_effect(self):
        catalog = Catalog()
        card = catalog.get("layer.add_layer")
        text = codegen.tool_description(card, catalog.side_effect_level(card))
        self.assertIn("图层", text)
        self.assertIn("副作用等级", text)

    def test_no_dispatch_tools_remain_in_main(self):
        source = Path("server/main.py").read_text(encoding="utf-8")
        for banned in ("def run_operation", "def list_capabilities",
                       "def get_operation_contract"):
            self.assertNotIn(banned, source)



class ArgumentCoercionTests(unittest.TestCase):
    """Reproduce the exact stringly-typed payloads from the live session."""

    def setUp(self):
        self.schema = {
            "type": "object",
            "properties": {
                "layer": {"type": "string"},
                "field": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string"},
                        "nullable": {"type": "boolean"},
                        "length": {"type": ["integer", "null"]},
                        "precision": {"type": ["integer", "null"]},
                        "scale": {"type": ["integer", "null"]},
                        "domain": {"type": "array"},
                    },
                },
            },
        }

    def test_live_session_payload_is_repaired(self):
        raw = {"layer": "districts", "field": {
            "name": "remark", "type": "TEXT",
            "length": "50", "nullable": "true"}}
        fixed = precheck_coerce(self.schema, raw)
        self.assertEqual(fixed["field"]["length"], 50)
        self.assertIs(fixed["field"]["nullable"], True)
        self.assertEqual(fixed["field"]["name"], "remark")

    def test_null_placeholders_and_item_wrapper_are_dropped(self):
        raw = {"layer": "districts", "field": {
            "domain": "null", "length": "50", "name": "remark",
            "nullable": "true", "precision": "0", "scale": "0",
            "type": "string"}}
        fixed = precheck_coerce(self.schema, raw)
        self.assertEqual(fixed["field"]["domain"], [])
        self.assertEqual(fixed["field"]["precision"], 0)

    def test_empty_domain_and_item_object(self):
        raw = {"layer": "districts", "field": {
            "domain": "", "name": "remark", "nullable": "false",
            "type": "string"}}
        fixed = precheck_coerce(self.schema, raw)
        self.assertEqual(fixed["field"]["domain"], [])
        self.assertIs(fixed["field"]["nullable"], False)

    def test_real_catalog_add_field_schema_coerces(self):
        catalog = Catalog()
        schema = catalog.get("table.add_field")["parameters_schema"]
        raw = {"layer": "districts", "field": {
            "name": "remark", "type": "string", "nullable": "true",
            "length": "50", "precision": "50", "scale": "0", "domain": ""}}
        fixed = precheck_coerce(schema, raw)
        field = fixed["field"]
        self.assertIs(field["nullable"], True)
        self.assertEqual(field["length"], 50)
        self.assertEqual(field["domain"], [])



class QuantityAndLayerResolutionTests(unittest.TestCase):

    def test_quantity_semantic_fill_and_unit_alias(self):
        schema = {"type": "object", "properties": {
            "distance": {"type": "object", "x-geopilot-semantic": "quantity",
                         "properties": {"value": {"type": "number"},
                                        "unit": {"type": "string"},
                                        "dimension": {"const": "length"},
                                        "tolerance": {"type": "number"},
                                        "crs": {"type": ["string", "null"]}}}}}
        fixed = precheck_coerce(schema, {"distance": {"value": "800", "unit": "m"}})
        quantity = fixed["distance"]
        self.assertEqual(quantity["value"], 800.0)
        self.assertEqual(quantity["unit"], "meters")
        self.assertEqual(quantity["dimension"], "length")
        self.assertEqual(quantity["tolerance"], 0.0)
        self.assertIsNone(quantity["crs"])
        self.assertEqual(set(quantity),
                         {"value", "unit", "dimension", "tolerance", "crs"})

    def test_field_spec_defaults_completed(self):
        schema = {"type": "object", "properties": {
            "field": {"type": "object", "x-geopilot-semantic": "field_spec",
                      "properties": {"name": {"type": "string"},
                                     "type": {"type": "string"}}}}}
        fixed = precheck_coerce(schema, {"field": {"name": "remark", "type": "string"}})
        field = fixed["field"]
        self.assertEqual(set(field),
                         {"name", "type", "nullable", "length", "precision", "scale", "domain"})
        self.assertEqual(field["length"], 50)
        self.assertEqual(field["domain"], [])

    def test_layer_names_resolved_to_refs(self):
        from server.precheck import resolve_layer_references
        schema = {"type": "object", "properties": {
            "layer": {"type": "string", "x-geopilot-kind": "layer"},
            "reference_layer": {"type": "string", "x-geopilot-kind": "layer"}}}
        context = {"layers": [
            {"layer_ref": "layer:0", "name": "districts"},
            {"layer_ref": "layer:3", "name": "hospitals"}]}
        resolved = resolve_layer_references(
            schema, {"layer": "districts", "reference_layer": "hospitals"}, context)
        self.assertEqual(resolved, {"layer": "layer:0", "reference_layer": "layer:3"})
        untouched = resolve_layer_references(schema, {"layer": "layer:0"}, context)
        self.assertEqual(untouched, {"layer": "layer:0"})

    def test_py2_quantity_finiteness(self):
        # emulate the ABI check on this (py3) interpreter via the same helper
        import sys
        sys.path.insert(0, r"D:\Development\Python\Arcpy")
        from shared_runtime.semantic_abi import _is_finite
        self.assertTrue(_is_finite(800.0))
        self.assertFalse(_is_finite(float("nan")))
        self.assertFalse(_is_finite(float("inf")))

if __name__ == "__main__":
    unittest.main()
