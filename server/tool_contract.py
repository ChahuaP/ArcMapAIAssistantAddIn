"""Business arguments for MCP, deterministically lowered to the runtime ABI.

The catalog is the execution contract. Constants and ABI bookkeeping are owned
by this compiler, never predicted by the model. No legacy argument shapes are
accepted. Validation runs before lowering and again before desktop dispatch.
"""
from copy import deepcopy


def omit_optional_nulls(value, schema):
    """Null means omitted only for declared, nullable optional properties."""
    if isinstance(value, list):
        return [omit_optional_nulls(item, schema.get('items', {})) for item in value]
    if not isinstance(value, dict):
        return value
    props = schema.get('properties', {})
    result = {}
    for name, item in value.items():
        spec = props.get(name, {})
        if item is None and name in props and name not in schema.get('required', []) and 'null' in spec.get('type', []):
            continue
        result[name] = omit_optional_nulls(item, spec)
    return result


def model_schema(schema):
    result = {key: deepcopy(value) for key, value in schema.items()
              if not key.startswith('x-geopilot-')}
    semantic = schema.get('x-geopilot-semantic')
    if semantic == 'angle':
        return {'type': 'number', 'description': '起始角度，单位为度；省略时为 -90。', 'default': -90}
    if 'properties' in schema:
        hidden = {name for name, spec in schema['properties'].items() if 'const' in spec}
        if semantic == 'quantity':
            hidden |= {'tolerance'}
        if semantic == 'field_spec':
            hidden |= {'domain'}
        result['properties'] = {name: model_schema(spec)
                                for name, spec in schema['properties'].items() if name not in hidden}
        result['required'] = [name for name in schema.get('required', []) if name not in hidden]
        if semantic == 'quantity':
            result['required'] = ['value', 'unit']
            result['properties']['crs']['description'] = '仅 map_units 或 degrees 需要，填写源数据坐标系；米或千米省略。'
            result['description'] = '距离：例如 {"value":1,"unit":"kilometers"}。米用 meters，千米用 kilometers。'
        elif semantic == 'field_spec':
            result['required'] = ['name', 'type']
            for name, default in [('nullable', True), ('length', 50), ('precision', None), ('scale', None)]:
                result['properties'][name]['default'] = default
        # Optional tool fields have one explicit null-as-omitted convention.
        # This does not rewrite values inside assignments or conditions.
        for name, spec in result['properties'].items():
            if name not in result['required'] and 'type' in spec:
                types = spec['type'] if isinstance(spec['type'], list) else [spec['type']]
                spec['type'] = list(dict.fromkeys(types + ['null']))
                if 'enum' in spec and None not in spec['enum']:
                    spec['enum'].append(None)
    if isinstance(schema.get('items'), dict):
        result['items'] = model_schema(schema['items'])
    for key in ('anyOf', 'allOf', 'oneOf'):
        if key in schema:
            result[key] = [model_schema(part) for part in schema[key]]
    if '$defs' in schema:
        result['$defs'] = {name: model_schema(spec) for name, spec in schema['$defs'].items()}
    return result


def lower_arguments(value, schema):
    """Compile validated business arguments, preserving literals and nulls."""
    semantic = schema.get('x-geopilot-semantic')
    if semantic == 'angle':
        return {'value': value, 'unit': 'degrees', 'dimension': 'angle', 'tolerance': 0, 'crs': None}
    if isinstance(value, list):
        return [lower_arguments(item, schema.get('items', {})) for item in value]
    if not isinstance(value, dict):
        return value
    properties = schema.get('properties', {})
    result = {}
    for name, item in value.items():
        spec = properties.get(name, {})
        types = spec.get('type', [])
        if item is None and name in properties and name not in schema.get('required', []) and 'null' not in types:
            continue
        result[name] = lower_arguments(item, spec)
    for name, spec in properties.items():
        if 'const' in spec:
            result[name] = deepcopy(spec['const'])
        elif name not in result and 'default' in spec:
            result[name] = deepcopy(spec['default'])
    if semantic == 'quantity':
        result['tolerance'] = 0
        result.setdefault('crs', None)
    elif semantic == 'field_spec':
        result.update(domain=[])
        result.setdefault('nullable', True)
        result.setdefault('length', 50 if result.get('type') == 'string' else None)
        result.setdefault('precision', None)
        result.setdefault('scale', None)
    return result
