import ast
import json
import pathlib
import unittest
import xml.etree.ElementTree as ET


ROOT = pathlib.Path(__file__).resolve().parents[2]
CATALOG_ROOT = ROOT / "operation_catalog"
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"
ADDIN_ROOT = ROOT / "ArcMapAIAssistantAddIn"
EXTERNAL_BRIDGE_ROOT = ROOT / "ArcMapBridgeExternal"
PACKAGING_ROOT = ROOT / "packaging"


class CatalogTests(unittest.TestCase):
    def test_catalog_operations_have_required_fields_and_unique_ids(self):
        catalog = _load_json(CATALOG_ROOT / "catalog.json")
        seen = set()
        required = {
            "id",
            "version",
            "category",
            "summary",
            "model_card",
            "parameters_schema",
            "context_requirements",
            "side_effects",
            "output_policy",
            "executor",
            "examples"
        }
        for pack_path in catalog["packs"]:
            pack = _load_json(CATALOG_ROOT / pack_path)
            for operation in pack["operations"]:
                self.assertFalse(required - set(operation), operation.get("id"))
                self.assertNotIn(operation["id"], seen)
                seen.add(operation["id"])
        self.assertGreaterEqual(len(seen), 18)

    def test_every_executor_function_exists(self):
        catalog = _load_json(CATALOG_ROOT / "catalog.json")
        for pack_path in catalog["packs"]:
            pack = _load_json(CATALOG_ROOT / pack_path)
            for operation in pack["operations"]:
                module_name, function_name = operation["executor"].rsplit(".", 1)
                module_path = RUNTIME_ROOT / (module_name.replace(".", "/") + ".py")
                self.assertTrue(module_path.exists(), operation["executor"])
                tree = ast.parse(module_path.read_text(encoding="utf-8"))
                functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
                self.assertIn(function_name, functions, operation["executor"])

    def test_functional_operation_packs_are_registered(self):
        catalog = _load_json(CATALOG_ROOT / "catalog.json")
        self.assertIn("packs/edit_geometry.json", catalog["packs"])
        self.assertIn("packs/data_management.json", catalog["packs"])
        self.assertIn("packs/layout.json", catalog["packs"])
        operations = {}
        for pack_path in catalog["packs"]:
            pack = _load_json(CATALOG_ROOT / pack_path)
            for operation in pack["operations"]:
                operations[operation["id"]] = operation

        self.assertEqual(operations["edit.create_star_polygon"]["output_policy"]["geometry_type"], "Polygon")
        self.assertEqual(operations["edit.append_star_polygons"]["side_effects"], "edits_data")
        self.assertEqual(operations["edit.append_star_polygons"]["output_policy"]["geometry_type"], "Polygon")
        self.assertEqual(operations["data.repair_geometry"]["side_effects"], "edits_data")
        self.assertEqual(operations["layout.export_pdf"]["output_policy"]["type"], "file")
        star_properties = operations["edit.create_star_polygon"]["parameters_schema"]["properties"]
        self.assertIn("features", star_properties)
        self.assertEqual(operations["edit.create_star_polygon"]["parameters_schema"]["required"], ["output_name"])
        self.assertEqual(star_properties["outer_radius_unit"]["enum"], ["map_units", "meters", "degrees"])

    def test_python_addin_exposes_only_console_button(self):
        tree = ET.parse(str(ADDIN_ROOT / "config.xml"))
        namespace = {"addin": "http://schemas.esri.com/Desktop/AddIns"}
        buttons = tree.findall(".//addin:Commands/addin:Button", namespace)
        toolbar_buttons = tree.findall(".//addin:Toolbars/addin:Toolbar/addin:Items/addin:Button", namespace)

        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].attrib.get("class"), "OpenAssistantButton")
        self.assertEqual(buttons[0].attrib.get("caption"), "启动控制台")
        self.assertEqual(len(toolbar_buttons), 1)
        self.assertEqual(toolbar_buttons[0].attrib.get("refID"), "ArcMapAIAssistant_addin.openAssistantButton")

    def test_external_arcmap_bridge_uses_rot_and_single_python_addin_command(self):
        source = (EXTERNAL_BRIDGE_ROOT / "Program.cs").read_text(encoding="utf-8")
        project = (EXTERNAL_BRIDGE_ROOT / "ArcMapBridgeExternal.csproj").read_text(encoding="utf-8")
        build_script = (EXTERNAL_BRIDGE_ROOT / "build.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("AppROTClass", source)
        self.assertIn("TcpListener", source)
        self.assertIn('"openAssistantButton"', source)
        self.assertNotIn('"syncContextButton"', source)
        self.assertNotIn('"executeWorkflowButton"', source)
        self.assertIn("allow_edits", source)
        self.assertIn("ExtractBool", source)
        self.assertIn("<AssemblyName>ArcMapBridge</AssemblyName>", project)
        self.assertIn("<PlatformTarget>x86</PlatformTarget>", project)
        self.assertIn("ArcMapBridge.exe", build_script)

    def test_addin_runtime_path_comes_from_install_config(self):
        addin_source = (ADDIN_ROOT / "Install" / "ArcMapAIAssistant_addin.py").read_text(encoding="utf-8")
        self.assertNotIn(str(ROOT), addin_source)
        self.assertNotIn(str(ROOT).replace("/", "\\"), addin_source)
        self.assertIn("install.json", addin_source)

    def test_runtime_process_paths_come_from_installed_app(self):
        bridge_client = (ROOT / "gateway_py3" / "arcmap_bridge_client.py").read_text(encoding="utf-8")
        gateway_client = (ROOT / "arcmap_runtime_py2" / "gateway_client.py").read_text(encoding="utf-8")
        runtime = (ROOT / "arcmap_runtime_py2" / "runtime.py").read_text(encoding="utf-8")

        self.assertIn('"install.json"', bridge_client)
        self.assertIn('"bridge_exe"', bridge_client)
        self.assertNotIn("GEOPILOT_ARCMAP_BRIDGE", bridge_client)
        self.assertNotIn('"ArcMapBridgeExternal"', bridge_client)
        self.assertIn('"gateway", "ArcMapAIAssistantGateway.exe"', gateway_client)
        self.assertNotIn('"dist"', gateway_client)
        self.assertNotIn('"python", "-m", "gateway_py3"', gateway_client)
        self.assertIn("def open_or_handle_bridge_command", runtime)
        self.assertIn('_LAST_SILENT_COMMAND.get("allow_edits")', runtime)
        self.assertIn("if _LAST_COMMAND_WAS_SILENT:", runtime)
        self.assertNotIn("def handle_command", runtime)

    def test_release_and_install_package_operation_catalog(self):
        build_script = (PACKAGING_ROOT / "build_release.ps1").read_text(encoding="utf-8-sig")
        install_script = (PACKAGING_ROOT / "install.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"operation_catalog"', build_script)
        self.assertIn('"app\\operation_catalog"', build_script)
        self.assertIn('"agent_integrations\\geopilot-arcmap"', build_script)
        self.assertIn("Build-ExternalArcMapBridge", build_script)
        self.assertIn('"ArcMapBridgeExternal\\build.ps1"', build_script)
        self.assertIn('"app\\bridge\\ArcMapBridge.exe"', build_script)
        self.assertIn('"app\\VERSION"', build_script)
        self.assertIn('"app\\help.html"', build_script)
        self.assertIn('"app\\uninstall.ico"', build_script)
        self.assertIn("Get-AppVersion", build_script)
        self.assertIn('"operation_catalog"', install_script)
        self.assertIn('"catalog.json"', install_script)
        self.assertIn('"VERSION"', install_script)
        self.assertIn("app_version", install_script)
        self.assertIn("bridge_exe", install_script)
        self.assertIn("Test-InstallHealth", install_script)
        self.assertIn("build\\release_staging\\ArcMapAIAssistant", build_script)
        self.assertIn("GeoPilotSetup-$appVersion.exe 和 geopilot-arcmap skill", build_script)

    def test_windows_installer_uses_inno_setup(self):
        build_script = (PACKAGING_ROOT / "build_release.ps1").read_text(encoding="utf-8-sig")
        inno_script = (PACKAGING_ROOT / "GeoPilotSetup.iss").read_text(encoding="utf-8")
        self.assertIn("ISCC.exe", build_script)
        self.assertIn("Programs\\Inno Setup 6\\ISCC.exe", build_script)
        self.assertIn("Stop-BuildOutputGateway", build_script)
        self.assertIn("PyInstaller 打包失败", build_script)
        self.assertIn("Inno Setup 打包失败", build_script)
        self.assertIn("PrivilegesRequired=admin", inno_script)
        self.assertIn(r"DefaultDirName={autopf}\GeoPilot", inno_script)
        self.assertIn("RunOnceId", inno_script)
        self.assertNotIn("ChineseSimplified.isl", inno_script)
        self.assertIn(r'Name: "{autoprograms}\GeoPilot\帮助"', inno_script)
        self.assertIn(r'Name: "{autoprograms}\GeoPilot\卸载 GeoPilot"', inno_script)
        self.assertIn('IconFilename: "{app}\\uninstall.ico"', inno_script)
        self.assertNotIn("打开 GeoPilot", inno_script)
        self.assertNotIn("启动 AI 后台", inno_script)


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
