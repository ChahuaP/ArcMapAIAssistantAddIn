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
import { Fill, Stroke, Style, Circle } from 'ol/style.js';
import { createEmpty, extend, isEmpty } from 'ol/extent.js';

const h = window.React.createElement;
const { useEffect, useRef, useState } = window.React;
const { Button, Input, Select: Choice, Table, Modal } = window.antd;
const api = window.guizhi;
const format = new GeoJSON();
const palette = ['#1785db', '#bf6f2b', '#17a489', '#8263ca', '#cc5785'];
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

function CoordinateTools({ layer, display, setDisplay, entries, request, onLayer, fail }) {
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
        wkt: entry.definition.wkt, units: entry.definition.axes?.[0]?.unit === 'degree' ? 'degrees' : 'm' })), ...standard];
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
            h(Choice, { value: plan || undefined, placeholder: '选择已保存转换方案', 'aria-label': '选择已保存转换方案',
                options: plans.map(entry => ({ value: entry.id + '@' + entry.revision, label: entry.title })), onChange: setPlan }),
            h(Button, { disabled: !plan, onClick: () => request({ action: 'transform', ref: layer.ref, plan }).then(onLayer).catch(fail) },
                '转换并保存')));
}

function WorkspaceMap({ layers, add, remove, selectLayer, active, hidden, toggle, entries, fail, close, selection, setSelection, useSelection, replace, editing }) {
    const node = useRef(null);
    const graph = useRef(null);
    const [display, setDisplay] = useState(null);
    const [editLayer, setEditLayer] = useState(null);
    const [edit, setEdit] = useState('');
    useEffect(() => { editing(Boolean(edit)); return () => editing(false); }, [Boolean(edit)]);
    const [count, setCount] = useState(0);
    const [fieldEdit, setFieldEdit] = useState(null);
    const edits = useRef([]);
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
    const units = display?.units || native?.units;
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
                const visible = !target || target === item.crs;
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
                target: target || null, plan: plan && plan.id + '@' + plan.revision })
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
    }, [layers, hidden, target, units, viewRevision, entries, Boolean(edit)]);

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
            draw.on('drawend', event => {
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
        return () => { for (const interaction of interactions) g.ol.removeInteraction(interaction); };
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

    async function sendSelection() {
        setBusy(true);
        try {
            const item = await api.map({ action: 'selection', ref: layer.ref, revision: layer.revision, fids: picked, name: layer.name });
            await useSelection(item, layers.filter(candidate => key(candidate) !== active && !hidden.includes(key(candidate))));
        } catch (error) { fail(error); }
        finally { setBusy(false); }
    }

    const columns = [{ title: '要素', dataIndex: 'id', width: 75 },
        ...(layer?.fields || []).map(field => ({ title: field.name, render: (_, row) => String(row.values[field.name] ?? '—') }))];
    return h('section', { className: 'workspace-map', 'aria-label': '地图工作区', onDragOver: event => event.preventDefault(),
        onDrop: event => { event.preventDefault(); if (!busy && !edit) void load(api.mapDrop([...event.dataTransfer.files])); } },
        h('header', { className: 'map-head' }, h('h2', null, '地图工作区'),
            h(Button, { loading: busy, disabled: Boolean(edit), onClick: () => load(api.mapImport()) }, '添加图层'),
            h(Button, { disabled: Boolean(edit) || busy, onClick: close }, '返回对话')),
        h('div', { className: 'map-workspace-body' },
            h('aside', { className: 'map-layers', 'aria-label': '图层和图例' },
                ...layers.map((item, index) => h('div', { className: 'map-layer-row', key: key(item) },
                    h('input', { type: 'checkbox', 'aria-label': '显示 ' + item.name, checked: !hidden.includes(key(item)), disabled: Boolean(edit),
                        onChange: () => toggle(key(item)) }),
                    h('span', { className: 'map-swatch', 'data-color': index % palette.length }),
                    h(Button, { type: key(item) === active ? 'primary' : 'text', title: item.name, disabled: Boolean(edit),
                        onClick: () => { setPage(0); setEditLayer(null); selectLayer(key(item)); } }, item.name),
                    h(Button, { type: 'text', size: 'small', disabled: Boolean(edit),
                        'aria-label': '移除 ' + item.name, title: '从地图移除', onClick: () => remove(key(item)) }, '×'))),
                !edit && h(CoordinateTools, { layer, display, setDisplay, entries, request: api.map, onLayer: item => add([item]), fail })),
            h('div', { className: 'map-viewport' },
                h('div', { className: 'geo-map', ref: node, tabIndex: 0, 'aria-label': '交互地图' }),
                !layers.length && h('div', { className: 'map-empty' }, '添加或拖入空间数据'),
                (issue || limited.length > 0) && h('div', { className: 'map-notice', role: 'status' },
                    issue || '当前视野要素较多，请放大地图后查看。'),
                h('div', { className: 'map-controls' },
                    h(Button, { size: 'small', onClick: () => { const g = graph.current; fit(g.view, [...g.byKey.values()], g.ol.getSize()); } }, '定位图层'),
                    h(Button, { size: 'small', disabled: !layer || layer.kind === 'raster', onClick: () => { setPage(0); setTable(table ? null : []); } }, '属性表'),
                    h(Button, { size: 'small', disabled: !layer?.crs || layer.kind === 'raster' || Boolean(edit), onClick: () => api.mapExport(layer.ref).catch(fail) }, '导出 SHP'),
                    !edit && h(Button, { size: 'small', disabled: !editable || busy || limited.includes(active), onClick: startEdit }, '编辑图层'),
                    !edit && picked.length > 0 && h(Button, { size: 'small', type: 'primary', loading: busy,
                        onClick: sendSelection }, '用选区办理 · ' + picked.length)),
                edit && h('div', { className: 'map-controls', 'aria-label': '图层编辑' },
                    ...[['select', '选择'], ['draw', '绘制'], ['modify', '改节点'], ['move', '移动']].map(([mode, title]) =>
                        h(Button, { size: 'small', key: mode, disabled: busy || (['modify', 'move'].includes(mode) && !picked.length),
                            type: edit === mode ? 'primary' : 'default', onClick: () => setEdit(mode) }, title)),
                    h(Button, { size: 'small', disabled: busy || picked.length !== 1 || !layer.fields.length, onClick: openFields }, '编辑属性'),
                    h(Button, { size: 'small', disabled: busy || !picked.length, onClick: () => {
                        push(picked.map(id => ({ action: 'delete', id }))); applyDraft();
                        setSelection({ key: active, ids: [] }); setEdit('select');
                    } }, '删除要素'),
                    h(Button, { size: 'small', disabled: busy || !count, onClick: () => {
                        edits.current.pop(); setCount(edits.current.length); applyDraft();
                    } }, '撤销'),
                    h(Button, { size: 'small', disabled: busy || !count, loading: busy, type: 'primary', onClick: saveEdit }, '保存新版本'),
                    h(Button, { size: 'small', disabled: busy, onClick: () => { edits.current = []; applyDraft(); stopEdit(); } }, '退出编辑')))),
        table && layer && layer.kind !== 'raster' && h(Table, { className: 'map-attributes', size: 'small', columns, rowKey: 'id', dataSource: tableLayer === active ? table : [],
            scroll: { x: 'max-content', y: 130 }, rowSelection: { selectedRowKeys: picked, preserveSelectedRowKeys: true,
                onChange: ids => setSelection({ key: active, ids }) },
            pagination: { current: page + 1, pageSize: 50, total: layer.count,
                showSizeChanger: false, onChange: value => setPage(value - 1) } }),
        fieldEdit && h(Modal, { open: true, title: '编辑属性', okText: '应用', cancelText: '取消',
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
