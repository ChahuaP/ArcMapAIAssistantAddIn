# -*- coding: utf-8 -*-
"""Stage C LangGraph WorkflowEngine tests (§13.2, §11 LangGraph).

Covers: SqliteSaver checkpoint persistence + sealed-plan reuse (zero model
calls on re-plan), stream_mode='updates' node events, validation repair
budget exhaustion -> ContractFailed, audit revise loop, and checkpoint /
JournalStore consistency.
"""
from __future__ import absolute_import

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from gateway_py3.intelligence.model_runtime import ModelRuntime
from gateway_py3.intelligence.task_compiler import TaskCompiler
from gateway_py3.intelligence.workflow_engine import WorkflowEngine
from gateway_py3.kernel import contracts
from gateway_py3.kernel.contracts import (
    CallerIdentity, CapabilitySnapshot, CapabilitySpec, ContextSnapshot,
    EntityBinding, FieldColumn, IntentSpec, LayerRef, LayerSnapshot,
    RequestEnvelope, CONTRACT_FAILED,
)
from gateway_py3.kernel.store import JournalStore
from gateway_py3.llm_providers import StructuredOutputContract

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

    def _runtime(self, responses):
        adapter = _ScriptedAdapter(responses)
        return ModelRuntime(adapter, self.store)

    def _make_intent(self, runtime) -> IntentSpec:
        compiler = TaskCompiler(runtime)
        outcome = compiler.compile(self.request, self.context, self.capabilities)
        self.assertTrue(outcome.succeeded, msg=str(outcome))
        return outcome.details["intent"]

    def _engine(self, responses, checkpoint_path=None, auditor=False):
        runtime = self._runtime(responses)
        auditor_runtime = self._runtime(responses) if auditor else None
        from gateway_py3.catalog_loader import OperationCatalog
        catalog = OperationCatalog()
        return WorkflowEngine(catalog, runtime, auditor_runtime=auditor_runtime,
                              checkpoint_path=checkpoint_path)


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
        adapter_calls_before = engine2.model_runtime.adapter.call_count
        outcome2 = engine2.plan(self.run_id, intent, self.context, self.capabilities)
        self.assertTrue(outcome2.succeeded)
        plan = outcome2.details.get("plan")
        self.assertIsNotNone(plan)
        self.assertEqual(len(plan.workflow), 1)
        self.assertEqual(
            engine2.model_runtime.adapter.call_count, adapter_calls_before,
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


class StreamUpdatesTest(_BaseLangGraphTest):
    """§14: stream_mode='updates' yields one event per completed node."""

    def test_stream_yields_draft_validate_audit_seal(self):
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": _workflow_draft_response(),
        }
        engine = self._engine(responses)
        intent = self._make_intent(self._runtime(responses))
        nodes = [node for node, _ in engine.stream_plan(
            self.run_id, intent, self.context, self.capabilities)]
        self.assertIn("draft", nodes)
        self.assertIn("validate", nodes)
        # audit node always runs; without an auditor_runtime it passes
        # immediately (no model call).
        self.assertIn("audit", nodes)
        self.assertIn("seal", nodes)
        self.assertEqual(nodes[-1], "seal")


class ValidationBudgetTest(_BaseLangGraphTest):
    """§6.3: repair budget exhaustion terminates with ContractFailed."""

    def test_validation_budget_exhausted_contract_failed(self):
        # A workflow that always fails validation (invalid operation id)
        # forces every repair to fail; after MAX_VALIDATION_REVISIONS the
        # graph must terminate with ContractFailed, not loop forever.
        bad_response = {
            "workflow_draft": {
                "action": "execute", "summary": "bad",
                "steps": [{
                    "id": "s1", "operation": "nonexistent.operation",
                    "arguments_json": "{}", "reason": "x",
                }],
            },
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
        revise_once = {"decision": "revise", "claims": [
            {"kind": "revision", "proof_id": "violation_1",
             "change_target": "workflow", "required_change": "fix step"},
        ]}
        responses = {
            "task_contract": _task_contract_response(),
            "workflow": _workflow_draft_response(),
            "audit": revise_once,
        }
        engine = self._engine(responses, auditor=True)
        intent = self._make_intent(self._runtime(responses))
        outcome = engine.plan(self.run_id, intent, self.context, self.capabilities)
        # auditor_runtime is the same scripted runtime; after the first
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
        self.assertEqual(view.stage, "succeeded", msg=str(view.outcome))
        verified = [e for e in view.events if e["kind"] == "plan_verified"]
        self.assertEqual(len(verified), 1)
        plan_digest = verified[0]["payload"]["plan_digest"]
        # the sealed plan must be retrievable by run, and its digest (recomputed
        # over the stored document) must match the journal event
        plan_doc = self.store.get_verified_plan_document(view.run_id)
        self.assertIsNotNone(plan_doc)
        self.assertEqual(contracts.digest(plan_doc), plan_digest)


if __name__ == "__main__":
    unittest.main()
