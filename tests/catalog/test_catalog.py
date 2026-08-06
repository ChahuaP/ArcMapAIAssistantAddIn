import ast
import copy
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
            "parameters_schema",
            "context_requirements",
            "side_effects",
            "output_policy",
            "executor",
            "examples"
            ,"capability_contract"
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

    def test_every_operation_has_a_closed_executable_capability_contract(self):
        from gateway_py3.catalog_loader import OperationCatalog

        registry = OperationCatalog().capabilities
        operations = list(OperationCatalog().all_operations())
        self.assertEqual(len(operations), 61)
        for operation in operations:
            contract = registry.get(operation["id"])
            self.assertEqual(contract["parameters_schema"], operation["parameters_schema"])
            self.assertEqual(contract["side_effects"], operation["side_effects"])
            self.assertTrue(contract["postconditions"])
            output_type = (operation.get("output_policy") or {}).get("type", "none")
            expected_kind = "map_state" if output_type == "none" and operation["side_effects"] in ("read_only", "changes_map") else output_type
            self.assertEqual(contract["outputs"]["kind"], expected_kind, operation["id"])

    def test_custom_operation_schema_uses_the_runtime_cardinality_descriptor(self):
        from arcmap_runtime_py2.capability_contract_protocol import CARDINALITY_DESCRIPTOR_SCHEMA

        schema = _load_json(CATALOG_ROOT / "schemas" / "operation_spec.schema.json")
        declared = schema["$defs"]["capabilityContract"]["properties"]["outputs"]["properties"]["cardinality"]

        self.assertEqual(CARDINALITY_DESCRIPTOR_SCHEMA, declared)

    def test_every_executor_declares_a_semantic_effect(self):
        from gateway_py3.catalog_loader import OperationCatalog

        contracts = OperationCatalog().capabilities
        self.assertEqual(61, sum(len(contracts.get(item["id"])["semantic_effects"]) for item in OperationCatalog().all_operations()))

    def test_semantic_effect_vocabulary_covers_the_business_contract(self):
        from gateway_py3.catalog_loader import OperationCatalog

        actual = {effect["kind"] for item in OperationCatalog().all_operations() for effect in OperationCatalog().capabilities.get(item["id"])["semantic_effects"]}
        required = {"inspect", "map_change", "attribute_filter", "spatial_filter", "buffer", "overlay", "spatial_join", "aggregate", "project", "merge", "append", "field_add", "field_delete", "field_update", "feature_create", "feature_append", "copy", "repair", "define_projection", "add_xy", "artifact_export", "layout_change"}
        self.assertEqual(required, actual)

    def test_semantic_effect_binding_rejects_non_executable_parameter(self):
        from gateway_py3.capability_registry import CapabilityContractError, CapabilityRegistry
        from gateway_py3.catalog_loader import OperationCatalog

        operation = copy.deepcopy(OperationCatalog().get("analysis.buffer"))
        operation["capability_contract"]["semantic_effects"][0]["distance"] = "invented"
        with self.assertRaisesRegex(CapabilityContractError, "binding cannot be resolved"):
            CapabilityRegistry([operation])

    def test_semantic_effect_optional_parameter_requires_an_executable_default(self):
        from gateway_py3.capability_registry import CapabilityContractError, CapabilityRegistry
        from gateway_py3.catalog_loader import OperationCatalog

        operation = copy.deepcopy(OperationCatalog().get("export.table_csv"))
        del operation["parameters_schema"]["properties"]["selected_only"]["default"]
        del operation["capability_contract"]["parameters_schema"]["properties"]["selected_only"]["default"]

        with self.assertRaisesRegex(CapabilityContractError, "requires an executable default"):
            CapabilityRegistry([operation])

    def test_artifact_export_semantic_action_uses_the_closed_domain_vocabulary(self):
        from gateway_py3.capability_registry import CapabilityContractError, CapabilityRegistry
        from gateway_py3.catalog_loader import OperationCatalog

        operation = copy.deepcopy(OperationCatalog().get("export.table_csv"))
        operation["capability_contract"]["semantic_effects"][0]["action"] = {
            "const": "export_table_csv",
        }

        with self.assertRaisesRegex(CapabilityContractError, "action.*closed vocabulary"):
            CapabilityRegistry([operation])

    def test_semantic_preservation_is_catalog_declared_and_requires_a_source_edge(self):
        from gateway_py3.capability_registry import CapabilityContractError, CapabilityRegistry
        from gateway_py3.catalog_loader import OperationCatalog

        catalog = OperationCatalog()
        dissolve = catalog.capabilities.get("analysis.dissolve")["semantic_effects"][0]
        self.assertEqual(["merge"], dissolve["preserves"])
        self.assertEqual({"parameter": "input_layer"}, dissolve["source"])

        operation = copy.deepcopy(catalog.get("edit.create_point_features"))
        operation["capability_contract"]["semantic_effects"][0]["preserves"] = ["merge"]
        with self.assertRaisesRegex(CapabilityContractError, "requires an explicit source binding"):
            CapabilityRegistry([operation])

    def test_registry_rejects_unresolvable_output_descriptor_binding(self):
        from gateway_py3.capability_registry import CapabilityContractError, CapabilityRegistry
        from gateway_py3.catalog_loader import OperationCatalog

        operation = copy.deepcopy(OperationCatalog().get("analysis.spatial_join"))
        operation["capability_contract"]["outputs"]["geometry"]["value"] = "input_layers"
        with self.assertRaisesRegex(CapabilityContractError, "geometry.value must bind"):
            CapabilityRegistry([operation])

        operation = copy.deepcopy(OperationCatalog().get("analysis.buffer"))
        operation["capability_contract"]["outputs"]["geometry"] = {
            "rule": "lowest_dimension",
            "value": "input_layer",
        }
        with self.assertRaisesRegex(CapabilityContractError, "many-valued input parameter"):
            CapabilityRegistry([operation])

        operation = copy.deepcopy(OperationCatalog().get("analysis.identity"))
        operation["capability_contract"]["outputs"]["fields"] = {
            "effect": "merge_inputs",
            "target": "input_layers",
            "static_fields": [],
            "parameter_field": "not_applicable",
        }
        with self.assertRaisesRegex(CapabilityContractError, "outputs.fields"):
            CapabilityRegistry([operation])

    def test_registry_rejects_legacy_scalar_output_cardinality(self):
        from gateway_py3.capability_registry import CapabilityContractError, CapabilityRegistry
        from gateway_py3.catalog_loader import OperationCatalog

        operation = copy.deepcopy(OperationCatalog().get("analysis.buffer"))
        operation["capability_contract"]["outputs"]["cardinality"] = "one_per_input_feature"

        with self.assertRaisesRegex(CapabilityContractError, "closed descriptor object"):
            CapabilityRegistry([operation])

    def test_registry_rejects_legacy_or_unbound_input_selection_requirements(self):
        from gateway_py3.capability_registry import CapabilityContractError, CapabilityRegistry
        from gateway_py3.catalog_loader import OperationCatalog

        operation = copy.deepcopy(OperationCatalog().get("selection.export_selected_features"))
        operation["capability_contract"]["inputs"][0]["selection"] = "selected_features_only"
        with self.assertRaisesRegex(CapabilityContractError, "selection must be an object"):
            CapabilityRegistry([operation])

        operation = copy.deepcopy(OperationCatalog().get("export.table_csv"))
        operation["capability_contract"]["inputs"][0]["selection"]["parameter"] = "output_name"
        with self.assertRaisesRegex(CapabilityContractError, "values must match the string parameter"):
            CapabilityRegistry([operation])

    def test_representative_executor_contract_semantics(self):
        from gateway_py3.catalog_loader import OperationCatalog

        contracts = OperationCatalog().capabilities
        self.assertEqual(contracts.get("table.add_field")["outputs"]["fields"]["effect"], "add_parameter_field")
        self.assertEqual(contracts.get("table.delete_field")["outputs"]["fields"]["effect"], "delete_parameter_field")
        spatial_join = contracts.get("analysis.spatial_join")["outputs"]
        self.assertEqual(spatial_join["geometry"], {"rule": "inherit", "value": "target_layer"})
        self.assertEqual(
            contracts.get("analysis.intersect")["outputs"]["geometry"],
            {"rule": "lowest_dimension", "value": "input_layers"},
        )
        self.assertEqual(
            contracts.get("analysis.identity")["outputs"]["fields"]["sources"],
            ["input_layer", "identity_layer"],
        )
        self.assertIn("Join_Count", spatial_join["fields"]["static_fields"])
        buffer_fields = contracts.get("analysis.buffer")["outputs"]["fields"]
        self.assertEqual("add_static_fields", buffer_fields["effect"])
        self.assertEqual(["BUFF_DIST"], buffer_fields["static_fields"])
        self.assertEqual(contracts.get("selection.select_by_attribute")["outputs"]["selection_state"], "applied")

    def test_registry_rejects_unresolvable_postcondition_target_and_stale_output_reference(self):
        from gateway_py3.capability_registry import CapabilityContractError, CapabilityRegistry
        from gateway_py3.catalog_loader import OperationCatalog

        operation = copy.deepcopy(OperationCatalog().get("table.add_field"))
        operation["capability_contract"]["postconditions"][0]["target"] = "missing_layer"
        with self.assertRaisesRegex(CapabilityContractError, "cannot be resolved"):
            CapabilityRegistry([operation])

        operation = copy.deepcopy(OperationCatalog().get("analysis.spatial_join"))
        operation["capability_contract"]["postconditions"][0]["expectation"]["geometry"] = {"ref": "outputs.fields"}
        with self.assertRaisesRegex(CapabilityContractError, "must reference outputs.geometry"):
            CapabilityRegistry([operation])

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

        self.assertEqual(operations["edit.create_star_polygon"]["capability_contract"]["outputs"]["geometry"]["value"], "polygon")
        self.assertEqual(operations["edit.append_star_polygons"]["side_effects"], "edits_data")
        self.assertEqual(
            operations["edit.append_star_polygons"]["capability_contract"]["outputs"]["cardinality"],
            {"rule": "fixed", "value": "in_place"},
        )
        self.assertEqual(operations["edit.create_empty_feature_layer"]["capability_contract"]["outputs"]["geometry"]["value"], "parameter_geometry_type")
        self.assertEqual(operations["edit.create_rectangle_polygon"]["capability_contract"]["outputs"]["geometry"]["value"], "polygon")
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

    def test_runtime_read_data_uses_live_layers_and_unicode_paths(self):
        common = (RUNTIME_ROOT / "operations" / "common.py").read_text(encoding="utf-8")
        export_ops = (RUNTIME_ROOT / "operations" / "export_ops.py").read_text(encoding="utf-8")
        condition_utils = (RUNTIME_ROOT / "operations" / "condition_utils.py").read_text(encoding="utf-8")
        layer_ops = (RUNTIME_ROOT / "operations" / "layer_ops.py").read_text(encoding="utf-8")

        self.assertIn("def read_layer(layer, selected_only=False, where_clause=None):", common)
        self.assertIn("arcpy.MakeFeatureLayer_management(self.layer, self.temp_layer, self.where_clause)", common)
        self.assertIn("clear_layer_selection(self.temp_layer)", common)
        self.assertIn("require_selection(self.layer)", common)
        self.assertIn('path = common._path_text(arguments["path"])', layer_ops)

        self.assertIn("with common.read_layer(layer, selected_only, where_clause) as source:", export_ops)
        self.assertIn("with common.read_layer(layer, selected_only) as source:", export_ops)
        self.assertIn("with common.read_layer(layer, False, where_clause) as source:", condition_utils)
        self.assertNotIn("common._safe_data_source(layer) or layer", export_ops)
        self.assertNotIn("class _read_layer", export_ops)

    def test_workflow_operations_do_not_publish_or_refresh_arcmap_ui(self):
        forbidden = (
            "add_output_layer",
            "auto_add_outputs_disabled",
            "RefreshTOC",
            "RefreshActiveView",
            "common.refresh",
        )
        for path in (RUNTIME_ROOT / "operations").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            for text in forbidden:
                self.assertNotIn(text, source, "%s still uses %s" % (path, text))

    def test_runtime_operations_route_paths_through_path_utils(self):
        operation_files = [
            RUNTIME_ROOT / "operations" / "common.py",
            RUNTIME_ROOT / "operations" / "layer_ops.py",
            RUNTIME_ROOT / "operations" / "export_ops.py",
            RUNTIME_ROOT / "operations" / "edit_geometry_ops.py",
        ]
        forbidden = [
            "os.path.exists(",
            "os.path.isfile(",
            "os.path.isdir(",
            "os.path.join(",
            "os.path.dirname(",
            "os.path.basename(",
            "os.makedirs(",
            "open(_path_text(",
            "open(output",
        ]
        for path in operation_files:
            source = path.read_text(encoding="utf-8")
            self.assertIn("path_utils", source, str(path))
            for text in forbidden:
                self.assertNotIn(text, source, "%s still uses %s" % (path, text))

    def test_release_and_install_package_operation_catalog(self):
        build_script = (PACKAGING_ROOT / "build_release.ps1").read_text(encoding="utf-8-sig")
        install_script = (PACKAGING_ROOT / "install.ps1").read_text(encoding="utf-8-sig")
        uninstall_script = (PACKAGING_ROOT / "uninstall.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('"operation_catalog"', build_script)
        self.assertIn('"app\\operation_catalog"', build_script)
        self.assertIn('"agent_integrations\\geopilot-arcmap"', build_script)
        self.assertIn("Build-ExternalArcMapBridge", build_script)
        self.assertIn('"ArcMapBridgeExternal\\build.ps1"', build_script)
        self.assertIn('"app\\bridge\\ArcMapBridge.exe"', build_script)
        self.assertIn('"app\\VERSION"', build_script)
        self.assertIn('"app\\uninstall.ico"', build_script)
        self.assertIn("Get-AppVersion", build_script)
        self.assertIn('"operation_catalog"', install_script)
        self.assertIn('"catalog.json"', install_script)
        self.assertIn('"VERSION"', install_script)
        self.assertIn("app_version", install_script)
        self.assertIn("Get-ArcMapDesktopVersions", install_script)
        self.assertIn("addin_dirs", install_script)
        self.assertIn("desktop_versions", install_script)
        self.assertIn("Desktop10\\.", install_script)
        self.assertIn("Get-AddinTargetDirs", uninstall_script)
        self.assertIn("bridge_exe", install_script)
        self.assertIn("Test-InstallHealth", install_script)
        self.assertIn("build\\release_staging\\ArcMapAIAssistant", build_script)
        self.assertIn("GeoPilotSetup-$appVersion.exe 和 geopilot-arcmap skill", build_script)

    def test_windows_installer_uses_inno_setup(self):
        build_script = (PACKAGING_ROOT / "build_release.ps1").read_text(encoding="utf-8-sig")
        inno_script = (PACKAGING_ROOT / "GeoPilotSetup.iss").read_text(encoding="utf-8")
        uninstall_script = (PACKAGING_ROOT / "uninstall.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("ISCC.exe", build_script)
        self.assertIn("Programs\\Inno Setup 6\\ISCC.exe", build_script)
        self.assertIn("Stop-BuildOutputGateway", build_script)
        self.assertIn("PyInstaller 打包失败", build_script)
        self.assertIn("Inno Setup 打包失败", build_script)
        self.assertIn("PrivilegesRequired=admin", inno_script)
        self.assertIn(r"DefaultDirName={autopf}\GeoPilot", inno_script)
        self.assertIn("RunOnceId", inno_script)
        self.assertNotIn("ChineseSimplified.isl", inno_script)
        self.assertIn(r'Name: "{autoprograms}\GeoPilot\卸载 GeoPilot"', inno_script)
        self.assertIn('IconFilename: "{app}\\uninstall.ico"', inno_script)
        self.assertIn("TNewCheckBox", inno_script)
        self.assertIn("同时删除用户配置和本地数据", inno_script)
        self.assertIn("UninstallUserDataParameter", inno_script)
        self.assertIn("-RemoveUserConfig", inno_script)
        self.assertIn('$appConfigDir = Join-Path $env:APPDATA "ArcMapAIAssistant"', uninstall_script)
        self.assertIn('$localDataDir = Join-Path $env:LOCALAPPDATA "ArcMapAIAssistant"', uninstall_script)
        self.assertIn("Remove-PathIfExists $appConfigDir", uninstall_script)
        self.assertIn("Remove-PathIfExists $localDataDir", uninstall_script)
        self.assertNotIn("打开 GeoPilot", inno_script)
        self.assertNotIn("启动 AI 后台", inno_script)


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
