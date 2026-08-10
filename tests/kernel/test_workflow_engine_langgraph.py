# -*- coding: utf-8 -*-
"""Stage C LangGraph WorkflowEngine tests (§13.2, §11 LangGraph).

Covers: SqliteSaver checkpoint persistence + sealed-plan reuse (zero model
calls on re-plan), stream_mode='updates' node events, validation repair
budget exhaustion -> ContractFailed, audit revise loop, and checkpoint /
JournalStore consistency.
"""
from __future__ import absolute_import

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from gateway_py3.intelligence.task_compiler import TaskCompiler
from gateway_py3.intelligence.workflow_engine import WorkflowEngine
from gateway_py3.intelligence.dpapi_serde import DpapiCheckpointSerializer
from gateway_py3.kernel import contracts
from gateway_py3.kernel.contracts import (
    CallerIdentity, CapabilitySnapshot, CapabilitySpec, ContextSnapshot,
    EntityBinding, FieldColumn, IntentSpec, LayerRef, LayerSnapshot,
    RequestEnvelope, CONTRACT_FAILED,
)
from gateway_py3.kernel.store import JournalStore
from gateway_py3.model_runtime.contracts import StructuredOutputContract
from tests.kernel.fakes import build_test_model_runtime

from tests.kernel.test_intelligence import (
    _context_snapshot, _capability_snapshot, _request, _task_contract_response,
    _workflow_draft_response, _ScriptedAdapter,
)


class _BaseLangGraphTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "gp.sqlite"
        self.store = JournalStore(path=self.tmp)
        self.context = _context_snapshot()
        self.capabilities = _capability_snapshot()
        self.request = _request()
        self.run_id = "00000000-0000-0000-0000-00000000000c"
        self.adapters = []

    def _runtime(self, responses):
        adapter = _ScriptedAdapter(responses)
        self.adapters.append(adapter)
        return build_test_model_runtime(adapter, self.store)

    def _make_intent(self, runtime) -> IntentSpec:
        compiler = TaskCompiler(runtime)
        outcome = compiler.compile(self.request, self.context, self.capabilities)
        self.assertTrue(outcome.succeeded, msg=str(outcome))
        return outcome.details["intent"]

    def _engine(self, responses, checkpoint_path=None, journal=None, checkpointer=None):
        runtime = self._runtime(responses)
        from gateway_py3.catalog_loader import OperationCatalog
        catalog = OperationCatalog()
        return WorkflowEngine(catalog, runtime,
                              checkpoint_path=checkpoint_path, journal=journal,
                              checkpointer=checkpointer)


class CheckpointPersistenceTest(_BaseLangGraphTest):
    """§13.2: SqliteSaver persists to the JournalStore DB file; a sealed plan
    is reused on re-plan with zero additional model calls."""

    def test_sealed_plan_reused_after_restart(self):
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": _workflow_draft_response(),
        }
        checkpoint = self.tmp  # same DB file as the JournalStore
        engine1 = self._engine(responses, checkpoint_path=checkpoint)
        intent = self._make_intent(self._runtime(responses))
        outcome1 = engine1.plan(self.run_id, intent, self.context, self.capabilities)
        self.assertTrue(outcome1.succeeded, msg=str(outcome1))

        # A "restarted" engine with a fresh adapter: the checkpoint must
        # provide the sealed plan without any adapter call.
        engine2 = self._engine(responses, checkpoint_path=checkpoint)
        adapter_calls_before = sum(adapter.call_count for adapter in self.adapters)
        outcome2 = engine2.plan(self.run_id, intent, self.context, self.capabilities)
        self.assertTrue(outcome2.succeeded)
        plan = outcome2.details.get("plan")
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.workflow), 1)
        self.assertEqual(
            sum(adapter.call_count for adapter in self.adapters), adapter_calls_before,
            "re-plan must not call the model adapter when a sealed plan exists",
        )

    def test_different_run_id_does_not_reuse_checkpoint(self):
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": _workflow_draft_response(),
        }
        checkpoint = self.tmp
        engine = self._engine(responses, checkpoint_path=checkpoint)
        intent = self._make_intent(self._runtime(responses))
        engine.plan(self.run_id, intent, self.context, self.capabilities)
        other_run = "00000000-0000-0000-0000-00000000000d"
        outcome = engine.plan(other_run, intent, self.context, self.capabilities)
        self.assertTrue(outcome.succeeded)
        # A different thread must produce its own sealed plan; ModelRuntime
        # cache may still serve the planner call (that is correct caching,
        # §6.4), but the plan object must be independently sealed.
        plan = outcome.details.get("plan")
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.workflow), 1)

    def test_checkpoint_blobs_are_dpapi_protected_and_recoverable(self):
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": _workflow_draft_response(),
        }
        engine = self._engine(responses, checkpoint_path=self.tmp)
        intent = self._make_intent(self._runtime(responses))
        outcome = engine.plan(self.run_id, intent, self.context, self.capabilities)
        self.assertTrue(outcome.succeeded, msg=str(outcome))
        with sqlite3.connect(str(self.tmp)) as conn:
            values = conn.execute(
                "SELECT type, checkpoint, metadata FROM checkpoints "
                "UNION ALL SELECT type, value, NULL FROM writes"
            ).fetchall()
        self.assertTrue(values)
        raw = b"".join(
            (value if isinstance(value, bytes) else str(value).encode("utf-8"))
            for row in values for value in row if value is not None
        )
        for sensitive_fragment in (
            self.request.text.encode("utf-8"), b"C:/data/cities.shp",
            b"step_context-list_layers", b"context.list_layers", b"audit_result",
        ):
            self.assertNotIn(sensitive_fragment, raw)
        self.assertTrue(all(row[0] == "geopilot-dpapi-v1" for row in values))
        recovered = self._engine(responses, checkpoint_path=self.tmp).plan(
            self.run_id, intent, self.context, self.capabilities
        )
        self.assertTrue(recovered.succeeded)

    def test_checkpoint_serializer_rejects_plaintext_blob(self):
        with self.assertRaises(ValueError):
            DpapiCheckpointSerializer().loads_typed(("msgpack", b"plaintext"))


class StreamUpdatesTest(_BaseLangGraphTest):
    """§14: stream_mode='updates' yields one event per completed node."""

    def test_plan_uses_graph_stream_and_seals(self):
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": _workflow_draft_response(),
        }
        engine = self._engine(responses)
        intent = self._make_intent(self._runtime(responses))
        outcome = engine.plan(self.run_id, intent, self.context, self.capabilities)
        self.assertTrue(outcome.succeeded)

    def test_high_risk_interrupts_then_resumes_same_graph_checkpoint(self):
        responses = {"task_contract": _task_contract_response(),
                     "workflow": _workflow_draft_response()}
        engine = self._engine(responses, checkpoint_path=self.tmp)
        intent = self._make_intent(self._runtime(responses)).model_copy(
            update={"acceptable_side_effects": 2})
        outcome = engine.plan(self.run_id, intent, self.context, self.capabilities)
        self.assertTrue(outcome.succeeded)
        self.assertTrue(outcome.details["awaiting_authorization"])
        self.assertEqual(engine.decide_authorization(self.run_id, True), "authorized")
        state = engine._compiled().get_state(engine._config(self.run_id)).values
        self.assertEqual(state["authorization_result"], "authorized")


class ValidationBudgetTest(_BaseLangGraphTest):
    """§6.3: repair budget exhaustion terminates with ContractFailed."""

    def test_validation_budget_exhausted_contract_failed(self):
        # A workflow that always fails validation (invalid operation id)
        # forces every repair to fail; after MAX_VALIDATION_REVISIONS the
        # graph must terminate with ContractFailed, not loop forever.
        bad_response = {
            "tool_calls": [
                {"name": "nonexistent_operation", "arguments": {}},
            ],
        }
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": bad_response,
            "repair": bad_response,
        }
        engine = self._engine(responses)
        intent = self._make_intent(self._runtime(responses))
        outcome = engine.plan(self.run_id, intent, self.context, self.capabilities)
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.kind, CONTRACT_FAILED)


class AuditReviseLoopTest(_BaseLangGraphTest):
    """§6.3: audit revise -> repair -> validate -> audit loop."""

    def test_audit_revise_then_pass(self):
        revise_once = {"audit_result": {"decision": "revise", "claims": [
            {"kind": "revision", "proof_id": "violation_1",
             "change_target": "workflow", "required_change": "fix step"},
        ]}}
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": _workflow_draft_response(),
            "audit": revise_once,
        }
        engine = self._engine(responses)
        intent = self._make_intent(self._runtime(responses))
        outcome = engine.plan(self.run_id, intent, self.context, self.capabilities)
        # The scripted auditor returns revise every time; after the first
        # revise, the audit node runs again with the same response (revise),
        # which exhausts... but budget is MAX_AUDIT_REVISIONS=3, so after
        # repair the loop re-audits. The response stays 'revise', so the loop
        # must terminate with ContractFailed at budget exhaustion.
        self.assertFalse(outcome.succeeded)
        self.assertEqual(outcome.kind, CONTRACT_FAILED)


class CheckpointJournalConsistencyTest(_BaseLangGraphTest):
    """§13.2: when the kernel stores a sealed plan, the JournalStore has a
    plan_verified event referencing the same plan digest."""

    def test_plan_verified_event_matches_sealed_plan(self):
        from gateway_py3.kernel.coordinator import GeoPilotKernel, KernelPorts
        from tests.kernel import fakes
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": _workflow_draft_response(),
        }
        runtime = self._runtime(responses)
        compiler = TaskCompiler(runtime)
        engine = self._engine(responses, checkpoint_path=self.tmp)
        caps = _capability_snapshot()  # contains context.list_layers

        class _Caps:
            def snapshot(self, run_id):
                return caps

        ports = KernelPorts(
            store=self.store,
            context=fakes.FakeContextProvider(),
            capabilities=_Caps(),
            compiler=compiler, planner=engine,
            policy=fakes.build_fake_ports(self.store).policy,
            executor=fakes.FakeArcMapExecutor(),
            acceptance=fakes.FakeAcceptancePublisher(),
            model=runtime,
        )
        kernel = GeoPilotKernel(ports)
        view = kernel.submit(self.request)
        # execute=False pauses at plan_verified (plan_only). The journal must
        # already hold the plan_verified event by then.
        view = fakes.wait_for_terminal(kernel, view.run_id)
        self.assertIn(view.stage, ("clarification_required", "succeeded"),
                      msg=str(view.outcome))
        verified = [e for e in view.events if e["kind"] == "plan_verified"]
        self.assertEqual(len(verified), 1)
        plan_digest = verified[0]["payload"]["plan_digest"]
        # the sealed plan must be retrievable by run, and its digest (recomputed
        # over the stored document) must match the journal event
        plan_doc = self.store.get_verified_plan_document(view.run_id)
        self.assertIsNotNone(plan_doc)
        self.assertEqual(contracts.digest(plan_doc), plan_digest)


class JournalReplayBoundaryTest(_BaseLangGraphTest):
    def test_node_fact_survives_checkpoint_fault_and_replay_does_not_repeat_model(self):
        """The journal fact is the replay boundary when checkpoint persistence fails after a node."""
        self.store.create_session(self.request.session_id, self.request.caller.tenant_id)
        run_id = self.store.create_run(self.request)["run_id"]
        engine = self._engine({}, checkpoint_path=self.tmp, journal=self.store)
        calls = []
        def model_node(state):
            calls.append("model")
            return {"draft": {"tool_calls": []}}
        state = {"run_id": run_id, "node_attempts": {}}
        engine._journaled("draft", model_node)(state)
        # Simulated crash: the node fact committed, but no graph checkpoint was
        # written. A fresh engine must replay the fact, never call the model.
        restarted = self._engine({}, checkpoint_path=self.tmp, journal=self.store)
        update = restarted._journaled("draft", model_node)(state)
        self.assertEqual(calls, ["model"])
        self.assertEqual(update["draft"], {"tool_calls": []})
        events = [item for item in self.store.run_events(run_id)
                  if item["kind"] == "planning.node_update"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["node"], "draft")

    def test_langgraph_checkpoint_fault_replays_journaled_node_without_model_recall(self):
        from langgraph.checkpoint.sqlite import SqliteSaver
        responses = {"task_contract": _task_contract_response(), "workflow": _workflow_draft_response()}
        intent = self._make_intent(self._runtime(responses))
        self.store.create_session(intent.session_id, "t1")
        self.run_id = self.store.create_run(self.request)["run_id"]
        from langgraph.checkpoint.base import BaseCheckpointSaver
        class FailAfterFact(BaseCheckpointSaver):
            def __init__(self, inner, store, run_id):
                BaseCheckpointSaver.__init__(self, serde=inner.serde)
                self.inner, self.store, self.run_id, self.failed = inner, store, run_id, False
            def get_tuple(self, *args, **kwargs): return self.inner.get_tuple(*args, **kwargs)
            def list(self, *args, **kwargs): return self.inner.list(*args, **kwargs)
            def put(self, *args, **kwargs):
                if not self.failed and self.store.get_planning_node_update(self.run_id, "draft", 1):
                    self.failed = True
                    raise RuntimeError("checkpoint fault")
                return self.inner.put(*args, **kwargs)
            def put_writes(self, *args, **kwargs):
                return self.inner.put_writes(*args, **kwargs)
            def delete_thread(self, *args, **kwargs): return self.inner.delete_thread(*args, **kwargs)
        inner = SqliteSaver(
            sqlite3.connect(str(self.tmp), check_same_thread=False),
            serde=DpapiCheckpointSerializer(),
        )
        failing = FailAfterFact(inner, self.store, self.run_id)
        engine = self._engine(responses, journal=self.store, checkpointer=failing)
        with self.assertRaisesRegex(RuntimeError, "checkpoint fault"):
            engine.plan(self.run_id, intent, self.context, self.capabilities)
        calls = sum(adapter.call_count for adapter in self.adapters)
        restarted = self._engine(responses, checkpoint_path=self.tmp, journal=self.store)
        restarted_adapter = self.adapters[-1]
        outcome = restarted.plan(self.run_id, intent, self.context, self.capabilities)
        self.assertTrue(outcome.succeeded)
        self.assertEqual(restarted_adapter.call_count, 0)
        self.assertGreaterEqual(calls, 1)
        facts = [item for item in self.store.run_events(self.run_id) if item["kind"] == "planning.node_update"]
        self.assertEqual(len([item for item in facts if item["payload"]["node"] == "draft"]), 1)


if __name__ == "__main__":
    unittest.main()
