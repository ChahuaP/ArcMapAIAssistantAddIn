import unittest

from gateway_py3.tool_builder_errors import ToolBuilderError
from gateway_py3.tool_builder_executor import validate_executor_contract


class ToolBuilderExecutorPathTests(unittest.TestCase):
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
        "model_card": "写文件。",
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
    }


if __name__ == "__main__":
    unittest.main()
