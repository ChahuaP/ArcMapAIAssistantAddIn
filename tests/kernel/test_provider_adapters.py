import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gateway_py3.model_runtime.adapters import create_provider_adapter
from gateway_py3.model_runtime.configuration import ModelConfigurationStore
from gateway_py3.model_runtime.contracts import (
    ProviderConnection,
    ProviderInvocation,
    StructuredOutputContract,
)
from gateway_py3.model_runtime.credentials import DpapiCredentialVault


class _Response:
    def __init__(self, document):
        self._body = json.dumps(document).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ProviderAdapterTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.vault = DpapiCredentialVault(self.directory / "credentials.json")
        self.vault.put("credential:deepseek-main", "secret")

    def test_openai_compatible_adapter_returns_forced_structured_result(self):
        connection = ProviderConnection(
            connection_id="deepseek-main",
            provider_type="deepseek",
            endpoint="https://api.deepseek.com/v1",
            credential_ref="credential:deepseek-main",
            enabled_models=("deepseek-chat",),
            deployment_fingerprint="deepseek-public-api",
        )
        adapter = create_provider_adapter(connection, self.vault)
        response = _Response({
            "choices": [{"message": {"tool_calls": [{"function": {
                "name": "emit_plan", "arguments": "{\"steps\": []}",
            }}]}}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        })
        call = ProviderInvocation(
            model_id="deepseek-chat",
            messages=[{"role": "user", "content": "plan"}],
            structured_contract=StructuredOutputContract(
                name="emit_plan", description="plan",
                schema={"type": "object", "properties": {"steps": {"type": "array"}}},
            ),
            tools=[], temperature=0.0, max_output_tokens=1024,
            context_token_limit=8192,
        )

        with patch("gateway_py3.model_runtime.adapters.openai_compatible.urllib.request.urlopen",
                   return_value=response) as request:
            result = adapter.invoke(call)

        self.assertEqual({"steps": []}, result.response)
        self.assertEqual(18, result.usage["total_tokens"])
        body = json.loads(request.call_args.args[0].data.decode("utf-8"))
        self.assertEqual("deepseek-chat", body["model"])
        self.assertEqual("emit_plan", body["tool_choice"]["function"]["name"])

    def test_anthropic_compatible_adapter_returns_forced_structured_result(self):
        self.vault.put("credential:zhipu-coding", "coding-secret")
        connection = ProviderConnection(
            connection_id="zhipu-coding",
            provider_type="zhipu_coding",
            endpoint="https://open.bigmodel.cn/api/anthropic",
            credential_ref="credential:zhipu-coding",
            enabled_models=("glm-5.2",),
            deployment_fingerprint="zhipu-coding-public-api",
        )
        adapter = create_provider_adapter(connection, self.vault)
        response = _Response({
            "content": [{"type": "tool_use", "name": "emit_plan",
                         "input": {"steps": []}}],
            "usage": {"input_tokens": 5, "output_tokens": 3},
        })
        call = ProviderInvocation(
            model_id="glm-5.2",
            messages=[{"role": "system", "content": "system"},
                      {"role": "user", "content": "plan"}],
            structured_contract=StructuredOutputContract(
                name="emit_plan", description="plan", schema={"type": "object"},
            ),
            tools=[], temperature=0.0, max_output_tokens=1024,
            context_token_limit=8192,
        )

        with patch("gateway_py3.model_runtime.adapters.anthropic_compatible.urllib.request.urlopen",
                   return_value=response):
            result = adapter.invoke(call)

        self.assertEqual({"steps": []}, result.response)
        self.assertEqual(8, result.usage["total_tokens"])


class ModelConfigurationStoreTest(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.vault = DpapiCredentialVault(self.directory / "credentials.json")
        self.store = ModelConfigurationStore(
            path=self.directory / "model_configuration.json", vault=self.vault,
        )

    def test_multiple_connections_and_per_role_bindings_round_trip(self):
        payload = {
            "connections": [
                {"connection_id": "minimax-official", "provider_type": "minimax",
                 "endpoint": "https://api.minimaxi.com/v1",
                 "enabled_models": ["MiniMax-M3"], "api_key": "mini-secret"},
                {"connection_id": "deepseek-main", "provider_type": "deepseek",
                 "endpoint": "https://api.deepseek.com/v1",
                 "enabled_models": ["deepseek-chat"], "api_key": "deep-secret"},
                {"connection_id": "ollama-local", "provider_type": "ollama",
                 "endpoint": "http://127.0.0.1:11434/v1",
                 "enabled_models": ["qwen3:32b"]},
            ],
            "agent_model_plan": {
                "compiler": {"connection_id": "deepseek-main", "model_id": "deepseek-chat"},
                "planner": {"connection_id": "ollama-local", "model_id": "qwen3:32b"},
                "auditor": {"connection_id": "minimax-official", "model_id": "MiniMax-M3"},
                "repairer": {"connection_id": "deepseek-main", "model_id": "deepseek-chat"},
            },
        }

        saved = self.store.save(payload)
        loaded = self.store.load()
        public = self.store.public()

        self.assertEqual(saved, loaded)
        self.assertEqual("ollama-local", loaded.plan.planner.connection_id)
        self.assertTrue(self.vault.has("credential:minimax-official"))
        self.assertTrue(self.vault.has("credential:deepseek-main"))
        self.assertNotIn("credential_ref", json.dumps(public))
        self.assertNotIn("mini-secret", json.dumps(public))
        self.assertNotIn("token_plan", json.dumps(public))
        self.assertEqual("deepseek-main", public["agent_model_plan"]["compiler"]["connection_id"])

    def test_unknown_provider_type_is_rejected(self):
        payload = {
            "connections": [{
                "connection_id": "mystery", "provider_type": "mystery",
                "endpoint": "https://example.invalid/v1",
                "enabled_models": ["model"], "api_key": "secret",
            }],
            "agent_model_plan": {
                role: {"connection_id": "mystery", "model_id": "model"}
                for role in ("compiler", "planner", "auditor", "repairer")
            },
        }
        with self.assertRaisesRegex(ValueError, "unsupported provider_type"):
            self.store.save(payload)


if __name__ == "__main__":
    unittest.main()
