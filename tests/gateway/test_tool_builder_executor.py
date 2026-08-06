import unittest
import copy
import jsonschema

from gateway_py3.tool_builder import canonicalize_operation_spec
from gateway_py3.tool_builder_errors import ToolBuilderError
from gateway_py3.tool_builder_executor import validate_executor_contract
from gateway_py3.custom_tool_contract import OPERATION_SPEC_SCHEMA


class ToolBuilderExecutorPathTests(unittest.TestCase):
    def test_canonicalize_custom_file_spec_preserves_examples(self):
        spec = canonicalize_operation_spec(_file_spec())

        self.assertEqual(spec["examples"], [{"output_name": "demo"}])

    def test_canonicalize_rejects_custom_file_collection_spec(self):
        spec = _file_spec()
        spec["output_policy"] = {
            "type": "file_collection",
            "formats": ["csv"],
        }

        with self.assertRaisesRegex(ToolBuilderError, "file_collection"):
            canonicalize_operation_spec(spec)

    def test_custom_tool_entry_schema_rejects_incomplete_contract_and_bare_binding(self):
        bad = _file_spec()
        bad["capability_contract"]["outputs"]["extra"] = True
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(bad, OPERATION_SPEC_SCHEMA)
        bad = _file_spec()
        bad["capability_contract"]["semantic_effects"][0]["action"] = "write_file"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(bad, OPERATION_SPEC_SCHEMA)

    def test_rejects_decode_path_workaround(self):
        with self.assertRaisesRegex(ToolBuilderError, "encode/decode"):
            validate_executor_contract(_file_spec(), """# -*- coding: utf-8 -*-
import sys

def execute(context, arguments, step_outputs):
    output_path = arguments["output_path"]
    output_path = output_path.decode(sys.getfilesystemencoding())
    with open(output_path, "w") as handle:
        handle.write("ok")
    return {"output": output_path}
""")

    def test_rejects_str_output_path_workaround(self):
        with self.assertRaisesRegex(ToolBuilderError, "str"):
            validate_executor_contract(_file_spec(), """# -*- coding: utf-8 -*-
def execute(context, arguments, step_outputs):
    output_path = arguments["output_path"]
    output_path = str(output_path)
    with open(output_path, "w") as handle:
        handle.write("ok")
    return {"output": output_path}
""")


def _file_spec():
    return {
        "id": "custom.write_file",
        "version": "1.0.0",
        "category": "custom",
        "summary": "写文件",
        "parameters_schema": {
            "type": "object",
            "required": ["output_name"],
            "properties": {"output_name": {"type": "string"}},
            "additionalProperties": False,
        },
        "context_requirements": {},
        "side_effects": "writes_data",
        "output_policy": {"type": "file", "extension": ".txt"},
        "executor": "custom_tool:demo:execute",
        "examples": [{"output_name": "demo"}],
        "capability_contract": {
            "inputs": [],
            "semantic_effects": [{"kind": "artifact_export", "action": {"const": "write_file"}, "output_format": {"const": "txt"}, "result": {"output": True}}],
            "parameters_schema": {"type": "object", "required": ["output_name"], "properties": {"output_name": {"type": "string"}}, "additionalProperties": False},
            "outputs": {"kind": "file", "geometry": {"rule": "not_applicable", "value": "not_applicable"}, "fields": {"effect": "not_applicable", "target": "not_applicable", "static_fields": [], "parameter_field": "not_applicable"}, "spatial_reference": {"rule": "not_applicable", "input": "not_applicable"}, "cardinality": {"rule": "fixed", "value": "one"}, "selection_state": "not_applicable", "map_publication": "none"},
            "side_effects": "writes_data",
            "authorization": {"required": True, "scope": "write_file"},
            "postconditions": [{"kind": "file_written", "target": "output_name", "expectation": {"kind": {"ref": "outputs.kind"}, "geometry": {"ref": "outputs.geometry"}, "fields": {"ref": "outputs.fields"}, "spatial_reference": {"ref": "outputs.spatial_reference"}, "cardinality": {"ref": "outputs.cardinality"}, "selection_state": {"ref": "outputs.selection_state"}, "map_publication": {"ref": "outputs.map_publication"}}}],
        },
    }


if __name__ == "__main__":
    unittest.main()
