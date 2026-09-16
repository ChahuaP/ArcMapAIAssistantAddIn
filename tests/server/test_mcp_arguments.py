"""Exercise the real FastMCP transport and Pydantic invocation, not just schemas.

Missing required fields stop at the real boundary pre-check, so these tests
never mutate ArcMap or substitute an execution backend.
"""
import unittest

from fastmcp import Client

from server.main import mcp, _catalog
from server.codegen import tool_name
from server.precheck import check
from server.tool_contract import model_schema, lower_arguments


class NativeArgumentsTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_operation_schemas_expose_catalog_fields_at_top_level(self):
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        for operation_id in _catalog.operation_ids():
            with self.subTest(operation_id=operation_id):
                schema = tools[tool_name(operation_id)].inputSchema
                expected = _catalog.get(operation_id).get('parameters_schema', {}).get('properties', {})
                self.assertEqual(set(schema.get('properties', {})), set(expected))
                self.assertNotIn('arguments', schema.get('properties', {}))
                self.assertEqual(schema.get('required'), _catalog.get(operation_id)['parameters_schema']['required'])

    async def test_top_level_path_reaches_boundary_clarification(self):
        async with Client(mcp) as client:
            result = await client.call_tool('layer__add_layer', {'path': ''})
        self.assertFalse(result.is_error)
        self.assertEqual(result.data['status'], 'unresolved')
        self.assertTrue(result.data['obligations'])

    async def test_every_required_operation_accepts_missing_fields_for_precheck(self):
        async with Client(mcp) as client:
            for operation_id in _catalog.operation_ids():
                if not _catalog.get(operation_id).get('parameters_schema', {}).get('required'):
                    continue
                with self.subTest(operation_id=operation_id):
                    result = await client.call_tool(tool_name(operation_id), {})
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.data['status'], 'unresolved')

    async def test_nested_envelope_is_rejected(self):
        async with Client(mcp) as client:
            result = await client.call_tool('layer__add_layer', {'arguments': {'path': ''}}, raise_on_error=False)
        self.assertTrue(result.is_error)

    async def test_nested_catalog_constraints_survive_mcp_transport(self):
        async with Client(mcp) as client:
            tools = {tool.name: tool for tool in await client.list_tools()}
        for operation_id in _catalog.operation_ids():
            expected = model_schema(_catalog.get(operation_id)['parameters_schema'])['properties']
            actual = tools[tool_name(operation_id)].inputSchema['properties']
            for name, spec in expected.items():
                with self.subTest(operation=operation_id, parameter=name):
                    for key, value in spec.items():
                        self.assertEqual(actual[name][key], value)

    async def test_recorded_units_typo_is_rejected_before_dispatch(self):
        async with Client(mcp) as client:
            result = await client.call_tool('analysis__buffer', {
                'input_layer': 'nanjing', 'output_name': 'nanjing_buffer_1km',
                'distance': {'units': 'Meters', 'value': 1000},
            })
        self.assertEqual(result.data['status'], 'violated')
        self.assertIn('unit', str(result.data['violations']))
        self.assertNotIn('receipt', result.data)

    async def test_valid_distance_only_leaves_genuinely_missing_layer_unresolved(self):
        async with Client(mcp) as client:
            result = await client.call_tool('analysis__buffer', {
                'output_name': 'nanjing_buffer_1km',
                'distance': {'unit': 'kilometers', 'value': 1},
            })
        self.assertEqual(result.data['status'], 'unresolved')
        self.assertEqual([item['proof_id'] for item in result.data['obligations']],
                         ['analysis.buffer.input_layer'])

    def test_invalid_nested_constraints_cannot_pass_precheck(self):
        card = _catalog.get('analysis.buffer')
        for patch in ({'value': -1}, {'unit': 'bananas'}, {'dimension': 'area'},
                      {'tolerance': 'invalid'}, {'crs': 123}, {'unexpected': 1}):
            with self.subTest(patch=patch):
                distance = dict(value=1000, unit='meters', dimension='length',
                                tolerance=0, crs=None)
                distance.update(patch)
                arguments = {
                    'input_layer': 'nanjing', 'output_name': 'test', 'distance': distance}
                self.assertEqual(check(card, arguments, None)['status'], 'violated')


if __name__ == '__main__':
    unittest.main()
