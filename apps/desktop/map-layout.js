const h = window.React.createElement;
const { useEffect, useRef, useState } = window.React;
const { Button, Checkbox, Input, Modal, Select } = window.antd;

const items = { frame: '主地图框', frame2: '第二地图框', title: '图名', legend: '图例', scale: '比例尺', north: '指北针', note: '注记' };
const positions = {
    frame: { x: .07, y: .16, width: .86, height: .75 },
    title: { x: .07, y: .045, width: .86, height: .08 },
    legend: { x: .68, y: .20, width: .21, height: .23 },
    scale: { x: .09, y: .81, width: .18, height: .07 },
    frame2: { x: .56, y: .56, width: .33, height: .31 },
    north: { x: .10, y: .20, width: .08, height: .13 },
    note: { x: .07, y: .93, width: .86, height: .055 },
};
export const layoutDefaults = { title: '', fileName: '', format: 'png', preset: 'formal', legend: true, legendTitle: '图例',
    legendStyle: 'boxed', scale: true, scaleStyle: 'line', border: true, paper: 'a4', orientation: 'landscape',
    monochrome: false, north: false, note: '', frames: { frame: { extent: 'current' } }, positions };

export function amendLayout(before, changes) {
    const base = changes.update && before ? before : layoutDefaults;
    const pair = changes.frames?.frame2 && !base.frames?.frame2 ? {
        frame: { x: .055, y: .17, width: .43, height: .69 },
        frame2: { x: .515, y: .17, width: .43, height: .69 },
        legend: { x: .295, y: .66, width: .175, height: .18 },
        scale: { x: .075, y: .79, width: .14, height: .065 },
    } : {};
    return { ...base, ...changes, frames: changes.frames || base.frames,
        positions: { ...positions, ...base.positions, ...pair, ...changes.positions } };
}

export function sheetSize(layout) {
    const [long, short] = layout.paper === 'a3' ? [1587, 1122] : [1122, 794];
    return layout.orientation === 'portrait' ? [short, long] : [long, short];
}

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
function mainDecoration(layout, name) {
    return layout.frames.frame[name] ?? layout[name];
}

function adjust(box, dx, dy, resize) {
    return resize ? { ...box, width: clamp(box.width + dx, .065, 1 - box.x), height: clamp(box.height + dy, .055, 1 - box.y) }
        : { ...box, x: clamp(box.x + dx, 0, 1 - box.width), y: clamp(box.y + dy, 0, 1 - box.height) };
}

export function MapLayout({ initial, busy, close, exportImage, draw, change, layerChoices, onRendered }) {
    const [value, setValue] = useState(initial);
    const [active, setActive] = useState('frame');
    const paper = useRef(null);
    const preview = useRef(null);
    const gesture = useRef(null);
    const [error, setError] = useState('');
    const [rendering, setRendering] = useState(true);
    const [warnings, setWarnings] = useState('');
    const handlers = useRef({ draw, change, onRendered });
    handlers.current = { draw, change, onRendered };
    const update = (field, next) => setValue(previous => ({ ...previous, [field]: next }));
    const move = (name, box) => setValue(previous => ({ ...previous, positions: { ...previous.positions, [name]: box } }));
    useEffect(() => {
        handlers.current.change(value);
        setRendering(true);
        let current = true;
        const id = setTimeout(async () => {
            try {
                const image = await handlers.current.draw(value);
                const canvas = preview.current;
                if (!canvas || !current) return;
                canvas.width = image.width;
                canvas.height = image.height;
                canvas.getContext('2d').drawImage(image, 0, 0);
                setError('');
                setWarnings(image.dataset.warnings || '');
                handlers.current.onRendered?.();
            } catch (issue) {
                if (current) {
                    setError(window.GuizhiErrorText(issue));
                    handlers.current.onRendered?.(window.GuizhiErrorText(issue));
                }
            } finally { if (current) setRendering(false); }
        }, 85);
        return () => { current = false; clearTimeout(id); };
    }, [value]);
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
        if (event.key === 'Delete' && name !== 'frame' && !busy) {
            event.preventDefault();
            event.stopPropagation();
            remove(name);
            return;
        }
        const vector = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[event.key];
        if (!vector || busy) return;
        event.preventDefault();
        move(name, adjust(value.positions[name], vector[0] / 100, vector[1] / 100, event.shiftKey));
    }
    const visible = name => name.startsWith('frame') ? Boolean(value.frames[name])
        : ['legend', 'scale', 'north'].includes(name) ? Boolean(mainDecoration(value, name)) : Boolean(value[name]);
    const frameUpdate = (id, values) => setValue(old => ({ ...old, frames: {
        ...old.frames, [id]: { ...old.frames[id], ...values },
    } }));
    function remove(name) {
        if (name === 'frame2') update('frames', { frame: value.frames.frame });
        else if (['legend', 'scale', 'north'].includes(name)) frameUpdate('frame', { [name]: false });
        else update(name, '');
        setActive('frame');
    }
    const choose = name => {
        if (name === 'title' && !value.title) update('title', '地图');
        else if (name === 'note' && !value.note) update('note', '图件说明');
        else if (name === 'frame2' && !visible(name)) frameUpdate(name, { extent: 'layers', legend: true, scale: true });
        else if (!visible(name) && ['legend', 'scale', 'north'].includes(name)) frameUpdate('frame', { [name]: true });
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
    const decoration = (key, label) => h(Checkbox, { checked: Boolean(mainDecoration(value, key)),
        onChange: event => frameUpdate('frame', { [key]: event.target.checked }) }, label);
    const [width, height] = sheetSize(value);
    return h(Modal, { open: true, centered: true, width: 'min(1250px, calc(100vw - 32px))', title: '版面布局',
        className: 'gis-dialog', rootClassName: 'gis-modal-root', onCancel: close,
        onOk: () => exportImage(value), okText: '导出', cancelText: '关闭', confirmLoading: busy, maskClosable: false,
        okButtonProps: { disabled: rendering || Boolean(error) } },
        h('div', { className: 'gis-layout-toolbar' }, options('paper', { a4: 'A4', a3: 'A3' }, '纸张'),
            options('orientation', { landscape: '横向', portrait: '纵向' }, '方向'),
            options('preset', { formal: '政务图件', compact: '简洁地图' }, '版式'),
            Object.entries(items).map(([name, label]) => h(Button, { size: 'small', key: name, onClick: () => choose(name),
                type: active === name ? 'primary' : 'default' }, label)),
            toggle('border', '图框'), toggle('monochrome', '黑白')),
        h('div', { className: 'gis-layout-workspace' },
            h('div', { className: 'gis-paper-viewport' }, h('div', { className: 'gis-paper', ref: paper, role: 'listbox',
                onPointerMove: drag, onPointerUp: end, onPointerCancel: end, onLostPointerCapture: end,
                'aria-label': '版面预览', style: { aspectRatio: width + '/' + height,
                    width: 'min(100%, ' + 49 * width / height + 'vh)' } },
                h('canvas', { ref: preview, 'aria-hidden': true }), Object.keys(items).filter(visible).map(element))),
            h('div', { className: 'gis-layout-properties map-field-editor' },
                h('strong', null, items[active]),
                active.startsWith('frame') && h(window.React.Fragment, null,
                    h('label', null, '地图框图层', h(Select, { mode: 'multiple', size: 'small', 'aria-label': '地图框图层',
                        value: value.frames[active]?.layers || [], placeholder: '当前可见图层', options: layerChoices,
                        onChange: next => frameUpdate(active, { layers: next.length ? next : undefined }) })),
                    h('label', null, '范围', h(Select, { size: 'small', 'aria-label': '地图框范围',
                        value: value.frames[active]?.extent || 'current',
                        options: [{ value: 'current', label: '当前地图范围' }, { value: 'layers', label: '图层范围' }],
                        onChange: next => frameUpdate(active, { extent: next }) })),
                    h('label', null, '分图名', h(Input, { size: 'small', maxLength: 80, value: value.frames[active]?.caption || '',
                        onChange: event => frameUpdate(active, { caption: event.target.value }) })),
                    h('div', { className: 'gis-layout-zoom', role: 'group', 'aria-label': '地图内容缩放' },
                        h(Button, { size: 'small', 'aria-label': '缩小地图内容', disabled: (value.frames[active]?.zoom || 1) <= .25,
                            onClick: () => frameUpdate(active, { zoom: Math.max(.25, (value.frames[active]?.zoom || 1) / 1.25) }) }, '−'),
                        h(Button, { size: 'small', 'aria-label': '重置地图缩放',
                            onClick: () => frameUpdate(active, { zoom: 1 }) }, Math.round((value.frames[active]?.zoom || 1) * 100) + '%'),
                        h(Button, { size: 'small', 'aria-label': '放大地图内容', disabled: (value.frames[active]?.zoom || 1) >= 8,
                            onClick: () => frameUpdate(active, { zoom: Math.min(8, (value.frames[active]?.zoom || 1) * 1.25) }) }, '+'))),
                active === 'north' && decoration('north', '显示指北针'),
                active === 'note' && text('note', '注记', 160),
                active === 'title' && text('title', '图名', 80),
                active === 'legend' && h(window.React.Fragment, null, decoration('legend', '显示图例'), text('legendTitle', '图例标题', 24),
                    options('legendStyle', { boxed: '带边框', plain: '无边框' }, '样式')),
                active === 'scale' && h(window.React.Fragment, null, decoration('scale', '显示比例尺'),
                    options('scaleStyle', { line: '刻线', bar: '黑白分段' }, '样式')),
                active !== 'frame' && visible(active) && h(Button, { size: 'small', disabled: busy, onClick: () => remove(active),
                    'aria-keyshortcuts': 'Delete' }, '移除' + items[active]),
                h('hr'), text('fileName', '文件名', 80), options('format', { png: 'PNG', jpg: 'JPG', pdf: 'PDF' }, '格式'))),
        error && h('p', { role: 'alert', className: 'error' }, error),
        !error && warnings && h('p', { role: 'status', className: 'muted' }, warnings));
}

export function drawMapLayers(ctx, viewport, match = () => true) {
    for (const layer of viewport.querySelectorAll('.ol-layer canvas')) {
        if (!layer.width || !match(layer)) continue;
        ctx.save();
        const matrix = new DOMMatrix(layer.style.transform || undefined);
        ctx.globalAlpha = Number(layer.parentElement?.style.opacity || layer.style.opacity || 1);
        ctx.transform(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f);
        ctx.drawImage(layer, 0, 0);
        ctx.restore();
    }
}

export function mapCanvas(frames, config) {
    const layout = config;
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
    function north(x, y, width, height) {
        const center = x + width / 2;
        const top = y + 15;
        const size = Math.min(14, height / 4, width / 4);
        ctx.fillStyle = ink;
        ctx.font = 'bold 13px sans-serif';
        ctx.fillText('N', center - 4, top - 2);
        ctx.beginPath();
        ctx.moveTo(center, top + 2);
        ctx.lineTo(center - size * .55, top + size * 2);
        ctx.lineTo(center, top + size * 1.4);
        ctx.lineTo(center + size * .55, top + size * 2);
        ctx.closePath();
        ctx.fill();
    }
    function scaleBar(scale, x, y, w, h) {
        if (!scale?.metresPerPixel || w < 40 || h < 25) return;
        const capacity = (w - 18) * scale.metresPerPixel;
        const step = 10 ** Math.floor(Math.log10(capacity));
        const metres = [5, 2, 1].map(n => n * step).find(n => n <= capacity);
        const length = metres / scale.metresPerPixel;
        const baseline = y + 25;
        ctx.fillStyle = '#ffffffed';
        ctx.fillRect(x, y, Math.min(w, Math.max(54, length + 16)), 30);
        ctx.fillStyle = ink;
        ctx.strokeStyle = ink;
        ctx.font = '13px "Microsoft YaHei UI", sans-serif';
        ctx.fillText(metres >= 1000 ? metres / 1000 + ' km' : metres + ' m', x + 8, baseline - 8);
        if (layout.scaleStyle === 'bar') {
            ctx.fillRect(x + 8, baseline - 5, length / 2, 5);
            ctx.strokeRect(x + 8, baseline - 5, length, 5);
        } else {
            ctx.beginPath();
            ctx.moveTo(x + 8, baseline - 5);
            ctx.lineTo(x + 8, baseline);
            ctx.lineTo(x + 8 + length, baseline);
            ctx.lineTo(x + 8 + length, baseline - 5);
            ctx.stroke();
        }
    }
    for (const [id, frame] of Object.entries(frames)) {
        const [x, y, w, h] = rect(id);
        const options = layout.frames[id];
        ctx.fillStyle = '#fff';
        ctx.fillRect(x, y, w, h);
        ctx.save();
        if (layout.monochrome) ctx.filter = 'grayscale(1)';
        ctx.drawImage(frame.image, x, y, w, h);
        ctx.restore();
        ctx.strokeStyle = ink;
        if (layout.border) ctx.strokeRect(x + .5, y + .5, w - 1, h - 1);
        ctx.fillStyle = ink;
        if (options.caption) {
            ctx.fillStyle = '#ffffffed';
            ctx.fillRect(x + 6, y + 6, w - 12, 22);
            ctx.fillStyle = ink;
            ctx.font = 'bold 14px "Microsoft YaHei UI", sans-serif';
            ctx.fillText(options.caption, x + 12, y + 22, w - 24);
        }
        if (id === 'frame2') {
            if (options.scale !== false) scaleBar(frame, x + 8, y + h - 36, Math.min(w - 16, 120), 34);
            if (options.north) north(x + w - 37, y + 30, 30, 45);
            if (options.legend !== false && frame.legend.length) {
                const height = Math.min(h / 3, 32 + frame.legend.length * 21);
                paintLegend(frame.legend, x + w * .55, y + h - height - 10, w * .42, height, '图例');
            }
        }
    }
    ctx.fillStyle = ink;
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
    if (mainDecoration(layout, 'scale')) scaleBar(frames.frame, ...rect('scale'));
    if (mainDecoration(layout, 'north')) north(...rect('north'));
    if (layout.note) {
        const [x, y, w, h] = rect('note');
        ctx.fillStyle = ink;
        ctx.font = '13px "Microsoft YaHei UI", sans-serif';
        ctx.fillText(layout.note, x, y + Math.min(h - 4, 18), w);
    }

    function paintLegend(legend, x, y, w, h, title) {
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
        ctx.fillText(title, x + 10, y + 20, w - 20);
        legend.slice(0, shown).forEach(({ name, color, fill }, index) => {
            const row = y + 29 + index * 21;
            if (fill) { ctx.fillStyle = layout.monochrome ? gray(fill) : fill; ctx.fillRect(x + 11, row, 15, 12); }
            ctx.strokeStyle = layout.monochrome ? gray(color) : color;
            ctx.strokeRect(x + 11, row, 15, 12);
            ctx.fillStyle = ink;
            ctx.fillText(name, x + 34, row + 11, w - 43);
        });
        if (count > shown) ctx.fillText('另有 ' + (legend.length - shown) + ' 项', x + 10, y + 40 + shown * 21, w - 20);
        ctx.restore();
    }
    if (mainDecoration(layout, 'legend') && frames.frame.legend.length) {
        paintLegend(frames.frame.legend, ...rect('legend'), layout.legendTitle?.trim() || '图例');
    }
    const warnings = [];
    if (Object.values(frames).some(frame => frame.limited)) warnings.push('当前比例尺以覆盖范围显示密集图层。');
    const [, , , legendHeight] = rect('legend');
    if (mainDecoration(layout, 'legend') &&
            frames.frame.legend.length > Math.floor((legendHeight - 32) / 21)) warnings.push('图例空间不足，可放大图例框。');
    canvas.dataset.warnings = warnings.join(' ');
    return canvas;
}

function gray(value) {
    const match = /^#([a-f0-9]{6})$/i.exec(value);
    if (!match) return '#777';
    const [r, g, b] = match[1].match(/../g).map(part => parseInt(part, 16));
    const brightness = Math.round(.2126 * r + .7152 * g + .0722 * b);
    return 'rgb(' + [brightness, brightness, brightness].join(',') + ')';
}

export function mapImage(canvas, format) {
    return canvas.toDataURL(format === 'jpg' ? 'image/jpeg' : 'image/png', .93);
}
