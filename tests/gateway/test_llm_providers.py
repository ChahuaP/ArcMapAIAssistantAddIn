import json
import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from gateway_py3.llm_providers import DeepSeekProvider, ProviderError, load_config, public_config


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


if __name__ == "__main__":
    unittest.main()
