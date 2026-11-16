const h = window.React.createElement;
const { useEffect, useRef, useState } = window.React;
const { Button, Checkbox, Input, Modal, Select } = window.antd;

const items = { frame: '地图框', title: '图名', legend: '图例', scale: '比例尺' };
const positions = {
    frame: { x: .07, y: .16, width: .86, height: .75 },
    title: { x: .07, y: .045, width: .86, height: .08 },
    legend: { x: .68, y: .20, width: .21, height: .23 },
    scale: { x: .09, y: .81, width: .18, height: .07 },
};
export const layoutDefaults = { title: '', fileName: '', format: 'png', preset: 'formal', legend: true, legendTitle: '图例',
    legendStyle: 'boxed', scale: true, scaleStyle: 'line', border: true, paper: 'a4', orientation: 'landscape', positions };

function sheetSize(layout) {
    const [long, short] = layout.paper === 'a3' ? [1587, 1122] : [1122, 794];
    return layout.orientation === 'portrait' ? [short, long] : [long, short];
}

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
function adjust(box, dx, dy, resize) {
    return resize ? { ...box, width: clamp(box.width + dx, .065, 1 - box.x), height: clamp(box.height + dy, .055, 1 - box.y) }
        : { ...box, x: clamp(box.x + dx, 0, 1 - box.width), y: clamp(box.y + dy, 0, 1 - box.height) };
}

export function MapLayout({ initial, busy, close, exportImage, draw, change }) {
    const [value, setValue] = useState(initial);
    const [active, setActive] = useState('frame');
    const paper = useRef(null);
    const preview = useRef(null);
    const gesture = useRef(null);
    const [error, setError] = useState('');
    const update = (field, next) => setValue(previous => ({ ...previous, [field]: next }));
    const move = (name, box) => setValue(previous => ({ ...previous, positions: { ...previous.positions, [name]: box } }));
    useEffect(() => {
        change(value);
        const id = requestAnimationFrame(() => {
            try {
                const image = draw(value);
                const canvas = preview.current;
                if (!canvas) return;
                canvas.width = image.width;
                canvas.height = image.height;
                canvas.getContext('2d').drawImage(image, 0, 0);
                setError('');
            } catch (issue) { setError(window.GuizhiErrorText(issue)); }
        });
        return () => cancelAnimationFrame(id);
    }, [value, draw, change]);
    function start(event, name, resize) {
        if (event.button !== 0 || busy) return;
        event.stopPropagation();
        setActive(name);
        const r = paper.current.getBoundingClientRect();
        gesture.current = { name, resize, id: event.pointerId, box: value.positions[name],
            x: event.clientX, y: event.clientY, width: r.width, height: r.height };
        paper.current.setPointerCapture(event.pointerId);
    }
    function drag(event) {
        const transaction = gesture.current;
        if (!transaction || event.pointerId !== transaction.id || !(event.buttons & 1)) return;
        move(transaction.name, adjust(transaction.box, (event.clientX - transaction.x) / transaction.width,
            (event.clientY - transaction.y) / transaction.height, transaction.resize));
    }
    function end(event) {
        if (gesture.current?.id === event.pointerId) gesture.current = null;
    }
    function nudge(event, name) {
        const vector = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
        if (!vector || busy) return;
        event.preventDefault();
        move(name, adjust(value.positions[name], vector[0] / 100, vector[1] / 100, event.shiftKey));
    }
    const visible = name => name === 'frame' || Boolean(value[name]);
    const choose = name => {
        if (name === 'title' && !value.title) update('title', '地图');
        else if (!visible(name)) update(name, true);
        setActive(name);
    };
    const element = name => {
        const box = value.positions[name];
        const style = { left: box.x * 100 + '%', top: box.y * 100 + '%', width: box.width * 100 + '%', height: box.height * 100 + '%' };
        return h('div', { key: name, className: 'gis-paper-element', style, tabIndex: 0, 'aria-label': items[name],
            'aria-selected': active === name, role: 'option', onFocus: () => setActive(name),
            onPointerDown: event => start(event, name, Boolean(event.target.closest('.gis-paper-resize'))),
            onKeyDown: event => nudge(event, name) },
        active === name && h('div', { className: 'gis-paper-resize', title: '调整大小' }));
    };
    const options = (key, choices, label) => h('label', null, label, h(Select, { 'aria-label': label, size: 'small', value: value[key],
        options: Object.entries(choices).map(([value, label]) => ({ value, label })), onChange: next => update(key, next) }));
    const text = (key, label, maxLength) => h('label', null, label, h(Input, { 'aria-label': label, size: 'small', maxLength,
        value: value[key], onChange: event => update(key, event.target.value) }));
    const toggle = (key, label) => h(Checkbox, { checked: value[key], onChange: event => update(key, event.target.checked) }, label);
    const [width, height] = sheetSize(value);
    return h(Modal, { open: true, centered: true, width: 'min(1250px, calc(100vw - 32px))', title: '版面布局',
        className: 'gis-dialog', rootClassName: 'gis-modal-root', onCancel: close,
        onOk: () => exportImage(value), okText: '导出', cancelText: '关闭', confirmLoading: busy, maskClosable: false,
        okButtonProps: { disabled: Boolean(error) } },
        h('div', { className: 'gis-layout-toolbar' }, options('paper', { a4: 'A4', a3: 'A3' }, '纸张'),
            options('orientation', { landscape: '横向', portrait: '纵向' }, '方向'),
            options('preset', { formal: '政务图件', compact: '简洁地图' }, '版式'),
            Object.entries(items).map(([name, label]) => h(Button, { size: 'small', key: name, onClick: () => choose(name),
                type: active === name ? 'primary' : 'default' }, label)),
            toggle('border', '图框')),
        h('div', { className: 'gis-layout-workspace' },
            h('div', { className: 'gis-paper-viewport' }, h('div', { className: 'gis-paper', ref: paper, role: 'listbox',
                onPointerMove: drag, onPointerUp: end, onPointerCancel: end, onLostPointerCapture: end,
                'aria-label': '版面预览', style: { aspectRatio: width + '/' + height,
                    width: 'min(100%, ' + 49 * width / height + 'vh)' } },
                h('canvas', { ref: preview, 'aria-hidden': true }), Object.keys(items).filter(visible).map(element))),
            h('div', { className: 'gis-layout-properties map-field-editor' },
                h('strong', null, items[active]),
                active === 'frame' && h('span', { className: 'muted' }, '拖动地图框摆放，拖动右下角调整大小。'),
                active === 'title' && text('title', '图名', 80),
                active === 'legend' && h(window.React.Fragment, null, toggle('legend', '显示图例'), text('legendTitle', '图例标题', 24),
                    options('legendStyle', { boxed: '带边框', plain: '无边框' }, '样式')),
                active === 'scale' && h(window.React.Fragment, null, toggle('scale', '显示比例尺'),
                    options('scaleStyle', { line: '刻线', bar: '黑白分段' }, '样式')),
                h('hr'), text('fileName', '文件名', 80), options('format', { png: 'PNG', jpg: 'JPG' }, '图片格式'))),
        error && h('p', { role: 'alert', className: 'error' }, error));
}

export function mapCanvas(map, viewport, background, legend, config) {
    const layout = { ...layoutDefaults, ...config, positions: { ...positions, ...config.positions } };
    const size = map.getSize();
    if (!size?.every(value => value > 0)) throw new Error('请先打开地图并等待图层显示');
    map.renderSync();
    const [width, height] = size;
    const canvas = document.createElement('canvas');
    [canvas.width, canvas.height] = sheetSize(layout);
    const ctx = canvas.getContext('2d');
    const ink = '#182638';
    const rect = id => {
        const r = layout.positions[id];
        return [r.x * canvas.width, r.y * canvas.height, r.width * canvas.width, r.height * canvas.height];
    };
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    const [left, top, frameWidth, frameHeight] = rect('frame');
    const factor = Math.min(frameWidth / width, frameHeight / height);
    ctx.save();
    ctx.beginPath();
    ctx.rect(left, top, frameWidth, frameHeight);
    ctx.clip();
    ctx.fillStyle = background;
    ctx.fillRect(left, top, frameWidth, frameHeight);
    ctx.translate(left + (frameWidth - width * factor) / 2, top + (frameHeight - height * factor) / 2);
    ctx.scale(factor, factor);
    for (const layer of viewport.querySelectorAll('.ol-layer canvas')) {
        if (!layer.width) continue;
        ctx.save();
        const matrix = new DOMMatrix(layer.style.transform || undefined);
        ctx.globalAlpha = Number(layer.parentElement?.style.opacity || layer.style.opacity || 1);
        ctx.transform(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f);
        ctx.drawImage(layer, 0, 0);
        ctx.restore();
    }
    ctx.restore();
    ctx.fillStyle = ink;
    ctx.strokeStyle = ink;
    if (layout.border) ctx.strokeRect(left + .5, top + .5, frameWidth - 1, frameHeight - 1);
    const title = layout.title?.trim();
    if (title) {
        const [x, y, w, h] = rect('title');
        ctx.font = '700 ' + clamp(h * .55, 16, 38) + 'px "Microsoft YaHei UI", sans-serif';
        ctx.textAlign = layout.preset === 'compact' ? 'left' : 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(title, ctx.textAlign === 'left' ? x : x + w / 2, y + h / 2, w - 8);
        ctx.textAlign = 'left';
        ctx.textBaseline = 'alphabetic';
    }
    const scale = viewport.querySelector('.ol-scale-line-inner');
    if (layout.scale && scale?.textContent) {
        const [x, y, w, h] = rect('scale');
        const length = Math.max(1, parseFloat(scale.style.width) || 1) * factor;
        const baseline = y + Math.min(h - 6, 32);
        ctx.fillStyle = '#fff';
        ctx.fillRect(x, y, Math.max(w, length + 22), Math.min(h, 39));
        ctx.fillStyle = ink;
        ctx.font = '13px "Microsoft YaHei UI", sans-serif';
        ctx.fillText(scale.textContent, x + 10, baseline - 9);
        const start = x + 10;
        ctx.strokeStyle = ink;
        if (layout.scaleStyle === 'bar') {
            ctx.fillRect(start, baseline - 5, length / 2, 5);
            ctx.strokeRect(start, baseline - 5, length, 5);
        } else {
            ctx.beginPath();
            ctx.moveTo(start, baseline - 6);
            ctx.lineTo(start, baseline);
            ctx.lineTo(start + length, baseline);
            ctx.lineTo(start + length, baseline - 6);
            ctx.stroke();
        }
    }
    if (layout.legend && legend.length) {
        const [x, y, w, h] = rect('legend');
        ctx.save();
        ctx.beginPath();
        ctx.rect(x, y, w, h);
        ctx.clip();
        ctx.font = '13px "Microsoft YaHei UI", sans-serif';
        const capacity = Math.max(0, Math.floor((h - 32) / 21));
        const shown = capacity < legend.length ? Math.max(0, capacity - 1) : capacity;
        const count = Math.min(capacity, legend.length);
        ctx.fillStyle = '#fffffff4';
        ctx.fillRect(x, y, w, h);
        ctx.strokeStyle = ink;
        if (layout.legendStyle !== 'plain') ctx.strokeRect(x + .5, y + .5, w - 1, h - 1);
        ctx.fillStyle = ink;
        ctx.fillText(layout.legendTitle?.trim() || '图例', x + 10, y + 20, w - 20);
        legend.slice(0, shown).forEach(({ name, color, fill }, index) => {
            const row = y + 29 + index * 21;
            if (fill) { ctx.fillStyle = fill; ctx.fillRect(x + 11, row, 15, 12); }
            ctx.strokeStyle = color;
            ctx.strokeRect(x + 11, row, 15, 12);
            ctx.fillStyle = ink;
            ctx.fillText(name, x + 34, row + 11, w - 43);
        });
        if (count > shown) ctx.fillText('另有 ' + (legend.length - shown) + ' 项', x + 10, y + 40 + shown * 21, w - 20);
        ctx.restore();
    }
    return canvas;
}

export function mapImage(canvas, format) {
    return canvas.toDataURL(format === 'jpg' ? 'image/jpeg' : 'image/png', .93);
}
