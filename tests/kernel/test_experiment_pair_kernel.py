"""Offline scripted GeoPilotKernel experiment-pair integration coverage."""
from __future__ import annotations
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from gateway_py3.catalog_loader import OperationCatalog
from gateway_py3.intelligence.task_compiler import TaskCompiler
from gateway_py3.intelligence.workflow_engine import WorkflowEngine
from gateway_py3.experiments.supervisor import ExperimentSupervisor, ExperimentSupervisorError
from gateway_py3.kernel.contracts import CallerIdentity, ExperimentSpec, RequestEnvelope, TargetSelector
from gateway_py3.kernel.coordinator import GeoPilotKernel, KernelPorts
from gateway_py3.kernel.store import JournalStore
from gateway_py3.runtime.policy import PolicyGate
from gateway_py3.runtime.capability_provider import CapabilityProvider
from tests.kernel.fakes import (
    FakeArcMapExecutor, FakeContextProvider, build_test_model_runtime,
    fake_agent_model_plan, fake_provider_connection, wait_for_terminal,
)
from tests.kernel.test_intelligence import _ScriptedAdapter, _task_contract_response, _workflow_draft_response

class ExperimentPairKernelTest(unittest.TestCase):
    def _offline_formal_kernel(self):
        root = Path(tempfile.mkdtemp())
        store = JournalStore(root / "journal.sqlite")
        scripted = _ScriptedAdapter({"task_contract": _task_contract_response(), "workflow": _workflow_draft_response()})
        scripted.provider_type = "minimax"
        scripted.connection_id = "minimax-offline"
        connection = fake_provider_connection(
            provider_type="minimax", model_id="MiniMax-M3",
            connection_id="minimax-offline", endpoint="https://api.minimaxi.com/v1",
        ).model_copy(update={"credential_ref": "credential:offline"})
        runtime = build_test_model_runtime(
            scripted, store, connection=connection,
            plan=fake_agent_model_plan("minimax-offline", "MiniMax-M3"),
        )
        catalog = OperationCatalog()
        engine = WorkflowEngine(catalog, runtime, checkpoint_path=store.path, journal=store)
        kernel = GeoPilotKernel(KernelPorts(
            store=store, context=FakeContextProvider(), capabilities=CapabilityProvider(catalog),
            compiler=TaskCompiler(runtime), planner=engine, policy=PolicyGate(),
            executor=FakeArcMapExecutor(), model=runtime,
        ))
        return kernel, root, store

    def test_baseline_context_is_reused_for_g3_planning(self):
        store = JournalStore(Path(tempfile.mkdtemp()) / "journal.sqlite")
        adapter = _ScriptedAdapter({"task_contract": _task_contract_response(), "workflow": _workflow_draft_response()})
        runtime = build_test_model_runtime(adapter, store)
        catalog = OperationCatalog()
        engine = WorkflowEngine(catalog, runtime, checkpoint_path=store.path, journal=store)
        kernel = GeoPilotKernel(KernelPorts(
            store=store, context=FakeContextProvider(), capabilities=CapabilityProvider(catalog),
            compiler=TaskCompiler(runtime), planner=engine, policy=PolicyGate(),
            executor=FakeArcMapExecutor(), model=runtime,
        ))
        selector = TargetSelector(bridge_pid=2001, bridge_port=8766, arcmap_pid=2000, hwnd=3000, deployment_hash="fake-deployment-v1")
        pair_id = str(uuid.uuid4())
        def submit(arm):
            request = RequestEnvelope(
                session_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), text="list layers",
                caller=CallerIdentity(user_id="test", tenant_id="experiment", role="operator"),
                target_selector=selector,
                experiment=ExperimentSpec(pair_id=pair_id, arm=arm, seed=7,
                                          provider="minimax", model="MiniMax-M3"),
            )
            return kernel.submit(request)
        g2 = wait_for_terminal(kernel, submit("g2").run_id)
        g3 = wait_for_terminal(kernel, submit("g3").run_id)
        self.assertIsNotNone(g2.plan, msg=str(g2.outcome))
        self.assertIsNotNone(g3.plan, msg=str(g3.outcome))
        fence = next(e["payload"] for e in g3.events if e["kind"] == "experiment_context_fenced")
        self.assertNotEqual(fence["captured_context_digest"], fence["baseline_context_digest"])
        self.assertEqual(g2.plan.context_digest, g3.plan.context_digest)
        self.assertEqual(g2.plan.intent_digest, g3.plan.intent_digest)
        self.assertTrue(any(e["kind"] == "experiment_baseline_reused" for e in g3.events))
        baseline = kernel.export_run_journal(g2.run_id)["experiment_baseline"]
        self.assertEqual(baseline["baseline_digest"], next(e["payload"] for e in g3.events if e["kind"] == "plan_verified")["baseline_digest"])
        self.assertNotIn("compiler", [(call.get("ledger") or {}).get("role") for call in kernel.export_run_journal(g3.run_id)["model_calls"]])

    def test_supervisor_waits_and_exports_valid_pair(self):
        kernel, root, _store = self._offline_formal_kernel()
        selector = TargetSelector(bridge_pid=2001, bridge_port=8766, arcmap_pid=2000, hwnd=3000, deployment_hash="fake-deployment-v1")
        result = ExperimentSupervisor(kernel, campaign_root=root / "campaigns").run_pair("list layers", 9, target_selector=selector, provider="minimax", model="MiniMax-M3", dry_run=False)
        self.assertTrue(result["pair_valid"], msg=str(result))
        for run_id in (result["g2_run_id"], result["g3_run_id"]):
            exported = kernel.export_run_journal(run_id)
            for field in ("request_envelope", "captured_context_snapshot",
                          "planning_context_snapshot", "capability_snapshot", "intent_spec",
                          "task_contract", "verified_plan", "digests", "experiment_baseline",
                          "model_calls", "run_events"):
                self.assertIn(field, exported)
        self.assertTrue(list((root / "campaigns").rglob("journal.json")))

    def test_supervisor_rejects_non_minimax_role_before_any_model_call(self):
        root = Path(tempfile.mkdtemp())
        store = JournalStore(root / "journal.sqlite")
        scripted = _ScriptedAdapter({})
        runtime = build_test_model_runtime(scripted, store)
        catalog = OperationCatalog()
        engine = WorkflowEngine(catalog, runtime, checkpoint_path=store.path, journal=store)
        kernel = GeoPilotKernel(KernelPorts(
            store=store, context=FakeContextProvider(), capabilities=CapabilityProvider(catalog),
            compiler=TaskCompiler(runtime), planner=engine, policy=PolicyGate(),
            executor=FakeArcMapExecutor(), model=runtime,
        ))
        selector = TargetSelector(bridge_pid=2001, bridge_port=8766, arcmap_pid=2000, hwnd=3000, deployment_hash="fake-deployment-v1")
        with self.assertRaises(ExperimentSupervisorError):
            ExperimentSupervisor(kernel, campaign_root=root / "campaigns").run_pair("list layers", 1, target_selector=selector, provider="minimax", model="MiniMax-M3", dry_run=False)
        self.assertEqual(0, scripted.call_count)

    def test_tampered_baseline_document_fails_fast(self):
        kernel, root, store = self._offline_formal_kernel()
        selector = TargetSelector(bridge_pid=2001, bridge_port=8766, arcmap_pid=2000, hwnd=3000, deployment_hash="fake-deployment-v1")
        result = ExperimentSupervisor(kernel, campaign_root=root / "campaigns").run_pair("list layers", 4, target_selector=selector, provider="minimax", model="MiniMax-M3", dry_run=False)
        with store._connection() as conn:
            conn.execute("UPDATE experiment_pair_baselines SET task_contract_json='{}' WHERE pair_id=?", (result["pair_id"],))
        with self.assertRaises(ValueError):
            store.get_experiment_baseline(result["pair_id"])

    def test_experiment_envelope_rejects_execute(self):
        selector = TargetSelector(bridge_pid=1, bridge_port=2, arcmap_pid=3, hwnd=4, deployment_hash="deployment")
        with self.assertRaises(ValueError):
            RequestEnvelope(session_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), text="x",
                caller=CallerIdentity(user_id="u", tenant_id="t", role="operator"), execute=True,
                target_selector=selector, experiment=ExperimentSpec(pair_id=str(uuid.uuid4()), arm="g2", seed=1,
                provider="minimax", model="MiniMax-M3"))

    def test_g3_rejects_any_frozen_binding_difference(self):
        selector = TargetSelector(bridge_pid=2001, bridge_port=8766, arcmap_pid=2000,
                                  hwnd=3000, deployment_hash="fake-deployment-v1")
        for changed in ("text", "inputs", "seed", "bridge_pid", "bridge_port",
                        "arcmap_pid", "hwnd", "deployment_hash"):
            with self.subTest(changed=changed):
                kernel, _root, _store = self._offline_formal_kernel()
                pair_id = str(uuid.uuid4())
                def request(arm, text="list layers", inputs=(), seed=1, target=selector):
                    return RequestEnvelope(
                        session_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), text=text,
                        caller=CallerIdentity(user_id="test", tenant_id="experiment", role="operator"),
                        inputs=inputs, target_selector=target,
                        experiment=ExperimentSpec(pair_id=pair_id, arm=arm, seed=seed,
                                                  provider="minimax", model="MiniMax-M3"),
                    )
                wait_for_terminal(kernel, kernel.submit(request("g2")).run_id)
                if changed == "text":
                    kwargs = {"text": "other"}
                elif changed == "inputs":
                    kwargs = {"inputs": ("cities",)}
                elif changed == "seed":
                    kwargs = {"seed": 2}
                elif changed == "deployment_hash":
                    kwargs = {"target": selector.model_copy(update={changed: "other-deployment"})}
                else:
                    kwargs = {"target": selector.model_copy(update={changed: getattr(selector, changed) + 1})}
                g3 = wait_for_terminal(kernel, kernel.submit(request("g3", **kwargs)).run_id)
                self.assertIsNotNone(g3.outcome)
                self.assertEqual("baseline_binding_mismatch", g3.outcome.code)

    def test_campaign_initialization_is_scoped_and_transactional(self):
        kernel, root, _store = self._offline_formal_kernel()
        supervisor = ExperimentSupervisor(kernel, campaign_root=root / "campaigns")
        for invalid in ("", ".", "..", "nested/name", str(root / "outside")):
            with self.subTest(invalid=invalid), self.assertRaises(ExperimentSupervisorError):
                supervisor.begin_campaign(invalid)
        with patch.object(supervisor, "_freeze_provenance", side_effect=RuntimeError("fault")):
            with self.assertRaisesRegex(RuntimeError, "fault"):
                supervisor.begin_campaign("failed")
        self.assertIsNone(supervisor._campaign)
        self.assertFalse((root / "campaigns" / "failed").exists())
        self.assertFalse(list((root / "campaigns").glob(".initializing-*")))
