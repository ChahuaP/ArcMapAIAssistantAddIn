from __future__ import annotations

import tempfile
import unittest
import uuid
import json
import hashlib
from pathlib import Path

from gateway_py3.kernel import contracts
from gateway_py3.kernel.coordinator import ExperimentKernelPort, GeoPilotKernel, _bind_server_destinations
from gateway_py3.kernel.store import JournalStore
from gateway_py3.task_contract import TaskContractError, parse_task_contract
from tests.kernel import fakes


class ExperimentRuntimeContractTest(unittest.TestCase):
    def _request(self):
        return contracts.RequestEnvelope(
            session_id=str(uuid.uuid4()), request_id=str(uuid.uuid4()), text="test",
            caller=contracts.CallerIdentity(user_id="u", tenant_id="t", role="operator"),
            target_selector=contracts.TargetSelector(
                bridge_pid=1, bridge_port=2, arcmap_pid=3, hwnd=4,
                deployment_hash="d" * 64),
            model_plan=fakes.fake_agent_model_plan().model_dump(mode="json"),
            model_binding_summary=fakes.fake_model_binding_summary(),
        )

    def test_server_rebinds_only_physical_destinations_per_arm(self):
        output = contracts.DeclaredOutput(
            output_id="affected", name="affected", kind="feature_class",
            output_format="gdb", destination_policy="server_derived")
        logical = contracts.VerifiedPlan(
            plan_id=str(uuid.uuid4()), version=1, intent_digest="i",
            context_digest="c", capability_digest="k",
            workflow=(contracts.WorkflowStep(
                id="select", operation="analysis.select",
                arguments={"layer": "cities", "distance": 10}, reason="test",
                declared_outputs=(output,)),),
            validation_report={"valid": True}, model_identity="m", prompt_version="p")
        g2 = _bind_server_destinations(logical, r"D:\campaign\g2\artifacts")
        g3 = _bind_server_destinations(logical, r"D:\campaign\g3\artifacts")
        self.assertEqual(logical.workflow[0].operation, g2.workflow[0].operation)
        self.assertEqual(g2.workflow[0].operation, g3.workflow[0].operation)
        self.assertEqual(g2.workflow[0].arguments["distance"], g3.workflow[0].arguments["distance"])
        self.assertNotEqual(g2.workflow[0].declared_outputs[0].destination_path,
                            g3.workflow[0].declared_outputs[0].destination_path)
        self.assertEqual(logical.workflow[0].arguments, g2.workflow[0].arguments)
        self.assertEqual(logical.workflow[0].arguments, g3.workflow[0].arguments)
        self.assertNotIn("campaign", logical.workflow[0].arguments)
        self.assertEqual(g2.experiment_output_signature(r"D:\campaign\g2\artifacts"),
                         g3.experiment_output_signature(r"D:\campaign\g3\artifacts"))
        with self.assertRaises(ValueError):
            g2.experiment_output_signature(r"D:\campaign\g3\artifacts")

    def test_server_derives_mixed_gdb_csv_png_destinations(self):
        outputs = (
            contracts.DeclaredOutput(output_id="feature", name="feature", kind="feature_class",
                                     output_format="gdb", destination_policy="server_derived"),
            contracts.DeclaredOutput(output_id="table", name="report", kind="file",
                                     output_format="csv", destination_policy="server_derived"),
            contracts.DeclaredOutput(output_id="map", name="map.png", kind="file",
                                     output_format="png", destination_policy="server_derived"),
        )
        logical = contracts.VerifiedPlan(
            plan_id=str(uuid.uuid4()), version=1, intent_digest="i", context_digest="c",
            capability_digest="k", workflow=(contracts.WorkflowStep(
                id="export", operation="artifact.export", arguments={}, reason="test",
                declared_outputs=outputs),), validation_report={"valid": True},
            model_identity="m", prompt_version="p")
        physical = _bind_server_destinations(logical, r"D:\campaign\g2\artifacts")
        paths = {item.output_id: item.destination_path for item in physical.workflow[0].declared_outputs}
        self.assertEqual(r"D:\campaign\g2\artifacts\published.gdb\feature", paths["feature"])
        self.assertEqual(r"D:\campaign\g2\artifacts\files\report.csv", paths["table"])
        self.assertEqual(r"D:\campaign\g2\artifacts\files\map.png", paths["map"])

    def test_all_six_formal_prompts_compile_without_model_owned_paths(self):
        root = Path(__file__).resolve().parents[2]
        cases = json.loads((root / "experiments" / "data" / "synthetic-city-formal-20260910" /
                            "task_cases.json").read_text(encoding="utf-8"))["cases"]
        formal = [case for case in cases if case["case_id"] in {"FLOOD_RESPONSE", "LAND_COMPLIANCE"}]
        self.assertEqual(6, sum(len(case["rounds"]) for case in formal))
        for case in formal:
            for round_doc in case["rounds"]:
                prompt = round_doc["prompt"]
                outputs = []
                for index, name in enumerate(round_doc["expected_outputs"]):
                    extension = Path(name).suffix.lower()
                    fmt = extension[1:] if extension in (".csv", ".png") else "gdb"
                    outputs.append({
                        "output_id": "output:%s" % index, "kind": "file" if fmt != "gdb" else "feature_class",
                        "name": name, "format": fmt, "geometry": "not_applicable" if fmt != "gdb" else "polygon",
                        "required_fields": [], "spatial_reference": "not_applicable" if fmt != "gdb" else "inherited",
                        "destination_policy": "server_derived", "evidence": prompt,
                    })
                contract = parse_task_contract({
                    "input_entities": [{"entity_id": "input:context", "role": "current map input",
                                        "kind": "feature_class", "reference": "current-map", "evidence": prompt}],
                    "outputs": outputs,
                    "requirements": [{"requirement_id": "req:preserve", "predicate": {
                        "kind": "source_preserved", "subject": "input:context"}, "evidence": prompt}],
                    "allowed_side_effects": ["writes_data"], "clarifications": [],
                }, prompt)
                self.assertTrue(all(item["destination_policy"] == "server_derived"
                                    and "destination" not in item for item in contract["outputs"]))
                obsolete = dict(contract)
                obsolete["outputs"] = [dict(contract["outputs"][0], destination="default")]
                with self.assertRaises(TaskContractError):
                    parse_task_contract(obsolete, prompt)

    def test_quota_resume_preserves_succeeded_calls_and_releases_only_quota_node(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = JournalStore(Path(temporary) / "journal.sqlite")
            request = self._request()
            store.create_session(request.session_id, request.caller.tenant_id)
            run_id = store.create_run(request)["run_id"]
            store.reserve_model_call("ok", run_id, "minimax", "MiniMax-M3", "ok", {"role": "compiler"})
            store.commit_model_call("ok", "succeeded", {"value": 1}, {"role": "compiler"})
            store.reserve_model_call("quota", run_id, "minimax", "MiniMax-M3", "quota", {"role": "planner"})
            store.commit_model_call("quota", "quota_stopped", None, {"role": "planner"})
            store.append_event(
                run_id, "plan_failed", contracts.INTENT_COMPILED, {},
                outcome=contracts.outcome_failed(
                    contracts.QUOTA_STOPPED, "plan", "quota", "quota"))
            resumed = store.reopen_quota_stopped_run(run_id)
            self.assertEqual(contracts.INTENT_COMPILED, resumed["stage"])
            self.assertIsNone(resumed["outcome_kind"])
            self.assertEqual("succeeded", store.get_model_call("ok")["status"])
            reconstructed = GeoPilotKernel(fakes.build_fake_ports(store))._reconstruct_request(
                store.get_run(run_id))
            self.assertEqual(request.model_plan_digest, reconstructed.model_plan_digest)

    def test_quota_recovery_reconstructs_the_original_model_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = JournalStore(Path(temporary) / "journal.sqlite")
            request = self._request()
            store.create_session(request.session_id, request.caller.tenant_id)
            run_id = store.create_run(request)["run_id"]
            store.reserve_model_call("quota", run_id, "minimax", "MiniMax-M3", "quota", {"role": "planner"})
            store.commit_model_call("quota", "quota_stopped", None, {"role": "planner"})
            store.append_event(run_id, "plan_failed", contracts.INTENT_COMPILED, {},
                outcome=contracts.outcome_failed(contracts.QUOTA_STOPPED, "plan", "quota", "quota"))
            store.reopen_quota_stopped_run(run_id)
            kernel = GeoPilotKernel(fakes.build_fake_ports(store))
            reconstructed = kernel._reconstruct_request(store.get_run(run_id))
            self.assertEqual(request.model_plan_digest, reconstructed.model_plan_digest)
            self.assertEqual(request.model_binding_summary, reconstructed.model_binding_summary)
            quota_calls = [item for item in store.list_model_calls_for_run(run_id)
                           if item["call_key"] == "quota"]
            self.assertEqual(["quota_stopped"], [item["status"] for item in quota_calls])
            store.reserve_model_call("quota", run_id, "minimax", "MiniMax-M3", "quota", {"role": "planner"})
            store.commit_model_call("quota", "succeeded", {"value": 2}, {"role": "planner"})
            quota_calls = [item for item in store.list_model_calls_for_run(run_id)
                           if item["call_key"] == "quota"]
            self.assertEqual(["quota_stopped", "succeeded"], [item["status"] for item in quota_calls])
            with self.assertRaises(ValueError):
                store.reopen_quota_stopped_run(run_id)

    def test_crash_after_resume_generation_can_resume_again_without_new_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = JournalStore(Path(temporary) / "journal.sqlite")
            request = self._request()
            store.create_session(request.session_id, request.caller.tenant_id)
            run_id = store.create_run(request)["run_id"]
            store.reserve_model_call("quota", run_id, "minimax", "MiniMax-M3", "quota",
                                     {"role": "planner"})
            store.commit_model_call("quota", "quota_stopped", None, {"role": "planner"})
            store.append_event(
                run_id, "plan_failed", contracts.INTENT_COMPILED, {},
                outcome=contracts.outcome_failed(
                    contracts.QUOTA_STOPPED, "plan", "quota", "quota"))
            store.reopen_quota_stopped_run(run_id)
            self.assertTrue(store.has_active_quota_resume(run_id))
            with store._connection() as conn:
                before = conn.execute(
                    "SELECT COUNT(*) FROM planning_lineages WHERE run_id=?", (run_id,),
                ).fetchone()[0]

            restarted = GeoPilotKernel(fakes.build_fake_ports(store))
            restarted._drive_lifecycle_locked = lambda actual_run_id: (
                "continued" if actual_run_id == run_id else None
            )
            self.assertEqual("continued", restarted.resume_quota(run_id))
            with store._connection() as conn:
                after = conn.execute(
                    "SELECT COUNT(*) FROM planning_lineages WHERE run_id=?", (run_id,),
                ).fetchone()[0]
            self.assertEqual(before, after)

    def test_each_quota_attempt_requires_a_new_resume_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = JournalStore(Path(temporary) / "journal.sqlite")
            request = self._request()
            store.create_session(request.session_id, request.caller.tenant_id)
            run_id = store.create_run(request)["run_id"]
            for expected_attempt in (1, 2):
                store.reserve_model_call("quota", run_id, "minimax", "MiniMax-M3", "quota",
                                         {"role": "planner"})
                store.commit_model_call("quota", "quota_stopped", None, {"role": "planner"})
                store.append_event(
                    run_id, "plan_failed", contracts.INTENT_COMPILED, {},
                    outcome=contracts.outcome_failed(
                        contracts.QUOTA_STOPPED, "plan", "quota", "quota"))
                store.reopen_quota_stopped_run(run_id)
                self.assertEqual(expected_attempt, len([
                    item for item in store.list_model_calls_for_run(run_id)
                    if item["call_key"] == "quota"]))
            self.assertTrue(store.reserve_model_call(
                "quota", run_id, "minimax", "MiniMax-M3", "quota", {"role": "planner"}))

    def test_failed_model_call_identity_cannot_be_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = JournalStore(Path(temporary) / "journal.sqlite")
            request = self._request()
            store.create_session(request.session_id, request.caller.tenant_id)
            run_id = store.create_run(request)["run_id"]
            store.reserve_model_call("failed", run_id, "minimax", "MiniMax-M3", "hash",
                                     {"role": "planner"})
            store.commit_model_call("failed", "failed", None, {"role": "planner"})
            with self.assertRaisesRegex(RuntimeError, "cannot be retried"):
                store.reserve_model_call("failed", run_id, "minimax", "MiniMax-M3", "hash",
                                         {"role": "planner"})

    def test_experiment_port_is_the_complete_supervisor_boundary(self):
        class Kernel(object):
            runtime_identity = {"planner": {"provider": "minimax"}}
            def submit(self, request): return ("submit", request)
            def inspect(self, run_id): return ("inspect", run_id)
            def decide(self, run_id, decision): return ("decide", run_id)
            def resume_quota(self, run_id): return ("resume", run_id)
            def export_run_journal(self, run_id): return {"run_id": run_id}
        kernel = Kernel()
        port = ExperimentKernelPort(kernel)
        decision = contracts.AuthorizationDecision(
            decision_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), plan_digest="p", approved=False)
        self.assertEqual(kernel.runtime_identity, port.runtime_identity)
        self.assertEqual(("decide", decision.run_id), port.decide(decision))
        self.assertEqual({"run_id": "r"}, port.export_run_journal("r"))

    def test_runtime_gate_schema_hash_is_single_source_for_all_three_endpoints(self):
        from shared_runtime.runtime_gate_protocol import SCHEMA_SHA256, RUNTIME_GATE_PROTOCOL
        root = Path(__file__).resolve().parents[2]
        raw = (root / "shared_runtime" / "runtime_gate.schema.json").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), SCHEMA_SHA256)
        bridge = (root / "ArcMapBridgeExternal" / "Program.cs").read_text(encoding="utf-8")
        self.assertIn('RuntimeGateSchemaSha256 = "%s"' % SCHEMA_SHA256, bridge)
        self.assertIn('RuntimeGateProtocol = "%s"' % RUNTIME_GATE_PROTOCOL, bridge)
        spec = (root / "packaging" / "pyinstaller_gateway.spec").read_text(encoding="utf-8")
        build = (root / "packaging" / "build_release.ps1").read_text(encoding="utf-8")
        self.assertIn("shared_runtime/runtime_gate.schema.json", spec)
        self.assertIn("shared_runtime\\runtime_gate.schema.json", build)


if __name__ == "__main__":
    unittest.main()
