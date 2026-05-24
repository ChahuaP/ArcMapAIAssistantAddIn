import json
import os
import pathlib
import tempfile
import unittest

from gateway_py3 import llm_providers


class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self._old_env = {
            name: os.environ.get(name)
            for name in ("APPDATA", "LOCALAPPDATA", "DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "ZHIPU_API_KEY", "BIGMODEL_API_KEY", "ARCMAP_AI_CONFIG")
        }
        for name in self._old_env:
            os.environ.pop(name, None)

    def tearDown(self):
        for name, value in self._old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_old_top_level_deepseek_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            config_dir = appdata / "ArcMapAIAssistant"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "config.json"
            config_path.write_text('{"deepseek_api_key": "unit-test-key", "model": "deepseek-chat"}', encoding="utf-8-sig")

            with self.assertRaisesRegex(llm_providers.ProviderError, "旧字段"):
                llm_providers.load_config()

    def test_public_config_reports_all_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            path = appdata / "ArcMapAIAssistant" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"providers":{"deepseek":{"api_key":"unit-test-key"},"zhipu":{"api_key":"unit-test-key"},"minimax":{"api_key":"unit-test-key"}}}',
                encoding="utf-8"
            )

            config = llm_providers.public_config()

        self.assertTrue(config["providers"]["deepseek"]["has_api_key"])
        self.assertTrue(config["providers"]["zhipu"]["has_api_key"])
        self.assertTrue(config["providers"]["minimax"]["has_api_key"])
        self.assertEqual(config["providers"]["zhipu"]["model"], "glm-5.1")
        self.assertIn({"provider": "zhipu", "model": "glm-5.1", "label": "智谱 GLM-5.1", "thinking": True}, config["model_options"])
        self.assertEqual(config["config_path"], str(path))

    def test_mode_model_selection_is_used_when_creating_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            path = appdata / "ArcMapAIAssistant" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({
                    "semi_agent_provider": "deepseek",
                    "semi_agent_model": "deepseek-v4-flash",
                    "full_agent_provider": "zhipu",
                    "full_agent_model": "glm-5.1",
                    "providers": {
                        "deepseek": {"api_key": "unit-test-key"},
                        "zhipu": {"api_key": "unit-test-key"},
                    },
                }),
                encoding="utf-8"
            )

            provider = llm_providers.create_provider(mode=llm_providers.FULL_AGENT_MODE)

        self.assertIsInstance(provider, llm_providers.ZhipuProvider)
        self.assertEqual(provider.model, "glm-5.1")

    def test_minimax_default_uses_token_plan_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)

            config = llm_providers.public_config()

        self.assertEqual(config["providers"]["minimax"]["base_url"], "https://api.minimaxi.com/v1")

    def test_minimax_custom_endpoint_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            path = appdata / "ArcMapAIAssistant" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"providers":{"minimax":{"api_key":"unit-test-key","base_url":"https://example.test/v1"}}}',
                encoding="utf-8"
            )

            config = llm_providers.load_config()

        self.assertEqual(config["providers"]["minimax"]["base_url"], "https://example.test/v1")

    def test_deepseek_v4_thinking_payload_uses_reasoning_effort(self):
        provider = llm_providers.DeepSeekProvider(api_key="unit-test-key", model="deepseek-v4-pro")

        body = provider._prepare_body({"model": provider.model, "messages": [], "temperature": 0})

        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "max")
        self.assertNotIn("temperature", body)

    def test_zhipu_glm_51_payload_enables_thinking(self):
        provider = llm_providers.ZhipuProvider(api_key="unit-test-key", model="glm-5.1")

        body = provider._prepare_body({"model": provider.model, "messages": [], "temperature": 0})

        self.assertEqual(body["thinking"], {"type": "enabled"})

    def test_provider_default_timeout_allows_slow_thinking_models(self):
        provider = llm_providers.ZhipuProvider(api_key="unit-test-key", model="glm-5.1")

        self.assertEqual(provider.timeout, 300)

    def test_minimax_text_tool_call_is_normalized_by_provider_layer(self):
        message = llm_providers._normalize_minimax_agent_message({
            "role": "assistant",
            "content": (
                "<think><minimax:tool_call>\n"
                "<invoke name=\"file_resolve\">\n"
                "<parameter name=\"drive\">D</parameter>\n"
                "<parameter name=\"directory_parts\">[\"Data\"]</parameter>\n"
                "<parameter name=\"file_name\">nanjing.shp</parameter>\n"
                "</invoke>\n"
                "</minimax:tool_call></think>"
            )
        })

        self.assertIsNone(message["content"])
        tool_call = message["tool_calls"][0]
        self.assertEqual(tool_call["function"]["name"], "file_resolve")
        arguments = json.loads(tool_call["function"]["arguments"])
        self.assertEqual(arguments["directory_parts"], ["Data"])

    def test_missing_key_message_names_provider_field(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            path = appdata / "ArcMapAIAssistant" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"providers":{"minimax":{"model":"MiniMax-M2.7"}}}', encoding="utf-8")

            message = llm_providers.missing_api_key_message("minimax")

        self.assertIn(str(path), message)
        self.assertIn("providers.minimax.api_key", message)

    def test_minimax_unauthorized_error_is_user_readable(self):
        raw = '{"type":"error","error":{"type":"authorized_error","message":"invalid api key (2049)","http_code":"401"}}'

        message = llm_providers.provider_http_error("minimax", 401, raw)

        self.assertIn("MiniMax Token Plan API Key 无效", message)
        self.assertIn("https://api.minimaxi.com", message)
        self.assertIn("invalid api key (2049)", message)
        self.assertNotIn('{"type"', message)


if __name__ == "__main__":
    unittest.main()
