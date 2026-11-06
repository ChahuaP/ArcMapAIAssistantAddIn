"""Seal independent verification inputs from the pre-execution map context."""
from copy import deepcopy
from pathlib import Path

from shared_runtime.acceptance_profile import comparison_strategy


def seal_request(card, arguments, context, staging_root):
    contract = card['capability_contract']
    output = contract['outputs']
    if output['kind'] == 'map_state':
        return {'probe_type': 'map_state', 'output_id': 'map',
                'postcondition': deepcopy(contract['postconditions'][0]),
                'arguments': deepcopy(arguments), 'acceptance_contract': {'rules': []}}
    if output['kind'] not in ('feature_class', 'table', 'file'):
        raise ValueError('Independent verification requires a declared artifact or map state')
    name = arguments.get('output_name')
    if not name or Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError('Independent verification requires a simple output_name')
    fmt = card['output_policy']['default_format']
    path = Path(staging_root) / 'staging.gdb' / name if fmt == 'gdb' else Path(staging_root) / (name + '.' + fmt)
    layers = {layer['layer_ref']: layer for layer in context.get('layers', [])}
    schemas = card['parameters_schema']['properties']

    def resolve(value, dataset=False):
        if isinstance(value, list):
            values = [resolve(item, dataset) for item in value]
            return [item for group in values for item in (group if isinstance(group, list) else [group])]
        if not isinstance(value, dict):
            return deepcopy(value)
        if value.get('output'):
            return '__output__'
        if 'const' in value:
            return deepcopy(value['const'])
        parameter = value.get('parameter')
        if not parameter:
            return deepcopy(value)
        actual = arguments.get(parameter, schemas[parameter].get('default'))
        if not dataset:
            return deepcopy(actual)
        def bind(ref):
            layer = layers[ref]
            if layer.get('selected_count', 0):
                raise ValueError('Independent dataset verification requires an unselected source')
            return {'path': layer['data_source'], 'identity_fields': layer.get('identity_fields', []),
                    'attribute_fields': [f['name'] for f in layer.get('fields', [])
                                         if f['type'].lower() not in ('oid', 'geometry')],
                    'crs': {'type': layer.get('crs_type'), 'meters_per_unit': layer.get('meters_per_unit'),
                            'name': layer.get('spatial_reference')}}
        return [bind(ref) for ref in actual] if isinstance(actual, list) else bind(actual)

    rules = []
    for index, effect in enumerate(contract['semantic_effects']):
        predicate = {'kind': effect['kind']}
        bindings = {}
        for key, value in effect.items():
            if key == 'kind':
                continue
            if key in ('source', 'sources', 'target', 'subject', 'result', 'join'):
                bindings[key] = resolve(value, True)
            else:
                predicate[key] = resolve(value)
        if effect['kind'] == 'buffer':
            bindings['crs_strategy'] = comparison_strategy(bindings['source']['crs'], predicate['distance'])
            # A fixed relative spatial tolerance accommodates independent
            # boundary discretization without accepting a different distance.
            bindings['linear_tolerance'] = max(abs(bindings['crs_strategy']['buffer_value']) * .001, 1e-6)
        if effect['kind'] == 'aggregate':
            fields = predicate.get('dissolve_fields', [])
            bindings['aggregate'] = {'dissolve_fields': fields, 'statistics': []}
            bindings['required_fields'] = fields or ['Shape']
        if effect['kind'] == 'overlay':
            sources = bindings['sources']
            inherited = sources[:1] if predicate['method'] in ('clip', 'erase') else sources
            bindings['overlay'] = {'field_mapping': [
                {'namespace': 'source_%d' % i, 'identity_fields': src['identity_fields'],
                 'fields': src['attribute_fields']} for i, src in enumerate(inherited)]}
            used = set()
            for mapping in bindings['overlay']['field_mapping']:
                mapping['output_fields'] = {}
                for field in mapping['fields']:
                    candidate, suffix = field, 1
                    while candidate.lower() in used:
                        candidate = '%s_%d' % (field, suffix)
                        suffix += 1
                    used.add(candidate.lower())
                    mapping['output_fields'][field] = candidate
        rules.append({'proof_id': 'acceptance:%d:%s' % (index, effect['kind']),
                      'output_id': 'result', 'predicate': predicate, 'bindings': bindings})
    return {'output_id': 'result', 'kind': output['kind'], 'output_format': fmt,
            'staged_path': str(path), 'acceptance_contract': {'rules': rules}}


def verdict(document):
    """An available probe is not a passing result."""
    if document.get('probe_type') == 'map_state':
        return document.get('passed') is True
    geometry = document.get('geometry_evidence', {})
    proofs = document.get('acceptance_proofs', [])
    return (document.get('exists') is True and bool(proofs)
            and all(p.get('status') == 'Proven' for p in proofs)
            and all(geometry.get(key) == 0 for key in ('null_count', 'empty_count', 'invalid_count')))
