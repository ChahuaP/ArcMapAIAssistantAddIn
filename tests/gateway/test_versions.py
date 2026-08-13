import os
import pathlib
import unittest
from unittest.mock import patch

from shared_runtime import platform_paths
from gateway_py3.release import APP_VERSION


ROOT = pathlib.Path(__file__).resolve().parents[2]


class VersionTests(unittest.TestCase):
    def test_gateway_versions_use_repository_version_file(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="ascii").strip(), APP_VERSION)
        sources = (
            ROOT / "gateway_py3" / "app.py",
            ROOT / "gateway_py3" / "open_web.py",
            ROOT / "gateway_py3" / "api" / "http_adapter.py",
            ROOT / "arcmap_runtime_py2" / "gateway_client.py",
            ROOT / "gateway_py3" / "web" / "app.js",
            ROOT / "README.md",
        )
        for source in sources:
            with self.subTest(source=source.name):
                text = source.read_text(encoding="utf-8")
                self.assertNotIn('"1.1.4"', text)
                self.assertNotIn("'2.0.0'", text)
                self.assertNotIn('"2.0.0"', text)

        addin_config = (ROOT / "ArcMapAIAssistantAddIn" / "config.xml").read_text(encoding="utf-8")
        addin_builder = (ROOT / "ArcMapAIAssistantAddIn" / "makeaddin.py").read_text(encoding="utf-8")
        self.assertIn("<Version>__GEOPILOT_VERSION__</Version>", addin_config)
        self.assertIn('os.path.join(REPO_ROOT, "VERSION")', addin_builder)
        self.assertIn("config.replace(VERSION_TOKEN, version)", addin_builder)

        bridge_info = (ROOT / "ArcMapBridgeExternal" / "Properties" / "AssemblyInfo.cs").read_text(encoding="utf-8")
        bridge_build = (ROOT / "ArcMapBridgeExternal" / "build.ps1").read_text(encoding="utf-8-sig")
        self.assertNotIn("AssemblyVersion(", bridge_info)
        self.assertIn('Join-Path $repoRoot "VERSION"', bridge_build)
        self.assertIn('AssemblyVersion(`"$version`")', bridge_build)
        bridge_project = (ROOT / "ArcMapBridgeExternal" / "ArcMapBridgeExternal.csproj").read_text(encoding="utf-8")
        self.assertIn('<PackageReference Include="Newtonsoft.Json">', bridge_project)
        self.assertNotIn('packages\\Newtonsoft.Json', bridge_project)
        self.assertFalse((ROOT / "ArcMapBridgeExternal" / "packages.config").exists())

    def test_installer_requires_version_from_build_script(self):
        setup = (ROOT / "packaging" / "GeoPilotSetup.iss").read_text(encoding="utf-8-sig")
        build = (ROOT / "packaging" / "build_release.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("#error MyAppVersion must be supplied", setup)
        self.assertIn('Join-Path $repoRoot "VERSION"', build)
        self.assertIn('Join-Path $repoRoot "operation_catalog"', build)

    def test_setup_has_one_authoritative_postinstall_exit_contract(self):
        """The elevated Setup process owns installation and propagates script failure."""
        setup = (ROOT / "packaging" / "GeoPilotSetup.iss").read_text(encoding="utf-8-sig")
        install = (ROOT / "packaging" / "install.ps1").read_text(encoding="utf-8-sig")

        self.assertEqual(setup.count(r"packaging\install.ps1"), 1)
        self.assertIn("InstallScriptExitCode := ResultCode;", setup)
        self.assertIn("if InstallScriptExitCode <> 0 then", setup)
        self.assertIn("$global:LASTEXITCODE = 0", install)
        self.assertTrue(install.rstrip().endswith("exit 0"))

    def test_web_opener_uses_clean_local_url(self):
        opener = (ROOT / "gateway_py3" / "open_web.py").read_text(encoding="utf-8")
        build_script = (ROOT / "packaging" / "build_release.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("WEB_URL = BASE_URL", opener)
        self.assertIn('"http://127.0.0.1:8765"', build_script)
        self.assertNotIn("?v=", build_script)

    def test_windows_platform_paths_fail_when_environment_is_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            for resolver in (platform_paths.appdata_root, platform_paths.localappdata_root,
                             platform_paths.user_profile, platform_paths.command_shell):
                with self.subTest(resolver=resolver.__name__), self.assertRaises(RuntimeError):
                    resolver()

if __name__ == "__main__":
    unittest.main()
