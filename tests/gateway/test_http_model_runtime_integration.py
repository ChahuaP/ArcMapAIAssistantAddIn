"""HTTP -> kernel -> ModelRuntime integration contract.

This starts the production ``app.Handler`` in a real ``ThreadingHTTPServer``.
The only substituted edge is a registered in-process provider adapter: no
network model provider is reachable from this test.
"""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from gateway_py3 import app
from gateway_py3.api.http_adapter import GeoPilotHttpAdapter
from gateway_py3.kernel.coordinator import GeoPilotKernel
from gateway_py3.kernel.store import JournalStore
from gateway_py3.model_runtime import ModelRuntime
from gateway_py3.model_runtime.adapter import ProviderError
from gateway_py3.model_runtime.contracts import ProviderInvocation, ProviderResponse
from gateway_py3.model_runtime.registry import ProviderRegistry
from tests.kernel import fakes


class _BoundCompiler:
    """Test port that preserves the public compiler contract and task plan."""

    def __init__(self, runtime):
        self.runtime = runtime

    def compile(self, request, context, capabilities, run_id=""):
        from gateway_py3.kernel import contracts
        from gateway_py3.kernel.contracts import outcome_failed, outcome_paused, outcome_succeeded
        from gateway_py3.model_runtime import ModelRequest, StructuredOutputContract
        result = self.runtime.invoke(ModelRequest(
            tenant_id=request.caller.tenant_id, security_scope_hash="http-test-scope",
            role="compiler", prompt_version="http-test-v1", system_prompt="compile",
            user_input=request.text, tool_contract={"type": "object"},
            capability_hash="http-test-capabilities", context_projection={"request": request.text},
            domain_rule_hash="http-test-rules", model_plan=request.model_plan,
            model_binding_summary=request.model_binding_summary, generation_params={}, run_id=run_id,
        ), StructuredOutputContract("submit_task_contract", "test", {"type": "object"}))
        if result.status == "quota_stopped":
            return outcome_failed(contracts.QUOTA_STOPPED, "intent", "quota", result.error or "quota")
        if not result.succeeded:
            return outcome_failed(contracts.CONTRACT_FAILED, "intent", "model", result.error or "model")
        clarifications = result.response["task_contract"]["clarifications"]
        if clarifications:
            requirements = [{"requirement_id": "req:selection", "predicate": {
                "kind": "attribute_filter", "subject": "input:cities",
                "target": "input:cities", "selection_type": "unresolved"}}]
            return outcome_paused(contracts.CLARIFICATION_REQUIRED, "intent", "clarification",
                                  clarifications[0]["question"],
                                  details={"clarifications": clarifications,
                                           "task_contract_draft": {"input_entities": [], "outputs": [],
                                               "requirements": requirements, "allowed_side_effects": ["read_only"],
                                               "clarifications": clarifications}})
        return outcome_succeeded("intent", "compiled", details={
            "intent": fakes._fake_intent(request, context, capabilities)})

    def resume_with_patch(self, request, context, capabilities, task_contract):
        from gateway_py3.kernel.contracts import outcome_succeeded
        return outcome_succeeded("intent", "compiled", details={
            "intent": fakes._fake_intent(request, context, capabilities)})


class _BoundPlanner:
    """Test port that makes its real ModelRuntime request under sealed plan."""

    def __init__(self, runtime):
        self.runtime = runtime

    def plan(self, run_id, intent, context, capabilities):
        return self.plan_ablation(run_id, intent, context, capabilities, auditor_enabled=True)

    def plan_ablation(self, run_id, intent, context, capabilities, auditor_enabled, sealed_baseline=None):
        from gateway_py3.kernel import contracts
        from gateway_py3.kernel.contracts import outcome_failed, outcome_succeeded
        from gateway_py3.model_runtime import ModelRequest
        request = ModelRequest(
            tenant_id="local-tenant", security_scope_hash="http-test-scope", role="planner",
            prompt_version="http-test-v1", system_prompt="plan", user_input=intent.business_goal,
            tool_contract={}, tools=[{"name": "select_layer"}],
            capability_hash="http-test-capabilities", context_projection={"intent": intent.business_goal},
            domain_rule_hash="http-test-rules", model_plan=self.runtime.model_plan,
            model_binding_summary=self.runtime.binding_summary(self.runtime.model_plan),
            generation_params={}, run_id=run_id,
        )
        result = self.runtime.invoke(request, None)
        if result.status == "quota_stopped":
            return outcome_failed(contracts.QUOTA_STOPPED, "plan", "quota", result.error or "quota")
        if not result.succeeded:
            return outcome_failed(contracts.CONTRACT_FAILED, "plan", "model", result.error or "model")
        return outcome_succeeded("plan", "planned", details={
            "plan": fakes._fake_plan(intent, context, capabilities)})

    def decide_authorization(self, run_id, approved):
        return "authorized" if approved else "denied"


class _RegisteredTestAdapter:
    """Strict local provider seam; records real ``ProviderInvocation`` values."""

    provider_type = "test"
    connection_id = "test-primary"

    def __init__(self, *, clarify=False, quota_once=False):
        self.clarify = clarify
        self.quota_once = quota_once
        self.calls = []

    def invoke(self, call: ProviderInvocation, on_token=None) -> ProviderResponse:
        self.calls.append(call)
        if self.quota_once:
            self.quota_once = False
            raise ProviderError("quota", "local quota stop")
        if call.tools:
            body = {"tool_calls": [{"name": "select_layer",
                                     "arguments": {"layer": "cities"}}]}
        elif call.structured_contract.name == "submit_task_contract":
            body = {"task_contract": {
                "input_entities": [], "outputs": [], "requirements": [],
                "allowed_side_effects": ["read_only"],
                "clarifications": ([{"option_id": "selection.state",
                                      "question": "current selection?"}]
                                   if self.clarify else []),
            }}
        if on_token is not None:
            on_token("ok")
        return ProviderResponse(response=body, usage={"total_tokens": 1, "provider": "test"})


class HttpModelRuntimeIntegrationTest(unittest.TestCase):
    """The public HTTP boundary must seal, invoke and journal one exact route."""

    def setUp(self):
        self.store = JournalStore(Path(tempfile.mkdtemp()) / "journal.sqlite")
        self.adapter_impl = _RegisteredTestAdapter()
        self.runtime = self._runtime(self.adapter_impl)
        self.kernel = self._kernel(self.runtime)
        self.http = GeoPilotHttpAdapter(self.kernel)
        self._old_state = app.STATE
        app.STATE = SimpleNamespace(adapter=self.http, projection=None)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        app.STATE = self._old_state

    def _runtime(self, adapter, *, duplicate=False):
        primary = fakes.fake_provider_connection(
            provider_type="test", model_id="Test-1", connection_id="test-primary",
            endpoint="http://127.0.0.1:19001/v1")
        registry = ProviderRegistry()
        registry.register(primary, adapter)
        if duplicate:
            other = fakes.fake_provider_connection(
                provider_type="test", model_id="Test-1", connection_id="test-secondary",
                endpoint="http://127.0.0.1:19002/v1")
            other_adapter = _RegisteredTestAdapter()
            other_adapter.connection_id = "test-secondary"
            registry.register(other, other_adapter)
        return ModelRuntime(registry, fakes.fake_agent_model_plan("test-primary", "Test-1"), self.store)

    def _kernel(self, runtime):
        from gateway_py3.kernel.coordinator import KernelPorts
        from gateway_py3.runtime.policy import PolicyGate
        return GeoPilotKernel(KernelPorts(
            store=self.store, context=fakes.FakeContextProvider(),
            capabilities=fakes.FakeCapabilityProvider(),
            compiler=_BoundCompiler(runtime), planner=_BoundPlanner(runtime),
            policy=PolicyGate(), executor=fakes.FakeArcMapExecutor(),
            acceptance=fakes.FakeAcceptancePublisher(), model=runtime, bridge=None,
        ))

    def _request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        values = dict(headers or {})
        if body is not None:
            values["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=values)
        response = connection.getresponse()
        decoded = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, decoded

    def _headers(self):
        status, session = self._request("GET", "/api/v1/active-session")
        self.assertEqual(200, status)
        return {"X-Session-Id": session["session_id"], "X-Session-Epoch": str(session["epoch"]), "Origin": "http://127.0.0.1:8765",
                "X-CSRF-Token": session["csrf_token"]}

    @staticmethod
    def _payload(**changes):
        value = {
            "text": "select cities", "model_bindings": {
                role: {"provider": "test", "model": "Test-1"}
                for role in ("compiler", "planner", "auditor", "repairer")
            },
            "target_selector": {"bridge_pid": 2001, "bridge_port": 8766,
                                "arcmap_pid": 2000, "hwnd": 3000,
                                "deployment_hash": "a" * 64},
        }
        value.update(changes)
        return value

    def _wait(self, run_id):
        return fakes.wait_for_terminal(self.kernel, run_id, timeout=5)

    def test_real_handler_seals_exact_binding_and_journals_actual_invocation(self):
        status, body = self._request("POST", "/api/v1/runs", self._payload(), self._headers())
        self.assertEqual(200, status)
        run_id = body["run"]["run_id"]
        view = self._wait(run_id)
        self.assertEqual("clarification_required", view.stage)
        self.assertEqual(2, len(self.adapter_impl.calls))
        self.assertTrue(all(call.model_id == "Test-1" for call in self.adapter_impl.calls))
        calls = self.store.list_model_calls_for_run(run_id)
        self.assertEqual(2, len(calls))
        received = next(event["payload"] for event in self.store.run_events(run_id)
                        if event["kind"] == "run_received")
        self.assertEqual(received["model_binding_summary"],
                         self.runtime.binding_summary(
                             self.runtime.seal_task_plan(self._payload()["model_bindings"])))
        self.assertEqual({"test"}, {call["provider"] for call in calls})
        self.assertEqual({"Test-1"}, {call["model"] for call in calls})
        for call in calls:
            self.assertEqual("test-primary", call["ledger"]["connection_id"])
            self.assertEqual("test", call["ledger"]["provider"])
            self.assertEqual("Test-1", call["ledger"]["model"])

    def test_http_rejects_missing_unknown_ambiguous_and_client_injected_routes(self):
        headers = self._headers()
        bad = self._payload()
        del bad["model_bindings"]["auditor"]
        self.assertEqual(400, self._request("POST", "/api/v1/runs", bad, headers)[0])
        for provider, model in (("unknown", "Test-1"), ("test", "Unknown")):
            value = self._payload()
            value["model_bindings"]["planner"] = {"provider": provider, "model": model}
            self.assertEqual(400, self._request("POST", "/api/v1/runs", value, headers)[0])
        injected = self._payload()
        injected["model_bindings"]["planner"]["connection_id"] = "test-primary"
        self.assertEqual(400, self._request("POST", "/api/v1/runs", injected, headers)[0])
        injected = self._payload()
        injected["model_bindings"]["planner"]["endpoint"] = "http://attacker.invalid/v1"
        self.assertEqual(400, self._request("POST", "/api/v1/runs", injected, headers)[0])

    def test_ambiguous_registered_provider_model_is_rejected_at_http_boundary(self):
        self.runtime = self._runtime(_RegisteredTestAdapter(), duplicate=True)
        self.kernel = self._kernel(self.runtime)
        app.STATE.adapter = GeoPilotHttpAdapter(self.kernel)
        self.assertEqual(400, self._request("POST", "/api/v1/runs", self._payload(), self._headers())[0])

    def test_clarification_and_quota_resume_preserve_sealed_ledger_identity(self):
        self.adapter_impl = _RegisteredTestAdapter(clarify=True)
        self.runtime = self._runtime(self.adapter_impl)
        self.kernel = self._kernel(self.runtime)
        app.STATE.adapter = GeoPilotHttpAdapter(self.kernel)
        status, body = self._request("POST", "/api/v1/runs", self._payload(text="quota verification task"), self._headers())
        self.assertEqual(200, status)
        run_id = body["run"]["run_id"]
        self.assertEqual("clarification_required", self._wait(run_id).stage)
        sealed = next(event["payload"]["model_binding_summary"]
                      for event in self.store.run_events(run_id) if event["kind"] == "run_received")
        self.assertTrue(all(call["ledger"]["connection_id"] == sealed[call["ledger"]["role"]]["connection_id"]
                            for call in self.store.list_model_calls_for_run(run_id)))

        # A fresh runtime with a quota stop is used to verify that only an
        # explicit resume creates a second immutable attempt.
        quota_adapter = _RegisteredTestAdapter(quota_once=True)
        quota_runtime = self._runtime(quota_adapter)
        quota_kernel = self._kernel(quota_runtime)
        app.STATE.adapter = GeoPilotHttpAdapter(quota_kernel)
        status, body = self._request("POST", "/api/v1/runs", self._payload(), self._headers())
        self.assertEqual(200, status)
        quota_run = body["run"]["run_id"]
        self.assertEqual("quota_stopped", fakes.wait_for_terminal(quota_kernel, quota_run).stage)
        before = self.store.list_model_calls_for_run(quota_run)
        self.assertEqual(["quota_stopped"], [item["status"] for item in before])
        status, resumed = self._request("POST", "/api/v1/runs/%s/resume-quota" % quota_run, {}, self._headers())
        self.assertEqual(200, status)
        self.assertEqual("clarification_required", resumed["run"]["stage"])
        # Resume uses a new ModelRuntime attempt, never a fallback route.
        self.assertEqual("clarification_required", fakes.wait_for_terminal(quota_kernel, quota_run).stage)
        after = self.store.list_model_calls_for_run(quota_run)
        self.assertEqual(["quota_stopped", "succeeded", "succeeded"],
                         [item["status"] for item in after])
        for item in after:
            self.assertEqual("test", item["provider"])
            self.assertEqual("Test-1", item["model"])
            self.assertEqual("test-primary", item["ledger"]["connection_id"])


if __name__ == "__main__":
    unittest.main()






