const h = window.React.createElement;
const { useRef, useState } = window.React;
const { Button, Modal, Select, InputNumber } = window.antd;
const hex = /^#[0-9a-f]{6}$/i;
export function symbol(value, color) {
    return { color: hex.test(value?.color) ? value.color : color,
        fill: value?.fill === null ? null : hex.test(value?.fill) ? value.fill : color,
        shape: ['circle', 'square', 'diamond', 'cross'].includes(value?.shape) ? value.shape : 'circle',
        pattern: ['solid', 'dash', 'dot'].includes(value?.pattern) ? value.pattern : 'solid',
        width: Math.min(8, Math.max(.5, Number(value?.width) || 1.6)),
        size: Math.min(28, Math.max(4, Number(value?.size) || 8)) };
}

const palettes = {
    planning: ['#258ac2', '#d59231', '#49a879', '#9468c7', '#dc705c', '#46aaa9', '#b95582', '#8887be'],
    terrain: ['#7e9258', '#dcaa57', '#4d8d97', '#a17262', '#c1bd72', '#627b53', '#ac7954', '#658cba'],
};
export function categoryValue(value) {
    return value === undefined || value === null ? null : String(value);
}
export function layerSymbol(value, color) {
    const base = symbol(value, color);
    return value?.classification ? { ...base, classification: value.classification } : base;
}
function colorInput(label, value, change) {
    return h('input', { type: 'color', 'aria-label': label, value, onChange: event => change(event.target.value) });
}

function Classification({ item, style, update, color, setLoading }) {
    const [scheme, setScheme] = useState(style.classification.scheme || 'planning');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState('');
    const request = useRef(0);
    const value = style.classification;
    function colors(list, selected) {
        return list.map((entry, index) => ({ ...entry, color: palettes[selected][index % palettes[selected].length] }));
    }
    async function field(name) {
        const token = ++request.current;
        setError('');
        update('classification', { field: name, classes: [], other: value.other });
        setBusy(true);
        setLoading(true);
        try {
            const result = await window.guizhi.map({ action: 'categories', ref: item.ref, field: name });
            if (token === request.current) update('classification', {
                field: name, scheme, classes: colors(result.classes, scheme), other: value.other, remaining: result.remaining });
        } catch (issue) { if (token === request.current) setError(window.GuizhiErrorText(issue)); }
        finally { if (token === request.current) { setBusy(false); setLoading(false); } }
    }
    return h('section', { className: 'gis-classification' },
        h('div', { className: 'gis-skill-parameters' },
            h('label', { className: 'gis-tool-field' }, '分类字段', h(Select, { 'aria-label': '分类字段', showSearch: true,
                value: value.field || undefined, placeholder: '选择字段', options: item.fields.map(entry => ({ value: entry.name })),
                onChange: field })),
            h('label', { className: 'gis-tool-field' }, '色板', h(Select, { 'aria-label': '分类色板', value: scheme, disabled: busy,
                options: [{ value: 'planning', label: '规划' }, { value: 'terrain', label: '地貌' }],
                onChange: next => { setScheme(next); update('classification', { ...value, scheme: next, classes: colors(value.classes, next) }); } }))),
        busy && h('p', { role: 'status' }, '正在读取分类值…'),
        error && h('p', { role: 'alert', className: 'error' }, error),
        value.classes.length > 0 && h('table', { className: 'gis-classes' },
            h('thead', null, h('tr', null, h('th', null, '颜色'), h('th', null, '字段值'), h('th', null, '要素数'))),
            h('tbody', null, ...value.classes.map((entry, index) => h('tr', { key: JSON.stringify(entry.value) },
                h('td', null, colorInput('分类颜色 ' + (entry.value ?? '空值'), entry.color,
                    next => update('classification', { ...value, classes: value.classes.map((item, i) => i === index ? { ...item, color: next } : item) }))),
                h('td', null, entry.label || (entry.value ?? '空值')), h('td', null, entry.count ?? '—'))),
            h('tr', null, h('td', null, colorInput('其他值颜色', value.other || color,
                next => update('classification', { ...value, other: next }))), h('td', null, '其他值' + (value.remaining ? '（' + value.remaining + ' 类）' : '')),
                h('td', null, '—')))));
}

export function LayerProperties({ item, color, close, save, glyph, start = 'info' }) {
    const [tab, setTab] = useState(start);
    const [style, setStyle] = useState(() => layerSymbol(item.style, color));
    const [classificationBusy, setClassificationBusy] = useState(false);
    const image = item.kind === 'raster';
    const points = /Point/.test(item.geometry || '');
    const polygon = /Polygon/.test(item.geometry || '');
    const update = (name, value) => setStyle(current => ({ ...current, [name]: value }));
    const prop = (label, value) => h('div', { key: label }, h('dt', null, label), h('dd', null, value));
    const field = (label, control) => h('label', { key: label, className: 'gis-tool-field' }, h('span', null, label), control);
    const styleColor = (name, label, fallback) => field(label,
        h('input', { type: 'color', 'aria-label': label, value: style[name] || fallback,
            onChange: event => update(name, event.target.value) }));
    const choices = (name, label, values) => field(label, h(Select, { 'aria-label': label, value: style[name],
        options: Object.entries(values).map(([value, text]) => ({ value, label: text })), onChange: value => update(name, value) }));
    const measure = (name, label, min, max, step) => field(label, h(InputNumber, { 'aria-label': label,
        min, max, step, value: style[name], onChange: value => { if (value !== null) update(name, value); } }));
    const bounds = item.bounds;
    const format = value => value.toLocaleString('zh-CN', { maximumFractionDigits: 6, useGrouping: false });
    const info = [
        ['数据源', item.sourceName || '工作区成果'], ...(item.group ? [['图层组', item.group]] : []),
        ['数据类型', image ? '栅格影像' : /Point/.test(item.geometry || '') ? '点要素' :
            /Line/.test(item.geometry || '') ? '线要素' : polygon ? '面要素' : '矢量要素'],
        ['坐标系', item.crsName || '未定义坐标系'],
        ...(/^EPSG:/.test(item.crs || '') ? [['坐标系代码', item.crs]] : []),
        ...(image ? [['像元尺寸', item.width + ' × ' + item.height], ['波段数', item.bands]]
            : [['要素数', item.count?.toLocaleString('zh-CN')]]),
        ['范围', bounds ? h('span', { className: 'gis-layer-extent' },
            'X ' + format(bounds[0]) + ' 至 ' + format(bounds[2]),
            h('br'), 'Y ' + format(bounds[1]) + ' 至 ' + format(bounds[3])) : '无有效范围'],
    ];
    const formatTab = tab === 'style' && !image;
    return h(Modal, { open: true, centered: true, width: 520, className: 'gis-dialog', rootClassName: 'gis-modal-root',
        title: h('span', { className: 'gis-dialog-title' }, glyph('layers'), item.name),
        onCancel: close, footer: formatTab ? [h(Button, { key: 'cancel', onClick: close }, '取消'),
            h(Button, { key: 'save', type: 'primary', disabled: classificationBusy ||
                (style.classification && !style.classification.classes.length),
                onClick: () => { save(layerSymbol(style, color)); close(); } }, '应用')]
            : h(Button, { onClick: close }, '关闭') },
        !image && h(window.antd.Segmented, { className: 'gis-layer-tabs', value: tab, onChange: setTab,
            options: [{ value: 'info', label: '图层属性' }, { value: 'style', label: '符号化' }] }),
        formatTab ? h('div', { className: 'map-field-editor' },
            item.fields?.length > 0 && h(window.antd.Segmented, { value: style.classification ? 'unique' : 'single',
                'aria-label': '符号类型', disabled: classificationBusy, options: [{ value: 'single', label: '单一符号' },
                    { value: 'unique', label: '唯一值分类' }],
                onChange: mode => update('classification', mode === 'unique' ? { field: '', classes: [], other: '#7d8999' } : undefined) }),
            style.classification && h(Classification, { item, style, update, color, setLoading: setClassificationBusy }),
            h('div', { className: 'gis-skill-parameters' },
            !style.classification && styleColor('color', points ? '点颜色' : polygon ? '边线颜色' : '线颜色', color),
            points ? choices('shape', '点形状', { circle: '圆形', square: '方形', diamond: '菱形', cross: '十字' })
                : choices('pattern', '线型', { solid: '实线', dash: '虚线', dot: '点线' }),
            points ? measure('size', '点大小', 4, 28, 1) : measure('width', '线宽', .5, 8, .5),
            polygon && field('面填充', h('input', { type: 'checkbox', checked: style.fill !== null,
                'aria-label': '面填充', onChange: event => update('fill', event.target.checked ? style.color : null) })),
            polygon && style.fill && !style.classification && styleColor('fill', '填充颜色', style.color)))
            : h('dl', { className: 'gis-layer-properties' }, ...info.map(([label, value]) => prop(label, value))));
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
