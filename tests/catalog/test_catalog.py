import ast
import json
import pathlib
import unittest
import xml.etree.ElementTree as ET


ROOT = pathlib.Path(__file__).resolve().parents[2]
CATALOG_ROOT = ROOT / "operation_catalog"
RUNTIME_ROOT = ROOT / "arcmap_runtime_py2"
ADDIN_ROOT = ROOT / "ArcMapAIAssistantAddIn"
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

    def test_addin_does_not_register_auto_sync_extension(self):
        tree = ET.parse(str(ADDIN_ROOT / "config.xml"))
        namespace = {"addin": "http://schemas.esri.com/Desktop/AddIns"}
        extension = tree.find(".//addin:Extensions/addin:Extension", namespace)
        self.assertIsNone(extension)

    def test_addin_runtime_path_comes_from_install_config(self):
        addin_source = (ADDIN_ROOT / "Install" / "ArcMapAIAssistant_addin.py").read_text(encoding="utf-8")
        self.assertNotIn(str(ROOT), addin_source)
        self.assertNotIn(str(ROOT).replace("/", "\\"), addin_source)
        self.assertIn("install.json", addin_source)

    def test_release_and_install_package_operation_catalog(self):
        build_script = (PACKAGING_ROOT / "build_release.ps1").read_text(encoding="utf-8-sig")
        install_script = (PACKAGING_ROOT / "install.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"operation_catalog"', build_script)
        self.assertIn('"app\\operation_catalog"', build_script)
        self.assertIn('"app\\VERSION"', build_script)
        self.assertIn("Get-AppVersion", build_script)
        self.assertIn('"operation_catalog"', install_script)
        self.assertIn('"catalog.json"', install_script)
        self.assertIn('"VERSION"', install_script)
        self.assertIn("app_version", install_script)
        self.assertIn("Test-InstallHealth", install_script)


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
