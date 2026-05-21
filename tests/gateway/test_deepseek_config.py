import os
import pathlib
import tempfile
import unittest

from gateway_py3 import deepseek_client


class DeepSeekConfigTests(unittest.TestCase):
    def setUp(self):
        self._old_env = {
            name: os.environ.get(name)
            for name in ("APPDATA", "LOCALAPPDATA", "DEEPSEEK_API_KEY", "ARCMAP_AI_CONFIG")
        }
        for name in self._old_env:
            os.environ.pop(name, None)

    def tearDown(self):
        for name, value in self._old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_load_config_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            config_dir = appdata / "ArcMapAIAssistant"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "config.json"
            config_path.write_text('{"deepseek_api_key": "sk-test", "model": "deepseek-chat"}', encoding="utf-8-sig")

            self.assertEqual(deepseek_client.load_api_key(), "sk-test")

    def test_public_config_reports_active_path(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            path = appdata / "ArcMapAIAssistant" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"deepseek_api_key": "sk-test"}', encoding="utf-8")

            config = deepseek_client.public_config()

        self.assertTrue(config["has_deepseek_api_key"])
        self.assertEqual(config["config_path"], str(path))
        self.assertTrue(config["config_file_exists"])

    def test_missing_key_message_names_wrong_field(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = pathlib.Path(directory) / "Roaming"
            localappdata = pathlib.Path(directory) / "Local"
            os.environ["APPDATA"] = str(appdata)
            os.environ["LOCALAPPDATA"] = str(localappdata)
            path = appdata / "ArcMapAIAssistant" / "config.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"api_key": "sk-test"}', encoding="utf-8")

            message = deepseek_client.missing_api_key_message()

        self.assertIn(str(path), message)
        self.assertIn("deepseek_api_key", message)


if __name__ == "__main__":
    unittest.main()
