"""Provider-neutral ModelRuntime interface tests."""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel

from gateway_py3.kernel import contracts
from gateway_py3.kernel.store import JournalStore
from gateway_py3.model_runtime import (
    AgentModelPlan, ModelBinding, ModelRequest, ModelRuntime,
    ProviderConnection, StructuredOutputContract, TokenPlan,
)
from gateway_py3.model_runtime.adapter import ProviderError
from gateway_py3.model_runtime.adapters.minimax import MiniMaxAdapter, _MiniMaxWireClient
from gateway_py3.model_runtime.adapters.minimax import MINIMAX_MODEL
from gateway_py3.model_runtime.configuration import ModelConfigurationStore
from gateway_py3.model_runtime.contracts import ProviderInvocation, ProviderResponse
from gateway_py3.model_runtime.registry import ProviderRegistry


CONTRACT = StructuredOutputContract(
    name="test_contract", description="test", schema={"type": "object"},
)


def _request(**overrides) -> ModelRequest:
    values = dict(
        tenant_id="t1", security_scope_hash="ssh1", role="planner",
        prompt_version="v1", system_prompt="you are planner",
        user_input="select cities", tool_contract={"type": "object"},
        capability_hash="ch", context_projection={"layers": ["cities"]},
        domain_rule_hash="drh", generation_params={},
        run_id="00000000-0000-0000-0000-000000000001",
    )
    values.update(overrides)
    return ModelRequest(**values)


def _connection(connection_id="fake-a", provider="fake", model="Fake",
                endpoint="http://fake-a.invalid/v1", deployment="deployment-a"):
    return ProviderConnection(
        connection_id=connection_id, provider_type=provider, endpoint=endpoint,
        credential_ref=None, enabled_models=(model,),
        deployment_fingerprint=deployment,
    )


def _plan(connection_id="fake-a", model="Fake", temperature=0.0,
          max_output_tokens=1000):
    budget = TokenPlan(
        call_budget=100, context_token_limit=100_000,
        output_token_limit=10_000, concurrency_limit=8,
        requests_per_minute=1_000, tokens_per_minute=10_000_000,
        cost_limit_microusd=None,
    )
    def bind(role):
        return ModelBinding(
            connection_id=connection_id, model_id=model, role=role,
            temperature=temperature, max_output_tokens=max_output_tokens,
            budget_policy=budget,
        )
    return AgentModelPlan(
        compiler=bind("compiler"), planner=bind("planner"),
        auditor=bind("auditor"), repairer=bind("repairer"),
    )


class _CountingAdapter:
    def __init__(self, connection_id="fake-a", provider_type="fake",
                 response: Optional[Dict[str, Any]] = None,
                 error: Optional[Exception] = None,
                 delay: float = 0.0, stream: bool = False):
        self.connection_id = connection_id
        self.provider_type = provider_type
        self.call_count = 0
        self.response = response or {"action": "ok", "summary": "done"}
        self.error = error
        self.delay = delay
        self.stream = stream
        self.calls = []

    def invoke(self, call: ProviderInvocation, on_token=None) -> ProviderResponse:
        self.call_count += 1
        self.calls.append(call)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        if self.stream and on_token is not None:
            for token in ("plan", ":", "cities"):
                on_token(token)
        return ProviderResponse(
            response=dict(self.response),
            usage={"provider": self.provider_type, "total_tokens": 10},
        )


def _runtime(store, adapter=None, connection=None, plan=None,
             extra_routes=()):
    adapter = adapter or _CountingAdapter()
    connection = connection or _connection(
        connection_id=adapter.connection_id, provider=adapter.provider_type,
    )
    registry = ProviderRegistry()
    registry.register(connection, adapter)
    for other_connection, other_adapter in extra_routes:
        registry.register(other_connection, other_adapter)
    return ModelRuntime(
        registry,
        plan or _plan(connection.connection_id, connection.enabled_models[0]),
        store,
    )


class _Base(unittest.TestCase):
    def setUp(self):
        self.store = JournalStore(Path(tempfile.mkdtemp()) / "gp.sqlite")

    def journalled_run_id(self):
        session_id = "00000000-0000-0000-0000-000000000099"
        request_id = "00000000-0000-0000-0000-000000000098"
        self.store.create_session(session_id, "t1")
        return self.store.create_run(contracts.RequestEnvelope(
            session_id=session_id, request_id=request_id, text="model journal",
            caller=contracts.CallerIdentity(user_id="u1", tenant_id="t1", role="analyst"),
            target_selector={"bridge_pid": 2001, "bridge_port": 8766,
                             "arcmap_pid": 2000, "hwnd": 3000,
                             "deployment_hash": "a" * 64},
        ))["run_id"]


class CacheIdentityTest(_Base):
    def test_identical_call_reuses_exact_result(self):
        adapter = _CountingAdapter()
        runtime = _runtime(self.store, adapter)
        first = runtime.invoke(_request(), CONTRACT)
        second = runtime.invoke(_request(), CONTRACT)
        self.assertTrue(first.succeeded and second.succeeded)
        self.assertEqual(1, adapter.call_count)

    def test_request_identity_components_each_miss(self):
        mutations = (
            {"tenant_id": "t2"}, {"security_scope_hash": "ssh2"},
            {"role": "auditor"}, {"prompt_version": "v2"},
            {"system_prompt": "audit"}, {"user_input": "roads"},
            {"tool_contract": {"type": "array"}},
            {"tools": [{"name": "x"}]}, {"capability_hash": "ch2"},
            {"context_projection": {"layers": ["roads"]}},
            {"domain_rule_hash": "drh2"},
            {"generation_params": {"diagnostics": ["x"]}},
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=mutation):
                store = JournalStore(Path(tempfile.mkdtemp()) / ("%d.sqlite" % index))
                adapter = _CountingAdapter()
                runtime = _runtime(store, adapter)
                runtime.invoke(_request(), CONTRACT)
                runtime.invoke(
                    _request(**mutation),
                    None if "tools" in mutation else CONTRACT,
                )
                self.assertEqual(2, adapter.call_count)

    def test_connection_model_endpoint_deployment_and_sampling_each_miss(self):
        variants = (
            (_connection(provider="other"), _plan()),
            (_connection(model="Other"), _plan(model="Other")),
            (_connection(endpoint="http://other.invalid/v1"), _plan()),
            (_connection(deployment="deployment-b"), _plan()),
            (_connection(), _plan(temperature=0.2)),
            (_connection(), _plan(max_output_tokens=2000)),
        )
        base_adapter = _CountingAdapter()
        _runtime(self.store, base_adapter).invoke(_request(), CONTRACT)
        for connection, plan in variants:
            adapter = _CountingAdapter(provider_type=connection.provider_type)
            _runtime(self.store, adapter, connection, plan).invoke(_request(), CONTRACT)
            self.assertEqual(1, adapter.call_count)


class RoleRoutingTest(_Base):
    def test_roles_resolve_independent_connections(self):
        first = _CountingAdapter("compiler-conn", "fake")
        second = _CountingAdapter("planner-conn", "local")
        first_connection = _connection("compiler-conn", "fake", "CompilerModel",
                                       "http://compiler.invalid/v1")
        second_connection = _connection("planner-conn", "local", "PlannerModel",
                                        "http://planner.invalid/v1")
        compiler_binding = _plan("compiler-conn", "CompilerModel").compiler
        planner_plan = _plan("planner-conn", "PlannerModel")
        plan = AgentModelPlan(
            compiler=compiler_binding, planner=planner_plan.planner,
            auditor=planner_plan.auditor, repairer=planner_plan.repairer,
        )
        runtime = _runtime(
            self.store, first, first_connection, plan,
            extra_routes=((second_connection, second),),
        )
        runtime.invoke(_request(role="compiler"), CONTRACT)
        runtime.invoke(_request(role="planner", user_input="planner"), CONTRACT)
        self.assertEqual((1, 1), (first.call_count, second.call_count))
        self.assertEqual("CompilerModel", first.calls[0].model_id)
        self.assertEqual("PlannerModel", second.calls[0].model_id)

    def test_missing_connection_fails_without_fallback(self):
        registry = ProviderRegistry()
        with self.assertRaises(LookupError):
            ModelRuntime(registry, _plan(), self.store)

    def test_provider_failure_never_switches_connection(self):
        failing = _CountingAdapter(error=ProviderError("transport", "offline"))
        unused = _CountingAdapter("fake-b")
        runtime = _runtime(
            self.store, failing,
            extra_routes=((_connection("fake-b", endpoint="http://b.invalid/v1"), unused),),
        )
        result = runtime.invoke(_request(), CONTRACT)
        self.assertEqual("uncertain", result.status)
        self.assertEqual((1, 0), (failing.call_count, unused.call_count))


class ContractAndRegistryTest(_Base):
    def test_installed_minimax_is_a_default_plan_not_a_runtime_constraint(self):
        configuration = ModelConfigurationStore(
            path=Path(tempfile.mkdtemp()) / "model_configuration.json",
        ).load()
        installed = configuration.plan
        self.assertTrue(all(binding.model_id == MINIMAX_MODEL
                            for binding in installed.bindings()))
        self.assertEqual("minimax", configuration.connections[0].provider_type)

        compiler = _plan("compiler-conn", "CompilerModel").compiler
        planner = _plan("planner-conn", "PlannerModel")
        mixed = AgentModelPlan(
            compiler=compiler, planner=planner.planner,
            auditor=planner.auditor, repairer=planner.repairer,
        )
        self.assertEqual("compiler-conn", mixed.compiler.connection_id)
        self.assertEqual("planner-conn", mixed.auditor.connection_id)

    def test_registry_rejects_duplicate_and_incomplete_adapters(self):
        connection = _connection()
        registry = ProviderRegistry()
        registry.register(connection, _CountingAdapter())
        with self.assertRaises(ValueError):
            registry.register(connection, _CountingAdapter())
        with self.assertRaises(TypeError):
            ProviderRegistry().register(connection, object())

    def test_binding_rejects_model_outside_connection(self):
        registry = ProviderRegistry()
        registry.register(_connection(), _CountingAdapter())
        with self.assertRaises(LookupError):
            ModelRuntime(registry, _plan(model="NotEnabled"), self.store)

    def test_endpoint_normalization_has_stable_fingerprint(self):
        first = _connection(endpoint="HTTPS://Example.Invalid/v1/")
        second = _connection(endpoint="https://example.invalid/v1")
        self.assertEqual(first.endpoint, second.endpoint)
        self.assertEqual(first.endpoint_fingerprint, second.endpoint_fingerprint)


class EvidenceAndFailureTest(_Base):
    def test_call_evidence_records_resolved_connection_and_parameters(self):
        run_id = self.journalled_run_id()
        runtime = _runtime(self.store)
        result = runtime.invoke(_request(run_id=run_id), CONTRACT)
        record = self.store.get_model_call(result.call_key)
        for field in (
            "connection_id", "endpoint_fingerprint", "deployment_fingerprint",
            "credential_ref", "role", "parameters", "token_plan",
        ):
            self.assertIn(field, record["ledger"])
        events = [event for event in self.store.run_events(run_id)
                  if event["kind"].startswith("model.call_")]
        self.assertEqual(2, len(events))
        self.assertEqual("planner", events[0]["payload"]["role"])
        self.assertIn("latency_ms", events[1]["payload"])

    def test_provider_error_classification_is_terminal_and_not_retried(self):
        for kind, status in (("quota", "quota_stopped"),
                             ("protocol", "failed"),
                             ("transport", "uncertain"),
                             ("uncertain", "uncertain")):
            with self.subTest(kind=kind):
                store = JournalStore(Path(tempfile.mkdtemp()) / (kind + ".sqlite"))
                adapter = _CountingAdapter(error=ProviderError(kind, kind))
                first = _runtime(store, adapter).invoke(_request(), CONTRACT)
                self.assertEqual(status, first.status)
                if status in ("quota_stopped", "uncertain"):
                    second_adapter = _CountingAdapter()
                    second = _runtime(store, second_adapter).invoke(_request(), CONTRACT)
                    self.assertEqual(status, second.status)
                    self.assertEqual(0, second_adapter.call_count)

    def test_response_schema_validation_is_not_cacheable(self):
        adapter = _CountingAdapter(response={"action": "missing summary"})
        result = _runtime(self.store, adapter).invoke(
            _request(), CONTRACT, response_model=_ResponseModel,
        )
        self.assertEqual("failed", result.status)


class ConcurrencyAndStreamingTest(_Base):
    def test_concurrent_identical_calls_are_single_flight(self):
        adapter = _CountingAdapter(delay=0.2)
        runtime = _runtime(self.store, adapter)
        results = []
        barrier = threading.Barrier(4)
        def call():
            barrier.wait()
            results.append(runtime.invoke(_request(), CONTRACT))
        threads = [threading.Thread(target=call) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)
        self.assertEqual(4, len(results))
        self.assertTrue(all(item.succeeded for item in results))
        self.assertEqual(1, adapter.call_count)

    def test_streaming_uses_same_adapter_interface_and_cache(self):
        adapter = _CountingAdapter(stream=True)
        runtime = _runtime(self.store, adapter)
        tokens = []
        runtime.invoke(_request(), CONTRACT, on_token=tokens.append)
        cached_tokens = []
        runtime.invoke(_request(), CONTRACT, on_token=cached_tokens.append)
        self.assertEqual(["plan", ":", "cities"], tokens)
        self.assertEqual([], cached_tokens)
        self.assertEqual(1, adapter.call_count)


class _MiniMaxStream(_MiniMaxWireClient):
    def __init__(self, events):
        super().__init__(api_key="test", endpoint="https://api.minimaxi.com/v1")
        self.events = events
        self.body = None

    def _stream_chat_completion(self, body):
        self.body = body
        return iter(self.events)


class MiniMaxAdapterNormalizationTest(unittest.TestCase):
    def test_missing_credential_fails_as_protocol_without_network_call(self):
        class MissingVault:
            def get(self, credential_ref):
                raise KeyError(credential_ref)

        connection = ModelConfigurationStore(
            path=Path(tempfile.mkdtemp()) / "model_configuration.json",
        ).load().connections[0]
        adapter = MiniMaxAdapter(connection, MissingVault())
        invocation = ProviderInvocation(
            model_id=MINIMAX_MODEL,
            messages=[{"role": "user", "content": "test"}],
            structured_contract=CONTRACT,
            tools=[],
            temperature=0.0,
            max_output_tokens=100,
            context_token_limit=1000,
        )
        with self.assertRaises(ProviderError) as raised:
            adapter.invoke(invocation)
        self.assertEqual("protocol", raised.exception.kind)

    def test_tool_call_and_text_deltas_normalize_identically(self):
        contract = StructuredOutputContract("operate", "test", {"type": "object"})
        tool = _MiniMaxStream([
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "operate", "arguments": '{"x":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "1}"}}]}}]},
            {"usage": {"total_tokens": 7}},
        ])
        text = _MiniMaxStream([
            {"choices": [{"delta": {"content": '<minimax:tool_call><invoke name="operate"><parameter name="x">1</parameter></invoke></minimax:tool_call>'}}]},
            {"usage": {"total_tokens": 7}},
        ])
        args = ([], contract, lambda _token: None, "MiniMax-M3", 0.0, 1024)
        first = tool.chat_structured_stream(*args)
        second = text.chat_structured_stream(*args)
        self.assertEqual(first, second)
        self.assertEqual({"x": 1, "_usage": {"provider": "minimax", "total_tokens": 7}}, first)
        self.assertEqual({"include_usage": True}, tool.body["stream_options"])


class _ResponseModel(BaseModel):
    action: str
    summary: str


if __name__ == "__main__":
    unittest.main()
