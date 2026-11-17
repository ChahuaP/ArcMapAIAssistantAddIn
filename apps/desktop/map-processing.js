import { attributeFilter, fieldCalculation, geometryCalculation, geometryProperties } from './map-attribute-tools.js';
import { AttributePreview, TokenPicker } from './map-pickers.js';

const h = window.React.createElement;
const { useEffect, useState } = window.React;
const { Button, Checkbox, Input, Modal, Segmented, Select } = window.antd;
const key = item => item && item.ref.run + ':' + item.ref.artifact;
const vector = item => Boolean(item?.geometry);
const polygon = item => vector(item) && item.geometry.includes('Polygon');
const raster = item => item?.kind === 'raster';
const options = values => Object.entries(values).map(([value, label]) => ({ value, label }));
const dropdown = (value, choices, onChange, extra = {}) => h(Select, {
    value: value ?? undefined, options: choices, onChange, placeholder: '请选择', ...extra,
});
const field = (label, control) => h('div', { className: 'gis-tool-field' },
    h('span', null, label), window.React.cloneElement(control, { 'aria-label': label }));
const numeric = (label, value, change, min) => field(label, h(Input, {
    type: 'number', step: 'any', min, value: value ?? '', onChange: event => change(event.target.value),
}));
const number = value => value === '' || value == null ? NaN : Number(value);
const tool = (id, title, icon, ...accepts) => ({ id, title, icon, accepts });
const area = tool('vector.area', '面积汇总', 'area', polygon);
const count = tool('data.describe', '要素计数', 'count', vector);
const selectAttr = tool('data.select_by_attribute', '按属性选择', 'filter', vector);
const selectLocation = tool('data.select_by_location', '按位置选择', 'spatialSelect', vector, vector);
const tools = [
    { title: '统计', icon: 'statistics', tools: [area, count,
        tool('flow.unique_values', '唯一值', 'filter', vector), tool('flow.calculate', '数值计算', 'calculator')] },
    { title: '矢量处理', icon: 'layers', tools: [
        tool('vector.buffer', '缓冲分析', 'buffer', vector),
        tool('vector.dissolve', '融合', 'dissolve', vector),
        tool('geometry.repair', '修复几何', 'pencil', vector),
    ] },
    { title: '叠加分析', icon: 'layers', tools: [
        tool('vector.intersect', '相交', 'intersect', vector, vector),
        tool('vector.erase', '擦除', 'erase', vector, polygon),
        tool('vector.clip', '裁剪', 'clip', vector, polygon),
        tool('vector.union', '联合', 'union', polygon, polygon),
        tool('vector.identity', '标识叠加', 'overlayIdentity', polygon, polygon),
        tool('vector.update_overlay', '更新叠加', 'refresh', polygon, polygon),
        tool('vector.symmetric_difference', '对称差集', 'difference', polygon, polygon),
        tool('vector.spatial_join', '空间连接', 'spatialJoin', vector, vector),
    ] },
    { title: '坐标系', icon: 'globe', tools: [
        tool('crs.reproject', '投影转换', 'globe', vector),
        tool('crs.assign', '定义坐标系', 'target', vector),
        tool('crs.transform', '按方案转换', 'globe', vector),
    ] },
    { title: '栅格处理', icon: 'grid', tools: [
        tool('raster.statistics', '栅格与波段统计', 'statistics', raster),
        tool('raster.clip', '裁切栅格', 'clip', raster, polygon),
        tool('raster.reproject', '栅格重投影', 'globe', raster),
        tool('raster.assign', '定义影像坐标系', 'target', raster),
        { ...tool('raster.mosaic', '拼接栅格', 'merge', raster), multiple: true },
    ] },
];
export const TOOL_GROUPS = tools;
const definitions = Object.fromEntries([...tools.flatMap(group => group.tools), selectAttr, selectLocation,
    tool('data.copy', '复制图层', 'copy', vector),
    { ...tool('data.merge', '合并图层', 'merge', vector), multiple: true },
    tool('geometry.create_dataset', '新建图层', 'newLayer'),
    tool('table.add_field', '添加字段', 'columnAdd', vector),
    tool('table.delete_field', '删除字段', 'columnRemove', vector),
    tool('table.calculate_field', '字段计算器', 'calculator', vector),
    tool('table.calculate_geometry', '计算几何', 'area', item => Object.keys(geometryProperties(item)).length > 0),
    tool('presentation.export_table', '导出属性表', 'download', vector),
].map(item => [item.id, item]));
export const toolIcon = id => definitions[id]?.icon;

const sameCrs = new Set(['vector.intersect', 'vector.erase', 'vector.clip', 'vector.union', 'vector.identity',
    'vector.update_overlay', 'vector.symmetric_difference', 'vector.spatial_join', 'data.merge', 'raster.clip', 'raster.mosaic']);

function toolInputIssue(operation, inputs, parameters) {
    const assign = ['crs.assign', 'raster.assign'].includes(operation);
    if (assign && inputs[0]?.crs) return '此图层已有坐标系，请使用投影转换';
    if (!assign && !['data.describe', 'raster.statistics'].includes(operation) && inputs.some(layer => layer?.crs === null))
        return '请先为输入图层定义实际坐标系';
    const destination = parameters.definition || parameters.target_crs;
    const codes = inputs.map(layer => layer?.crs);
    if (['crs.reproject', 'raster.reproject'].includes(operation) && destination && destination === codes[0])
        return '请选择与源图层不同的目标坐标系';
    if (sameCrs.has(operation) && new Set(codes.filter(code => /^EPSG:\d+$/.test(code))).size > 1)
        return '输入图层的坐标系不同，请先转换为同一坐标系';
    if ((['vector.area', 'vector.buffer'].includes(operation) || operation === 'data.select_by_location' &&
            parameters.predicate === 'within_a_distance') && inputs[0]?.units === 'degrees')
        return '请先将输入图层转换为投影坐标系';
    if (operation === 'flow.calculate' && parameters.operator === 'divide' && parameters.right === 0)
        return '除数不能为 0';
    return '';
}
const layerOptions = layers => layers.map(item => ({ value: key(item), label: item.name }));
export const layerMatches = (role, item) => (raster(item) ? 'raster' : 'vector') === role.kind &&
    (role.geometry === 'any' || ({ line: 'LineString', polygon: 'Polygon', point: 'Point' }[role.geometry] &&
        item.geometry?.includes({ line: 'LineString', polygon: 'Polygon', point: 'Point' }[role.geometry]))) &&
    (role.coordinate === 'any' || Boolean(item.crs) && (role.coordinate !== 'projected_meters' || item.units === 'm'));
export function mapLayerMaterial(item) {
    const id = key(item);
    const kind = raster(item) ? 'raster' : 'vector';
    const roleInfo = { name: item.name, geometry: item.geometry, crs: item.crs, projected_meters: item.units === 'm' };
    return { file: id, mapKey: id, ref: item.ref, kind, name: item.name,
        layer: kind === 'raster' ? null : item.name, layers: kind === 'raster' ? [] : [item.name],
        layerInfo: [roleInfo], metadata: roleInfo };
}
export const roleLayerPicker = (layers, busy) => (role, items, append, pending) => {
    const choices = layers.filter(layer => layer.ref && layerMatches(role, layer) && !items.some(item => item.mapKey === key(layer)));
    return choices.length > 0 && dropdown(null, layerOptions(choices), id => {
        append([mapLayerMaterial(choices.find(layer => key(layer) === id))]);
    }, { disabled: busy || pending, showSearch: true, optionFilterProp: 'label', placeholder: '从地图选图层',
        'aria-label': '选择' + role.label + '图层' });
};
const locationGuide = {
    intersect: ['有任何公共部分，包括只碰到边或点。', 'overlap'],
    contain: ['输入要素包住查询要素。', 'contain'],
    within: ['输入要素完全落在查询要素内。', 'within'],
    touch: ['边界相碰，内部没有交集。', 'touch'],
    overlap: ['同维度要素共享部分范围，双方均有未重叠的部分。例如两个面部分盖住。', 'overlap'],
    cross: ['要素穿过另一个要素，交集维度低于其中较高的维度。例如一条路穿过一个地块。两个面不会判为穿越。', 'cross'],
    within_a_distance: ['两要素之间最短距离小于或等于设定值。', 'distance'],
};
function SpatialExample({ predicate }) {
    const [text, shape] = locationGuide[predicate] || locationGuide.overlap;
    return h('div', { className: 'gis-tool-help' }, h('div', { className: 'gis-spatial-example ' + shape, 'aria-hidden': true },
        h('span'), h('span')), h('span', null, text));
}
const skillHints = {
    'control-check': '用项目范围与管控范围叠加，按相交后去重的面积计算覆盖率。只碰到边或点时，重叠面积为零。',
    'farmland-balance': '分别填写拟调出范围和补划范围，按同一面积口径比较调出面积和有效补划面积。',
    'annual-land': '选择两期地类面图层，通过叠加识别类别保留和变化范围。',
    'permit-history': '用范围叠加比对用地手续与历史地类，按时间区分不同记录。',
};
const stats = {
    project_area_m2: '项目面积（平方米）', covered_area_m2: '重叠面积（平方米）', coverage_ratio: '覆盖率',
    updated: '已计算要素数',
    area_m2: '合并面积（平方米）', file_count: '交付文件数', year_count: '统计年份数', feature_count: '要素数', selected_count: '选中要素数', row_count: '统计记录数',
    removed_area_m2: '调出面积（平方米）', replacement_area_m2: '补划面积（平方米）',
    qualified_area_m2: '符合质量阈值面积（平方米）', required_area_m2: '要求补划面积（平方米）',
    unrated_area_m2: '未评级面积（平方米）', meets_declared_ratio: '达到本次比例要求',
    uncovered_area_m2: '未覆盖面积（平方米）', changed_area_m2: '变化面积（平方米）',
    common_coverage_m2: '共同覆盖面积（平方米）', band_count: '波段数', width: '宽度（像元）', height: '高度（像元）',
};


function CoordinateChoice({ value, onPick, placeholder, standardOnly }) {
    const [query, setQuery] = useState('');
    const [items, setItems] = useState([]);
    const [custom, setCustom] = useState([]);
    const [error, setError] = useState('');
    useEffect(() => {
        if (standardOnly) return;
        let current = true;
        window.guizhi.coordinates().then(entries => {
            if (!current) return;
            setCustom(entries.filter(item => item.kind === 'definition').map(item => {
                const unit = item.definition.axes?.[0]?.unit;
                return { value: item.id + '@' + item.revision, label: item.title,
                    kind: unit === 'degree' ? '地理坐标系' : '投影坐标系',
                    units: unit === 'degree' ? 'degrees' : ['metre', 'meter'].includes(unit) ? 'm' : undefined };
            }));
        }).catch(problem => { if (current) setError(window.GuizhiErrorText(problem)); });
        return () => { current = false; };
    }, [standardOnly]);
    useEffect(() => {
        let current = true;
        const timer = setTimeout(async () => {
            try {
                const found = await window.guizhi.map({ action: 'crs-search', query });
                if (current) { setItems(found); setError(''); }
            } catch (problem) { if (current) setError(window.GuizhiErrorText(problem)); }
        }, 160);
        return () => { current = false; clearTimeout(timer); };
    }, [query]);
    const groups = ['地理坐标系', '投影坐标系'].map(label => ({
        label, options: [...custom.filter(item => item.label.toLowerCase().includes(query.trim().toLowerCase())), ...items]
            .filter(item => item.kind === label),
    })).filter(group => group.options.length);
    return h(Select, { showSearch: true, filterOption: false, onSearch: setQuery, labelInValue: true,
        'aria-label': placeholder, placeholder, value: value || undefined, options: groups,
        notFoundContent: error || '没有匹配的坐标系', onChange: (_, item) => {
            onPick({ value: item.value, label: item.label, units: item.units, metresPerUnit: item.metresPerUnit });
        } });
}

function toolForm(definition, inputs, values, update, entries) {
    const { id } = definition;
    const input = inputs[0];
    const names = input?.fields?.map(item => item.name) || [];
    const fields = names.map(name => ({ value: name, label: name }));
    const select = (name, label, choices, defaultValue) => field(label, dropdown(values[name] ?? defaultValue, options(choices),
        value => update(name, value)));
    const text = (name, label, placeholder) => field(label, h(Input, {
        value: values[name] ?? '', placeholder, onChange: event => update(name, event.target.value),
    }));
    const columns = (name, label, extra = {}) => field(label, dropdown(values[name], fields, value => update(name, value), extra));
    const controls = [];
    const coordinates = label => h('label', { className: 'gis-tool-field' }, h('span', null, label),
        h(CoordinateChoice, { value: values.coordinate, placeholder: label, standardOnly: id === 'geometry.create_dataset',
            onPick: value => update('coordinate', value) }));
    let parameters = {};
    let valid = true;
    const attributeTools = { 'data.select_by_attribute': attributeFilter,
        'table.calculate_field': fieldCalculation,
        'table.calculate_geometry': geometryCalculation };
    if (attributeTools[id]) {
        const form = attributeTools[id](input, values, update, { field });
        return { controls: [...controls, ...form.parts], valid: valid && form.valid, parameters: form.parameters };
    }
    if (id === 'geometry.create_dataset') {
        controls.push(field('图层名称', h(Input, { maxLength: 120, value: values.name ?? '', onChange: event => update('name', event.target.value) })),
            select('geometry_type', '要素类型', { point: '点', polyline: '线', polygon: '面' }),
            coordinates('图层坐标系'));
        parameters = { geometry_type: values.geometry_type,
            epsg: number(values.coordinate?.value.slice(5)) };
        valid = Boolean(values.name?.trim() && values.geometry_type && Number.isInteger(parameters.epsg) && parameters.epsg > 0);
    } else if (id === 'vector.buffer') {
        const choice = (name, label, defaultValue, labels) => h(TokenPicker, { label,
            selected: values[name] || defaultValue, items: options(labels), onPick: value => update(name, value) });
        controls.push(h('div', { className: 'gis-condition-line' },
            h(Input, { type: 'number', size: 'small', step: 'any', min: 0, placeholder: '距离',
                className: 'gis-tool-distance', 'aria-label': '缓冲距离', value: values.distance ?? '',
                onChange: event => update('distance', event.target.value) }),
            choice('unit', '距离单位', 'meters', { meters: '米', kilometers: '千米', map_units: '图层单位' })),
        h('details', null, h('summary', null, '高级参数'),
            h('div', { className: 'gis-condition-line' },
                choice('cap', '端点形状', 'round', { round: '圆头', flat: '平头', square: '方头' }),
                choice('join', '拐角形状', 'round', { round: '圆角', mitre: '尖角', bevel: '斜角' })),
            h(Checkbox, { checked: values.dissolve === 'all',
                onChange: event => update('dissolve', event.target.checked ? 'all' : 'keep') }, '融合缓冲区')));
        parameters = { distance: number(values.distance), unit: values.unit || 'meters', cap: values.cap || 'round',
            join: values.join || 'round', dissolve: values.dissolve === 'all' };
        valid &&= Number.isFinite(parameters.distance) && parameters.distance > 0;
    } else if (id === 'data.select_by_location') {
        controls.push(h(TokenPicker, { label: '空间关系', selected: values.predicate, items: options({ intersect: '相交', contain: '包含', within: '位于范围内', touch: '接触',
            overlap: '重叠', cross: '穿越', within_a_distance: '距离范围内' }), onPick: value => update('predicate', value) }));
        controls.push(h(SpatialExample, { predicate: values.predicate }));
        parameters = { predicate: values.predicate };
        valid &&= Boolean(values.predicate);
        if (values.predicate === 'within_a_distance') {
            controls.push(numeric('最大距离（米）', values.distance, value => update('distance', value), 0));
            parameters.distance = number(values.distance);
            valid &&= Number.isFinite(parameters.distance) && parameters.distance > 0;
        }
    } else if (id === 'vector.dissolve' || id === 'presentation.export_table') {
        controls.push(columns('fields', '字段（选填）', { mode: 'multiple', maxTagCount: 'responsive' }));
        if (values.fields?.length) {
            parameters = { fields: values.fields };
            valid &&= values.fields.every(name => names.includes(name));
        }
        if (id === 'presentation.export_table') valid &&= names.length > 0;
    } else if (id === 'table.add_field') {
        controls.push(text('name', '字段名称'), select('type', '字段类型', { string: '文本', integer: '整数',
            double: '小数', date: '日期时间' }));
        const name = values.name?.trim();
        parameters = { field: { name, type: values.type } };
        valid &&= Boolean(name && values.type && ![...names, input?.fidField].some(field => field?.toLowerCase() === name.toLowerCase()));
    } else if (id === 'table.delete_field') {
        controls.push(columns('field', '删除字段'));
        parameters = { field: values.field };
        valid &&= names.includes(values.field);
    } else if (id === 'vector.spatial_join') {
        controls.push(select('mode', '连接方式', { one_to_one: '一对一', one_to_many: '一对多' }, 'one_to_one'));
        parameters = { mode: values.mode || 'one_to_one' };
    } else if (id === 'crs.reproject' || id === 'crs.assign') {
        controls.push(coordinates(id === 'crs.assign' ? '数据的实际坐标系' : '目标坐标系'));
        parameters = { definition: values.coordinate?.value };
        valid &&= Boolean(parameters.definition);
    } else if (id === 'crs.transform') {
        const plans = entries.filter(item => item.kind === 'plan').map(item => ({ value: item.id + '@' + item.revision, label: item.title }));
        controls.push(field('转换方案', dropdown(values.plan, plans, value => update('plan', value))));
        parameters = { plan: values.plan };
        valid &&= Boolean(parameters.plan);
    } else if (id === 'raster.assign') {
        controls.push(coordinates('影像的实际坐标系'));
        parameters = { target_crs: values.coordinate?.value };
        valid &&= Boolean(parameters.target_crs);
    } else if (id === 'raster.reproject') {
        controls.push(coordinates('目标坐标系'), select('resampling', '重采样',
            { nearest: '最邻近', bilinear: '双线性', cubic: '三次卷积' }, 'nearest'));
        parameters = { target_crs: values.coordinate?.value, resampling: values.resampling || 'nearest' };
        valid &&= Boolean(parameters.target_crs);
    } else if (id === 'flow.unique_values') {
        controls.push(columns('field', '字段'), text('query', '匹配文字（选填）'));
        parameters = { field: values.field, query: values.query?.trim() || '' };
        valid &&= names.includes(values.field);
    } else if (id === 'flow.calculate') {
        controls.push(field('运算', h(Segmented, { block: true, value: values.operator || 'add',
            options: options({ add: '+', subtract: '−', multiply: '×', divide: '÷' }), onChange: value => update('operator', value) })),
        numeric('左操作数', values.left, value => update('left', value)), numeric('右操作数', values.right, value => update('right', value)));
        parameters = { operator: values.operator || 'add', left: number(values.left), right: number(values.right) };
        valid &&= Number.isFinite(parameters.left) && Number.isFinite(parameters.right);
    }
    return { controls, valid, parameters };
}

const toolInputs = {
    'data.select_by_location': ['待选择图层', '参照图层'],
    'vector.clip': ['输入图层', '裁切范围'], 'vector.erase': ['输入图层', '擦除范围'],
    'vector.spatial_join': ['目标图层', '连接图层'], 'vector.update_overlay': ['输入图层', '更新图层'],
    'raster.clip': ['输入影像', '裁切范围'], 'raster.mosaic': ['拼接影像'], 'data.merge': ['合并图层'],
};

export function toolError(result) {
    if (!['failed', 'interrupted'].includes(result.status)) return '';
    return window.GuizhiErrorText(result.message) + (result.diagnosticId ? '　故障编号 ' + result.diagnosticId : '');
}

function ResultSummary({ result }) {
    const metrics = Object.entries(result.statistics || {}).filter(([name, value]) => stats[name] &&
        (typeof value === 'number' || typeof value === 'boolean'));
    const format = value => typeof value === 'number' ? value.toLocaleString('zh-CN', { maximumFractionDigits: 3 }) : value ? '是' : '否';
    return h(window.React.Fragment, null,
        metrics.length > 0 && h('dl', { className: 'gis-result-metrics', role: 'status' },
            metrics.map(([name, value]) => h('div', { key: name }, h('dt', null, stats[name]), h('dd', null, format(value))))),
        result.statistics?.band_statistics?.map(band => h('p', { key: band.band }, '波段 ' + band.band + ' · 有效像元 ' + band.valid_count +
            ' · 无数据像元 ' + band.nodata_count + ' · 最小 ' + (band.min ?? '—') + ' · 最大 ' + (band.max ?? '—') + ' · 平均 ' + (band.mean ?? '—'))),
        Array.isArray(result.value) ? h('ul', { className: 'gis-tool-values', 'aria-label': '唯一值' },
            result.value.map((value, index) => h('li', { key: index }, value === null ? '空值' : value === '' ? '空字符串' : String(value))))
            : !metrics.length && result.value !== undefined && h('output', null, String(result.value)));
}

export function MapTool({ operation, active, layers, entries, components, openComponents, glyph, busy, setBusy, close, onResult }) {
    const api = window.guizhi;
    const definition = definitions[operation];
    const initial = operation === 'geometry.create_dataset' ? { geometry_type: 'polygon' }
        : operation === 'data.select_by_location' ? { predicate: 'intersect' } : {};
    const [ids, setIds] = useState(() => definition.accepts[0]?.(layers.find(item => key(item) === active)) ? [active] : []);
    const [values, setValues] = useState(initial);
    const [result, setResult] = useState(null);
    const [error, setError] = useState('');
    const update = (name, value) => setValues(old => ({ ...old, [name]: value }));
    const inputs = ids.map(id => layers.find(item => key(item) === id));
    const names = toolInputs[operation] || (definition.accepts.length > 1 ? ['输入图层', '叠加图层'] : ['图层']);
    const pickLayer = (index, records) => field(names[index], dropdown(definition.multiple ? ids : ids[index], layerOptions(records), value => {
        if (definition.multiple) setIds(value);
        else setIds(old => Object.assign([...old], { [index]: value }));
        if (!index) setValues(initial);
        setError('');
    }, { showSearch: true, optionFilterProp: 'label', notFoundContent: records.length ? '没有匹配的图层' : '暂无图层',
        ...(definition.multiple && { mode: 'multiple', maxCount: 32, maxTagCount: 'responsive' }) }));
    const selectors = definition.accepts.map((accepts, index) => pickLayer(index, layers.filter(item => accepts(item) &&
        (definition.multiple || !ids.some((id, slot) => slot !== index && key(item) === id)))));
    const form = toolForm(definition, inputs, values, update, entries);
    const minimum = definition.multiple ? 2 : definition.accepts.length;
    const validInputs = inputs.length >= minimum && Array.from({ length: inputs.length }, (_, i) =>
        (definition.accepts[i] || definition.accepts.at(-1))(inputs[i])).every(Boolean);
    const problem = validInputs ? toolInputIssue(operation, inputs, form.parameters) : '';
    const missing = operation.startsWith('raster.') && components?.raster?.installed === false;
    const valid = validInputs && form.valid && !problem && !missing;
    const verb = { 'geometry.create_dataset': '确定', 'table.add_field': '确定', 'table.delete_field': '删除',
        'table.calculate_field': '计算', 'table.calculate_geometry': '计算', 'flow.calculate': '计算',
        'data.select_by_attribute': '选择', 'data.select_by_location': '选择', 'presentation.export_table': '导出' }[operation] || '运行';
    async function run() {
        if (busy || !valid) return;
        setError('');
        setBusy(true);
        try {
            if (operation === 'presentation.export_table') {
                if (await api.mapExport(inputs[0].ref, true, undefined, undefined, form.parameters.fields)) close();
                return;
            }
            const output = await api.map({ action: 'tool', operation, refs: inputs.map(item => item.ref), parameters: form.parameters,
                ...(operation === 'geometry.create_dataset' && { name: values.name.trim() }) });
            const failure = toolError(output);
            if (failure) { setError(failure); return; }
            if (output.layers.length || output.selection) onResult(output, inputs[0]);
            if (!output.layers.length) setResult(output);
        } catch (issue) { setError(window.GuizhiErrorText(issue)); }
        finally { setBusy(false); }
    }
    return h(Modal, { open: true, centered: true, width: 480, className: 'gis-dialog', rootClassName: 'gis-modal-root',
        title: h('span', { className: 'gis-dialog-title' }, glyph(definition.icon), definition.title),
        closable: !busy, keyboard: !busy, maskClosable: false, onCancel: () => { if (!busy) close(); }, onOk: result ? close : run,
        okText: result ? '完成' : verb, cancelText: '取消', confirmLoading: busy,
        okButtonProps: { disabled: !result && !valid }, cancelButtonProps: { disabled: busy, className: result ? 'gis-result-cancel' : undefined } },
        result ? h(ResultSummary, { result }) : h('fieldset', { className: 'gis-tool-fields', disabled: busy, 'aria-busy': busy },
            ...[...selectors, ...form.controls].map((control, index) => h(window.React.Fragment, { key: index }, control)),
            missing && h('div', { className: 'gis-component-message' }, '请先安装栅格处理组件',
                h(Button, { size: 'small', onClick: openComponents }, '打开组件设置')),
            (error || problem) && h('p', { className: 'error', role: 'alert' }, error || problem)));
}

export function MapResultDialog({ result, close, add, replace }) {
    const [preview, setPreview] = useState(false);
    const layer = result.layers.length === 1 ? result.layers[0] : null;
    return h(Modal, { open: true, centered: true, width: 480, className: 'gis-dialog', rootClassName: 'gis-modal-root',
        title: '加入地图？', onCancel: close, maskClosable: false,
        footer: [h(Button, { key: 'decline', onClick: close }, '不加入'), replace && h(Button, { key: 'replace', onClick: replace }, '替换原图层'),
            h(Button, { key: 'add', type: 'primary', onClick: add }, '加入地图')] },
        h('div', { className: 'map-field-editor' }, h('ul', { className: 'gis-tool-values', 'aria-label': '成果图层' },
            result.layers.map(item => h('li', { key: key(item) }, item.name, item.crs && ' · ' + (/^EPSG:\d+$/.test(item.crs) ? item.crs : item.crsName || '')))),
            h(ResultSummary, { result }), layer?.geometry && h('div', null,
                h(Button, { size: 'small', onClick: () => setPreview(!preview) }, preview ? '收起预览' : '预览属性'),
                preview && h(AttributePreview, { layer }))));
}

function parseMapping(text) {
    const rows = text?.split(/\r?\n/).map(line => line.trim()).filter(Boolean) || [];
    const pairs = rows.map(row => {
        const at = row.search(/[=＝]/);
        return at < 0 ? [] : [row.slice(0, at).trim(), row.slice(at + 1).trim()];
    });
    if (!pairs.length || pairs.some(row => row.length !== 2 || !row[0] || !row[1]) ||
            new Set(pairs.map(row => row[0])).size !== pairs.length) return null;
    return Object.fromEntries(pairs);
}

function SkillParameters({ skill, materials, layers, entries, values, setValues, disabled }) {
    const update = (name, value) => setValues(old => ({ ...old, [name]: value }));
    const layer = role => layers.find(item => key(item) === materials[role]?.[0]?.mapKey);
    const columns = (role, other) => {
        const first = layer(role)?.fields;
        const second = other && layer(other)?.fields;
        return first?.filter(item => !second || second.some(field => field.name === item.name))
            .map(item => ({ value: item.name, label: item.name }));
    };
    const fieldOptions = skill.id === 'farmland-balance' ? columns('quality')
        : ['annual-land', 'permit-history'].includes(skill.id) ? columns('from', 'to') : undefined;
    return Object.entries(skill.parameters).map(([name, label]) => {
        let control;
        const selectedFields = name === 'fields' && skill.id === 'shared-delivery' ? columns('vectors')
            : ['field', 'quality_field'].includes(name) ? fieldOptions : undefined;
        if (selectedFields) {
            const multi = name === 'fields';
            control = dropdown(values[name], selectedFields, value => update(name, value),
                { disabled, ...(multi ? { mode: 'multiple', placeholder: '选择允许交付的字段' } : {}) });
        } else if (name === 'required' && skill.id === 'shared-delivery') {
            control = dropdown(values[name] || [], options({ pdf: 'PDF', docx: 'Word', xlsx: 'Excel' }),
                value => update(name, value), { disabled, mode: 'multiple', placeholder: '选择必需附件，可留空' });
        } else if (name === 'plan' && skill.id === 'cad-coordinates') {
            control = dropdown(values[name], entries.filter(item => item.kind === 'plan')
                .map(item => ({ value: item.id + '@' + item.revision, label: item.title })),
                value => update(name, value), { disabled, allowClear: true, placeholder: '沿用原坐标' });
        } else if (name === 'force_2d' && skill.id === 'cad-coordinates') {
            control = dropdown(String(values[name] ?? false), options({ false: '保留原维度', true: '转换为二维' }),
                value => update(name, value === 'true'), { disabled });
        } else if (name === 'classifications' && ['annual-land', 'permit-history'].includes(skill.id)) {
            return h('div', { className: 'gis-mapping', key: name }, ...['前期', '后期'].map((period, index) => field(period + '地类对照表',
                h(Input.TextArea, { disabled, rows: 3, value: values['mapping' + index] ?? '', placeholder: '每行一个对照，例如 0101=水田',
                    onChange: event => update('mapping' + index, event.target.value) }))));
        } else {
            const type = name.endsWith('_date') || name === 'as_of' ? 'date'
                : /_year$|minimum_quality|replacement_ratio/.test(name) ? 'number' : 'text';
            control = h(Input, { type, disabled, value: values[name] ?? '', step: 'any',
                placeholder: name === 'source_crs' ? '例如 EPSG:4548' : name === 'fields' ? '多个字段用逗号分隔' : undefined,
                onChange: event => update(name, event.target.value) });
        }
        return h(window.React.Fragment, { key: name }, field(name === 'cad_layer' && skill.id === 'cad-coordinates'
            ? '目标图层（留空为全部）' : label, control));
    });
}

function skillValues(skill, values) {
    const params = {};
    for (const name of Object.keys(skill.parameters)) {
        let value = values[name];
        if (skill.id === 'cad-coordinates' && ['cad_layer', 'plan', 'force_2d'].includes(name)) {
            value = name === 'force_2d' ? value === true : value?.trim() || null;
        } else if (name === 'required' && skill.id === 'shared-delivery') {
            value = value || [];
        } else if (name === 'fields' && skill.id === 'shared-delivery') {
            if (typeof value === 'string') value = value.split(/[,，]/).map(item => item.trim()).filter(Boolean);
            if (!value?.length) return null;
        } else if (name === 'classifications' && ['annual-land', 'permit-history'].includes(skill.id)) {
            const a = parseMapping(values.mapping0), b = parseMapping(values.mapping1);
            const from = number(values.from_year), to = number(values.to_year);
            if (!a || !b || !Number.isInteger(from) || !Number.isInteger(to) || from < 1 || from >= to || to > 9999) return null;
            value = { [from]: a, [to]: b };
        } else if (/_year$|minimum_quality|replacement_ratio/.test(name)) {
            value = number(value);
            if (!Number.isFinite(value) || (name === 'replacement_ratio' && value <= 0)) return null;
        } else {
            if (!value?.trim()) return null;
            value = value.trim();
            if (!['annual-land', 'permit-history', 'farmland-balance', 'cad-coordinates', 'shared-delivery', 'imagery-change'].includes(skill.id)) {
                try { value = JSON.parse(value); } catch { /* Ordinary text is a valid custom business parameter. */ }
            }
        }
        params[name] = value;
    }
    return params;
}

export function MapProcessing({ skill, layers, entries, components, openComponents, glyph, busy, setBusy,
    close, Preview, onResult, onAddLayers, initialResult }) {
    const api = window.guizhi;
    const [values, setValues] = useState({});
    const [materials, setMaterials] = useState({});
    const [result, setResult] = useState(initialResult || null);
    const [error, setError] = useState('');
    const [pendingMaterial, setPendingMaterial] = useState('');
    const fail = issue => setError(window.GuizhiErrorText(issue));
    const missing = (skill.components || [])
        .filter(kind => components?.[kind]?.installed === false);
    const roles = skill.materials.filter(role => !role.resource);
    const { RoleMaterials, roleReady } = window.GuizhiMaterials;
    const picker = roleLayerPicker(layers, busy);
    const parameters = skillValues(skill, values);
    const valid = !missing.length && !pendingMaterial && parameters && roles.every(role => roleReady(role, materials[role.id]));
    async function run() {
        if (busy || !valid) return;
        setError('');
        setBusy(true);
        try {
            const output = await api.mapSkill({ skill: window.GuizhiTaskCommands.reference(skill), materials, parameters,
                coordinatePlans: Object.fromEntries(['project', 'control'].filter(role => materials[role]?.[0]?.plan)
                    .map(role => [role + '_plan', materials[role][0].plan])) });
            onResult(output);
            setResult(output);
        } catch (issue) { fail(issue); }
        finally { setBusy(false); }
    }
    const metric = (name, value) => typeof value === 'boolean' ? (value ? '是' : '否')
        : name === 'coverage_ratio' ? (value * 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + '%'
            : value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const dismiss = () => { if (!busy) close(); };
    const metrics = result && Object.entries(result.statistics)
        .filter(([name, value]) => stats[name] && ['boolean', 'number'].includes(typeof value));
    const pendingLayers = result?.layers.filter(item => !layers.some(existing => key(existing) === key(item))) || [];
    const offer = pendingLayers.length > 0;
    return h(Modal, { open: true, centered: true, width: 640, className: 'gis-dialog',
        rootClassName: 'gis-modal-root', closable: !busy, keyboard: !busy, maskClosable: false,
        title: h('span', { className: 'gis-dialog-title' }, glyph(window.GuizhiShell.businessIcon(skill.id)), skill.title),
        onCancel: dismiss, onOk: offer ? () => { onAddLayers(pendingLayers); dismiss(); } : result ? dismiss : run,
        okText: offer ? '加入地图' : result ? '完成' : '办理',
        cancelText: offer ? '不加入' : '取消', confirmLoading: busy, okButtonProps: { disabled: !result && !valid },
        cancelButtonProps: { disabled: busy, className: result && !offer ? 'gis-result-cancel' : undefined } },
        h('div', { className: 'map-field-editor', 'aria-busy': busy },
            missing.length > 0 && h('div', { className: 'gis-component-message', role: 'status' },
                '请先安装' + missing.map(kind => window.GuizhiComponentNames[kind]).join('、'),
                h(Button, { size: 'small', onClick: openComponents }, '打开组件设置')),
            result ? h(window.React.Fragment, null,
                metrics.length > 0 && h('dl', { className: 'gis-result-metrics', role: 'status' },
                    ...metrics.map(([name, value]) => h('div', { key: name }, h('dt', null, stats[name]), h('dd', null, metric(name, value))))),
                offer && h('p', { role: 'status' }, '是否将 ' + pendingLayers.length + ' 个成果图层加入地图？'),
                result.map && h(Preview, { map: result.map }),
                result.business?.reportDigest && h(window.GuizhiBusinessLog.ReportPanel, { id: result.business.id })) :
                h(window.React.Fragment, null,
                skillHints[skill.id] && h('details', { className: 'gis-tool-guide' },
                    h('summary', null, '适用条件'), skill.id === 'control-check'
                        ? h('div', { className: 'gis-tool-help' }, h('div', { className: 'gis-spatial-example overlap', 'aria-hidden': true },
                            h('span'), h('span')), h('span', null, skillHints[skill.id]))
                        : h('p', null, skillHints[skill.id])),
                h(RoleMaterials, { roles, materials, change: setMaterials, plans: entries.filter(item => item.kind === 'plan'),
                    fail, disabled: busy || missing.length > 0, picker, onPending: setPendingMaterial }),
                pendingMaterial && h('p', { role: 'status', className: 'muted' }, '正在读取材料…'),
                Object.keys(skill.parameters).length > 0 && h('div', { className: 'gis-skill-parameters' }, h(SkillParameters, { skill, materials, layers, entries, values,
                    setValues, disabled: busy || missing.length > 0 }))),
            error && h('p', { className: 'error', role: 'alert' }, error)));
}
