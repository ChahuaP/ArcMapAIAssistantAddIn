"""Production schema/compiler tests. No GIS backend or model is substituted."""
import math
import unittest
from jsonschema import Draft202012Validator
from server.catalog import Catalog
from server.tool_contract import model_schema, lower_arguments
from server.precheck import check, resolve_layer_references


class BusinessContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = Catalog()

    def compile(self, operation, arguments):
        card = self.catalog.get(operation)
        public = model_schema(card['parameters_schema'])
        self.assertEqual(check(dict(card, parameters_schema=public), arguments, None)['status'], 'proven')
        compiled = lower_arguments(arguments, card['parameters_schema'])
        self.assertEqual(check(card, compiled, None)['status'], 'proven')
        return compiled

    def test_every_public_and_runtime_schema_is_valid(self):
        for operation in self.catalog.operation_ids():
            with self.subTest(operation=operation):
                schema = self.catalog.get(operation)['parameters_schema']
                Draft202012Validator.check_schema(schema)
                Draft202012Validator.check_schema(model_schema(schema))

    def test_buffer_business_distance_compiles_to_exact_runtime_quantity(self):
        for value, unit in [(1, 'kilometers'), (1000, 'meters')]:
            arguments = dict(input_layer='nanjing', output_name='buffer', distance=dict(value=value, unit=unit))
            compiled = self.compile('analysis.buffer', arguments)
            self.assertEqual(compiled['distance'], dict(value=value, unit=unit, dimension='length', tolerance=0, crs=None))
            self.assertEqual(set(arguments['distance']), {'value', 'unit'})

    def test_recorded_dimension_cannot_be_generated_from_public_schema(self):
        schema = model_schema(self.catalog.get('analysis.buffer')['parameters_schema'])
        props = schema['properties']['distance']['properties']
        self.assertNotIn('dimension', props)
        self.assertNotIn('tolerance', props)
        card = dict(id='analysis.buffer', parameters_schema=schema)
        for dimension in ({}, 'length'):
            result = check(card, dict(input_layer='nanjing', output_name='test',
                                     distance=dict(value=1, unit='kilometers', dimension=dimension)), None)
            self.assertEqual(result['status'], 'violated')

    def test_angle_and_nested_batch_quantities_are_compiled(self):
        compiled = self.compile('edit.create_regular_polygon', dict(output_name='pentagon', wkid=3857,
            features=[dict(center_x=0, center_y=0, radius=dict(value=10, unit='meters'), sides=5, start_angle_degrees=30)]))
        feature = compiled['features'][0]
        self.assertEqual(feature['start_angle_degrees']['dimension'], 'angle')
        self.assertEqual(feature['start_angle_degrees']['value'], 30)
        self.assertEqual(feature['radius']['dimension'], 'length')

    def test_field_defaults_are_internal_and_user_length_is_preserved(self):
        compiled = self.compile('table.add_field', dict(layer='nanjing', field=dict(name='remark', type='string', length=128)))
        self.assertEqual(compiled['field'], dict(name='remark', type='string', length=128,
                                               nullable=True, precision=None, scale=None, domain=[]))

    def test_literals_are_never_coerced_or_dropped(self):
        args = dict(layer='nanjing', where=dict(op='eq', field='name', value='001'),
                    assignments={'remark': '', 'other': None, 'text': 'null'})
        self.assertEqual(self.compile('table.update_rows', args), args)
        text = dict(element_name='title', text='')
        self.assertEqual(self.compile('layout.set_text', text), text)

    def test_recursive_conditions_resolve_real_schema_references(self):
        where = dict(op='and', conditions=[dict(op='eq', field='name', value='南京'),
                                         dict(op='not', condition=dict(op='is_null', field='name'))])
        self.compile('selection.select_by_attribute', dict(layer='nanjing', where=where))

    def test_invalid_inputs_never_become_valid_defaults(self):
        card = self.catalog.get('analysis.buffer')
        card = dict(card, parameters_schema=model_schema(card['parameters_schema']))
        for value in ('1000', True, -1, math.inf, math.nan):
            with self.subTest(value=value):
                self.assertEqual(check(card, dict(input_layer='nanjing', output_name='test',
                    distance=dict(value=value, unit='meters')), None)['status'], 'violated')

    def test_ambiguous_layer_names_and_arrays(self):
        card = self.catalog.get('analysis.merge')
        context = {'layers': [dict(name='roads', layer_ref='layer:0'), dict(name='roads', layer_ref='layer:1'),
                              dict(name='water', layer_ref='layer:2')]}
        args = dict(input_layers=['roads', 'water'], output_name='merged')
        resolved = resolve_layer_references(card['parameters_schema'], args, context)
        self.assertEqual(resolved['input_layers'], ['roads', 'layer:2'])
        self.assertEqual(check(card, resolved, context)['status'], 'violated')
        args['input_layers'] = ['layer:1', 'water']
        resolved = resolve_layer_references(card['parameters_schema'], args, context)
        self.assertEqual(check(card, resolved, context)['status'], 'proven')

    def test_map_unit_distance_requires_real_crs(self):
        card = self.catalog.get('analysis.buffer')
        args = lower_arguments(dict(input_layer='nanjing', output_name='test',
                                    distance=dict(value=1, unit='degrees')), card['parameters_schema'])
        self.assertEqual(check(card, args, None)['status'], 'violated')

    def test_coordinates_and_alternative_geometry_inputs(self):
        self.compile('edit.create_point_features', dict(output_name='points', wkid=3857, points=[dict(x=0, y=1)]))
        card = self.catalog.get('edit.create_polygon_feature')
        schema = model_schema(card['parameters_schema'])
        for coordinates in ([{}], [dict(x=0, y=0), dict(x=1, y=1)]):
            self.assertEqual(check(dict(card, parameters_schema=schema),
                                   dict(output_name='polygon', coordinates=coordinates), None)['status'], 'violated')

    def test_missing_conditional_business_fields_are_clarifications(self):
        card = self.catalog.get('edit.create_polygon_feature')
        public = dict(card, parameters_schema=model_schema(card['parameters_schema']))
        result = check(public, dict(output_name='polygon'), None)
        self.assertEqual(result['status'], 'unresolved')
        self.assertIn('coordinates', str(result['obligations']))

    def test_optional_null_is_omitted_but_literal_null_is_preserved(self):
        self.compile('edit.create_point_features', dict(output_name='points', wkid=3857,
                     spatial_reference_layer=None, points=[dict(x=0, y=1)]))


if __name__ == '__main__':
    unittest.main()
