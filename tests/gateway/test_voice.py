import base64
import json
import unittest
from unittest import mock

from gateway_py3 import voice
from gateway_py3.llm_providers import MiniMaxProvider


class VoiceTests(unittest.TestCase):
    def test_audio_data_uri_validation_accepts_browser_audio(self):
        data = base64.b64encode(b"abc").decode("ascii")

        voice.validate_audio_data_uri("data:audio/webm;codecs=opus;base64,%s" % data)

    def test_audio_data_uri_validation_rejects_non_audio(self):
        data = base64.b64encode(b"abc").decode("ascii")

        with self.assertRaises(ValueError):
            voice.validate_audio_data_uri("data:text/plain;base64,%s" % data)

    def test_compact_context_keeps_layer_names_and_fields(self):
        result = voice.compact_arcmap_context({
            "mxd_path": "D:/Maps/test.mxd",
            "is_saved": True,
            "spatial_reference": {"name": "WGS 1984", "type": "Geographic"},
            "layers": [{
                "name": "nanjing",
                "longName": "Group\\nanjing",
                "geometry_type": "Polygon",
                "selected_count": 2,
                "fields": [{"name": "NAME", "type": "String"}],
            }],
        })

        self.assertEqual(result["layers"][0]["name"], "nanjing")
        self.assertEqual(result["layers"][0]["fields"][0]["name"], "NAME")

    def test_correction_uses_current_mode_provider(self):
        provider = mock.Mock()
        provider.chat_json.return_value = {"needs_value_profile": False, "layers": []}
        provider.chat_text.return_value = {"text": "按 @nanjing 的 NAME 字段选择玄武区"}

        with mock.patch("gateway_py3.voice.create_provider", return_value=provider) as factory:
            result = voice.correct_voice_command("按南京的内木字段选择玄武区", {"layers": []}, "full_agent")

        factory.assert_called_once_with(mode="full_agent")
        provider.chat_json.assert_called_once()
        provider.chat_text.assert_called_once()
        self.assertEqual(result, "按 @nanjing 的 NAME 字段选择玄武区")

    def test_correction_reads_field_value_samples_when_model_requests_them(self):
        provider = mock.Mock()
        provider.chat_json.return_value = {"needs_value_profile": True, "layers": ["layer:0"]}
        provider.chat_text.return_value = {"text": "按 @nanjing 的 NAME 字段选择玄武区"}
        context = {
            "layers": [{
                "layer_ref": "layer:0",
                "name": "nanjing",
                "longName": "nanjing",
                "fields": [
                    {"name": "NAME", "type": "String", "value_samples": ["玄武区", "秦淮区"]},
                    {"name": "Shape_Area", "type": "Double", "value_samples": []},
                ],
            }]
        }

        with mock.patch("gateway_py3.voice.create_provider", return_value=provider):
            result = voice.correct_voice_command("按南京的内木字段选择宣武区", context, "full_agent")

        payload = json.loads(provider.chat_text.call_args[0][0][1]["content"])
        profiles = payload["attribute_value_profiles"]
        self.assertEqual(result, "按 @nanjing 的 NAME 字段选择玄武区")
        self.assertEqual(profiles[0]["layer_ref"], "layer:0")
        self.assertEqual(profiles[0]["fields"][0]["name"], "NAME")
        self.assertEqual(profiles[0]["fields"][0]["value_samples"], ["玄武区", "秦淮区"])
        self.assertEqual(len(profiles[0]["fields"]), 1)

    def test_correction_requires_layer_reference_when_field_values_are_needed(self):
        provider = mock.Mock()
        provider.chat_json.return_value = {"needs_value_profile": True, "layers": []}

        with mock.patch("gateway_py3.voice.create_provider", return_value=provider):
            with self.assertRaises(voice.ProviderError):
                voice.correct_voice_command("选择宣武区", {"layers": []}, "full_agent")

    def test_minimax_text_strips_thinking_block(self):
        provider = MiniMaxProvider(api_key="key", model="MiniMax-M3")
        with mock.patch.object(provider, "_post_chat_completion", return_value={
            "choices": [{"message": {"content": "<think>内部推理</think>按 @nanjing 选择玄武区"}}],
            "usage": {},
        }):
            result = provider.chat_text([])

        self.assertEqual(result["text"], "按 @nanjing 选择玄武区")

    def test_minimax_json_strips_thinking_before_parse(self):
        provider = MiniMaxProvider(api_key="key", model="MiniMax-M3")
        with mock.patch.object(provider, "_post_chat_completion", return_value={
            "choices": [{"message": {"content": '<think>内部推理</think>{"command":"打开 nanjing"}'}}],
            "usage": {},
        }):
            result = provider.chat_json([])

        self.assertEqual(result["command"], "打开 nanjing")

    def test_minimax_agent_message_strips_thinking_before_tool_parse(self):
        provider = MiniMaxProvider(api_key="key", model="MiniMax-M3")
        with mock.patch.object(provider, "_post_chat_completion", return_value={
            "choices": [{
                "message": {
                    "content": (
                        "<think>内部推理</think>"
                        "<minimax:tool_call><invoke name=\"catalog_get_operation_schema\">"
                        "<parameter name=\"operation_id\">\"layer.add_layer\"</parameter>"
                        "</invoke></minimax:tool_call>"
                    )
                }
            }],
            "usage": {},
        }):
            result = provider.chat_agent([], [])

        message = result["message"]
        self.assertIsNone(message["content"])
        arguments = json.loads(message["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(arguments["operation_id"], "layer.add_layer")


if __name__ == "__main__":
    unittest.main()
