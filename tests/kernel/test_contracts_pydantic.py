# -*- coding: utf-8 -*-
"""Stage B Pydantic v2 contract tests (§13.1, §11 Pydantic v2).

Covers: ValidationError with field paths, model_json_schema/validator
same-source, frozen + extra='forbid' + validate_assignment, digest stability
over model_dump.
"""
from __future__ import absolute_import

import unittest

from pydantic import ValidationError

from gateway_py3.kernel.contracts import (
    CallerIdentity, CapabilitySnapshot, CapabilitySpec, ContextSnapshot,
    DeclaredOutput,
    IntentSpec, LayerRef, LayerSnapshot, RequestEnvelope, SideEffectScope,
    VerifiedPlan, WorkflowStep,
    EMPTY,
)
from gateway_py3.kernel.coordinator import _runtime_step_document


def _caller(user="u1", tenant="t1", role="analyst"):
    return CallerIdentity(user_id=user, tenant_id=tenant, role=role)


def _envelope(session_id="00000000-0000-0000-0000-000000000001",
              request_id="00000000-0000-0000-0000-000000000002",
              text="select cities", execute=False, side_effects=None):
    return RequestEnvelope(
        session_id=session_id, request_id=request_id, text=text,
        caller=_caller(), execute=execute, side_effects=side_effects,
        target_selector={"bridge_pid": 2001, "bridge_port": 8766,
                         "arcmap_pid": 2000, "hwnd": 3000,
                         "deployment_hash": "a" * 64},
    )


class RuntimeStagingContractTest(unittest.TestCase):
    def test_execution_uses_staging_without_overwriting_sealed_destination(self):
        step = WorkflowStep(
            id="buffer",
            operation="analysis.buffer",
            arguments={
                "input_layer": "layer:roads",
                "output_name": "roads_buffer",
                "output_workspace": r"D:\published\results.gdb",
            },
            reason="buffer roads",
            declared_outputs=(DeclaredOutput(
                output_id="output:buffer",
                name="roads_buffer",
                kind="feature_class",
                destination=r"D:\published\results.gdb\roads_buffer",
            ),),
        )

        runtime_document = _runtime_step_document(step)

        self.assertNotIn("output_workspace", runtime_document["arguments"])
        self.assertEqual(step.arguments["output_workspace"], r"D:\published\results.gdb")
        self.assertEqual(
            step.declared_outputs[0].destination,
            r"D:\published\results.gdb\roads_buffer",
        )


class FrozenImmutabilityTest(unittest.TestCase):
    """§11: construction after assignment raises."""

    def test_frozen_rejects_assignment(self):
        caller = _caller()
        with self.assertRaises(ValidationError):
            caller.user_id = "other"


class ExtraForbiddenTest(unittest.TestCase):
    """§11: unknown fields are rejected."""

    def test_extra_field_rejected(self):
        with self.assertRaises(ValidationError):
            CallerIdentity(user_id="u1", tenant_id="t1", role="analyst",
                           extra_field="nope")


class FieldPathInErrorTest(unittest.TestCase):
    """§11: ValidationError carries the failing field path."""

    def test_missing_text_has_loc(self):
        try:
            RequestEnvelope(
                session_id="00000000-0000-0000-0000-000000000001",
                request_id="00000000-0000-0000-0000-000000000002",
                text="",
                caller=_caller(),
                target_selector={"bridge_pid": 2001, "bridge_port": 8766,
                                 "arcmap_pid": 2000, "hwnd": 3000,
                                 "deployment_hash": "a" * 64},
            )
            self.fail("empty text should fail")
        except ValidationError as exc:
            errors = exc.errors()
            self.assertTrue(errors)
            # Field(min_length=1) reports the failing path precisely.
            self.assertTrue(
                any(tuple(e["loc"]) == ("text",) for e in errors),
                "expected loc ('text',), got %r" % [e["loc"] for e in errors],
            )

    def test_execute_without_side_effects_raises(self):
        with self.assertRaises(ValidationError):
            _envelope(execute=True)

    def test_target_selector_is_required_for_plan_only_requests(self):
        with self.assertRaises(ValidationError):
            RequestEnvelope(
                session_id="00000000-0000-0000-0000-000000000001",
                request_id="00000000-0000-0000-0000-000000000002",
                text="select cities", caller=_caller(),
            )


class JsonSchemaSameSourceTest(unittest.TestCase):
    """§11: model_json_schema and the validator agree on field presence."""

    def test_schema_contains_declared_fields(self):
        schema = RequestEnvelope.model_json_schema()
        properties = schema.get("properties", {})
        for name in ("session_id", "request_id", "text", "caller", "execute"):
            self.assertIn(name, properties)

    def test_schema_reflects_required(self):
        schema = RequestEnvelope.model_json_schema()
        required = schema.get("required", [])
        self.assertIn("session_id", required)
        self.assertIn("caller", required)


class DigestStabilityTest(unittest.TestCase):
    """§11: model_dump(mode='json') serialization matches digest().

    Identical content yields identical digests; any change yields a new one.
    """

    def test_identical_snapshot_same_digest(self):
        s1 = self._snapshot()
        s2 = self._snapshot()
        self.assertEqual(s1.digest, s2.digest)

    def test_changed_snapshot_different_digest(self):
        s1 = self._snapshot()
        s2 = self._snapshot().model_copy(update={"edit_session_state": "single"})
        self.assertNotEqual(s1.digest, s2.digest)

    @staticmethod
    def _snapshot() -> ContextSnapshot:
        return ContextSnapshot(
            lease_id="00000000-0000-0000-0000-00000000000a",
            arcmap_pid=1, bridge_pid=2, bridge_port=3, target_hwnd=4,
            document_identity={"mxd": "a.mxd"},
            layers=(
                LayerSnapshot(
                    identity=LayerRef(name="cities", layer_ref="cities"),
                    selection_count=0,
                ),
            ),
            deployment_hash="dh", content_hash="ch", captured_at=1.0,
        )


class NestedValidationTest(unittest.TestCase):
    """§11: nested BaseModels are validated through model_validate."""

    def test_model_validate_rebuilds_nested(self):
        document = {
            "lease_id": "00000000-0000-0000-0000-00000000000a",
            "arcmap_pid": 1, "bridge_pid": 2, "bridge_port": 3, "target_hwnd": 4,
            "document_identity": {"mxd": "a.mxd"},
            "layers": [
                {"identity": {"name": "cities", "layer_ref": "cities"},
                 "selection_count": 5},
            ],
            "deployment_hash": "dh", "content_hash": "ch", "captured_at": 1.0,
        }
        snapshot = ContextSnapshot.model_validate(document)
        self.assertEqual(snapshot.layers[0].identity.name, "cities")
        self.assertEqual(snapshot.layers[0].selection_count, 5)

    def test_invalid_nested_layer_rejected(self):
        document = {
            "lease_id": "00000000-0000-0000-0000-00000000000a",
            "arcmap_pid": 1, "bridge_pid": 2, "bridge_port": 3, "target_hwnd": 4,
            "document_identity": {"mxd": "a.mxd"},
            "layers": [{"identity": {"name": "", "layer_ref": ""}}],
            "deployment_hash": "dh", "content_hash": "ch", "captured_at": 1.0,
        }
        with self.assertRaises(ValidationError):
            ContextSnapshot.model_validate(document)


class OutcomeClassificationTest(unittest.TestCase):
    """§4.8: recoverable/terminal classification is derived, not stored."""

    def test_outcome_recoverable_derived(self):
        from gateway_py3.kernel.contracts import (
            CLARIFICATION_REQUIRED, outcome_paused,
        )
        outcome = outcome_paused(CLARIFICATION_REQUIRED, "intent", "need_layer", "msg")
        self.assertTrue(outcome.is_recoverable)
        self.assertFalse(outcome.is_terminal)
        # recoverable is a property, not a stored field: model_dump excludes it
        self.assertNotIn("recoverable", outcome.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()
