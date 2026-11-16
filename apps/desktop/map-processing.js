import { attributeFilter, fieldCalculation, geometryCalculation, geometryProperties } from './map-attribute-tools.js';

const h = window.React.createElement;
const { useState } = window.React;
const { Button, Input, Modal, Select } = window.antd;
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
const selectAttr = tool('data.select_by_attribute', '按属性选择', 'select', vector);
const selectLocation = tool('data.select_by_location', '按位置选择', 'target', vector, vector);
const tools = [
    { title: '统计', icon: 'analysis', tools: [area, count] },
    { title: '矢量处理', icon: 'layers', tools: [
        tool('vector.buffer', '缓冲分析', 'buffer', vector),
        tool('vector.dissolve', '融合', 'layers', vector),
    ] },
    { title: '叠加分析', icon: 'clip', tools: [
        tool('vector.intersect', '相交', 'clip', vector, vector),
        tool('vector.erase', '擦除', 'clear', vector, polygon),
        tool('vector.clip', '裁剪', 'clip', vector, polygon),
        tool('vector.union', '联合', 'layers', polygon, polygon),
        tool('vector.identity', '标识叠加', 'clip', polygon, polygon),
        tool('vector.update_overlay', '更新叠加', 'layers', polygon, polygon),
        tool('vector.symmetric_difference', '对称差集', 'clip', polygon, polygon),
        tool('vector.spatial_join', '空间连接', 'target', vector, vector),
    ] },
    { title: '栅格处理', icon: 'map', tools: [
        tool('raster.statistics', '栅格与波段统计', 'analysis', raster),
        tool('raster.clip', '裁切栅格', 'clip', raster, polygon),
        tool('raster.reproject', '栅格重投影', 'globe', raster),
        { ...tool('raster.mosaic', '拼接栅格', 'layers', raster), multiple: true },
    ] },
];
export const TOOL_GROUPS = tools;
const definitions = Object.fromEntries([...tools.flatMap(group => group.tools), selectAttr, selectLocation,
    tool('data.copy', '复制图层', 'layers', vector),
    { ...tool('data.merge', '合并图层', 'layers', vector), multiple: true },
    tool('data.export_selection', '选中要素另存为图层', 'select', vector),
    tool('geometry.create_dataset', '新建图层', 'add'),
    tool('geometry.repair', '修复几何', 'pencil', vector),
    tool('table.add_field', '添加字段', 'add', vector),
    tool('table.delete_field', '删除字段', 'trash', vector),
    tool('table.calculate_field', '字段计算器', 'analysis', vector),
    tool('table.calculate_geometry', '计算几何', 'area', item => Object.keys(geometryProperties(item)).length > 0),
    tool('presentation.export_table', '导出属性表', 'table', vector),
].map(item => [item.id, item]));
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
const toolExamples = {
    'vector.buffer': ['buffer', '按指定距离向外扩展，生成缓冲范围。'],
    'vector.dissolve': ['union', '合并同一组范围，移除组内边界。'],
    'vector.intersect': ['intersection', '保留两个图层共有的部分，并合并相交部分的属性。'],
    'vector.erase': ['erase', '从输入图层减去叠加图层覆盖的部分，保留未覆盖部分。'],
    'vector.clip': ['intersection', '只保留裁切范围内的输入要素，输出沿用输入图层的属性。'],
    'vector.union': ['union', '保留两层的全部覆盖范围，按边界切分并保留两边属性。'],
    'vector.identity': ['overlap', '保留输入范围，用叠加边界切分并补充属性。'],
    'vector.symmetric_difference': ['difference', '保留只被其中一个图层覆盖的部分。'],
    'vector.update_overlay': ['union', '用叠加图层覆盖同位置的输入要素，保留其余部分。'],
    'vector.spatial_join': ['overlap', '用位置关系匹配要素，将匹配图层的属性带到输入图层。'],
    'raster.clip': ['intersection', '只保留裁切边界内的像元。'],
};
const namedResult = id => /^(vector|raster|data)\./.test(id) && ![
    'vector.area', 'raster.statistics', 'data.describe', 'data.select_by_attribute', 'data.select_by_location',
].includes(id);
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


function toolForm(definition, layers, refs, setRefs, values, update, picked) {
    const { id, accepts, multiple } = definition;
    const input = layers.find(item => key(item) === refs[0]);
    const names = input?.fields?.map(item => item.name) || [];
    const fields = names.map(name => ({ value: name, label: name }));
    const select = (name, label, choices, extra = {}) => field(label, dropdown(values[name], options(choices),
        value => update(name, value), extra));
    const text = (name, label, placeholder) => field(label, h(Input, {
        value: values[name] ?? '', placeholder, onChange: event => update(name, event.target.value),
    }));
    const columns = (name, label, extra = {}) => field(label, dropdown(values[name], fields, value => update(name, value), extra));
    const layerSelect = (index, title, predicate, isMultiple = false) => field(title, dropdown(
        isMultiple ? refs.slice(index).filter(Boolean) : refs[index],
        layerOptions(layers.filter(item => predicate(item) && (index === 0 || key(item) !== refs[0]))),
        value => setRefs(isMultiple ? [...refs.slice(0, index), ...value] : Object.assign([...refs], { [index]: value }),
            index === 0), { showSearch: true, optionFilterProp: 'label', ...(isMultiple ? { mode: 'multiple' } : {}) }));
    const location = id === 'data.select_by_location';
    const controls = accepts.map((predicate, index) => layerSelect(index,
        location ? (index === 0 ? '目标图层' : '参照图层') : index === 0 ? '输入图层' : id.endsWith('.clip') ? '裁切范围' : '叠加图层',
        predicate, multiple && index > 0));
    if (multiple && accepts.length === 1) controls.push(layerSelect(1, '其他图层', accepts[0], true));
    if (namedResult(id)) controls.push(text('resultName', '输出图层名（选填）', definition.title));
    let parameters = {};
    let valid = refs.length >= accepts.length && (!multiple || refs.length > 1) && refs.every((ref, index) =>
        (accepts[index] || accepts.at(-1))?.(layers.find(item => key(item) === ref)));
    const attributeTools = { 'data.select_by_attribute': attributeFilter, 'table.calculate_field': fieldCalculation,
        'table.calculate_geometry': geometryCalculation };
    if (attributeTools[id]) {
        const form = attributeTools[id](input, values, update, { field, select, text, columns });
        return { controls: [...controls, ...form.parts], valid: valid && form.valid, parameters: form.parameters };
    }
    if (id === 'geometry.create_dataset') {
        controls.push(text('name', '图层名称'), select('geometry_type', '要素类型', { point: '点', polyline: '线', polygon: '面' }),
            numeric('EPSG', values.epsg, value => update('epsg', value), 1));
        parameters = { geometry_type: values.geometry_type, epsg: number(values.epsg) };
        valid = Boolean(values.name?.trim() && values.geometry_type && Number.isInteger(parameters.epsg) && parameters.epsg > 0);
    } else if (id === 'vector.buffer') {
        controls.push(numeric('缓冲距离', values.distance, value => update('distance', value), 0),
            select('unit', '距离单位', { meters: '米', kilometers: '千米', map_units: '图层单位' }),
            select('cap', '端点', { round: '圆头', flat: '平头', square: '方头' }),
            select('join', '拐角', { round: '圆角', mitre: '尖角', bevel: '斜角' }),
            select('dissolve', '重叠缓冲区', { keep: '保留各要素', all: '融合全部' }));
        parameters = { distance: number(values.distance), unit: values.unit || 'meters', cap: values.cap || 'round',
            join: values.join || 'round', dissolve: values.dissolve === 'all' };
        valid &&= Number.isFinite(parameters.distance) && parameters.distance > 0;
    } else if (id === 'data.select_by_location') {
        controls.push(select('predicate', '空间关系', { intersect: '相交', contain: '包含', within: '位于范围内', touch: '接触',
            overlap: '重叠', cross: '穿越', within_a_distance: '距离范围内' }));
        controls.push(h(SpatialExample, { predicate: values.predicate }));
        parameters = { predicate: values.predicate };
        valid &&= Boolean(values.predicate);
        if (values.predicate === 'within_a_distance') {
            controls.push(numeric('最大距离（米）', values.distance, value => update('distance', value), 0));
            parameters.distance = number(values.distance);
            valid &&= Number.isFinite(parameters.distance) && parameters.distance > 0;
        }
    } else if (id === 'data.export_selection') {
        parameters = { fids: picked };
        valid &&= key(input) === values.selectedLayer && picked.length > 0 && picked.length <= 500;
    } else if (id === 'vector.dissolve' || id === 'presentation.export_table') {
        controls.push(columns('fields', '字段（选填）', { mode: 'multiple' }));
        if (values.fields?.length) parameters = { fields: values.fields };
    } else if (id === 'table.add_field') {
        controls.push(text('name', '字段名称'), select('type', '字段类型', { string: '文本', integer: '整数',
            double: '小数', date: '日期时间' }));
        parameters = { field: { name: values.name, type: values.type } };
        valid &&= Boolean(values.name?.trim() && values.type);
    } else if (id === 'table.delete_field') {
        controls.push(columns('field', '删除字段'));
        parameters = { field: values.field };
        valid &&= names.includes(values.field);
    } else if (id === 'vector.spatial_join') {
        controls.push(select('mode', '连接方式', { one_to_one: '一对一', one_to_many: '一对多' }));
        parameters = { mode: values.mode || 'one_to_one' };
    } else if (id === 'raster.reproject') {
        controls.push(text('target_crs', '目标坐标系', '例如 EPSG:4548'), select('resampling', '重采样',
            { nearest: '最邻近', bilinear: '双线性', cubic: '三次卷积' }));
        parameters = { target_crs: values.target_crs?.trim(), resampling: values.resampling };
        valid &&= Boolean(parameters.target_crs && parameters.resampling);
    }
    return { controls, valid, parameters };
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

export function MapProcessing({ tool: id, skill, layers, active, picked, entries, components, openComponents, glyph, busy, setBusy,
    close, Preview, onResult, initialResult }) {
    const api = window.guizhi;
    const definition = skill ? undefined : definitions[id];
    const activeLayer = layers.find(item => key(item) === active);
    const [refs, setRefs] = useState(() => definition?.accepts[0]?.(activeLayer) ? [active] : []);
    const [values, setValues] = useState(() => ({ selectedLayer: active, geometry_type: 'polygon', op: 'eq',
        predicate: 'intersect', mode: 'one_to_one', resampling: 'nearest', epsg: activeLayer?.crs?.match(/^EPSG:(\d+)$/)?.[1] }));
    const [materials, setMaterials] = useState({});
    const [result, setResult] = useState(initialResult || null);
    const [error, setError] = useState('');
    const [pendingMaterial, setPendingMaterial] = useState('');
    const update = (name, value) => setValues(old => ({ ...old, [name]: value }));
    const fail = issue => setError(window.GuizhiErrorText(issue));
    const title = skill?.title || definition.title;
    const submit = ({ 'geometry.create_dataset': '确定', 'table.add_field': '确定', 'table.delete_field': '删除',
        'table.calculate_field': '计算', 'table.calculate_geometry': '计算',
        'data.select_by_attribute': '确定', 'data.select_by_location': '确定' })[id] || '运行';
    const missing = (skill?.components || (id?.startsWith('raster.') ? ['raster'] : []))
        .filter(kind => components?.[kind]?.installed === false);
    const roles = skill?.materials.filter(role => !role.resource) || [];
    const { RoleMaterials, roleReady } = window.GuizhiMaterials;
    const picker = roleLayerPicker(layers, busy);
    const form = definition ? toolForm(definition, layers, refs, (next, inputChanged) => {
        setRefs(inputChanged ? [next[0], ...next.slice(1).filter(ref => ref !== next[0])] : next);
        if (inputChanged) setValues(old => ({ ...old, field: undefined, fields: undefined, source_field: undefined,
            property: undefined, unit: undefined, expressionField: undefined, filters: undefined, value: undefined }));
    }, values, update, picked) : null;
    const parameters = skill ? skillValues(skill, values) : form.parameters;
    const valid = !missing.length && !pendingMaterial && (skill ? parameters && roles.every(role => roleReady(role, materials[role.id])) : form.valid);
    async function run() {
        if (busy || !valid) return;
        setError('');
        setBusy(true);
        try {
            const output = skill ? await api.mapSkill({ skill: window.GuizhiTaskCommands.reference(skill), materials, parameters,
                coordinatePlans: Object.fromEntries(['project', 'control'].filter(role => materials[role]?.[0]?.plan)
                    .map(role => [role + '_plan', materials[role][0].plan])) })
                : await api.map({ action: 'tool', operation: id, refs: refs.map(ref => layers.find(item => key(item) === ref).ref),
                    parameters, name: id === 'geometry.create_dataset' ? values.name.trim() : values.resultName?.trim() || undefined });
            onResult({ ...output, input: refs[0] });
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
    return h(Modal, { open: true, centered: true, width: skill || result?.map ? 640 : 460, className: 'gis-dialog',
        rootClassName: 'gis-modal-root', closable: !busy, keyboard: !busy, maskClosable: false,
        title: h('span', { className: 'gis-dialog-title' }, glyph(definition?.icon || 'analysis'), title),
        onCancel: dismiss, onOk: result ? dismiss : run, okText: result ? '完成' : submit,
        cancelText: '取消', confirmLoading: busy, okButtonProps: { disabled: !result && !valid },
        cancelButtonProps: { disabled: busy, className: result ? 'gis-result-cancel' : undefined } },
        h('div', { className: 'map-field-editor', 'aria-busy': busy },
            missing.length > 0 && h('div', { className: 'gis-component-message', role: 'status' },
                '请先安装' + missing.map(kind => window.GuizhiComponentNames[kind]).join('、'),
                h(Button, { size: 'small', onClick: openComponents }, '打开组件设置')),
            result ? h(window.React.Fragment, null,
                metrics.length > 0 && h('dl', { className: 'gis-result-metrics', role: 'status' },
                    ...metrics.map(([name, value]) => h('div', { key: name }, h('dt', null, stats[name]), h('dd', null, metric(name, value))))),
                result.layers.length > 0 && h('p', { role: 'status', className: 'gis-added-layers' },
                    id?.startsWith('table.') ? '已更新属性表' : '已加入地图 ' + result.layers.length + ' 个图层'),
                result.map && h(Preview, { map: result.map }),
                result.business?.reportDigest && h(window.GuizhiBusinessLog.ReportPanel, { id: result.business.id })) : skill ? h(window.React.Fragment, null,
                skillHints[skill.id] && h('details', { className: 'gis-tool-guide' },
                    h('summary', null, '适用条件'), skill.id === 'control-check'
                        ? h('div', { className: 'gis-tool-help' }, h('div', { className: 'gis-spatial-example overlap', 'aria-hidden': true },
                            h('span'), h('span')), h('span', null, skillHints[skill.id]))
                        : h('p', null, skillHints[skill.id])),
                h(RoleMaterials, { roles, materials, change: setMaterials, plans: entries.filter(item => item.kind === 'plan'),
                    fail, disabled: busy || missing.length > 0, picker, onPending: setPendingMaterial }),
                pendingMaterial && h('p', { role: 'status', className: 'muted' }, '正在读取材料…'),
                Object.keys(skill.parameters).length > 0 && h('div', { className: 'gis-skill-parameters' }, h(SkillParameters, { skill, materials, layers, entries, values,
                    setValues, disabled: busy || missing.length > 0 })))
                : h('fieldset', { className: 'gis-tool-fields', disabled: busy },
                    toolExamples[id] && h('div', { className: 'gis-tool-help' },
                        h('div', { className: 'gis-spatial-example ' + toolExamples[id][0], 'aria-hidden': true }, h('span'), h('span')),
                        h('span', null, toolExamples[id][1])),
                    form.controls.map((control, index) => h(window.React.Fragment, { key: index }, control))),
            error && h('p', { className: 'error', role: 'alert' }, error)));
}
