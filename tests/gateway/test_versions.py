import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


class VersionTests(unittest.TestCase):
    def test_gateway_versions_stay_aligned(self):
        app_version = _find(ROOT / "gateway_py3" / "app.py", r'APP_VERSION = "([^"]+)"')
        opener_version = _find(ROOT / "gateway_py3" / "open_web.py", r'EXPECTED_APP_VERSION = "([^"]+)"')
        web_version = _find(ROOT / "gateway_py3" / "web" / "app.js", r"EXPECTED_GATEWAY_VERSION = '([^']+)'")
        runtime_version = _find(ROOT / "arcmap_runtime_py2" / "gateway_client.py", r'EXPECTED_APP_VERSION = "([^"]+)"')
        self.assertEqual(app_version, opener_version)
        self.assertEqual(app_version, web_version)
        self.assertEqual(app_version, runtime_version)

    def test_web_opener_uses_clean_local_url(self):
        opener = (ROOT / "gateway_py3" / "open_web.py").read_text(encoding="utf-8")
        build_script = (ROOT / "packaging" / "build_release.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("WEB_URL = BASE_URL", opener)
        self.assertIn('"http://127.0.0.1:8765"', build_script)
        self.assertNotIn("?v=", build_script)


def _find(path, pattern):
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    if not match:
        raise AssertionError("Version not found in %s" % path)
    return match.group(1)


if __name__ == "__main__":
    unittest.main()
