import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


class VersionTests(unittest.TestCase):
    def test_gateway_versions_stay_aligned(self):
        app_version = _find(ROOT / "gateway_py3" / "app.py", r'APP_VERSION = "([^"]+)"')
        opener_version = _find(ROOT / "gateway_py3" / "open_web.py", r'EXPECTED_APP_VERSION = "([^"]+)"')
        web_version = _find(ROOT / "gateway_py3" / "web" / "index.html", r"EXPECTED_GATEWAY_VERSION = '([^']+)'")
        self.assertEqual(app_version, opener_version)
        self.assertEqual(app_version, web_version)


def _find(path, pattern):
    match = re.search(pattern, path.read_text(encoding="utf-8"))
    if not match:
        raise AssertionError("Version not found in %s" % path)
    return match.group(1)


if __name__ == "__main__":
    unittest.main()
