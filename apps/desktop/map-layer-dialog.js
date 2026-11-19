import { presetPrimaryColors } from '@ant-design/colors';

const h = window.React.createElement;
const { useState } = window.React;
const { Button, Checkbox, ColorPicker, InputNumber, Modal, Segmented, Select } = window.antd;
const hex = /^#[0-9a-f]{6}$/i;
const geometryNames = { Point: '点', LineString: '线', Polygon: '面' };
export const geometryName = type => geometryNames[type?.split(' ')[0].replace(/^Multi/, '')];
export const opacity = (value, defaultValue = 100) => Number.isFinite(value)
    ? Math.round(Math.min(100, Math.max(0, value))) : defaultValue;
export function symbol(value, color) {
    return { color: hex.test(value?.color) ? value.color : color,
        fill: value?.fill === null ? null : hex.test(value?.fill) ? value.fill : color,
        fillOpacity: opacity(value?.fillOpacity),
        opacity: opacity(value?.opacity),
        noOutline: value?.noOutline === true,
        shape: ['circle', 'square', 'diamond', 'cross'].includes(value?.shape) ? value.shape : 'circle',
        pattern: ['solid', 'dash', 'dot'].includes(value?.pattern) ? value.pattern : 'solid',
        width: Math.min(8, Math.max(.5, Number(value?.width) || 1.6)),
        size: Math.min(28, Math.max(4, Number(value?.size) || 8)) };
}

const classColors = ['blue', 'orange', 'green', 'purple', 'red', 'cyan', 'gold', 'magenta', 'geekblue', 'lime', 'volcano', 'yellow']
    .map(name => presetPrimaryColors[name]);
export const defaultMapBackground = '#ffffff';
export function categoryValue(value) {
    return value === undefined || value === null ? null : String(value);
}
export function layerSymbol(value, color) {
    const base = symbol(value, color);
    return { ...base, ...(value?.classification && { classification: value.classification }),
        ...(value?.labelField && { labelField: value.labelField }),
        ...(value?.outlines && { outlines: true }),
        ...(value?.rasterBands && { rasterBands: value.rasterBands }) };
}
const commonColors = [
    ['白色', '#ffffff'], ['浅灰', '#d9d9d9'], ['灰色', '#808080'], ['深灰', '#434343'], ['黑色', '#000000'],
    ['红色', '#d4380d'], ['橙色', '#fa8c16'], ['金色', '#faad14'], ['黄色', '#fadb14'], ['棕色', '#bf6f2b'],
    ['黄绿', '#73d13d'], ['绿色', '#389e0d'], ['深绿', '#135200'], ['青绿', '#17a489'], ['青色', '#13c2c2'],
    ['天蓝', '#69b1ff'], ['蓝色', '#1785db'], ['深蓝', '#003a8c'], ['紫色', '#8263ca'], ['玫红', '#cc5785'],
];
function ColorInput({ label, value, change, compact = false, disabled = false }) {
    const [open, setOpen] = useState(false);
    const [custom, setCustom] = useState(false);
    function toggle(next) {
        setOpen(next);
        if (!next) setCustom(false);
    }
    return h(ColorPicker, { value, open, disabled, disabledAlpha: true, defaultFormat: 'hex', onOpenChange: toggle,
        onChange: next => change(next.toHexString()),
        panelRender: panel => custom ? panel : h('div', { className: 'gis-color-palette' },
            h('div', { className: 'gis-color-swatches', role: 'group', 'aria-label': '常用颜色' },
                ...commonColors.map(([name, color]) => h('button', { key: color, type: 'button', 'aria-label': name,
                    'aria-pressed': color.toLowerCase() === value.toLowerCase(), title: name, style: { backgroundColor: color },
                    onClick: () => { change(color); toggle(false); } }))),
            h(Button, { type: 'text', size: 'small', onClick: () => setCustom(true) }, '自定义颜色…')) },
        h(Button, { className: 'gis-color-input', size: compact ? 'small' : 'middle', disabled,
            'aria-label': label, title: label },
            h('span', { className: 'gis-color-chip', style: { backgroundColor: value } }),
            !compact && h('span', { 'aria-hidden': true }, '▾')));
}

export async function classifyLayer(item, field, existing = {}) {
    const { classes, remaining } = await window.guizhi.map({ action: 'categories', ref: item.ref, field });
    return { field, other: existing.other || '#798696', otherFill: existing.otherFill || '#798696', remaining,
        classes: classes.map((entry, index) => {
            const color = classColors[index % classColors.length];
            return { ...entry, color, ...(/Polygon$/.test(item.geometry) && { fillColor: color }) };
        }) };
}

function Classification({ item, style, update, polygon, points, setLoading }) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const value = style.classification;
    async function field(name) {
        setError('');
        update('classification', { ...value, field: name, classes: [], remaining: 0 });
        setBusy(true);
        setLoading(true);
        try {
            update('classification', await classifyLayer(item, name, value));
        } catch (issue) { setError(window.GuizhiErrorText(issue)); }
        finally { setBusy(false); setLoading(false); }
    }
    const colorLabel = points ? '点颜色' : polygon ? '边线颜色' : '线颜色';
    const fillEnabled = style.fill !== null;
    const paint = (label, color, change, disabled) => h('td', null,
        h(ColorInput, { label, value: color, change, compact: true, disabled }));
    function row(entry, index) {
        const name = entry.label || (entry.value ?? '空值');
        const other = index === undefined;
        const setColor = (property, next) => {
            if (other) update('classification', { ...value, [property === 'color' ? 'other' : 'otherFill']: next });
            else update('classification', { ...value, classes: value.classes.map((item, i) =>
                i === index ? { ...item, [property]: next } : item) });
        };
        return h('tr', { key: other ? 'other' : JSON.stringify(entry.value) },
            h('td', null, name),
            polygon && paint(name + ' 填充颜色', entry.fillColor ?? entry.color, next => setColor('fillColor', next), !fillEnabled),
            paint(name + ' ' + colorLabel, entry.color, next => setColor('color', next), polygon && style.noOutline),
            h('td', null, entry.count ?? '—'));
    }
    return h('section', { className: 'gis-classification' },
        h('label', { className: 'gis-tool-field' }, h('span', null, '分类字段'),
            h(Select, { 'aria-label': '分类字段', showSearch: true, loading: busy, disabled: busy,
                value: value.field || undefined, placeholder: '选择字段', options: item.fields.map(entry => ({ value: entry.name })),
                onChange: field })),
        busy && h('p', { role: 'status' }, '正在读取分类值…'),
        error && h('p', { role: 'alert', className: 'error' }, error),
        value.classes.length > 0 && h('div', { className: 'gis-class-list' },
            h('table', { className: 'gis-classes' },
                h('thead', null, h('tr', null, h('th', { scope: 'col' }, '分类值'),
                    polygon && h('th', { scope: 'col' }, '填充颜色'), h('th', { scope: 'col' }, colorLabel),
                    h('th', { scope: 'col' }, '要素数'))),
                h('tbody', null, ...value.classes.map(row),
                    row({ label: value.remaining ? '其他值（' + value.remaining + ' 类）' : '其他值',
                        color: value.other, fillColor: value.otherFill })))));
}

export function LayerProperties({ item, color, close, save, glyph, start = 'info' }) {
    const [tab, setTab] = useState(start);
    const [style, setStyle] = useState(() => layerSymbol({ ...item.style, opacity: item.style?.opacity ?? item.opacity }, color));
    const [fill, setFill] = useState(() => symbol(item.style, color).fill || color);
    const [classificationBusy, setClassificationBusy] = useState(false);
    const image = item.kind === 'raster';
    const points = /Point/.test(item.geometry || '');
    const polygon = /Polygon/.test(item.geometry || '');
    const update = (name, value) => setStyle(current => ({ ...current, [name]: value }));
    const prop = (label, value) => h('div', { key: label }, h('dt', null, label), h('dd', null, value));
    const field = (label, control) => h('label', { key: label, className: 'gis-tool-field' }, h('span', null, label), control);
    const styleColor = (name, label, value, disabled = false) => field(label,
        h(ColorInput, { label, value, disabled, change: next => {
            if (name === 'fill') setFill(next);
            update(name, next);
        } }));
    const choices = (name, label, values, disabled = false) => field(label, h(Select, { 'aria-label': label, value: style[name], disabled,
        options: Object.entries(values).map(([value, text]) => ({ value, label: text })), onChange: value => update(name, value) }));
    const measure = (name, label, min, max, step, disabled = false) => field(label, h(InputNumber, { 'aria-label': label, disabled,
        min, max, step, value: style[name], onChange: value => { if (value !== null) update(name, value); } }));
    const classified = Boolean(style.classification);
    const noFill = style.fill === null;
    const lineOptions = { solid: '实线', dash: '虚线', dot: '点线' };
    const bounds = item.bounds;
    const format = value => value.toLocaleString('zh-CN', { maximumFractionDigits: 6, useGrouping: false });
    const info = [
        ['数据源', item.sourceName || '工作区成果'], ...(item.group ? [['图层组', item.group]] : []),
        ['数据类型', image ? '栅格影像' : geometryName(item.geometry) || '矢量要素'],
        ['坐标系', item.crsName || '未定义坐标系'],
        ...(/^EPSG:/.test(item.crs || '') ? [['坐标系代码', item.crs]] : []),
        ...(image ? [['像元尺寸', item.width + ' × ' + item.height], ['波段数', item.bands]]
            : [['要素数', item.count?.toLocaleString('zh-CN')]]),
        ['范围', bounds ? h('span', { className: 'gis-layer-extent' },
            'X ' + format(bounds[0]) + ' 至 ' + format(bounds[2]),
            h('br'), 'Y ' + format(bounds[1]) + ' 至 ' + format(bounds[3])) : '无有效范围'],
    ];
    const formatTab = tab === 'style';
    const rasterBands = style.rasterBands || item.displayBands || (item.bands >= 3 ? [1, 2, 3] : [1]);
    const bandChoices = Array.from({ length: image ? item.bands : 0 }, (_, index) => {
        const number = index + 1;
        const name = item.bandNames?.[index];
        return { value: number, label: '波段 ' + number + (name ? ' · ' + name : '') };
    });
    return h(Modal, { open: true, centered: true, width: 520, className: 'gis-dialog', rootClassName: 'gis-modal-root',
        title: h('span', { className: 'gis-dialog-title' }, glyph('layers'), item.name),
        onCancel: close, footer: tab !== 'info' ? [h(Button, { key: 'cancel', onClick: close }, '取消'),
            h(Button, { key: 'save', type: 'primary', disabled: classificationBusy ||
                (style.classification && !style.classification.classes.length),
                onClick: () => { save(layerSymbol(style, color)); close(); } }, '应用')]
            : h(Button, { onClick: close }, '关闭') },
        h(Segmented, { className: 'gis-layer-tabs', value: tab, onChange: setTab, disabled: classificationBusy,
            options: [{ value: 'info', label: '图层属性' }, { value: 'style', label: image ? '显示' : '符号化' },
                ...(!image ? [{ value: 'labels', label: '标注' }] : [])] }),
        formatTab ? h('div', { className: 'gis-symbol-editor' },
            measure('opacity', '图层不透明度（%）', 0, 100, 1),
            image && h('fieldset', { className: 'gis-symbol-group' },
                h('legend', null, '波段设置'),
                field('显示方式', h(Select, { 'aria-label': '显示方式', value: rasterBands.length,
                    options: [{ value: 1, label: '单波段灰度' }, ...(item.bands > 1 ? [{ value: 3, label: 'RGB 合成' }] : [])],
                    onChange: count => update('rasterBands', count === 1 ? [rasterBands[0]]
                        : (item.displayBands?.length === 3 ? item.displayBands : [1, 2, Math.min(item.bands, 3)])) })),
                ...(rasterBands.length === 3 ? ['红色通道', '绿色通道', '蓝色通道'] : ['灰度波段']).map((label, index) =>
                    field(label, h(Select, { 'aria-label': label, options: bandChoices, value: rasterBands[index],
                        onChange: band => update('rasterBands', rasterBands.map((current, i) => i === index ? band : current)) })))),
            !image && item.fields?.length > 0 && h(Segmented, { value: classified ? 'unique' : 'single',
                'aria-label': '符号类型', disabled: classificationBusy, options: [{ value: 'single', label: '单一符号' },
                    { value: 'unique', label: '唯一值分类' }],
                onChange: mode => update('classification', mode === 'unique'
                    ? { field: '', classes: [], other: '#7d8999', otherFill: '#7d8999' } : undefined) }),
            classified && h(Classification, { item, style, update, polygon, points, setLoading: setClassificationBusy }),
            !image && h('div', { className: polygon ? 'gis-polygon-symbol' : undefined },
            polygon && h('fieldset', { className: 'gis-symbol-group' },
                h('legend', null, '填充'),
                h(Checkbox, { checked: noFill, onChange: event => update('fill', event.target.checked ? null : fill) }, '无填充'),
                h('div', { className: 'gis-skill-parameters' },
                    !classified && styleColor('fill', '填充颜色', fill, noFill),
                    measure('fillOpacity', '填充不透明度（%）', 0, 100, 1, noFill))),
            h('fieldset', { className: 'gis-symbol-group' },
                h('legend', null, points ? '点符号' : polygon ? '边线' : '线符号'),
                polygon && h(Checkbox, { checked: style.noOutline, onChange: event => update('noOutline', event.target.checked) }, '无边线'),
                h('div', { className: 'gis-skill-parameters' },
                    !classified && styleColor('color', points ? '点颜色' : polygon ? '边线颜色' : '线颜色', style.color, style.noOutline),
                    points ? choices('shape', '形状', { circle: '圆形', square: '方形', diamond: '菱形', cross: '十字' })
                        : choices('pattern', '线型', lineOptions, style.noOutline),
                    points ? measure('size', '大小', 4, 28, 1) : measure('width', '线宽', .5, 8, .5, style.noOutline)))))
            : tab === 'labels' ? h('div', { className: 'gis-symbol-editor' },
                field('标注字段', h(Select, { 'aria-label': '标注字段', showSearch: true, value: style.labelField || '',
                    options: [{ value: '', label: '不显示标注' },
                        ...item.fields.map(entry => ({ value: entry.name, label: entry.name }))],
                    onChange: field => update('labelField', field) })))
                : h('dl', { className: 'gis-layer-properties' }, ...info.map(([label, value]) => prop(label, value))));
}

export function MapProperties({ background, close, save, glyph, coordinates }) {
    const [color, setColor] = useState(background);
    return h(Modal, { open: true, centered: true, width: 480, className: 'gis-dialog', rootClassName: 'gis-modal-root',
        title: h('span', { className: 'gis-dialog-title' }, glyph('map'), '地图属性'), onCancel: close,
        footer: [h(Button, { key: 'cancel', onClick: close }, '取消'),
            h(Button, { key: 'save', type: 'primary', onClick: () => { save(color); close(); } }, '应用')] },
        h('div', { className: 'gis-map-background' },
            h('span', null, '背景颜色'),
            h(ColorInput, { label: '背景颜色', value: color, change: setColor }),
            h(Button, { disabled: color.toLowerCase() === defaultMapBackground,
                onClick: () => setColor(defaultMapBackground) }, '恢复白色')),
        h('section', { className: 'map-properties-coordinates' }, h('h3', null, '坐标系'), coordinates));
}

export function MxdOpen({ document, busy, open, close, glyph, issue, locateAvailable }) {
    const [frame, setFrame] = useState(0);
    const current = document.frames[frame];
    const canLocate = current?.missing > 0 || locateAvailable === frame;
    return h(Modal, { open: true, centered: true, width: 440, className: 'gis-dialog', rootClassName: 'gis-modal-root',
        title: h('span', { className: 'gis-dialog-title' }, glyph('map'), document.name),
        onCancel: busy ? undefined : close, closable: !busy, keyboard: !busy, maskClosable: false,
        footer: [h(Button, { key: 'close', disabled: busy, onClick: close }, '取消'),
            canLocate && h(Button, { key: 'locate', disabled: busy, onClick: () => open(frame, true) }, '找数据'),
            h(Button, { key: 'open', type: 'primary', loading: busy, disabled: !current, onClick: () => open(frame, false) }, '打开')] },
        h('div', { className: 'mxd-open-fields' },
            document.frames.length > 1 && h('label', null, '数据框', h(Select, { 'aria-label': '数据框', value: frame, disabled: busy,
                options: document.frames.map((item, value) => ({ value, label: item.name + ' · ' + item.count + ' 个图层' })), onChange: setFrame })),
            canLocate && h('p', { className: 'muted' }, '部分图层找不到数据。点「找数据」选择原始数据所在的文件夹。'),
            current?.unsupported > 0 && h('p', { className: 'muted' }, current.unsupported + ' 个图层的类型暂不支持。'),
            issue && h('p', { className: 'error', role: 'alert' }, issue)));
}
