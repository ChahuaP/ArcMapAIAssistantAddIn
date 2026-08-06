import json
import os
import pathlib
import socket
import tempfile
import urllib.error
import unittest
from unittest.mock import patch

from gateway_py3.llm_providers import (
    DeepSeekProvider,
    MiniMaxProvider,
    ProviderError,
    ProviderProtocolError,
    StructuredOutputContract,
    ZHIPU_CODING_PROVIDER,
    ZhipuCodingProvider,
    load_config,
    provider_api_key,
    provider_api_key_source,
    public_config,
    save_config,
)
from gateway_py3.routes.common import config_payload
from gateway_py3.task_contract import (
    bind_model_task_contract,
    parse_task_contract,
    task_contract_for_context,
)


class _UrlResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class LlmProviderConfigTests(unittest.TestCase):
    def test_minimax_structured_result_preserves_the_fixed_task_contract_wire(self):
        command = "\n筛选人口不少于800且脆弱性为HIGH的社区，生成 affected_comm.shp。输出到 D:\\results。"
        context = {"layers": [{
            "layer_ref": "layer:communities", "name": "communities",
            "geometry_type": "Point", "spatial_reference": "EPSG:32650",
            "selected_count": 0,
            "fields": [{"name": "POP"}, {"name": "VULN_LVL"}],
        }]}
        draft = {
            "input_entities": [{
                "entity_id": "input:communities", "role": "source",
                "reference": "layer:communities",
            }],
            "outputs": [{
                "output_id": "output:affected_comm", "kind": "feature_class",
                "name": "affected_comm", "format": "shp", "geometry": "point",
                "required_fields": [], "spatial_reference": "EPSG:32650",
                "destination": r"D:\results", "evidence": "affected_comm.shp",
            }],
            "requirements": [{
                "requirement_id": "filter",
                "predicate_json": json.dumps({
                    "kind": "attribute_filter", "subject": "input:communities",
                    "target": "input:communities", "selection_type": "new_selection",
                    "where": {"op": "and", "conditions": [
                        {"field": "POP", "op": "gte", "value": 800},
                        {"field": "VULN_LVL", "op": "eq", "value": "HIGH"},
                    ]},
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            }, {
                "requirement_id": "export",
                "predicate_json": json.dumps({
                    "kind": "artifact_export", "subject": "output:affected_comm",
                    "target": "input:communities", "action": "export_selected_features",
                    "selected_only": True,
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            }],
            "allowed_side_effects": ["changes_map", "writes_data"],
            "clarifications": [],
        }
        contract = task_contract_for_context(context, command)
        response_payload = {
            "choices": [{"message": {"tool_calls": [{
                "type": "function",
                "function": {
                    "name": contract.name,
                    "arguments": json.dumps({"task_contract": draft}, ensure_ascii=False),
                },
            }]}}],
            "usage": {},
        }
        provider = MiniMaxProvider(
            api_key="minimax-test-key", model="MiniMax-M3",
            base_url="https://api.minimaxi.com/v1",
        )

        with patch("urllib.request.urlopen", return_value=_UrlResponse(response_payload)):
            result = provider.chat_structured(
                [{"role": "user", "content": command}], contract,
            )

        parsed = parse_task_contract(
            bind_model_task_contract(result["task_contract"], command, context), command, context,
        )
        self.assertEqual(command, parsed["input_entities"][0]["evidence"])
        self.assertIsInstance(parsed["requirements"][0]["predicate"]["where"]["conditions"], list)

    def test_zhipu_coding_plan_uses_forced_anthropic_tool_contract(self):
        contract = StructuredOutputContract(
            name="submit_audit_result",
            description="Submit the audit result.",
            schema={
                "type": "object",
                "properties": {"audit_result": {"type": "object"}},
                "required": ["audit_result"],
                "additionalProperties": False,
            },
        )
        response_payload = {
            "id": "msg-1",
            "model": "glm-5.2",
            "content": [{
                "type": "tool_use",
                "id": "tool-1",
                "name": "submit_audit_result",
                "input": {"audit_result": {"decision": "pass"}},
            }],
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }
        response = _UrlResponse(response_payload)
        provider = ZhipuCodingProvider(
            api_key="coding-plan-test-key",
            model="glm-5.2",
            base_url="https://open.bigmodel.cn/api/anthropic",
        )

        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            result = provider.chat_structured(
                [
                    {"role": "system", "content": "Audit."},
                    {"role": "user", "content": "{}"},
                ],
                contract,
            )

        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, "https://open.bigmodel.cn/api/anthropic/v1/messages")
        self.assertEqual(body["model"], "glm-5.2")
        self.assertEqual(body["system"], "Audit.")
        self.assertEqual(body["tool_choice"], {"type": "tool", "name": "submit_audit_result"})
        self.assertEqual(body["tools"][0]["input_schema"], contract.schema)
        self.assertNotIn("coding-plan-test-key", request.data.decode("utf-8"))
        self.assertEqual(result["audit_result"], {"decision": "pass"})
        self.assertEqual(result["_usage"]["provider"], ZHIPU_CODING_PROVIDER)
        self.assertEqual(result["_provider_response"], response_payload)

    def test_structured_contract_rejects_missing_tool_without_text_fallback(self):
        contract = StructuredOutputContract(
            name="submit_workflow",
            description="Submit a workflow.",
            schema={"type": "object"},
        )
        response_payload = {
            "id": "msg-2",
            "model": "glm-5.2",
            "content": [{"type": "text", "text": "```json\n{}\n```"}],
            "usage": {},
        }
        provider = ZhipuCodingProvider(
            api_key="coding-plan-test-key",
            model="glm-5.2",
            base_url="https://open.bigmodel.cn/api/anthropic",
        )

        with patch("urllib.request.urlopen", return_value=_UrlResponse(response_payload)):
            with self.assertRaises(ProviderProtocolError) as raised:
                provider.chat_structured([{"role": "user", "content": "{}"}], contract)

        self.assertEqual(raised.exception.evidence, response_payload)

    def test_deepseek_thinking_option_maps_to_api_model_with_thinking_enabled(self):
        provider = DeepSeekProvider(api_key="sk-test", model="deepseek-v4-flash-thinking")

        body = provider._prepare_body({"model": provider.model, "temperature": 0.1})

        self.assertEqual(provider.model, "deepseek-v4-flash")
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "max")
        self.assertNotIn("temperature", body)

    def test_deepseek_non_thinking_option_explicitly_disables_default_thinking(self):
        provider = DeepSeekProvider(api_key="sk-test", model="deepseek-v4-flash-non-thinking")

        body = provider._prepare_body({"model": provider.model, "temperature": 0.1})

        self.assertEqual(provider.model, "deepseek-v4-flash")
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", body)
        self.assertEqual(body["temperature"], 0.1)

    def test_public_config_exposes_invalid_saved_model_without_losing_key_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.json"
            path.write_text(json.dumps({
                "primary_provider": "deepseek",
                "primary_model": "deepseek-chat",
                "providers": {
                    "deepseek": {
                        "api_key": "sk-test",
                        "model": "deepseek-chat",
                        "base_url": "https://api.deepseek.com",
                    }
                },
            }), encoding="utf-8")

            with patch.dict(os.environ, {"ARCMAP_AI_CONFIG": str(path)}, clear=False):
                config = public_config()
                with self.assertRaises(ProviderError):
                    load_config()

        self.assertIn("deepseek-chat", config["config_error"])
        self.assertTrue(config["providers"]["deepseek"]["has_api_key"])

    def test_unknown_saved_config_field_fails_fast(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.json"
            path.write_text(json.dumps({"unknown_slot": "value"}), encoding="utf-8")

            with patch.dict(os.environ, {"ARCMAP_AI_CONFIG": str(path)}, clear=False):
                with self.assertRaises(ProviderError):
                    load_config()

    def test_qwen_token_plan_key_has_explicit_runtime_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.json"
            path.write_text(json.dumps({
                "providers": {
                    "qwen": {
                        "api_key": "regular-config-key",
                        "token_plan_api_key": "token-plan-config-key",
                    }
                },
            }), encoding="utf-8")

            with patch.dict(os.environ, {
                "ARCMAP_AI_CONFIG": str(path),
                "BAILIAN_TOKEN_PLAN_API_KEY": "",
                "DASHSCOPE_TOKEN_PLAN_API_KEY": "",
                "DASHSCOPE_API_KEY": "regular-env-key",
            }, clear=False):
                key = provider_api_key("qwen")
                source = provider_api_key_source("qwen")
                config = public_config()

        self.assertEqual(key, "token-plan-config-key")
        self.assertEqual(source["field"], "token_plan_api_key")
        self.assertEqual(source["source"], "config")
        self.assertIn("Token Plan", source["label"])
        self.assertTrue(config["providers"]["qwen"]["key_status"]["token_plan_api_key"])
        self.assertTrue(config["providers"]["qwen"]["key_status"]["api_key"])

    def test_save_config_can_clear_qwen_token_plan_key_without_removing_regular_key(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "appdata"
            localappdata = pathlib.Path(directory) / "localappdata"
            appdata.mkdir()
            localappdata.mkdir()
            with patch.dict(os.environ, {
                "APPDATA": str(appdata),
                "LOCALAPPDATA": str(localappdata),
                "ARCMAP_AI_CONFIG": "",
                "BAILIAN_TOKEN_PLAN_API_KEY": "",
                "DASHSCOPE_TOKEN_PLAN_API_KEY": "",
            }, clear=False):
                save_config({
                    "providers": {
                        "qwen": {
                            "api_key": "regular-config-key",
                            "token_plan_api_key": "token-plan-config-key",
                        }
                    }
                })
                save_config({
                    "providers": {
                        "qwen": {
                            "clear_secret_fields": ["token_plan_api_key"],
                        }
                    }
                })
                config = load_config()
                key = provider_api_key("qwen")
                public = public_config(config)

        self.assertEqual(key, "regular-config-key")
        self.assertEqual(config["providers"]["qwen"].get("api_key"), "regular-config-key")
        self.assertNotIn("token_plan_api_key", config["providers"]["qwen"])
        self.assertTrue(public["providers"]["qwen"]["key_status"]["api_key"])
        self.assertFalse(public["providers"]["qwen"]["key_status"]["token_plan_api_key"])

    def test_save_config_repairs_invalid_saved_provider_models(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "appdata"
            localappdata = pathlib.Path(directory) / "localappdata"
            config_dir = appdata / "ArcMapAIAssistant"
            config_dir.mkdir(parents=True)
            localappdata.mkdir()
            (config_dir / "config.json").write_text(json.dumps({
                "primary_provider": "deepseek",
                "primary_model": "deepseek-v4-flash",
                "providers": {
                    "deepseek": {
                        "api_key": "sk-test",
                        "model": "deepseek-chat",
                        "base_url": "https://api.deepseek.com",
                    },
                    "zhipu": {
                        "model": "glm-5.1",
                        "base_url": "https://open.bigmodel.cn/api/paas/v4",
                    }
                },
            }), encoding="utf-8")

            with patch.dict(os.environ, {
                "APPDATA": str(appdata),
                "LOCALAPPDATA": str(localappdata),
                "ARCMAP_AI_CONFIG": "",
            }, clear=False):
                with self.assertRaises(ProviderError):
                    load_config()
                save_config({
                    "primary_provider": "deepseek",
                    "primary_model": "deepseek-v4-flash-thinking",
                    "providers": {
                        "deepseek": {"model": "deepseek-v4-flash-thinking"},
                        "zhipu": {"model": "glm-5.1-thinking"},
                    }
                })
                config = load_config()

        self.assertEqual(config["primary_model"], "deepseek-v4-flash-thinking")
        self.assertEqual(config["providers"]["deepseek"]["model"], "deepseek-v4-flash-thinking")
        self.assertEqual(config["providers"]["zhipu"]["model"], "glm-5.1-thinking")
        self.assertEqual(config["providers"]["deepseek"]["api_key"], "sk-test")

    def test_config_payload_only_allows_known_secret_fields_to_clear(self):
        payload = config_payload({
            "providers": {
                "qwen": {
                    "clear_secret_fields": ["token_plan_api_key"],
                }
            }
        })

        self.assertEqual(payload["providers"]["qwen"]["clear_secret_fields"], ["token_plan_api_key"])
        with self.assertRaises(ValueError):
            config_payload({
                "providers": {
                    "qwen": {
                        "clear_secret_fields": ["unknown_key"],
                    }
                }
            })

    def test_model_dns_failure_returns_chinese_message(self):
        provider = DeepSeekProvider(api_key="sk-test", model="deepseek-v4-flash-thinking", base_url="https://bad.example")
        error = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))

        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ProviderError) as raised:
                provider.chat_text([{"role": "user", "content": "你好"}])

        message = str(raised.exception)
        self.assertIn("网络连接失败", message)
        self.assertIn("无法解析模型接口域名", message)
        self.assertIn("https://bad.example", message)
        self.assertNotIn("urlopen", message)
        self.assertNotIn("getaddrinfo failed", message)


if __name__ == "__main__":
    unittest.main()
