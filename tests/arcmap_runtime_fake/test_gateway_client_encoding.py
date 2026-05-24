import importlib.util
import pathlib
import sys
import types
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"


class GatewayClientEncodingTests(unittest.TestCase):
    def test_post_payload_escapes_non_ascii_for_python2_runtime(self):
        captured = {}

        class Request(object):
            def __init__(self, url, data=None, headers=None):
                self.url = url
                self.data = data
                self.headers = headers or {}

        sys.modules["urllib2"] = types.SimpleNamespace(Request=Request)
        spec = importlib.util.spec_from_file_location("gateway_client_encoding", RUNTIME_ROOT / "gateway_client.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def fake_request_json(request, timeout):
            captured["data"] = request.data
            captured["headers"] = request.headers
            return {"ok": True}

        module._request_json = fake_request_json
        module._post("/execution-result", {"summary": "对 nanjing 图层创建10米缓冲区"})

        data = captured["data"]
        self.assertIsInstance(data, bytes)
        self.assertTrue(all(byte < 128 for byte in data))
        self.assertEqual(captured["headers"]["Content-Type"], "application/json; charset=utf-8")

    def test_plan_waits_longer_than_model_timeout(self):
        captured = {}

        class Request(object):
            def __init__(self, url, data=None, headers=None):
                self.url = url
                self.data = data
                self.headers = headers or {}

        sys.modules["urllib2"] = types.SimpleNamespace(Request=Request)
        spec = importlib.util.spec_from_file_location("gateway_client_timeout", RUNTIME_ROOT / "gateway_client.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        def fake_request_json(request, timeout):
            captured["url"] = request.url
            captured["timeout"] = timeout
            return {"workflow": {}}

        module._request_json = fake_request_json
        module.plan("生成 OBJ", {"layers": []})

        self.assertTrue(captured["url"].endswith("/plan"))
        self.assertEqual(captured["timeout"], 360)


if __name__ == "__main__":
    unittest.main()
