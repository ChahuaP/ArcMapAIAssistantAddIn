import 'ol/ol.css';
import Map from 'ol/Map.js';
import View from 'ol/View.js';
import Projection from 'ol/proj/Projection.js';
import ImageLayer from 'ol/layer/Image.js';
import Static from 'ol/source/ImageStatic.js';
import VectorLayer from 'ol/layer/Vector.js';
import VectorSource from 'ol/source/Vector.js';
import GeoJSON from 'ol/format/GeoJSON.js';
import Select from 'ol/interaction/Select.js';
import Draw from 'ol/interaction/Draw.js';
import Modify from 'ol/interaction/Modify.js';
import Translate from 'ol/interaction/Translate.js';
import Snap from 'ol/interaction/Snap.js';
import { click } from 'ol/events/condition.js';
import ScaleLine from 'ol/control/ScaleLine.js';
import { Fill, Stroke, Style, Circle } from 'ol/style.js';
import { createEmpty, extend, isEmpty } from 'ol/extent.js';

const h = window.React.createElement;
const { useEffect, useRef, useState } = window.React;
const { Button, Input, Select: Choice, Table, Modal } = window.antd;
const api = window.guizhi;
const format = new GeoJSON();
const palette = ['#1785db', '#bf6f2b', '#17a489', '#8263ca', '#cc5785'];
const glyphs = {
    map: 'm3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3zm6-3v15m6-12v15',
    layers: 'm12 3 9 5-9 5-9-5zm-9 9 9 5 9-5M3 16l9 5 9-5',
    pencil: 'm14 6 4 4M4 20l4-1 12-12-4-4L4 15z',
    analysis: 'M3 3v18h18M8 16v-4m5 4V8m5 8V5',
    globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM3 12h18M12 3c5 4 5 14 0 18-5-4-5-14 0-18Z',
    extent: 'M9 4H4v5m11-5h5v5m0 6v5h-5M4 15v5h5M9 9h6v6H9z',
    target: 'M12 3v3m0 12v3M3 12h3m12 0h3m-3 0a6 6 0 1 1-12 0 6 6 0 0 1 12 0Z',
    zoomIn: 'M15.5 15.5 21 21M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0ZM7 10h6m-3-3v6',
    zoomOut: 'M15.5 15.5 21 21M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0ZM7 10h6',
    select: 'm4 4 15 7-7 2-2 7-6-16Z',
    clear: 'M8 4H4v4m12-4h4v4m0 8v4h-4M4 16v4h4m0-12 8 8m0-8-8 8',
    table: 'M3 4h18v16H3zM3 9h18M3 14h18M9 9v11',
    add: 'M3 5h7l2 2h9v13H3zM12 10v7m-3.5-3.5h7',
    download: 'M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5',
    area: 'M3 3h18v18H3zM7 17l10-10m-5 0h5v5m-5 5H7v-5',
    buffer: 'M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Zm5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
    clip: 'M4 4h12v12H4zM8 8h12v12H8z',
    count: 'M5 8h14M5 16h14M10 4 8 20m8-16-2 16',
    node: 'M5 5h4v4H5zm10 10h4v4h-4zM9 9l6 6',
    move: 'M12 2v20M2 12h20M9 5l3-3 3 3M9 19l3 3 3-3M5 9l-3 3 3 3m14-6 3 3-3 3',
    undo: 'M4 5v7h7m-7 0 5-5a6 6 0 1 1 5 10',
    save: 'M4 3h13l3 3v15H4zM8 3v6h8V3m-8 18v-7h8v7',
    trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13m-8 4v5m4-5v5',
    close: 'm6 6 12 12M18 6 6 18',
    check: 'm5 12 4 4L19 6',
    chat: 'M4 5h16v12H9l-5 4zM8 9h8m-8 4h5',
    more: 'M5 12h.1m6.9 0h.1m6.9 0h.1',
};
const commandIcons = {
    全图: 'extent', 定位图层: 'target', 放大: 'zoomIn', 缩小: 'zoomOut', 清除选择: 'clear',
    属性表: 'table', 添加图层: 'add', '导出 SHP': 'download', 坐标系设置: 'globe',
    编辑图层: 'pencil', 选择: 'select', 绘制: 'pencil', 改节点: 'node', 移动: 'move',
    完成绘制: 'check', 撤销绘制点: 'undo', 编辑属性: 'table', 删除要素: 'trash',
    撤销: 'undo', 保存新版本: 'save', 退出编辑: 'close', 面积汇总: 'area', 缓冲分析: 'buffer',
    裁切: 'clip', 要素计数: 'count', 返回对话: 'chat',
};
const tabIcons = { 地图: 'map', 数据: 'layers', 编辑: 'pencil', 分析: 'analysis', 坐标系: 'globe' };
function glyph(name, size = 17) {
    return h('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
        stroke: 'currentColor', strokeWidth: name === 'more' ? 3 : 1.65, strokeLinecap: 'round',
        strokeLinejoin: 'round', 'aria-hidden': true }, h('path', { d: glyphs[name] }));
}

const roles = { project: '项目范围', coverage: '重叠范围' };
const key = item => item.ref.run + ':' + item.ref.artifact;
const projection = (code, units) => new Projection({ code: code || 'local', units: units || 'm' });
const feature = item => format.readFeature({ type: 'Feature', ...item });
const style = (color, selected) => {
    const stroke = new Stroke({ color: selected ? '#f7be49' : color, width: selected ? 3 : 1.6 });
    const fill = new Fill({ color: selected ? '#f7be494e' : color + '31' });
    return new Style({ stroke, fill, image: new Circle({ radius: selected ? 6 : 4, fill, stroke }) });
};
const layerFor = (items, color) => new VectorLayer({
    source: new VectorSource({ features: items.map(feature) }), style: style(color, false),
});

function attach(node, view, layers) {
    return new Map({ target: node, view, layers, controls: [], pixelRatio: Math.min(devicePixelRatio, 2) });
}

function fit(view, layers, size) {
    const extent = createEmpty();
    for (const layer of layers) {
        if (!layer.getVisible()) continue;
        const bounds = layer.getSource()?.getExtent?.() || layer.getExtent();
        if (bounds) extend(extent, bounds);
    }
    if (!isEmpty(extent) && size?.every(n => n > 0)) view.fit(extent, { size, padding: [28, 28, 28, 28], maxZoom: 20 });
}

function Preview({ map, choose, selected, open, hasLayers }) {
    const node = useRef(null);
    const graph = useRef(null);
    const [visible, show] = useState(true);
    useEffect(() => {
        if (!map?.features?.length) return;
        const layers = Object.entries(roles).map(([role], index) => {
            const layer = layerFor(map.features.filter(item => (item.role in roles ? item.role : 'project') === role)
                .map((item, n) => ({ ...item, id: item.id ?? String(n), properties: { label: item.label } })),
                palette[index]);
            layer.set('role', role);
            return layer;
        });
        const view = new View({ projection: projection(map.crs), center: [0, 0], zoom: 0 });
        const ol = attach(node.current, view, layers);
        const selection = new Select({ condition: click, layers: [layers[0]], style: style(palette[0], true) });
        ol.addInteraction(selection);
        selection.on('select', event => choose?.({ id: event.selected[0]?.getId() || null }));
        graph.current = { ol, view, layers, selection };
        fit(view, layers, ol.getSize());
        const observer = new ResizeObserver(() => {
            ol.updateSize();
            if (ol.getSize()?.every(n => n > 0)) fit(view, layers, ol.getSize());
        });
        observer.observe(node.current);
        return () => { observer.disconnect(); ol.dispose(); graph.current = null; };
    }, [map]);
    useEffect(() => graph.current?.layers[1].setVisible(visible), [visible, map]);
    useEffect(() => {
        const state = graph.current;
        if (!state) return;
        state.selection.getFeatures().clear();
        const match = selected && state.layers[0].getSource().getFeatureById(selected);
        if (match) state.selection.getFeatures().push(match);
    }, [selected, map]);
    if (!map && !hasLayers) return null;
    return h('section', { className: 'map-panel', 'aria-label': '成果地图' },
        h('div', { className: 'map-head' }, h('h2', null, '成果地图'),
            h(Button, { size: 'small', onClick: open }, '展开地图')),
        map?.features?.length > 0 && h('div', { className: 'geo-map', ref: node, tabIndex: 0, 'aria-label': '空间成果地图' }),
        map?.features?.some(item => item.role === 'coverage') && h('label', { className: 'map-legend-entry' },
            h('input', { type: 'checkbox', checked: visible, onChange: event => show(event.target.checked) }), '重叠范围'));
}

function CoordinateTools({ layer, display, setDisplay, entries, request, onLayer, fail, displayPlan, setDisplayPlan }) {
    const [query, search] = useState('');
    const [standard, setStandard] = useState([]);
    const [plan, setPlan] = useState('');
    useEffect(() => {
        let current = true;
        const timer = setTimeout(() => api.map({ action: 'crs-search', query }).then(items => {
            if (current) setStandard(items);
        }).catch(error => { if (current) fail(error); }), 180);
        return () => { current = false; clearTimeout(timer); };
    }, [query]);
    useEffect(() => setPlan(''), [layer]);
    const definitions = entries.filter(entry => entry.kind === 'definition');
    const options = [...definitions.map(entry => ({ value: entry.id + '@' + entry.revision, label: entry.title,
        wkt: entry.definition.wkt, units: { degree: 'degrees', metre: 'm', meter: 'm' }[entry.definition.axes?.[0]?.unit] })), ...standard];
    const plans = entries.filter(entry => entry.kind === 'plan');
    return h('details', { className: 'map-coordinate' }, h('summary', null, '坐标系'),
        h('span', null, layer?.crsName || '尚未添加图层'),
        h(Choice, { showSearch: true, filterOption: false, allowClear: true, onSearch: search,
            placeholder: '地图显示坐标系', 'aria-label': '地图显示坐标系',
            options: [...options, ...(display && !options.some(item => item.value === display.value) ? [display] : [])],
            value: display?.value, onChange: (value, item) => setDisplay(value ? item : null) }),
        layer && !layer.crs && h(Choice, { showSearch: true, filterOption: false, onSearch: search, options,
            placeholder: '指定图层已有坐标系', 'aria-label': '指定图层已有坐标系',
            onChange: value => request({ action: 'assign', ref: layer.ref, crs: value }).then(onLayer).catch(fail) }),
        layer?.crs && layer.kind !== 'raster' && plans.length > 0 && h('div', { className: 'map-coordinate-actions' },
            display && h(Choice, { value: displayPlan || undefined, placeholder: '为该图层选择显示转换',
                'aria-label': '显示转换方案', allowClear: true, onChange: setDisplayPlan,
                options: plans.map(entry => ({ value: entry.id + '@' + entry.revision, label: entry.title })) }),
            h(Choice, { value: plan || undefined, placeholder: '选择已保存转换方案', 'aria-label': '选择已保存转换方案',
                options: plans.map(entry => ({ value: entry.id + '@' + entry.revision, label: entry.title })), onChange: setPlan }),
            h(Button, { disabled: !plan, onClick: () => request({ action: 'transform', ref: layer.ref, plan }).then(onLayer).catch(fail) },
                '转换并保存')));

}

function WorkspaceMap({ layers, add, remove, selectLayer, active, hidden, toggle, entries, fail, close, selection, setSelection, replace, editing }) {
    const node = useRef(null);
    const graph = useRef(null);
    const [display, setDisplay] = useState(null);
    const [displayPlans, setDisplayPlans] = useState({});
    const [editLayer, setEditLayer] = useState(null);
    const [edit, setEdit] = useState('');
    const [ribbon, setRibbon] = useState('地图');
    const [layerSearch, setLayerSearch] = useState('');
    const [analysis, setAnalysis] = useState(null);
    useEffect(() => { editing(Boolean(edit)); return () => editing(false); }, [Boolean(edit)]);
    const [count, setCount] = useState(0);
    const [fieldEdit, setFieldEdit] = useState(null);
    const edits = useRef([]);
    const drawingTool = useRef(null);
    const [drawingPoints, setDrawingPoints] = useState(0);
    const original = useRef(new globalThis.Map());
    const [busy, setBusy] = useState(false);
    const [issue, setIssue] = useState('');
    const [table, setTable] = useState(null);
    const [tableLayer, setTableLayer] = useState(null);
    const [page, setPage] = useState(0);
    const [limited, setLimited] = useState([]);
    const [viewRevision, setViewRevision] = useState(0);
    const selectionRef = useRef(selection);
    selectionRef.current = selection;
    const layer = layers.find(item => key(item) === active);
    const native = editLayer || layers.find(item => item.crs && item.kind !== 'raster') || layers.find(item => item.crs) || layers[0];
    const target = display?.wkt || display?.value || native?.crs;
    const units = display ? display.units : native?.units;
    const activeRef = useRef(active);
    activeRef.current = active;

    async function load(job) {
        setBusy(true);
        try {
            const { layers: items, issues } = await job;
            add(items);
            setIssue(issues.map(item => item.name + '：' + item.message).join('；'));
        } catch (error) { setIssue(window.GuizhiErrorText(error)); }
        finally { setBusy(false); }
    }

    useEffect(() => {
        const view = new View({ projection: projection(target, units), center: [0, 0], zoom: 0 });
        const ol = attach(node.current, view, []);
        if (units === 'm' || units === 'degrees') ol.addControl(new ScaleLine({ units: 'metric' }));
        const select = new Select({ condition: click, layers: candidate => candidate.get('key') === activeRef.current,
            style: style(palette[0], true) });
        ol.addInteraction(select);
        select.on('select', () => setSelection({ key: activeRef.current, ids: select.getFeatures().getArray().map(item => item.getId()) }));
        graph.current = { ol, view, select, byKey: new globalThis.Map(), fitNeeded: true };
        const observer = new ResizeObserver(() => ol.updateSize());
        observer.observe(node.current);
        const moved = () => setViewRevision(value => value + 1);
        ol.on('moveend', moved);
        return () => { observer.disconnect(); ol.dispose(); graph.current = null; };
    }, [target, units]);

    useEffect(() => {
        const state = graph.current;
        if (!state || edit) return;
        let alive = true;
        const previous = state.byKey;
        const next = new globalThis.Map();
        const layerKeys = layers.map(key);
        for (const [id, existing] of previous) if (!layerKeys.includes(id)) state.ol.removeLayer(existing);
        const bbox = !state.fitNeeded && state.ol.getSize()?.every(size => size > 0)
            ? state.view.calculateExtent(state.ol.getSize()) : null;
        const jobs = layers.map((item, index) => {
            const id = key(item);
            const drawing = previous.get(id) || (item.kind === 'raster'
                ? new ImageLayer({ extent: item.bounds, zIndex: -1 }) : layerFor([], palette[index % palette.length]));
            if (!previous.has(id)) {
                drawing.set('key', id);
                state.ol.addLayer(drawing);
            }
            drawing.setVisible(!hidden.includes(id));
            next.set(id, drawing);
            if (hidden.includes(id)) return null;
            if (item.kind === 'raster') {
                const visible = !target || target === item.crs || display?.value === item.crs;
                drawing.setVisible(visible);
                if (!visible) return Promise.resolve({ id, drawing, raster: true,
                    error: new Error('影像 ' + item.name + ' 的坐标系与地图不同。请切换到影像坐标系或先转换影像。') });
                if (drawing.getSource()) return Promise.resolve({ id, drawing, raster: true });
                return api.map({ action: 'raster-preview', ref: item.ref }).then(image => ({ id, drawing, raster: true,
                    image: new Static({ url: 'data:image/png;base64,' + image, imageExtent: item.bounds,
                        projection: state.view.getProjection() }) })).catch(error => ({ id, drawing, raster: true, error }));
            }
            if (!item.crs && target) {
                drawing.getSource().clear();
                return null;
            }
            const plan = entries.find(entry => entry.kind === 'plan' &&
                (entry.plan.source_wkt === item.crs || entry.source_reference === item.crs) &&
                (entry.plan.target_wkt === target || entry.target_reference === display?.value));
            return api.map({ action: 'features', ref: item.ref, bbox,
                target: target || null, plan: displayPlans[id] || (plan && plan.id + '@' + plan.revision) })
                .then(result => ({ id, drawing, result })).catch(error => ({ id, drawing, error }));
        }).filter(Boolean);
        state.byKey = next;
        Promise.all(jobs).then(items => {
            if (!alive) return;
            const errors = items.filter(item => item.error);
            setIssue(errors.map(item => window.GuizhiErrorText(item.error)).join('；'));
            setLimited(items.filter(item => item.result?.limited).map(item => item.id));
            for (const item of items) {
                if (item.raster) {
                    if (item.image) item.drawing.setSource(item.image);
                    continue;
                }
                item.drawing.getSource().clear();
                if (item.result) item.drawing.getSource().addFeatures(item.result.features.map(feature));
            }
            if (state.fitNeeded && items.length) {
                state.fitNeeded = false;
                const drawn = [...next.values()];
                if (drawn.some(item => item.getVisible() && (item instanceof ImageLayer ? Boolean(item.getSource()) : !isEmpty(item.getSource().getExtent())))) fit(state.view, drawn, state.ol.getSize());
                else if (native?.bounds && native.crs === target) state.view.fit(native.bounds, { maxZoom: 20, padding: [28, 28, 28, 28] });
            }
            restoreSelection(state, selectionRef.current);
        });
        return () => { alive = false; };
    }, [layers, hidden, target, units, viewRevision, entries, displayPlans, Boolean(edit)]);

    function restoreSelection(state, selected) {
        const drawing = selected && state.byKey.get(selected.key);
        const source = drawing instanceof VectorLayer && drawing.getSource();
        const collection = state.select.getFeatures();
        if (!collection.getLength() && !source) return;
        collection.clear();
        if (source) for (const id of selected.ids) {
            const found = source.getFeatureById(id);
            if (found) collection.push(found);
        }
    }
    useEffect(() => {
        if (graph.current) restoreSelection(graph.current, selection);
    }, [selection]);
    useEffect(() => {
        if (!layer || layer.kind === 'raster' || !table) return;
        let alive = true;
        api.map({ action: 'table', ref: layer.ref, page }).then(result => {
            if (alive) { setTable(result.rows); setTableLayer(key(layer)); }
        }).catch(error => { if (alive) fail(error); });
        return () => { alive = false; };
    }, [layer, page, Boolean(table)]);

    const picked = selection?.key === active ? selection.ids : [];
    const editable = Boolean(layer?.crs && !hidden.includes(active) &&
        /^(Multi)?(Point|LineString|Polygon)$/.test(layer.geometry));
    function push(changes) {
        edits.current.push(changes);
        setCount(edits.current.length);
    }
    function applyDraft() {
        const source = graph.current?.byKey.get(active)?.getSource();
        if (!source) return;
        const items = new globalThis.Map(original.current);
        for (const group of edits.current) for (const change of group) {
            const id = change.id ?? change.fid;
            if (change.action === 'delete') items.delete(id);
            else {
                const previous = items.get(id) || {};
                items.set(id, { id, geometry: change.geometry || previous.geometry, properties: {} });
            }
        }
        source.clear();
        source.addFeatures([...items.values()].map(feature));
        restoreSelection(graph.current, selectionRef.current);
    }
    function startEdit() {
        if (display || target !== layer.crs) {
            setDisplay(null);
            setEditLayer(layer);
            setIssue('已切换到图层坐标系，请在地图就绪后开始编辑。');
            return;
        }
        const source = graph.current.byKey.get(active)?.getSource();
        if (!source || graph.current.fitNeeded) return;
        original.current = new globalThis.Map((source?.getFeatures() || []).map(item => [item.getId(), {
            id: item.getId(), geometry: format.writeGeometryObject(item.getGeometry()), properties: {},
        }]));
        edits.current = [];
        setEdit('select');
        setRibbon('编辑');
    }
    function stopEdit() {
        edits.current = [];
        setCount(0);
        setEdit('');
        setFieldEdit(null);
    }
    useEffect(() => {
        if (!edit) return;
        const g = graph.current;
        const drawing = g?.byKey.get(active);
        if (!drawing) return;
        const source = drawing.getSource();
        const selected = g.select.getFeatures();
        const interactions = [];
        const makeChanges = event => {
            const changes = event.features.getArray().map(item => {
                const id = item.getId();
                return { action: 'update', id, geometry: format.writeGeometryObject(item.getGeometry()) };
            });
            if (changes.length) push(changes);
        };
        if (edit === 'draw') {
            const draw = new Draw({ type: layer.geometry.replace(/^Multi/, '') });
            drawingTool.current = draw;
            draw.on('drawstart', event => {
                const geometry = event.feature.getGeometry();
                const measure = () => {
                    const coordinates = geometry.getCoordinates();
                    const vertices = layer.geometry.endsWith('Polygon') ? coordinates[0] : coordinates;
                    setDrawingPoints(Math.max(0, vertices.length - 1));
                };
                geometry.on('change', measure);
                measure();
            });
            draw.on('drawabort', () => setDrawingPoints(0));
            draw.on('drawend', event => {
                setDrawingPoints(0);
                const id = 'new-' + crypto.randomUUID();
                push([{ action: 'add', id, geometry: format.writeGeometryObject(event.feature.getGeometry()) }]);
                applyDraft();
                setEdit('select');
            });
            interactions.push(draw);
        }
        if (edit === 'modify') {
            const modify = new Modify({ features: selected });
            modify.on('modifyend', makeChanges);
            interactions.push(modify);
        }
        if (edit === 'move') {
            const translate = new Translate({ features: selected });
            translate.on('translateend', makeChanges);
            interactions.push(translate);
        }
        if (edit !== 'select') {
            for (const candidate of g.byKey.values()) {
                if (candidate instanceof VectorLayer && candidate.getVisible()) interactions.push(new Snap({ source: candidate.getSource() }));
            }
        }
        for (const interaction of interactions) g.ol.addInteraction(interaction);
        return () => {
            drawingTool.current?.abortDrawing();
            drawingTool.current = null;
            for (const interaction of interactions) g.ol.removeInteraction(interaction);
        };
    }, [edit, active]);

    async function saveEdit() {
        const merged = new globalThis.Map();
        for (const group of edits.current) for (const change of group) {
            const id = change.id ?? change.fid;
            const before = merged.get(id);
            if (change.action === 'delete' && before?.action === 'add') { merged.delete(id); continue; }
            if (change.action === 'delete') { merged.set(id, { action: 'delete', fid: id }); continue; }
            const next = { action: before?.action === 'add' || change.action === 'add' ? 'add' : 'update' };
            if (next.action === 'update') next.fid = id;
            const geometry = change.geometry || before?.geometry;
            if (geometry) next.geometry = geometry;
            if (change.fields || before?.fields) next.fields = { ...before?.fields, ...change.fields };
            merged.set(id, next);
        }
        if (!merged.size) { stopEdit(); return; }
        setBusy(true);
        try {
            const saved = await api.map({ action: 'edit', ref: layer.ref, revision: layer.revision, changes: [...merged.values()] });
            stopEdit();
            replace(layer, saved);
        } catch (error) { fail(error); }
        finally { setBusy(false); }
    }

    async function openFields() {
        const fid = picked[0];
        const pending = Object.assign({}, ...edits.current.flat().filter(item => item.id === fid).map(item => item.fields || {}));
        try {
            const fields = typeof fid === 'number' ? (await api.map({ action: 'features', ref: layer.ref, fids: [fid] })).features[0].properties : {};
            setFieldEdit({ fid, values: { ...fields, ...pending }, changed: {} });
        } catch (error) { fail(error); }
    }

    const columns = [{ title: '要素', dataIndex: 'id', width: 75 },
        ...(layer?.fields || []).map(field => ({ title: field.name, render: (_, row) => String(row.values[field.name] ?? '—') }))];
    const command = (label, onClick, props = {}) => h(Button, { size: 'small', icon: glyph(commandIcons[label]),
        onClick, ...props }, label);
    const group = (label, ...items) => h('section', { className: 'ribbon-group', 'aria-label': label },
        h('div', { className: 'ribbon-actions' }, ...items), h('span', null, label));
    function position(onlyActive) {
        const g = graph.current;
        const activeDrawing = g.byKey.get(active);
        fit(g.view, onlyActive && activeDrawing ? [activeDrawing] : [...g.byKey.values()], g.ol.getSize());
    }
    const clear = () => setSelection({ key: active, ids: [] });
    const editCommands = edit ? [
        group('要素编辑', ...[['select', '选择'], ['draw', '绘制'], ['modify', '改节点'], ['move', '移动']].map(([mode, title]) =>
            command(title, () => setEdit(mode), { key: mode, disabled: busy || (['modify', 'move'].includes(mode) && !picked.length),
                type: edit === mode ? 'primary' : 'default' })),
            edit === 'draw' && drawingPoints > 0 && command('完成绘制', () => drawingTool.current?.finishDrawing(),
                { disabled: drawingPoints < (layer.geometry.endsWith('Polygon') ? 3 : 2) }),
            edit === 'draw' && drawingPoints > 0 && command('撤销绘制点', () => drawingTool.current?.removeLastPoint())),
        group('属性与删除', command('编辑属性', openFields, { disabled: busy || picked.length !== 1 || !layer.fields.length }),
            command('删除要素', () => {
                push(picked.map(id => ({ action: 'delete', id }))); applyDraft(); clear(); setEdit('select');
            }, { disabled: busy || !picked.length })),
        group('提交编辑', command('撤销', () => { edits.current.pop(); setCount(edits.current.length); applyDraft(); },
            { disabled: busy || !count }),
        command('保存新版本', saveEdit, { disabled: busy || !count, loading: busy, type: 'primary' }),
        command('退出编辑', () => { edits.current = []; applyDraft(); stopEdit(); }, { disabled: busy })),
    ] : [group('图层编辑', command('编辑图层', startEdit, { disabled: !editable || busy || limited.includes(active) }))];
    const commands = {
        地图: [group('导航', command('全图', () => position(false), { disabled: !layers.length }),
            command('定位图层', () => position(true), { disabled: !layer }),
            command('放大', () => graph.current.view.adjustZoom(1)), command('缩小', () => graph.current.view.adjustZoom(-1))),
            group('选择', command('清除选择', clear, { disabled: !picked.length }),
                command('属性表', () => { setPage(0); setTable(table ? null : []); }, { disabled: !layer || layer.kind === 'raster' }))],
        数据: [group('导入', command('添加图层', () => load(api.mapImport()), { loading: busy, type: 'primary' })),
            group('交付', command('导出 SHP', () => api.mapExport(layer.ref).catch(fail), { disabled: !layer?.crs || layer.kind === 'raster' })),
            group('坐标参考', command('坐标系设置', () => setRibbon('坐标系')))],
        编辑: editCommands,
        分析: [group('矢量分析',
            command('面积汇总', () => setAnalysis({ kind: 'area' }), { disabled: !layer?.geometry?.includes('Polygon') }),
            command('缓冲分析', () => setAnalysis({ kind: 'buffer', distance: '' }), { disabled: !layer?.geometry }),
            command('裁切', () => setAnalysis({ kind: 'clip' }), { disabled: !layer?.geometry || !layers.some(item => key(item) !== active && item.geometry?.includes('Polygon')) })),
            group('要素统计', command('要素计数', () => setAnalysis({ kind: 'count' }), { disabled: !layer?.geometry }))],
        坐标系: [group('坐标参考', h(CoordinateTools, { layer, display,
            setDisplay: item => { setDisplayPlans({}); setDisplay(item); },
            displayPlan: displayPlans[active], setDisplayPlan: value => setDisplayPlans(old => ({ ...old, [active]: value })),
            entries, request: api.map, onLayer: item => add([item]), fail }))],
    };
    return h('section', { className: 'workspace-map', 'aria-label': '地图工作区', onDragOver: event => event.preventDefault(),
        onDrop: event => { event.preventDefault(); if (!busy && !edit) void load(api.mapDrop([...event.dataTransfer.files])); } },
        h('header', { className: 'map-ribbon' },
            h('div', { className: 'ribbon-tabs' },
                h('div', { role: 'tablist', 'aria-label': '地图工具' }, ...Object.keys(commands).map(tab => h(Button, {
                    key: tab, type: 'text', icon: glyph(tabIcons[tab], 16), role: 'tab', 'aria-selected': ribbon === tab,
                    disabled: Boolean(edit) && tab !== '编辑', onClick: () => setRibbon(tab),
                }, tab))),
                command('返回对话', close, { disabled: Boolean(edit) || busy })),
            h('div', { className: 'ribbon-tools', role: 'tabpanel', 'aria-label': ribbon }, ...commands[ribbon].map((item, i) =>
                window.React.cloneElement(item, { key: i })))),
        h('div', { className: 'map-workspace-body' },
            h('aside', { className: 'map-layers', 'aria-label': '图层和图例' },
                h('div', { className: 'map-layer-head' }, h('strong', null, glyph('layers'), '图层'),
                    command('添加图层', () => load(api.mapImport()), { disabled: Boolean(edit) || busy })),
                layers.length > 5 && h(Input, { size: 'small', placeholder: '查找图层', 'aria-label': '查找图层',
                    allowClear: true, value: layerSearch, onChange: event => setLayerSearch(event.target.value) }),
                ...layers.map((item, index) => (!layerSearch || item.name.toLowerCase().includes(layerSearch.toLowerCase())) &&
                    h('div', { className: 'map-layer-row', key: key(item) },
                    h('input', { type: 'checkbox', 'aria-label': '显示 ' + item.name, checked: !hidden.includes(key(item)), disabled: Boolean(edit),
                        onChange: () => toggle(key(item)) }),
                    h('span', { className: 'map-swatch', 'data-color': index % palette.length }),
                    h(Button, { type: 'text', className: key(item) === active ? 'map-layer-active' : '',
                        title: item.name, disabled: Boolean(edit),
                        onClick: () => { setPage(0); setEditLayer(null); selectLayer(key(item)); } }, item.name),
                    h(antd.Dropdown, { trigger: ['click'], menu: { items: [
                        { key: 'fit', label: '缩放到图层' }, { key: 'table', label: '打开属性表', disabled: item.kind === 'raster' },
                        { key: 'edit', label: '定位编辑工具', disabled: item.kind === 'raster' },
                        { key: 'remove', label: '从地图移除', danger: true },
                    ], onClick: ({ key: action }) => {
                        const id = key(item);
                        if (action === 'remove') { remove(id); return; }
                        selectLayer(id);
                        if (action === 'table') { setPage(0); setTable([]); }
                        else if (action === 'edit') setRibbon('编辑');
                        else {
                            const g = graph.current, drawing = g?.byKey.get(id);
                            if (drawing) fit(g.view, [drawing], g.ol.getSize());
                        }
                    } } }, h(Button, { type: 'text', size: 'small', disabled: Boolean(edit),
                        'aria-label': item.name + '菜单', icon: glyph('more', 16) }))))),
            h('div', { className: 'map-viewport' },
                h('div', { className: 'geo-map', ref: node, tabIndex: 0, 'aria-label': '交互地图' }),
                !layers.length && h('div', { className: 'map-empty' }, '添加或拖入空间数据'),
                (issue || limited.length > 0) && h('div', { className: 'map-notice', role: 'status' },
                    issue || '当前视野要素较多，请放大地图后查看。'))),
        h('footer', { className: 'map-status' },
            h('span', null, layers.length + ' 个图层'),
            layer && h('span', null, layer.name),
            picked.length > 0 && h('span', null, '已选 ' + picked.length + ' 个要素'),
            h('span', { className: 'map-status-crs' }, glyph('globe', 14), h('span', null, display?.label || native?.crsName || '未设置坐标系'))),
        table && layer && layer.kind !== 'raster' && h('section', { className: 'map-attribute-panel', 'aria-label': '属性表' },
            h('header', { className: 'map-attribute-head' }, h('strong', null, glyph('table'), '属性表'),
                h('span', null, layer.name), h(Button, { type: 'text', size: 'small', icon: glyph('close', 15),
                    'aria-label': '关闭属性表', onClick: () => setTable(null) })),
            h(Table, { className: 'map-attributes', size: 'small', columns, rowKey: 'id', dataSource: tableLayer === active ? table : [],
            scroll: { x: 'max-content', y: 130 }, rowSelection: { selectedRowKeys: picked, preserveSelectedRowKeys: true,
                onChange: ids => setSelection({ key: active, ids }) },
            pagination: { current: page + 1, pageSize: 50, total: layer.count,
                showSizeChanger: false, onChange: value => setPage(value - 1) } })),
        analysis && h(Modal, { open: true, centered: true, width: 440, className: 'gis-dialog', rootClassName: 'gis-modal-root',
            title: h('span', { className: 'gis-dialog-title' }, glyph(analysis.kind), { area: '面积汇总', buffer: '缓冲分析', clip: '裁切', count: '要素计数' }[analysis.kind]),
            okText: analysis.result ? '完成' : '运行', cancelText: '取消', confirmLoading: busy,
            onCancel: () => { if (!busy) setAnalysis(null); },
            okButtonProps: { disabled: (analysis.kind === 'clip' && !analysis.mask) ||
                (analysis.kind === 'buffer' && (!analysis.distance.trim() || !Number.isFinite(Number(analysis.distance)) || Number(analysis.distance) <= 0)) },
            onOk: async () => {
                if (analysis.result) { setAnalysis(null); return; }
                setBusy(true);
                try {
                    const result = await api.map({ action: 'analyze', ref: layer.ref, kind: analysis.kind,
                        distance: Number(analysis.distance), mask: layers.find(item => key(item) === analysis.mask)?.ref });
                    if (result.layer) { add([{ ...result.layer, name: layer.name + ' · ' + result.title }]); setAnalysis(null); }
                    else setAnalysis({ ...analysis, result });
                } catch (error) {
                    setAnalysis({ ...analysis, error: window.GuizhiErrorText(error) });
                } finally { setBusy(false); }
            } }, h('div', { className: 'map-field-editor' },
                h('div', { className: 'gis-input-layer' }, glyph('layers'),
                    h('div', null, h('small', null, '输入图层'), h('strong', null, layer.name))),
                analysis.kind === 'buffer' && h('label', null, '缓冲距离（米）',
                    h(Input, { type: 'number', min: 0, value: analysis.distance,
                        onChange: event => setAnalysis({ ...analysis, distance: event.target.value, error: null }) })),
                analysis.kind === 'clip' && h(Choice, { value: analysis.mask, placeholder: '选择裁切范围',
                    'aria-label': '裁切范围', options: layers.filter(item => key(item) !== active && item.geometry?.includes('Polygon'))
                        .map(item => ({ value: key(item), label: item.name })),
                    onChange: mask => setAnalysis({ ...analysis, mask, error: null }) }),
                analysis.error && h('p', { className: 'error', role: 'alert' }, analysis.error),
                analysis.result && h('p', { role: 'status', className: 'gis-analysis-result' }, analysis.result.area_m2 === undefined
                    ? '要素数 ' + analysis.result.feature_count
                    : '合并面积 ' + analysis.result.area_m2.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' 平方米'))),
        fieldEdit && h(Modal, { open: true, centered: true, className: 'gis-dialog', rootClassName: 'gis-modal-root',
            title: h('span', { className: 'gis-dialog-title' }, glyph('table'), '编辑属性'), okText: '应用', cancelText: '取消',
            onCancel: () => setFieldEdit(null), onOk: () => {
                if (Object.keys(fieldEdit.changed).length) push([{ action: 'update', id: fieldEdit.fid, fields: fieldEdit.changed }]);
                setFieldEdit(null);
            } }, h('div', { className: 'map-field-editor' }, ...layer.fields.map(({ name }) => h('label', { key: name }, name,
                h(Input, { value: String(fieldEdit.values[name] ?? ''), onChange: event => {
                    const value = event.target.value;
                    setFieldEdit(old => ({ ...old, values: { ...old.values, [name]: value },
                        changed: { ...old.changed, [name]: value === '' ? null : value } }));
                } }))))));
}

window.GuizhiMap = { Preview, WorkspaceMap, key };
