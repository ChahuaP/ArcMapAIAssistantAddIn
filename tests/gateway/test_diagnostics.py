import json
import os
import pathlib
import tempfile
import unittest

from gateway_py3.diagnostics import collect_diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_collect_diagnostics_checks_install_files_and_version(self):
        old_appdata = os.environ.get("APPDATA")
        old_localappdata = os.environ.get("LOCALAPPDATA")
        old_key = os.environ.get("DEEPSEEK_API_KEY")
        try:
            with tempfile.TemporaryDirectory() as root:
                root_path = pathlib.Path(root)
                appdata = root_path / "AppData"
                localappdata = root_path / "LocalAppData"
                install_dir = root_path / "ArcMapAIAssistant"
                addin_dir = root_path / "AddIns"
                os.environ["APPDATA"] = str(appdata)
                os.environ["LOCALAPPDATA"] = str(localappdata)
                os.environ.pop("DEEPSEEK_API_KEY", None)

                _write(install_dir / "arcmap_runtime_py2" / "runtime.py", "")
                _write(install_dir / "operation_catalog" / "catalog.json", "{}")
                _write(install_dir / "gateway" / "ArcMapAIAssistantGateway.exe", "")
                _write(install_dir / "OpenAssistantWeb.cmd", "")
                _write(install_dir / "StartGateway.cmd", "")
                _write(install_dir / "VERSION", "0.10.5")
                _write(addin_dir / "arcmapaiassistantaddin.esriaddin", "")
                _write(appdata / "ArcMapAIAssistant" / "config.json", json.dumps({
                    "providers": {
                        "deepseek": {"api_key": "unit-test-key"},
                        "minimax": {"api_key": "unit-test-key"}
                    }
                }))
                _write(
                    appdata / "ArcMapAIAssistant" / "install.json",
                    json.dumps({"install_dir": str(install_dir), "app_version": "0.10.5", "addin_dir": str(addin_dir)}),
                )

                result = collect_diagnostics("0.10.5", 39, network_check=False)
                checks = {item["id"]: item for item in result["checks"]}
                self.assertTrue(result["ok"])
                self.assertEqual(checks["installed_catalog"]["status"], "ok")
                self.assertEqual(checks["installed_version"]["status"], "ok")
                self.assertEqual(checks["installed_addin"]["status"], "ok")
            self.assertEqual(checks["provider_key"]["status"], "ok")
        finally:
            _restore_env("APPDATA", old_appdata)
            _restore_env("LOCALAPPDATA", old_localappdata)
            _restore_env("DEEPSEEK_API_KEY", old_key)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _restore_env(name, value):
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
