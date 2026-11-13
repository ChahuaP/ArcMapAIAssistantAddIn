const h = window.React.createElement;
const { useState } = window.React;
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

export function LayerProperties({ item, color, close, save, glyph, start = 'info' }) {
    const [tab, setTab] = useState(start);
    const [style, setStyle] = useState(() => symbol(item.style, color));
    const image = item.kind === 'raster';
    const points = /Point/.test(item.geometry || '');
    const polygon = /Polygon/.test(item.geometry || '');
    const update = (name, value) => setStyle(current => ({ ...current, [name]: value }));
    const prop = (label, value) => h('div', { key: label }, h('dt', null, label), h('dd', null, value));
    const field = (label, control) => h('label', { key: label, className: 'gis-tool-field' }, h('span', null, label), control);
    const colorInput = (name, label, fallback) => field(label,
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
            h(Button, { key: 'save', type: 'primary', onClick: () => { save(symbol(style, color)); close(); } }, '应用')]
            : h(Button, { onClick: close }, '关闭') },
        !image && h(window.antd.Segmented, { className: 'gis-layer-tabs', value: tab, onChange: setTab,
            options: [{ value: 'info', label: '图层属性' }, { value: 'style', label: '符号化' }] }),
        formatTab ? h('div', { className: 'gis-skill-parameters' },
            colorInput('color', points ? '点颜色' : polygon ? '边线颜色' : '线颜色', color),
            points ? choices('shape', '点形状', { circle: '圆形', square: '方形', diamond: '菱形', cross: '十字' })
                : choices('pattern', '线型', { solid: '实线', dash: '虚线', dot: '点线' }),
            points ? measure('size', '点大小', 4, 28, 1) : measure('width', '线宽', .5, 8, .5),
            polygon && field('面填充', h('input', { type: 'checkbox', checked: style.fill !== null,
                'aria-label': '面填充', onChange: event => update('fill', event.target.checked ? style.color : null) })),
            polygon && style.fill && colorInput('fill', '填充颜色', style.color))
            : h('dl', { className: 'gis-layer-properties' }, ...info.map(([label, value]) => prop(label, value))));
}

export function MxdOpen({ document, busy, open, close, glyph, issue }) {
    const [frame, setFrame] = useState(0);
    const current = document.frames[frame];
    return h(Modal, { open: true, centered: true, width: 490, className: 'gis-dialog', rootClassName: 'gis-modal-root',
        title: h('span', { className: 'gis-dialog-title' }, glyph('map'), document.name),
        onCancel: busy ? undefined : close, closable: !busy, keyboard: !busy, maskClosable: false,
        footer: [h(Button, { key: 'close', disabled: busy, onClick: close }, '取消'),
            h(Button, { key: 'locate', disabled: busy || !current, onClick: () => open(frame, true) }, '指定数据目录并打开'),
            h(Button, { key: 'open', type: 'primary', loading: busy, disabled: !current, onClick: () => open(frame, false) }, '打开数据框')] },
        h('div', { className: 'map-field-editor' },
            h('label', null, '数据框', h(Select, { 'aria-label': '数据框', value: frame, disabled: busy,
                options: document.frames.map((item, value) => ({ value, label: item.name + ' · ' + item.count + ' 个图层' })), onChange: setFrame })),
            current?.unavailable > 0 && h('p', { className: 'muted' }, current.unavailable + ' 个图层的数据源缺失或类型暂不支持。'),
            issue && h('p', { className: 'error', role: 'alert' }, issue)));
}
