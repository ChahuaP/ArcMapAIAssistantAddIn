const h = window.React.createElement;
const { useState } = window.React;
const { Checkbox, Input, Modal, Select } = window.antd;

export function MapLayout({ initial, busy, close, exportImage }) {
    const [value, setValue] = useState(initial);
    const update = (field, next) => setValue(previous => ({ ...previous, [field]: next }));
    return h(Modal, { open: true, centered: true, title: '导出地图', className: 'gis-dialog', rootClassName: 'gis-modal-root',
        onCancel: close, onOk: () => exportImage(value), okText: '导出', cancelText: '取消',
        confirmLoading: busy, maskClosable: false },
        h('div', { className: 'map-field-editor' },
            h('label', null, '版式', h(Select, { 'aria-label': '版式', value: value.preset,
                options: [{ value: 'formal', label: '政务图件' }, { value: 'compact', label: '简洁地图' }],
                onChange: preset => setValue(previous => ({ ...previous, preset, border: preset === 'formal',
                    scaleStyle: preset === 'formal' ? 'line' : 'bar' })) })),
            h('label', null, '文件名', h(Input, { 'aria-label': '文件名', value: value.fileName,
                placeholder: value.title || '小六地图', maxLength: 80,
                onChange: event => update('fileName', event.target.value) })),
            h('label', null, '图名', h(Input, { 'aria-label': '图名', value: value.title,
                maxLength: 80, onChange: event => update('title', event.target.value) })),
            h('label', null, '图片格式', h(Select, { 'aria-label': '图片格式', value: value.format,
                options: ['png', 'jpg'].map(format => ({ value: format, label: format.toUpperCase() })),
                onChange: next => update('format', next) })),
            h('div', { className: 'map-layout-options' },
                ...[['legend', '图例'], ['scale', '比例尺'], ['border', '边框']].map(([field, label]) =>
                    h(Checkbox, { key: field, checked: value[field], onChange: event => update(field, event.target.checked) }, label))),
            value.legend && h('label', null, '图例标题', h(Input, { 'aria-label': '图例标题', value: value.legendTitle,
                maxLength: 24, onChange: event => update('legendTitle', event.target.value) })),
            value.scale && h('label', null, '比例尺样式', h(Select, { 'aria-label': '比例尺样式', value: value.scaleStyle,
                options: [{ value: 'line', label: '刻线' }, { value: 'bar', label: '黑白分段' }],
                onChange: next => update('scaleStyle', next) }))));
}

export const layoutDefaults = { title: '', fileName: '', format: 'png', preset: 'formal', legend: true, legendTitle: '图例', scale: true, scaleStyle: 'line', border: true };

export function mapImage(map, viewport, background, legend, layout) {
    const size = map.getSize();
    if (!size?.every(value => value > 0)) throw new Error('请先打开地图并等待图层显示');
    // OpenLayers shares and positions canvases for adjacent layers. Preserve their actual stacking.
    map.renderSync();
    const [width, height] = size;
    const compact = layout.preset === 'compact';
    const margin = compact ? 10 : 24;
    const title = layout.title?.trim();
    const top = title ? (compact ? 46 : 68) : margin;
    const canvas = document.createElement('canvas');
    canvas.width = width + margin * 2;
    canvas.height = top + height + margin;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = background;
    ctx.fillRect(margin, top, width, height);
    for (const layer of viewport.querySelectorAll('.ol-layer canvas')) {
        if (!layer.width) continue;
        ctx.save();
        const matrix = new DOMMatrix(layer.style.transform || undefined);
        ctx.translate(margin, top);
        ctx.transform(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f);
        ctx.drawImage(layer, 0, 0);
        ctx.restore();
    }
    ctx.fillStyle = '#182638';
    if (title) {
        ctx.font = (compact ? '600 17px' : '700 26px') + ' "Microsoft YaHei UI", sans-serif';
        ctx.textAlign = compact ? 'left' : 'center';
        ctx.fillText(title, compact ? margin : canvas.width / 2, compact ? 30 : 43, width);
        ctx.textAlign = 'left';
    }
    if (layout.border) ctx.strokeRect(margin + .5, top + .5, width - 1, height - 1);
    const scale = viewport.querySelector('.ol-scale-line-inner');
    if (layout.scale && scale?.textContent) {
        const x = margin + 12, y = top + height - 14;
        const length = Math.max(24, parseFloat(scale.style.width) || 24);
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(x - 6, y - 24, length + 12, 29);
        ctx.fillStyle = '#182638';
        ctx.font = '12px "Microsoft YaHei UI", sans-serif';
        ctx.fillText(scale.textContent, x, y - 8);
        if (layout.scaleStyle === 'bar') {
            ctx.fillRect(x, y - 4, length / 2, 5);
            ctx.strokeRect(x + .5, y - 3.5, length, 4);
        } else {
            ctx.beginPath();
            ctx.moveTo(x, y - 5);
            ctx.lineTo(x, y);
            ctx.lineTo(x + length, y);
            ctx.lineTo(x + length, y - 5);
            ctx.stroke();
        }
    }
    if (layout.legend && legend.length) {
        ctx.font = '13px "Microsoft YaHei UI", sans-serif';
        const lines = legend.slice(0, Math.max(0, Math.floor((height - 60) / 21)));
        const box = Math.min(240, width - 22);
        const x = margin + width - box - 10, y = top + 10;
        ctx.fillStyle = '#fffffff2';
        ctx.fillRect(x, y, box, 31 + lines.length * 21);
        ctx.strokeRect(x + .5, y + .5, box - 1, 30 + lines.length * 21);
        ctx.fillStyle = '#182638';
        ctx.fillText(layout.legendTitle?.trim() || '图例', x + 10, y + 20, box - 20);
        lines.forEach(({ name, color, fill }, index) => {
            const row = y + 29 + index * 21;
            if (fill) { ctx.fillStyle = fill; ctx.fillRect(x + 11, row, 15, 12); }
            ctx.strokeStyle = color;
            ctx.strokeRect(x + 11, row, 15, 12);
            ctx.fillStyle = '#182638';
            ctx.fillText(name, x + 34, row + 11, box - 43);
        });
    }
    return canvas.toDataURL(layout.format === 'jpg' ? 'image/jpeg' : 'image/png', .93);
}
