# -*- coding: utf-8 -*-
"""Stage B ModelRuntime tests (§B.5, §11 Cache).

Covers: identical call zero-second-request, any hash component miss,
concurrent single-flight, cross-tenant isolation, failed/quota/uncertain
never cached, and restart reuse.
"""
from __future__ import absolute_import

import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Dict, List

from gateway_py3.intelligence.model_runtime import (
    ModelAdapter, ModelRequest, ModelRuntime, render_messages,
)
from gateway_py3.kernel.store import JournalStore
from gateway_py3.llm_providers import StructuredOutputContract, ProviderError
from pydantic import BaseModel


CONTRACT = StructuredOutputContract(
    name="test_contract", description="test", schema={"type": "object"},
)


def _request(**overrides) -> ModelRequest:
    defaults = dict(
        tenant_id="t1", security_scope_hash="ssh1", provider="fake",
        model="Fake", role="planner", prompt_version="v1",
        system_prompt="you are planner", user_input="select cities",
        tool_contract={"type": "object"}, capability_hash="ch",
        context_projection={"layers": ["cities"]}, domain_rule_hash="drh",
        generation_params={}, run_id="00000000-0000-0000-0000-000000000001",
    )
    defaults.update(overrides)
    return ModelRequest(**defaults)


class _CountingAdapter:
    """Fake adapter that counts calls and returns a fixed structured result."""

    def __init__(self, response: Dict[str, Any] = None,
                 error: Exception = None,
                 delay: float = 0.0):
        self.provider = "fake"
        self.model = "Fake"
        self.call_count = 0
        self._response = response or {"action": "ok", "summary": "done"}
        self._error = error
        self._delay = delay

    def chat_structured(self, messages: List[Dict[str, str]],
                        contract: StructuredOutputContract) -> Dict[str, Any]:
        self.call_count += 1
        if self._delay:
            import time; time.sleep(self._delay)
        if self._error:
            raise self._error
        return dict(self._response, _usage={"provider": "fake", "total_tokens": 10})


class _BaseModelRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "gp.sqlite"
        self.store = JournalStore(path=self.tmp)


class CacheHitTest(_BaseModelRuntimeTest):
    """Identical call zero-second-request (§11 Cache)."""

    def test_identical_call_uses_cache_no_second_adapter_call(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        r1 = runtime.invoke(_request(), CONTRACT)
        r2 = runtime.invoke(_request(), CONTRACT)
        self.assertTrue(r1.succeeded)
        self.assertTrue(r2.succeeded)
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(r1.response, r2.response)


class HashComponentMissTest(_BaseModelRuntimeTest):
    """Any one of the 13 hash components changing causes a cache miss (§11)."""

    def test_tenant_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(tenant_id="t2"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_security_scope_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(security_scope_hash="ssh2"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_provider_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(provider="minimax"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_model_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(model="MiniMax-M3"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_role_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(role="auditor"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_prompt_version_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(prompt_version="v2"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_system_prompt_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(system_prompt="you are auditor"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_user_input_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(user_input="select roads"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_tool_contract_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(tool_contract={"type": "object", "extra": True}), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_capability_hash_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(capability_hash="ch2"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_context_projection_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(context_projection={"layers": ["roads"]}), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_domain_rule_hash_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(domain_rule_hash="drh2"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)

    def test_generation_params_change_misses(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT)
        runtime.invoke(_request(generation_params={"temperature": 0.1}), CONTRACT)
        self.assertEqual(adapter.call_count, 2)


class CrossTenantIsolationTest(_BaseModelRuntimeTest):
    """Cross-tenant cache never hits (§11 Cache, §5.3)."""

    def test_cross_tenant_never_hits(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(tenant_id="tenant_a"), CONTRACT)
        runtime.invoke(_request(tenant_id="tenant_b"), CONTRACT)
        self.assertEqual(adapter.call_count, 2)


class FailedQuotaUncertainNotCachedTest(_BaseModelRuntimeTest):
    """Failed, quota_stopped and uncertain calls are never reused (§11 Cache)."""

    def test_failed_not_cached(self):
        adapter = _CountingAdapter(error=ProviderError("protocol error"))
        runtime = ModelRuntime(adapter, self.store)
        r1 = runtime.invoke(_request(), CONTRACT)
        self.assertEqual(r1.status, "uncertain")
        adapter2 = _CountingAdapter()
        runtime2 = ModelRuntime(adapter2, self.store)
        r2 = runtime2.invoke(_request(), CONTRACT)
        self.assertEqual(r2.status, "succeeded")
        self.assertEqual(adapter2.call_count, 1)

    def test_quota_stopped_not_cached(self):
        adapter = _CountingAdapter(error=ProviderError("额度不足，余额已用完"))
        runtime = ModelRuntime(adapter, self.store)
        r1 = runtime.invoke(_request(), CONTRACT)
        self.assertEqual(r1.status, "quota_stopped")
        adapter2 = _CountingAdapter()
        runtime2 = ModelRuntime(adapter2, self.store)
        r2 = runtime2.invoke(_request(), CONTRACT)
        self.assertEqual(r2.status, "succeeded")
        self.assertEqual(adapter2.call_count, 1)


class RestartReuseTest(_BaseModelRuntimeTest):
    """A succeeded call persists; a new ModelRuntime reuses it (§11 Cache)."""

    def test_restart_reuses_succeeded_record(self):
        adapter1 = _CountingAdapter()
        runtime1 = ModelRuntime(adapter1, self.store)
        runtime1.invoke(_request(), CONTRACT)
        self.assertEqual(adapter1.call_count, 1)
        adapter2 = _CountingAdapter()
        runtime2 = ModelRuntime(adapter2, self.store)
        r = runtime2.invoke(_request(), CONTRACT)
        self.assertTrue(r.succeeded)
        self.assertEqual(adapter2.call_count, 0)


class ConcurrencySingleFlightTest(_BaseModelRuntimeTest):
    """Concurrent identical calls produce one adapter call (§11 Cache)."""

    def test_concurrent_identical_calls_single_adapter_call(self):
        adapter = _CountingAdapter(delay=0.3)
        runtime = ModelRuntime(adapter, self.store)
        results = []
        barrier = threading.Barrier(4)

        def call():
            barrier.wait()
            results.append(runtime.invoke(_request(), CONTRACT))

        threads = [threading.Thread(target=call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r.succeeded for r in results))
        self.assertEqual(adapter.call_count, 1)


class QuotaStopNoRetryTest(_BaseModelRuntimeTest):
    """Quota stop is terminal: no retry, no second provider (§2.7, §11)."""

    def test_quota_stop_does_not_retry(self):
        adapter = _CountingAdapter(error=ProviderError("余额不足"))
        runtime = ModelRuntime(adapter, self.store)
        r = runtime.invoke(_request(), CONTRACT)
        self.assertEqual(r.status, "quota_stopped")
        self.assertEqual(adapter.call_count, 1)


class _StreamingCountingAdapter(_CountingAdapter):
    """Fake adapter that supports streaming via ``chat_structured_stream``."""

    def chat_structured_stream(self, messages, contract, on_token):
        self.call_count += 1
        if self._delay:
            import time; time.sleep(self._delay)
        if self._error:
            raise self._error
        for word in ["plan", ":", "select", "cities"]:
            on_token(word)
        return dict(self._response, _usage={"provider": "fake", "total_tokens": 10})


class StreamingOutputTest(_BaseModelRuntimeTest):
    """§14: streaming pushes tokens via on_token before the final result."""

    def test_streaming_pushes_tokens(self):
        adapter = _StreamingCountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        tokens = []
        r = runtime.invoke(_request(), CONTRACT, on_token=tokens.append)
        self.assertTrue(r.succeeded)
        self.assertEqual(tokens, ["plan", ":", "select", "cities"])

    def test_non_streaming_adapter_silently_skips_on_token(self):
        adapter = _CountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        tokens = []
        r = runtime.invoke(_request(), CONTRACT, on_token=tokens.append)
        self.assertTrue(r.succeeded)
        self.assertEqual(tokens, [])

    def test_streaming_result_is_cached(self):
        adapter = _StreamingCountingAdapter()
        runtime = ModelRuntime(adapter, self.store)
        tokens1 = []
        runtime.invoke(_request(), CONTRACT, on_token=tokens1.append)
        tokens2 = []
        r2 = runtime.invoke(_request(), CONTRACT, on_token=tokens2.append)
        self.assertTrue(r2.succeeded)
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(tokens2, [])


class _ResponseModel(BaseModel):
    """A Pydantic model for response validation testing (§13.1)."""
    action: str
    summary: str


class ResponseModelValidationTest(_BaseModelRuntimeTest):
    """§13.1: response_model validates the adapter output via Pydantic."""

    def test_valid_response_passes_validation(self):
        adapter = _CountingAdapter(response={"action": "execute", "summary": "ok"})
        runtime = ModelRuntime(adapter, self.store)
        r = runtime.invoke(_request(), CONTRACT, response_model=_ResponseModel)
        self.assertTrue(r.succeeded)
        self.assertEqual(r.response["action"], "execute")

    def test_invalid_response_fails_not_uncertain(self):
        adapter = _CountingAdapter(response={"action": "execute"})
        runtime = ModelRuntime(adapter, self.store)
        r = runtime.invoke(_request(), CONTRACT, response_model=_ResponseModel)
        self.assertEqual(r.status, "failed")
        self.assertIn("schema validation failed", r.error)

    def test_failed_validation_not_cached(self):
        adapter = _CountingAdapter(response={"action": "execute"})
        runtime = ModelRuntime(adapter, self.store)
        runtime.invoke(_request(), CONTRACT, response_model=_ResponseModel)
        adapter2 = _CountingAdapter(response={"action": "execute", "summary": "ok"})
        runtime2 = ModelRuntime(adapter2, self.store)
        r2 = runtime2.invoke(_request(), CONTRACT, response_model=_ResponseModel)
        self.assertTrue(r2.succeeded)
        self.assertEqual(adapter2.call_count, 1)


if __name__ == "__main__":
    unittest.main()
