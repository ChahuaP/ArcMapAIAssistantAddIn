import { attributeFilter, fieldCalculation, geometryCalculation, geometryProperties } from './map-attribute-tools.js';
import { AttributePreview, TokenPicker } from './map-pickers.js';
import { geometryName } from './map-layer-dialog.js';

const h = window.React.createElement;
const { useEffect, useState } = window.React;
const { Button, Checkbox, Input, Modal, Segmented, Select } = window.antd;
const key = item => item && item.ref.run + ':' + item.ref.artifact;
const vector = item => Boolean(item?.geometry);
const polygon = item => vector(item) && item.geometry.includes('Polygon');
const raster = item => item?.kind === 'raster';
const spatial = item => vector(item) || raster(item);
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
const tool = (id, title, icon, action, ...inputs) => ({ id, title, icon, action, inputs });
const area = tool('vector.area', '面积汇总', 'area', '计算', ['统计图层', polygon]);
const areaUnits = { square_meters: '平方米', mu: '亩', hectares: '公顷', square_kilometers: '平方千米' };
const selectAttr = tool('data.select_by_attribute', '按属性选择', 'filter', '选择要素', ['待选择图层', vector]);
const selectLocation = tool('data.select_by_location', '按位置选择', 'spatialSelect', '选择要素',
    ['待选择图层', vector], ['参照图层', vector]);
const tools = [
    { title: '统计', icon: 'statistics', tools: [area] },
    { title: '矢量处理', icon: 'layers', tools: [
        tool('vector.buffer', '缓冲分析', 'buffer', '生成缓冲区', ['输入图层', vector]),
        tool('geometry.repair', '修复几何', 'pencil', '修复', ['待修复图层', vector]),
        tool('geometry.check_topology', '拓扑检查', 'info', '检查', ['待检查图层', vector]),
    ] },
    { title: '叠加分析', icon: 'layers', tools: [
        tool('vector.intersect', '相交', 'intersect', '相交', ['图层一', vector], ['图层二', vector]),
        tool('vector.erase', '擦除', 'erase', '擦除', ['待擦除图层', vector], ['擦除范围', polygon]),
        tool('vector.dissolve', '融合', 'dissolve', '融合', ['待融合图层', vector]),
        tool('vector.clip', '裁剪', 'clip', '裁剪', ['待裁剪图层', vector], ['裁剪范围', polygon]),
        tool('vector.union', '联合', 'union', '联合', ['图层一', polygon], ['图层二', polygon]),
        tool('vector.identity', '标识叠加', 'overlayIdentity', '叠加', ['输入图层', polygon], ['标识图层', polygon]),
        tool('vector.update_overlay', '更新叠加', 'refresh', '更新', ['待更新图层', polygon], ['更新图层', polygon]),
        tool('vector.symmetric_difference', '对称差集', 'difference', '计算差集', ['图层一', polygon], ['图层二', polygon]),
        tool('vector.spatial_join', '空间连接', 'spatialJoin', '连接', ['目标图层', vector], ['提供属性的图层', vector]),
    ] },
    { title: '坐标系', icon: 'globe', tools: [
        tool('crs.reproject', '投影', 'globe', '运行', ['图层', spatial]),
        tool('crs.assign', '定义坐标系', 'target', '定义', ['图层', spatial]),
    ] },
    { title: '栅格处理', icon: 'grid', tools: [
        tool('raster.statistics', '栅格与波段统计', 'statistics', '统计', ['统计影像', raster]),
        tool('raster.clip', '裁剪栅格', 'clip', '裁剪', ['待裁剪影像', raster], ['裁剪范围', polygon]),
        { ...tool('raster.mosaic', '拼接栅格', 'merge', '拼接', ['待拼接影像', raster]), multiple: true },
    ] },
];
export const TOOL_GROUPS = tools;
const definitions = Object.fromEntries([...tools.flatMap(group => group.tools), selectAttr, selectLocation,
    tool('data.copy', '复制图层', 'copy', '复制', ['待复制图层', vector]),
    { ...tool('data.merge', '合并图层', 'merge', '合并', ['待合并图层', vector]), multiple: true },
    tool('geometry.create_dataset', '新建图层', 'newLayer', '创建'),
    tool('geometry.multipart', '合并为多部件', 'union', '合并', ['源图层', item => Boolean(geometryName(item?.geometry))]),
    tool('geometry.explode', '拆分多部件', 'split', '拆分', ['待拆分图层', item => Boolean(geometryName(item?.geometry))]),
    tool('table.add_field', '添加字段', 'columnAdd', '确定', ['图层', vector]),
    tool('table.delete_field', '删除字段', 'columnRemove', '删除', ['图层', vector]),
    tool('table.calculate_field', '字段计算器', 'calculator', '计算', ['计算图层', vector]),
    tool('table.calculate_geometry', '计算几何', 'area', '计算',
        ['计算图层', item => Object.keys(geometryProperties(item)).length > 0]),
    tool('presentation.export_table', '导出属性表', 'download', '导出', ['导出图层', vector]),
].map(item => [item.id, item]));
export const toolIcon = id => definitions[id]?.icon;

const sameCrs = new Set(['vector.intersect', 'vector.erase', 'vector.clip', 'vector.union', 'vector.identity',
    'vector.update_overlay', 'vector.symmetric_difference', 'vector.spatial_join', 'data.merge', 'raster.clip', 'raster.mosaic']);

function toolInputIssue(operation, inputs, parameters) {
    const assign = operation === 'crs.assign';
    if (!assign && !['data.describe', 'raster.statistics'].includes(operation) && inputs.some(layer => layer?.crs === null))
        return '请先为输入图层定义实际坐标系';
    const codes = inputs.map(layer => layer?.crs);
    if (sameCrs.has(operation) && new Set(codes.filter(code => /^EPSG:\d+$/.test(code))).size > 1)
        return '输入图层的坐标系不同，请先转换为同一坐标系';
    if ((['vector.area', 'vector.buffer'].includes(operation) || operation === 'data.select_by_location' &&
            parameters.predicate === 'within_a_distance') && inputs[0]?.units === 'degrees')
        return '请先将输入图层转换为投影坐标系';
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
    intersect: ['与参照要素有交集，包括边界或点接触。', 'overlap'],
    contain: ['完全包含参照要素。', 'contain'],
    within: ['完全位于参照要素内。', 'within'],
    touch: ['边界相碰，内部没有交集。', 'touch'],
    overlap: ['同类要素部分重叠，双方各有一部分位于交集外。', 'overlap'],
    cross: ['与参照要素交叉，例如道路穿过地块或两条道路相交。', 'cross'],
    within_a_distance: ['与参照要素的最短距离在设定值以内。', 'distance'],
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
    empty_geometry_count: '空几何数', invalid_geometry_count: '无效几何数', overlap_pair_count: '重叠要素对',
    project_area_m2: '项目面积（平方米）', covered_area_m2: '重叠面积（平方米）', coverage_ratio: '覆盖率',
    updated: '已计算要素数',
    area_m2: '合并面积（平方米）', file_count: '交付文件数', year_count: '统计年份数', feature_count: '要素数', selected_count: '选中要素数', source_feature_count: '源要素数', part_count: '部件数', row_count: '统计记录数',
    removed_area_m2: '调出面积（平方米）', replacement_area_m2: '补划面积（平方米）',
    qualified_area_m2: '符合质量阈值面积（平方米）', required_area_m2: '要求补划面积（平方米）',
    unrated_area_m2: '未评级面积（平方米）', meets_declared_ratio: '达到本次比例要求',
    uncovered_area_m2: '未覆盖面积（平方米）', changed_area_m2: '变化面积（平方米）',
    common_coverage_m2: '共同覆盖面积（平方米）', band_count: '波段数', width: '宽度（像元）', height: '高度（像元）',
};


export function CoordinateChoice({ value, onPick, placeholder, standardOnly }) {
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

function ProjectionChoice({ layer, value, onPick }) {
    const [query, setQuery] = useState('');
    const [rows, setRows] = useState([]);
    const [error, setError] = useState('');
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        let active = true;
        setLoading(true);
        const timer = setTimeout(async () => {
            try {
                const options = await window.guizhi.map({ action: 'crs-projection-options', ref: layer.ref, query });
                if (active) { setRows(options); setError(''); }
            } catch (problem) {
                if (active) { setRows([]); setError(window.GuizhiErrorText(problem)); }
            } finally {
                if (active) setLoading(false);
            }
        }, 180);
        return () => { active = false; clearTimeout(timer); };
    }, [layer, query]);

    const groups = ['地理坐标系', '投影坐标系', '其他坐标系'].map(label => ({
        label, options: rows.filter(row => row.kind === label),
    })).filter(group => group.options.length);
    const choices = value?.target.plans || [];
    return h(window.React.Fragment, null,
        field('目标坐标系', h(Select, {
            showSearch: true, filterOption: false, onSearch: setQuery, labelInValue: true, loading,
            value: value ? { value: value.target.value, label: value.target.label } : undefined,
            options: groups, placeholder: '选择或搜索坐标系（如 4548）',
            notFoundContent: loading ? '正在匹配坐标系…' : error || '暂无适用的坐标系',
            onChange: (_, target) => onPick({ target, plan: target.plans[0].value }),
        })),
        value?.target.outsideArea && value.plan === 'automatic' &&
            h('p', { className: 'gis-tool-description', role: 'status' }, '图层超出该坐标系的标称适用范围，请确认项目要求。'),
        choices.length > 0 && field('投影方案', dropdown(value.plan, choices,
            plan => onPick({ target: value.target, plan }))),
        error && h('p', { className: 'error', role: 'alert' }, error));
}

function toolForm(definition, inputs, values, update, selection) {
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
        if (id === 'data.select_by_attribute') {
            const selected = Boolean(input && selection?.key === key(input) && selection.ids.length);
            const mode = selected ? (values.mode || 'new_selection') : 'new_selection';
            const modes = { new_selection: '新建选区', select_subset: '在当前选区内筛选',
                add_to_selection: '添加到当前选区', remove_from_selection: '从当前选区移除' };
            controls.push(field('选择方式', dropdown(mode, options(modes).map(item => ({
                ...item, disabled: !selected && item.value !== 'new_selection',
            })), value => update('mode', value))));
            form.parameters.mode = mode;
            if (mode !== 'new_selection') form.parameters.previous = { revision: input.revision, fids: selection.ids };
        }
        return { controls: [...controls, ...form.parts], valid: valid && form.valid, parameters: form.parameters };
    }
    if (id === 'geometry.create_dataset') {
        controls.push(field('图层名称', h(Input, { maxLength: 120, value: values.name ?? '', onChange: event => update('name', event.target.value) })),
            select('geometry_type', '图层类型', { point: '点', polyline: '线', polygon: '面' }),
            coordinates('图层坐标系'));
        parameters = { geometry_type: values.geometry_type,
            epsg: number(values.coordinate?.value.slice(5)) };
        valid = Boolean(values.name?.trim() && values.geometry_type && Number.isInteger(parameters.epsg) && parameters.epsg > 0);
    } else if (id === 'geometry.multipart' || id === 'geometry.explode') {
        const fids = selection?.key === key(input) ? selection.ids : [];
        const destination = values.destination ?? 'current';
        controls.push(field('输出到', h(Segmented, { value: destination, options: options({ current: '当前图层', new: '新图层' }),
            onChange: value => update('destination', value) })));
        parameters = { fids, destination };
        valid = fids.length > 0 && fids.length <= 500 && (destination === 'current' || fids.every(Number.isSafeInteger));
        if (id === 'geometry.multipart') {
            const source = values.attribute_fid ?? fids[0];
            controls.push(field('属性来源', dropdown(source, fids.map(fid => ({ value: fid, label: '要素 ' + fid })),
                value => update('attribute_fid', value))));
            parameters.attribute_fid = source;
            valid &&= fids.length >= 2 && fids.length <= 500 && fids.includes(source);
        }
    } else if (id === 'geometry.repair') {
        const repairInvalid = values.repairInvalid ?? true;
        const deleteNull = values.deleteNull ?? false;
        const ids = selection?.key === key(input) ? selection.ids : [];
        const canCut = polygon(input) && ids.length === 2 && ids.every(Number.isSafeInteger);
        const cut = canCut && values.cutOverlap;
        controls.push(h(Checkbox, { checked: repairInvalid && !cut, disabled: cut,
                onChange: event => update('repairInvalid', event.target.checked) }, '修复无效几何'),
            h(Checkbox, { checked: deleteNull && !cut, disabled: cut,
                onChange: event => update('deleteNull', event.target.checked) }, '删除空要素'),
            h(Checkbox, { checked: Boolean(cut), disabled: !canCut,
                title: '先定位一对重叠面，或在当前图层选择两个面',
                onChange: event => update('cutOverlap', event.target.checked) }, '裁除选中两面的重叠'));
        const keep = values.keepOverlap ?? ids[0];
        if (cut) controls.push(field('保留边界的要素', dropdown(keep,
            ids.map(value => ({ value, label: '要素 ' + value })), value => update('keepOverlap', value))));
        parameters = cut ? { fids: ids.filter(id => id !== keep), preserve_overlap_fid: keep } :
            { repair_invalid: repairInvalid, delete_null: deleteNull };
        valid = cut ? ids.includes(keep) : repairInvalid || deleteNull;
    } else if (id === 'data.merge') {
        const name = values.name ?? '合并图层';
        controls.push(field('结果图层名称', h(Input, { maxLength: 120, value: name,
            onChange: event => update('name', event.target.value) })));
        valid = Boolean(name.trim());
    } else if (id === 'vector.area') {
        controls.push(select('method', '重叠处理', { union: '重叠区域只计一次', sum: '逐个要素累加' }, 'union'),
            select('areaUnit', '面积单位', areaUnits, 'square_meters'));
        parameters = { method: values.method || 'union' };
    } else if (id === 'vector.buffer') {
        const choice = (name, label, defaultValue, labels) => h(TokenPicker, { label,
            selected: values[name] || defaultValue, items: options(labels), onPick: value => update(name, value) });
        controls.push(h('div', { className: 'gis-condition-line' },
            h(Input, { type: 'number', size: 'small', step: 'any', min: 0, placeholder: '距离',
                className: 'gis-tool-distance', 'aria-label': '缓冲距离', value: values.distance ?? '',
                onChange: event => update('distance', event.target.value) }),
            choice('unit', '距离单位', 'meters', { meters: '米', kilometers: '千米', map_units: '图层单位' })),
        h('details', { className: 'gis-tool-options' }, h('summary', null, '高级参数'),
            /LineString|Polygon/.test(input?.geometry) && h('div', { className: 'gis-condition-line' },
                /LineString/.test(input.geometry) &&
                    choice('cap', '线端样式', 'round', { round: '圆头', flat: '平头', square: '方头' }),
                choice('join', '转角样式', 'round', { round: '圆角', mitre: '尖角', bevel: '斜角' })),
            h(Checkbox, { checked: values.dissolve === 'all',
                onChange: event => update('dissolve', event.target.checked ? 'all' : 'keep') }, '合并为一个要素')));
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
        controls.push(columns('fields', id === 'vector.dissolve' ? '分组字段（选填）' : '导出字段（选填）',
            { mode: 'multiple', maxTagCount: 'responsive', placeholder: id === 'vector.dissolve' ? '留空则融合全部要素' : '留空则导出全部字段' }));
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
        controls.push(columns('field', '待删除字段'));
        parameters = { field: values.field };
        valid &&= names.includes(values.field);
    } else if (id === 'vector.spatial_join') {
        controls.push(select('mode', '连接方式', { one_to_one: '一对一', one_to_many: '一对多' }, 'one_to_one'));
        parameters = { mode: values.mode || 'one_to_one' };
    } else if (id === 'crs.assign' || id === 'crs.reproject') {
        const rasterInput = raster(input);
        const target = rasterInput ? 'target_crs' : 'definition';
        if (input) controls.push(h('p', { className: 'gis-tool-description' }, '当前坐标系　' + (input.crsName || input.crs || '未定义')));
        if (id === 'crs.assign') {
            controls.push(coordinates('数据的实际坐标系'));
            parameters = { [target]: values.coordinate?.value };
            valid = Boolean(values.coordinate);
        } else {
            if (input?.crs) controls.push(h(ProjectionChoice, { key: key(input), layer: input,
                value: values.projection, onPick: value => update('projection', value) }));
            if (rasterInput && input.crs) {
                controls.push(select('resampling', '重采样方法',
                    { nearest: '最邻近', bilinear: '双线性', cubic: '三次卷积' }, 'nearest'));
                parameters.resampling = values.resampling || 'nearest';
            }
            const projection = values.projection;
            if (projection) {
                if (projection.plan === 'automatic') parameters[target] = projection.target.value;
                else parameters.plan = projection.plan;
            }
            valid = Boolean(input?.crs && projection);
        }
    }
    return { controls, valid, parameters };
}

const toolDescription = {
    'vector.intersect': '提取两个图层相交的部分。',
    'vector.erase': '保留待擦除图层中位于擦除范围之外的部分。',
    'vector.clip': '保留待裁剪图层中位于裁剪范围内的部分。',
    'vector.union': '保留两个面图层的全部范围，并沿重叠边界拆分。',
    'vector.identity': '保留输入图层的范围，并从标识图层补充重叠部分的属性。',
    'vector.update_overlay': '用更新图层替换重叠部分，并保留两个图层其余的区域。',
    'vector.symmetric_difference': '保留两个面图层各自未重叠的部分。',
    'vector.spatial_join': '按相交关系把属性连接到目标图层。目标图层的形状保持原样。',
    'data.merge': '把多个图层的要素汇集到一个新图层。',
    'geometry.repair': '修复自相交等无效几何，生成修复后的图层。',
    'geometry.check_topology': '检查空几何、无效几何和同一面图层内的重叠。',
    'crs.assign': '设置或覆盖坐标系定义，保留原坐标值和像元。',
    'crs.reproject': '根据源图层匹配可用的内置转换和已保存的自定义方案。',
};

export function toolError(result) {
    if (!['failed', 'interrupted'].includes(result.status)) return '';
    return window.GuizhiErrorText(result.message) + (result.diagnosticId ? '　故障编号 ' + result.diagnosticId : '');
}

function ResultSummary({ result, areaUnit }) {
    const metrics = Object.entries(result.statistics || {}).filter(([name, value]) => stats[name] && !(areaUnit && name === 'area_m2') &&
        (typeof value === 'number' || typeof value === 'boolean'));
    const format = value => typeof value === 'number' ? value.toLocaleString('zh-CN', { maximumFractionDigits: 3 }) : value ? '是' : '否';
    const labels = { ...stats };
    if (areaUnit && typeof result.statistics?.area?.[areaUnit] === 'number') {
        metrics.push(['area', result.statistics.area[areaUnit]]);
        labels.area = '总面积（' + areaUnits[areaUnit] + '）';
    }
    const projection = result.statistics?.projection;
    return h(window.React.Fragment, null,
        projection?.method && h('p', null, projection.method),
        metrics.length > 0 && h('dl', { className: 'gis-result-metrics', role: 'status' },
            metrics.map(([name, value]) => h('div', { key: name }, h('dt', null, labels[name]),
                h('dd', null, name === 'area' ? value.toLocaleString('zh-CN', { maximumSignificantDigits: 9 }) : format(value))))),
        result.statistics?.band_statistics?.map(band => h('p', { key: band.band }, '波段 ' + band.band + ' · 有效像元 ' + band.valid_count +
            ' · 无数据像元 ' + band.nodata_count + ' · 最小 ' + (band.min ?? '—') + ' · 最大 ' + (band.max ?? '—') + ' · 平均 ' + (band.mean ?? '—'))),
        Array.isArray(result.value) ? result.value.length ? h('ul', { className: 'gis-tool-values',
            'aria-label': result.statistics?.invalid_geometry_count === undefined ? '唯一值' : '无效几何示例' },
            result.value.map((value, index) => h('li', { key: index }, value === null ? '空值' : value === '' ? '空字符串' : String(value))))
            : result.statistics?.invalid_geometry_count === undefined && h('p', { className: 'muted' }, '没有找到匹配的值。')
            : !metrics.length && result.value !== undefined && h('output', null, String(result.value)));
}

export function ToolProgress({ busy }) {
    const [progress, setProgress] = useState(null);
    useEffect(() => {
        if (!busy) { setProgress(null); return; }
        return window.guizhi.onMapToolProgress(setProgress);
    }, [busy]);
    if (!busy) return null;
    const measured = progress?.total > 0;
    const label = ({ processing: '正在处理', verifying: '正在校验成果', topology: '正在检查重叠', geometry: '正在修复几何',
        overlay: '正在叠加图层', raster_statistics: '正在统计像元' })[progress?.phase] || '正在准备数据';
    return h('div', { className: 'gis-tool-progress', role: 'status' },
        h('div', null, h('span', null, label), measured && h('span', null,
            progress.completed.toLocaleString('zh-CN') + ' / ' + progress.total.toLocaleString('zh-CN'))),
        h('progress', { 'aria-label': label, ...(measured && { max: progress.total, value: progress.completed }) }));
}

const topologyKinds = { empty: '空几何', invalid: '无效几何', overlap: '面重叠' };
function geometryReason(reason) {
    if (!reason) return '';
    const type = reason.split('[')[0];
    return ({ 'Ring Self-intersection': '环自相交', 'Self-intersection': '自相交',
        'Hole lies outside shell': '内环位于外环之外', 'Nested shells': '外环嵌套', 'Nested holes': '内环嵌套',
        'Too few points in geometry component': '几何顶点过少', 'IllegalArgumentException: Points of LinearRing do not form a closed linestring': '环未闭合' })[type] || '几何结构无效';
}

export function TopologyPanel({ report, close, locate, busy }) {
    const [kind, setKind] = useState('all');
    const [page, setPage] = useState(0);
    const [data, setData] = useState(null);
    const [problem, setProblem] = useState('');
    useEffect(() => {
        let current = true;
        setData(null);
        setProblem('');
        window.guizhi.map({ action: 'topology-issues', run: report.run, kind, page }).then(value => {
            if (current) setData(value);
        }).catch(error => { if (current) setProblem(window.GuizhiErrorText(error)); });
        return () => { current = false; };
    }, [report.run, kind, page]);
    const counts = report.statistics;
    return h('section', { className: 'gis-topology-panel', 'aria-label': '拓扑问题' },
        h('header', null, h('strong', null, '拓扑问题'), h(Button, { size: 'small', type: 'text',
            'aria-label': '关闭拓扑问题', onClick: close }, window.GuizhiShell.icon('close'))),
        h(Select, { 'aria-label': '问题类型', value: kind, onChange: value => { setKind(value); setPage(0); },
            options: [['all', '全部', report.topology.issue_count], ['empty', '空几何', counts.empty_geometry_count],
                ['invalid', '无效几何', counts.invalid_geometry_count], ['overlap', '面重叠', counts.overlap_pair_count]]
                .map(([value, label, count]) => ({ value, label: label + ' · ' + count.toLocaleString('zh-CN') })) }),
        h(ToolProgress, { busy }),
        problem && h('p', { role: 'alert', className: 'error' }, problem),
        h('div', { className: 'gis-topology-list', 'aria-busy': !data && !problem },
            data?.total === 0 && h('p', { className: 'muted' }, '没有此类问题。'),
            ...((data?.rows || []).map(row => h('article', { key: row.id },
                h('strong', null, topologyKinds[row.kind] + ' · ' + row.fid + (row.peer !== null ? '、' + row.peer : '')),
                row.reason && h('span', { className: 'muted', title: row.reason }, geometryReason(row.reason)),
                h('div', null,
                    h(Button, { size: 'small', disabled: busy || row.kind === 'empty', onClick: () => locate(row) }, '定位')))))),
        data?.total > 25 && h('footer', null,
            h(Button, { size: 'small', disabled: !page, onClick: () => setPage(page - 1) }, '上一页'),
            h('span', null, (page + 1) + ' / ' + Math.ceil(data.total / 25)),
            h(Button, { size: 'small', disabled: (page + 1) * 25 >= data.total, onClick: () => setPage(page + 1) }, '下一页')));
}

export function MapTool({ getContainer, operation, active, lockInput, layers, selection, components, openComponents, glyph, busy, setBusy, close, onResult, onParts, editingParts }) {
    const api = window.guizhi;
    const definition = definitions[operation];
    const crsLayer = layers.find(item => key(item) === active && item.geometry && item.crs) ||
        layers.find(item => item.geometry && item.crs);
    const initial = operation === 'geometry.create_dataset' ? { geometry_type: 'polygon', coordinate: crsLayer &&
        { value: crsLayer.crs.startsWith('EPSG:') ? crsLayer.crs : 'layer:' + key(crsLayer),
            label: crsLayer.crsName || crsLayer.crs } }
        : operation === 'data.select_by_location' ? { predicate: 'intersect' } : {};
    const [ids, setIds] = useState(() => definition.inputs[0]?.[1](layers.find(item => key(item) === active)) ? [active] : []);
    const [values, setValues] = useState(initial);
    const [result, setResult] = useState(null);
    const [error, setError] = useState('');
    const update = (name, value) => setValues(old => ({ ...old, [name]: typeof value === 'function' ? value(old[name]) : value }));
    const inputs = ids.map(id => layers.find(item => key(item) === id));
    const pickLayer = (name, index, records) => field(name, dropdown(definition.multiple ? ids : ids[index], layerOptions(records), value => {
        if (definition.multiple) setIds(value);
        else setIds(old => Object.assign([...old], { [index]: value }));
        if (!index) setValues(initial);
        setError('');
    }, { showSearch: true, optionFilterProp: 'label', notFoundContent: records.length ? '没有匹配的图层' : '暂无图层',
        ...(definition.multiple && { mode: 'multiple', maxCount: 32, maxTagCount: 'responsive' }) }));
    const selectors = definition.inputs.map(([name, accepts], index) => {
        if (!index && lockInput) return h('div', { className: 'gis-tool-field' },
            h('span', null, '编辑图层'), h('strong', null, inputs[0]?.name),
            ['geometry.multipart', 'geometry.explode'].includes(operation) &&
                h('span', null, '已选 ' + (selection?.key === active ? selection.ids.length : 0) + ' 个要素'));
        return pickLayer(name, index, layers.filter(item => accepts(item) &&
            (definition.multiple || !ids.some((id, slot) => slot !== index && key(item) === id))));
    });
    const form = toolForm(definition, inputs, values, update, selection);
    const executable = operation.startsWith('crs.') && raster(inputs[0]) ? operation.replace('crs.', 'raster.') : operation;
    const minimum = definition.multiple ? 2 : definition.inputs.length;
    const validInputs = inputs.length >= minimum && Array.from({ length: inputs.length }, (_, i) =>
        (definition.inputs[i] || definition.inputs.at(-1))[1](inputs[i])).every(Boolean);
    const problem = editingParts && ['geometry.multipart', 'geometry.explode'].includes(operation) && form.parameters.destination === 'new'
        ? '请先保存当前编辑，再输出到新图层' : validInputs ? toolInputIssue(operation, inputs, form.parameters) : '';
    const missing = executable.startsWith('raster.') && components?.raster?.installed === false;
    const inherit = operation === 'geometry.create_dataset' && crsLayer && values.coordinate?.value === 'layer:' + key(crsLayer);
    const valid = validInputs && (inherit ? Boolean(values.name?.trim() && values.geometry_type) : form.valid) && !problem && !missing;
    async function run() {
        if (busy || !valid) return;
        setError('');
        setBusy(true);
        try {
            if (operation === 'presentation.export_table') {
                if (await api.mapExport(inputs[0].ref, true, undefined, undefined, form.parameters.fields)) close();
                return;
            }
            if (['geometry.multipart', 'geometry.explode'].includes(operation) && form.parameters.destination === 'current' && onParts) {
                await onParts(operation, form.parameters);
                return;
            }
            const output = await api.map({ action: 'tool', operation: executable,
                refs: inherit ? [crsLayer.ref] : inputs.map(item => item.ref),
                parameters: inherit ? { geometry_type: values.geometry_type } : form.parameters,
                ...(operation === 'geometry.create_dataset' && { name: values.name.trim() }),
                ...(operation === 'data.merge' && { name: (values.name ?? '合并图层').trim() }) });
            const failure = toolError(output);
            if (failure) { setError(failure); return; }
            if (output.layers.length || output.selection || output.topology) await onResult(output, inputs[0], form.parameters.destination);
            if (!output.layers.length) setResult(output);
        } catch (issue) { setError(window.GuizhiErrorText(issue)); }
        finally { setBusy(false); }
    }
    const refine = result && operation === 'data.select_by_attribute' && result.selection?.fids.length > 0;
    const confirm = !result ? run : refine ? () => { setValues({ mode: 'select_subset' }); setResult(null); } : close;
    return h(Modal, { open: true, centered: true, getContainer, width: 480, className: 'gis-dialog', rootClassName: 'gis-modal-root',
        title: h('span', { className: 'gis-dialog-title' }, glyph(definition.icon), definition.title),
        closable: !busy, keyboard: !busy, maskClosable: false, onCancel: () => { if (!busy) close(); }, onOk: confirm,
        okText: refine ? '继续筛选' : result ? '关闭' : definition.action, cancelText: result ? '关闭' : '取消', confirmLoading: busy,
        okButtonProps: { disabled: !result && !valid }, cancelButtonProps: { disabled: busy, className: result && !refine ? 'gis-result-cancel' : undefined } },
        h(ToolProgress, { busy }),
        result ? h('div', { className: 'gis-result-body' }, h(ResultSummary, { result,
            areaUnit: operation === 'vector.area' ? (values.areaUnit || 'square_meters') : undefined }))
            : h('fieldset', { className: 'gis-tool-fields', disabled: busy, 'aria-busy': busy },
            toolDescription[operation] && h('p', { className: 'gis-tool-description' }, toolDescription[operation]),
            definition.multiple && h('p', { className: 'gis-tool-description' }, '选择两个或更多图层。'),
            ...[...selectors, ...form.controls].map((control, index) => h(window.React.Fragment, { key: index }, control)),
            missing && h('div', { className: 'gis-component-message' }, '请先安装栅格处理模块',
                h(Button, { size: 'small', onClick: openComponents }, '打开模块设置')),
            (error || problem) && h('p', { className: 'error', role: 'alert' }, error || problem)));
}

function ResultLayers({ layers }) {
    const rows = layers.map(item => {
        const system = item.crs?.startsWith('EPSG:') ? item.crs : item.crsName || (item.crs && '自定义坐标系');
        const details = [geometryName(item.geometry), system].filter(Boolean).join(' · ');
        return h('li', { key: key(item) }, h('strong', null, item.name), details && h('small', null, details));
    });
    return h('ul', { className: 'gis-result-layers', 'aria-label': '成果图层' }, rows);
}

export function MapResultDialog({ getContainer, result, close, add, replace }) {
    const [preview, setPreview] = useState(false);
    const layer = result.layers.length === 1 ? result.layers[0] : null;
    return h(Modal, { open: true, centered: true, getContainer, width: 540, className: 'gis-dialog', rootClassName: 'gis-modal-root',
        title: '将成果加入地图？', onCancel: close, maskClosable: false,
        footer: [h(Button, { key: 'decline', onClick: close }, '不加入'), replace && h(Button, { key: 'replace', onClick: replace }, '替换原图层'),
            h(Button, { key: 'add', type: 'primary', onClick: add }, '加入地图')] },
        h('div', { className: 'gis-result-body' },
            h(ResultLayers, { layers: result.layers }),
            h(ResultSummary, { result }),
            layer?.geometry && h('details', { className: 'gis-result-details', onToggle: event => setPreview(event.currentTarget.open) },
                h('summary', null, '预览属性'),
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
    close, Preview, onAddLayers }) {
    const api = window.guizhi;
    const [values, setValues] = useState({});
    const [materials, setMaterials] = useState({});
    const [result, setResult] = useState(null);
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
        okText: offer ? '加入地图' : result ? '关闭' : '开始办理',
        cancelText: offer ? '不加入' : '取消', confirmLoading: busy, okButtonProps: { disabled: !result && !valid },
        cancelButtonProps: { disabled: busy, className: result && !offer ? 'gis-result-cancel' : undefined } },
        h('div', { className: 'map-field-editor', 'aria-busy': busy },
            h(ToolProgress, { busy }),
            missing.length > 0 && h('div', { className: 'gis-component-message', role: 'status' },
                '请先安装' + missing.map(kind => window.GuizhiComponentNames[kind]).join('、'),
                h(Button, { size: 'small', onClick: openComponents }, '打开模块设置')),
            result ? h(window.React.Fragment, null,
                metrics.length > 0 && h('dl', { className: 'gis-result-metrics', role: 'status' },
                    ...metrics.map(([name, value]) => h('div', { key: name }, h('dt', null, stats[name]), h('dd', null, metric(name, value))))),
                offer && h('section', { className: 'gis-result-section' },
                    h('strong', null, '将成果加入地图？'), h(ResultLayers, { layers: pendingLayers })),
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
