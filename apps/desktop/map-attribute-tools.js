const h = window.React.createElement;
const { useState } = window.React;
const { Button, Input, Select } = window.antd;
const numeric = type => /^(?:u?int|float)/.test(type);
const integer = type => /^u?int/.test(type);
const numericLiteral = value => typeof value === 'string' && /^[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?$/i.test(value) && Number.isFinite(Number(value));
const quoted = value => "'" + String(value).replaceAll("'", "''") + "'";
const identifier = name => '"' + name.replaceAll('"', '""') + '"';
const sqlValue = value => value === null ? 'NULL' : typeof value === 'number' ? String(value) : quoted(value);
const options = items => Object.entries(items).map(([value, label]) => ({ value, label }));
const typeOf = (layer, name) => layer?.fields?.find(field => field.name === name)?.type || '';
const valueLabel = (value, type) => value === null ? '空值（NULL）' : value === '' ? '空字符串' :
    type === 'bool' ? (value ? '是' : '否') : String(value);

const axes = { center_x: '质心 X 坐标', center_y: '质心 Y 坐标' };
export function geometryProperties(layer) {
    if (!layer?.crs) return {};
    const shape = layer.geometry?.split(' ')[0];
    if (/^(Multi)?Polygon$/.test(shape)) return { area: '面积', perimeter: '周长', ...axes };
    if (/^(Multi)?LineString$/.test(shape)) return { length: '长度', ...axes };
    if (shape === 'Point') return { point_x: 'X 坐标', point_y: 'Y 坐标' };
    return shape === 'MultiPoint' ? axes : {};
}

function UniqueValues({ layer, name, choose }) {
    const [result, setResult] = useState(null);
    const [pending, setPending] = useState(false);
    const [error, setError] = useState('');
    async function load() {
        setPending(true);
        setError('');
        try { setResult(await window.guizhi.map({ action: 'table-unique-values', ref: layer.ref, field: name })); }
        catch (issue) { setError(window.GuizhiErrorText(issue)); }
        finally { setPending(false); }
    }
    const type = typeOf(layer, name);
    return h('div', { className: 'gis-unique-values' },
        h(Button, { size: 'small', disabled: !name, loading: pending, onClick: load }, '获取唯一值'),
        result && h(Select, { showSearch: true, optionFilterProp: 'label', placeholder: result.limited ? '选择唯一值（前 200 项）' : '选择唯一值',
            'aria-label': '唯一值', notFoundContent: '没有可选值', options: result.values.map((value, index) => ({ value: index, label: valueLabel(value, type) })),
            onSelect: index => choose(result.values[index]) }),
        error && h('small', { role: 'alert', className: 'error' }, error));
}

const unique = (layer, name, choose) => h(UniqueValues, { key: JSON.stringify([layer?.ref, name]), layer, name, choose });

function expressionFields(layer, values, update, name, controls, placeholder) {
    const insert = part => update(name, [values[name]?.trimEnd(), part].filter(Boolean).join(' '));
    const selected = values.expressionField;
    const type = typeOf(layer, selected);
    return [controls.text(name, 'SQL 表达式', placeholder),
        controls.columns('expressionField', '可用字段', { showSearch: true, optionFilterProp: 'label' }),
        h('div', { className: 'gis-expression-actions' },
            h(Button, { size: 'small', disabled: !selected, onClick: () => insert(identifier(selected)) }, '插入字段'),
            unique(layer, selected, value => insert(integer(type) && numericLiteral(value) ? value : sqlValue(value))))];
}

function valueInput(label, value, change, disabled, type, field) {
    return field(label, type === 'bool' ? h(Select, { value: value === null ? undefined : value, disabled,
        placeholder: value === null ? '空值（NULL）' : '请选择', options: options({ true: '是', false: '否' }), onChange: change }) :
        h(Input, { value: value ?? '', disabled, inputMode: numeric(type) ? 'decimal' : undefined,
            placeholder: value === null ? '空值（NULL）' : undefined, onChange: event => change(event.target.value) }));
}

function typedValue(type, value, allowNull = false) {
    if (value === null && allowNull) return { valid: true, sql: 'NULL' };
    if (type === 'bool') return { valid: value === 'true' || value === 'false', value: value === 'true', sql: value === 'true' ? '1' : '0' };
    if (numeric(type)) {
        const raw = value?.trim();
        const valid = integer(type) ? /^[-+]?\d+$/.test(raw) && Number.isFinite(Number(raw)) : numericLiteral(raw);
        return { valid, value: Number(raw), sql: raw };
    }
    return { valid: typeof value === 'string', value, sql: quoted(value) };
}

export function attributeFilter(layer, values, update, controls) {
    const { field } = controls;
    const parts = [field('筛选方式', h(Select, { value: values.method || 'form', options: options({ form: '字段条件', sql: 'SQL 表达式' }),
        onChange: value => update('method', value) }))];
    if (values.method === 'sql') {
        parts.push(...expressionFields(layer, values, update, 'sql', controls, '例如 "地类" = \'耕地\''));
        return { parts, parameters: { sql: values.sql?.trim() }, valid: Boolean(values.sql?.trim()) };
    }
    const filters = values.filters || [{ id: 'first', op: 'eq' }];
    const logic = values.logic || 'and';
    const clauses = filters.map((filter, index) => attributeClause(layer, filter, changes =>
        update('filters', filters.map((item, i) => i === index ? { ...item, ...changes } : item)), controls));
    if (filters.length > 1) parts.push(field('匹配条件', h(Select, { value: logic,
        options: options({ and: '全部条件（且）', or: '任一条件（或）' }), onChange: value => update('logic', value) })));
    parts.push(...clauses.map((clause, index) => h('fieldset', { key: filters[index].id, className: 'gis-filter-clause',
        'aria-label': '条件 ' + (index + 1) }, ...clause.parts,
        filters.length > 1 && h(Button, { size: 'small', onClick: () => update('filters', filters.filter((_, i) => i !== index)) }, '删除条件'))),
    h(Button, { size: 'small', disabled: filters.length >= 32 || !layer, onClick: () =>
        update('filters', [...filters, { id: crypto.randomUUID(), op: 'eq' }]) }, '添加条件'));
    const parameters = clauses.some(clause => clause.exactSql) ? { sql: clauses.map(clause => clause.sql).join(logic === 'and' ? ' AND ' : ' OR ') }
        : { where: clauses.length === 1 ? clauses[0].where : { op: logic, conditions: clauses.map(clause => clause.where) } };
    return { parts, parameters, valid: clauses.every(clause => clause.valid) };
}

function attributeClause(layer, filter, update, { field, select, columns }) {
    const type = typeOf(layer, filter.field);
    const op = filter.op;
    const where = { field: filter.field, op };
    const column = filter.field && identifier(filter.field);
    const relations = { eq: '等于', ne: '不等于',
        ...(type !== 'bool' && { gt: '大于', gte: '大于或等于', lt: '小于', lte: '小于或等于' }),
        ...(!numeric(type) && type !== 'bool' && !type.startsWith('datetime') && { like: '文本匹配（% 为通配符）' }),
        is_null: '为空', is_not_null: '不为空' };
    const parts = [columns('field', '字段', { value: filter.field, showSearch: true, optionFilterProp: 'label',
        onChange: value => update({ field: value, op: 'eq', value: undefined }) }),
    select('op', '条件', relations, { value: op, onChange: value => update({ op: value }) })];
    if (op.startsWith('is_')) return { parts, where, sql: `(${column} IS ${op === 'is_not_null' ? 'NOT ' : ''}NULL)`, valid: Boolean(type) };
    const change = value => update({ value });
    parts.push(valueInput('匹配值', filter.value, change, !type, type, field), unique(layer, filter.field, value => {
        if (value === null) update({ op: op === 'ne' ? 'is_not_null' : 'is_null', value: undefined });
        else change(type === 'bool' ? String(Boolean(value)) : String(value));
    }));
    const match = typedValue(type, filter.value);
    const sqlOps = { eq: '=', ne: '<>', gt: '>', gte: '>=', lt: '<', lte: '<=', like: 'LIKE' };
    return { parts, where: { ...where, value: match.value }, sql: `(${column} ${sqlOps[op]} ${match.sql})`,
        exactSql: integer(type) && match.valid && !Number.isSafeInteger(match.value), valid: Boolean(type && relations[op] && match.valid) };
}

export function fieldCalculation(layer, values, update, controls) {
    const { field, select, columns } = controls;
    const type = typeOf(layer, values.field);
    const action = values.operator || 'constant';
    const operations = { constant: '填入固定值', copy: '复制其他字段', add: '加上数值', subtract: '减去数值',
        multiply: '乘以数值', divide: '除以数值', sql: 'SQL 表达式' };
    const parts = [columns('field', '写入字段', { showSearch: true, optionFilterProp: 'label',
        onChange: name => { update('field', name); update('value', undefined); update('source_field', undefined); } }),
    select('operator', '操作', operations, { value: action })];
    const parameters = { field: values.field };
    if (action === 'sql') {
        parts.push(...expressionFields(layer, values, update, 'expression', controls, '例如 ROUND("面积" / 10000.0, 2)'));
        return { parts, parameters: { ...parameters, expression: values.expression?.trim() }, valid: Boolean(type && values.expression?.trim()) };
    }
    if (action === 'constant') {
        const change = value => update('value', value);
        parts.push(valueInput('填入值', values.value, change, !type, type, field), unique(layer, values.field,
            value => change(value === null ? null : type === 'bool' ? String(Boolean(value)) : String(value))));
        const result = typedValue(type, values.value, true);
        return { parts, parameters: { ...parameters, expression: result.sql }, valid: Boolean(type && result.valid) };
    }
    const sources = layer?.fields.filter(item => action === 'copy' ? item.name !== values.field : numeric(item.type)) || [];
    const source = sources.find(item => item.name === values.source_field);
    parts.push(field('使用字段', h(Select, { value: source?.name, options: sources.map(item => ({ value: item.name, label: item.name })),
        showSearch: true, optionFilterProp: 'label', placeholder: '请选择', onChange: name => update('source_field', name) })));
    const amount = values.operand?.trim() || '';
    if (action !== 'copy') parts.push(valueInput('数值', values.operand, value => update('operand', value), false, 'float64', field));
    return { parts, parameters: { ...parameters, source_field: source?.name, operator: action,
        ...(action !== 'copy' && { operand: Number(amount) }) },
    valid: Boolean(type && source && (action === 'copy' || numericLiteral(amount) && (action !== 'divide' || Number(amount) !== 0))) };
}

export function geometryCalculation(layer, values, update, { field, select }) {
    const properties = geometryProperties(layer);
    const property = values.property || Object.keys(properties)[0];
    const units = property === 'area' ? { square_meters: '平方米', hectares: '公顷', square_kilometers: '平方千米' } :
        ['perimeter', 'length'].includes(property) ? { meters: '米', kilometers: '千米' } : { layer: '图层坐标单位' };
    const unit = values.unit || Object.keys(units)[0];
    const suffix = { square_meters: '_M2', hectares: '_HA', square_kilometers: '_KM2', meters: '_M', kilometers: '_KM', layer: '' };
    const base = property && property.toUpperCase() + suffix[unit];
    const names = new Set([layer?.fidField, ...(layer?.fields || []).map(item => item.name)].filter(Boolean).map(name => name.toLowerCase()));
    let generated = base;
    for (let number = 2; generated && names.has(generated.toLowerCase()); number++) generated = base + '_' + number;
    const destinations = (layer?.fields || []).filter(item => item.type.startsWith('float')).map(item => ({ value: item.name, label: item.name }));
    if (generated) destinations.push({ value: generated, label: '新建字段 ' + generated });
    const destination = values.field || generated;
    const parts = [select('property', '几何属性', properties, { value: property, onChange: value => {
        update('property', value); update('unit', undefined); update('field', undefined);
    } }), Object.keys(units).length > 1 && select('unit', '单位', units, { value: unit, onChange: value => {
        update('unit', value); update('field', undefined);
    } }), field('写入字段', h(Select, { value: destination, options: destinations, placeholder: '请选择',
        onChange: value => update('field', value) }))].filter(Boolean);
    return { parts, parameters: { property, unit, field: destination }, valid: Boolean(properties[property] && units[unit] &&
        destinations.some(item => item.value === destination)) };
}
