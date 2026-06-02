import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from gateway_py3.llm_providers import DeepSeekProvider, ProviderError, load_config, provider_api_key, provider_api_key_source, public_config, save_config
from gateway_py3.routes.common import config_payload


class LlmProviderConfigTests(unittest.TestCase):
    def test_deepseek_thinking_option_maps_to_api_model_with_thinking_enabled(self):
        provider = DeepSeekProvider(api_key="sk-test", model="deepseek-v4-flash-thinking")

        body = provider._prepare_body({"model": provider.model, "temperature": 0.1})

        self.assertEqual(provider.model, "deepseek-v4-flash")
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "max")
        self.assertNotIn("temperature", body)

    def test_deepseek_non_thinking_option_maps_to_same_api_model_without_thinking(self):
        provider = DeepSeekProvider(api_key="sk-test", model="deepseek-v4-flash-non-thinking")

        body = provider._prepare_body({"model": provider.model, "temperature": 0.1})

        self.assertEqual(provider.model, "deepseek-v4-flash")
        self.assertNotIn("thinking", body)
        self.assertEqual(body["temperature"], 0.1)

    def test_public_config_exposes_invalid_saved_model_without_losing_key_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "config.json"
            path.write_text(json.dumps({
                "semi_agent_provider": "deepseek",
                "semi_agent_model": "deepseek-chat",
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


if __name__ == "__main__":
    unittest.main()
