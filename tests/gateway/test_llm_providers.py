import os
import pathlib
import tempfile
import unittest

from gateway_py3 import llm_providers


class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self._old_env = {
            name: os.environ.get(name)
            for name in ("APPDATA", "LOCALAPPDATA", "DEEPSEEK_API_KEY", "MINIMAX_API_KEY", "ARCMAP_AI_CONFIG")
        }
        for name in self._old_env:
            os.environ.pop(name, None)

    def tearDown(self):
        for name, value in self._old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_legacy_deepseek_config_is_migrated_in_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            config_dir = appdata / "ArcMapAIAssistant"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "config.json"
            config_path.write_text('{"deepseek_api_key": "unit-test-key", "model": "deepseek-chat"}', encoding="utf-8-sig")

            config = llm_providers.load_config()

        self.assertEqual(config["providers"]["deepseek"]["api_key"], "unit-test-key")
        self.assertEqual(config["semi_agent_provider"], "deepseek")
        self.assertEqual(config["full_agent_provider"], "minimax")

    def test_public_config_reports_both_providers(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            path = appdata / "ArcMapAIAssistant" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"providers":{"deepseek":{"api_key":"unit-test-key"},"minimax":{"api_key":"unit-test-key"}}}', encoding="utf-8")

            config = llm_providers.public_config()

        self.assertTrue(config["providers"]["deepseek"]["has_api_key"])
        self.assertTrue(config["providers"]["minimax"]["has_api_key"])
        self.assertEqual(config["config_path"], str(path))

    def test_minimax_default_uses_token_plan_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)

            config = llm_providers.public_config()

        self.assertEqual(config["providers"]["minimax"]["base_url"], "https://api.minimaxi.com/v1")

    def test_legacy_minimax_endpoint_is_migrated_to_token_plan_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            path = appdata / "ArcMapAIAssistant" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                '{"providers":{"minimax":{"api_key":"unit-test-key","base_url":"https://api.minimax.io/v1"}}}',
                encoding="utf-8"
            )

            config = llm_providers.load_config()

        self.assertEqual(config["providers"]["minimax"]["base_url"], "https://api.minimaxi.com/v1")

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
