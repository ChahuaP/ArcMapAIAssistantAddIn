import { TokenPicker, UniquePicker } from './map-pickers.js';

const h = window.React.createElement;
const { Button, Input, Select } = window.antd;
const numeric = type => /^(?:u?int|float)/.test(type);
const integer = type => /^u?int/.test(type);
const numericLiteral = value => typeof value === 'string' && /^[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?$/i.test(value) && Number.isFinite(Number(value));
const options = items => Object.entries(items).map(([value, label]) => ({ value, label }));
const typeOf = (layer, name) => layer?.fields?.find(field => field.name === name)?.type || '';
const valueLabel = (value, type) => value === null ? '空值' : value === '' ? '空字符串' :
    type === 'bool' ? (value ? '是' : '否') : String(value);

const axes = { center_x: '质心 X 坐标', center_y: '质心 Y 坐标' };
export function geometryProperties(layer) {
    const shape = layer?.geometry?.split(' ')[0];
    if (/^(Multi)?Polygon$/.test(shape)) return { area: '面积', perimeter: '周长', ...axes };
    if (/^(Multi)?LineString$/.test(shape)) return { length: '长度', ...axes };
    if (shape === 'Point' && layer.singlePoint !== false) return { point_x: 'X 坐标', point_y: 'Y 坐标' };
    return /^(Multi)?Point$/.test(shape) ? axes : {};
}

const fieldsOf = layer => (layer?.fields || []).map(field => ({ value: field.name, label: field.name }));
const token = (label, selected, items, onPick, disabled, dragType) =>
    h(TokenPicker, { label, selected, items, onPick, disabled, dragType });
const unique = (layer, name, choose, current, manual = false, label = '唯一值') => h(UniquePicker, {
    key: JSON.stringify([layer?.ref, name]), layer, field: name, onPick: choose,
    display: value => valueLabel(value, typeOf(layer, name)), current, manual, label,
});
const boolean = raw => {
    const value = String(raw).trim().toLowerCase();
    return ['true', '1', '是'].includes(value) ? 'true' : ['false', '0', '否'].includes(value) ? 'false' : value;
};


function valueInput(label, value, change, disabled, type, field) {
    return field(label, type === 'bool' ? h(Select, { value: value === null ? undefined : value, disabled,
        placeholder: value === null ? '空值（NULL）' : '请选择', options: options({ true: '是', false: '否' }), onChange: change }) :
        h(Input, { value: value ?? '', disabled, inputMode: numeric(type) ? 'decimal' : undefined,
            placeholder: value === null ? '空值（NULL）' : undefined, onChange: event => change(event.target.value) }));
}

function typedValue(type, value, allowNull = false) {
    if (value === null && allowNull) return { valid: true, value };
    if (type === 'bool') return { valid: value === 'true' || value === 'false', value: value === 'true' };
    if (numeric(type)) {
        const raw = value?.trim() || '';
        if (integer(type)) {
            const valid = /^[-+]?\d{1,20}$/.test(raw) && BigInt(raw) >= -(2n ** 63n) && BigInt(raw) < 2n ** 63n;
            return { valid, value: raw };
        }
        return { valid: numericLiteral(raw), value: Number(raw) };
    }
    return { valid: typeof value === 'string', value };
}

export function attributeFilter(layer, values, update) {
    const parts = [];
    const makeClause = () => ({ id: crypto.randomUUID(), op: 'eq' });
    const filters = values.filters || [{ id: 'first', op: 'eq' }];
    const setFilters = change => update('filters', current => change(current || [{ id: 'first', op: 'eq' }]));
    const logic = (label, value, onChange) => h(Select, { size: 'small', 'aria-label': label, value, onChange,
        options: options({ and: '且（全部满足）', or: '或（任一满足）' }) });
    function group(rows, operator, changeRows, setLogic, groupId) {
        const clauses = rows.map((row, index) => {
            const replace = change => changeRows(current =>
                current.map(item => item.id === row.id ? change(item) : item));
            const remove = rows.length > 1 && h(Button, { size: 'small',
                onClick: () => changeRows(current => current.filter(item => item.id !== row.id)) },
                row.filters ? '删除条件组' : '删除条件');
            if (row.filters) {
                const nested = group(row.filters, row.logic, change => replace(item => ({ ...item, filters: change(item.filters) })),
                    next => replace(item => ({ ...item, logic: next })), row.id);
                return { ...nested, view: h('fieldset', { key: row.id, className: 'gis-filter-group' },
                    h('legend', null, '条件组'), nested.view, remove) };
            }
            const clause = attributeClause(layer, row, changes => replace(item => ({ ...item, ...changes })));
            return { ...clause, view: h('fieldset', { key: row.id, className: 'gis-filter-clause',
                'aria-label': '条件 ' + (index + 1) }, ...clause.parts, remove) };
        });
        return { where: clauses.length === 1 ? clauses[0].where : { op: operator, conditions: clauses.map(clause => clause.where) },
            valid: clauses.every(clause => clause.valid),
            view: h(window.React.Fragment, null,
                rows.length > 1 && logic(groupId ? '组内关系' : '条件关系', operator, setLogic),
                ...clauses.map(clause => clause.view),
                h('div', { className: 'gis-condition-line' },
                    h(Button, { size: 'small', disabled: rows.length >= 32 || !layer,
                        onClick: () => {
                            const clause = makeClause();
                            changeRows(current => [...current, clause]);
                        } }, '添加条件'),
                    !groupId && h(Button, { size: 'small', disabled: rows.length >= 32 || !layer,
                        onClick: () => {
                            const nested = { id: crypto.randomUUID(), logic: 'or', filters: [makeClause(), makeClause()] };
                            changeRows(current => [...current, nested]);
                        } }, '添加条件组'))) };
    }
    const query = group(filters, values.logic || 'and', setFilters, value => update('logic', value));
    parts.push(query.view);
    return { parts, parameters: { where: query.where }, valid: query.valid };
}

function attributeClause(layer, filter, update) {
    const type = typeOf(layer, filter.field);
    const op = filter.op;
    const where = { field: filter.field, op };
    const relations = { eq: '等于', ne: '不等于',
        ...(type !== 'bool' && { gt: '大于', gte: '大于或等于', lt: '小于', lte: '小于或等于' }),
        ...(!numeric(type) && type !== 'bool' && !type.startsWith('datetime') && { contains: '包含' }),
        is_null: '为空', is_not_null: '不为空' };
    const nullCheck = op === 'is_null' || op === 'is_not_null';
    const peers = layer?.fields.filter(item => item.name !== filter.field &&
        (item.type === type || numeric(type) && numeric(item.type))) || [];
    const fromField = !nullCheck && op !== 'contains' && peers.length > 0 && filter.source === 'field';
    const choose = value => {
        if (value === null) update({ op: op === 'ne' ? 'is_not_null' : 'is_null', value: undefined });
        else update({ value: type === 'bool' ? boolean(value) : String(value) });
    };
    const parts = [h('div', { className: 'gis-condition-line' },
        token('字段', filter.field, fieldsOf(layer), value => update({
            field: value, op: 'eq', value: undefined, source: undefined, valueField: undefined,
        }), !layer),
        token('条件', op, options(relations), value => update({ op: value }), !type)),
    !nullCheck && h('div', { className: 'gis-condition-line' },
        peers.length > 0 && op !== 'contains' && token('比较来源', fromField ? 'field' : 'value',
            options({ value: '指定值', field: '其他字段' }), value => update({ source: value })),
        fromField ? token('比较字段', filter.valueField, fieldsOf({ fields: peers }), value => update({ valueField: value }))
            : unique(layer, filter.field, choose,
                filter.value === undefined ? undefined : valueLabel(type === 'bool' ? filter.value === 'true' : filter.value, type), true, '匹配值'))];
    if (nullCheck) return { parts, where, valid: Boolean(type) };
    if (fromField) return { parts, where: { ...where, value_field: filter.valueField },
        valid: peers.some(item => item.name === filter.valueField) };
    const match = typedValue(type, filter.value);
    return { parts, where: { ...where, value: match.value }, valid: Boolean(type && relations[op] && match.valid) };
}

export function fieldCalculation(layer, values, update, controls) {
    const { field } = controls;
    const type = typeOf(layer, values.field);
    const action = values.operator || 'constant';
    const operations = { constant: '填入固定值', copy: '复制其他字段的值', add: '加上数值', subtract: '减去数值',
        multiply: '乘以数值', divide: '除以数值' };
    const parts = [
        field('写入字段', token('写入字段', values.field, fieldsOf(layer), name => {
            update('field', name); update('value', undefined); update('source_field', undefined);
        }, !layer, 'application/x-guizhi-field')),
        field('填值方式', token('填值方式', action, options(operations), value => update('operator', value))),
    ];
    const parameters = { field: values.field };
    if (action === 'constant') {
        const change = value => update('value', value);
        parts.push(valueInput('填入值', values.value, change, !type, type, field));
        const result = typedValue(type, values.value, true);
        return { parts, parameters: { ...parameters, constant: { value: result.value } }, valid: Boolean(type && result.valid) };
    }
    const sources = layer?.fields.filter(item => action === 'copy' ? item.name !== values.field : numeric(item.type)) || [];
    const source = sources.find(item => item.name === values.source_field);
    const sourceLabel = action === 'copy' ? '从哪个字段复制' : '参与计算的字段';
    parts.push(field(sourceLabel, token(sourceLabel, source?.name, sources.map(item => ({ value: item.name, label: item.name })),
        name => update('source_field', name), !layer, 'application/x-guizhi-field')));
    const amount = values.operand?.trim() || '';
    if (action !== 'copy') parts.push(valueInput('数值', values.operand, value => update('operand', value), false, 'float64', field));
    return { parts, parameters: { ...parameters, source_field: source?.name, operator: action,
        ...(action !== 'copy' && { operand: Number(amount) }) },
    valid: Boolean(type && operations[action] && source &&
        (action === 'copy' || numericLiteral(amount) && (action !== 'divide' || Number(amount) !== 0))) };
}

export function geometryCalculation(layer, values, update, { field }) {
    const properties = geometryProperties(layer);
    const property = values.property || Object.keys(properties)[0];
    const units = property === 'area' ? { square_meters: '平方米', mu: '亩', hectares: '公顷', square_kilometers: '平方千米' } :
        ['perimeter', 'length'].includes(property) ? { meters: '米', kilometers: '千米' } : { layer: '图层坐标单位' };
    const unit = values.unit || Object.keys(units)[0];
    const suffix = { square_meters: '_M2', mu: '_MU', hectares: '_HA', square_kilometers: '_KM2', meters: '_M', kilometers: '_KM', layer: '' };
    const base = property && property.toUpperCase() + suffix[unit];
    const names = new Set([layer?.fidField, ...(layer?.fields || []).map(item => item.name)].filter(Boolean).map(name => name.toLowerCase()));
    let generated = base;
    for (let number = 2; generated && names.has(generated.toLowerCase()); number++) generated = base + '_' + number;
    const destinations = (layer?.fields || []).filter(item => item.type.startsWith('float')).map(item => ({ value: item.name, label: item.name }));
    if (generated) destinations.push({ value: generated, label: '新建字段 ' + generated });
    const destination = values.field || generated;
    const parts = [
        field('计算内容', token('计算内容', property, options(properties), value => {
            update('property', value); update('unit', undefined); update('field', undefined);
        }, !layer)),
        Object.keys(units).length > 1 && field('结果单位', token('结果单位', unit, options(units), value => {
            update('unit', value); update('field', undefined);
        }, !layer)),
        field('写入字段', token('写入字段', destination, destinations, value => update('field', value), !destinations.length)),
    ];
    return { parts, parameters: { property, unit, field: destination }, valid: Boolean(properties[property] && units[unit] &&
        destinations.some(item => item.value === destination)) };
}
