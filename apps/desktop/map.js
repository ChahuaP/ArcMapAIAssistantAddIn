import 'ol/ol.css';
import { MapProcessing, TOOL_GROUPS, roleLayerPicker } from './map-processing.js';
import { MapLayout, layoutDefaults, mapImage } from './map-layout.js';
import { LayerProperties, MapProperties, MxdOpen, categoryValue, defaultMapBackground, layerSymbol, opacity, symbol } from './map-layer-dialog.js';
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
import { Fill, Stroke, Style, Circle, RegularShape } from 'ol/style.js';
import { createEmpty, extend, isEmpty } from 'ol/extent.js';
import { getPointResolution } from 'ol/proj.js';

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
    chevron: 'm6 9 6 6 6-6',
    check: 'm5 12 4 4L19 6',
    chat: 'M4 5h16v12H9l-5 4zM8 9h8m-8 4h5',
    more: 'M5 12h.1m6.9 0h.1m6.9 0h.1',
};
const commandIcons = {
    全图: 'extent', 定位图层: 'target', 放大: 'zoomIn', 缩小: 'zoomOut', 清除选择: 'clear', 按属性选择: 'select', 按位置选择: 'target', 选中要素另存为图层: 'download',
    属性表: 'table', 添加图层: 'add', 导出属性表: 'table', 最近成果: 'table', 添加字段: 'add', 删除字段: 'trash', 修复几何: 'pencil', 新建图层: 'add', 复制图层: 'layers', 合并图层: 'layers', '导出 SHP': 'download', 坐标系设置: 'globe', 地图属性: 'map', '打开 MXD': 'map', '保存 MXD': 'save',
    编辑图层: 'pencil', 选择: 'select', 绘制: 'pencil', 编辑顶点: 'node', 移动: 'move',
    完成绘制: 'check', 撤销绘制点: 'undo', 编辑属性: 'table', 删除要素: 'trash',
    撤销: 'undo', 保存新版本: 'save', 退出编辑: 'close', 面积汇总: 'area', 缓冲分析: 'buffer',
    裁切: 'clip', 要素计数: 'count', 打开: 'map', 保存: 'save', 字段计算: 'analysis',
};
const tabIcons = { 地图: 'map', 数据: 'layers', 编辑: 'pencil', 分析: 'analysis', 制图: 'map', 业务: 'analysis' };
function glyph(name, size = 17) {
    return h('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none',
        stroke: 'currentColor', strokeWidth: name === 'more' ? 3 : 1.65, strokeLinecap: 'round',
        strokeLinejoin: 'round', 'aria-hidden': true }, h('path', { d: glyphs[name] }));
}

const roles = { project: '项目范围', coverage: '重叠范围' };
const key = item => item.ref.run + ':' + item.ref.artifact;
const projection = (code, units) => new Projection({ code: code || 'local', units: units || 'm' });
const fillColor = (color, percent) => color + Math.round(percent * 255 / 100).toString(16).padStart(2, '0');
const classFill = (settings, color, fill) => settings.fill === null || fill === null ? null : fill || color;
const feature = item => format.readFeature({ type: 'Feature', ...item });
const style = (color, selected, settings) => {
    const appearance = symbol(settings, color);
    const lineDash = appearance.pattern === 'dash' ? [9, 5] : appearance.pattern === 'dot' ? [2, 4] : undefined;
    const stroke = !appearance.noOutline || selected ? new Stroke({ color: selected ? '#f7be49' : appearance.color,
        width: selected ? 3 : appearance.width, lineDash: selected ? undefined : lineDash }) : undefined;
    const fill = appearance.fill || selected
        ? new Fill({ color: selected ? '#f7be494e' : fillColor(appearance.fill, appearance.fillOpacity) }) : undefined;
    const point = { radius: selected ? 6 : appearance.size / 2,
        fill: new Fill({ color: selected ? '#f7be49' : appearance.color }),
        stroke: new Stroke({ color: selected ? '#522d0c' : appearance.color, width: 1 }) };
    const shape = appearance.shape;
    const image = shape === 'circle' ? new Circle(point) : new RegularShape({ ...point,
        points: 4, radius2: shape === 'cross' ? 0 : undefined, angle: shape === 'square' ? Math.PI / 4 : 0 });
    return new Style({ stroke, fill, image });
};
const layerFor = (items, color) => new VectorLayer({
    source: new VectorSource({ features: items.map(feature) }), style: style(color, false),
});
function classSwatch(item, entry) {
    if (!/Polygon$/.test(item.geometry)) return { backgroundColor: entry.color };
    const fill = classFill(item.style, entry.color, entry.fillColor);
    return { backgroundColor: fill ? fillColor(fill, opacity(item.style.fillOpacity)) : 'transparent',
        borderColor: item.style.noOutline ? 'transparent' : entry.color };
}

function attach(node, view, layers) {
    return new Map({ target: node, view, layers, controls: [], pixelRatio: Math.min(devicePixelRatio, 2) });
}

function fit(view, layers, size) {
    const extent = createEmpty();
    for (const layer of layers) {
        if (!layer.getVisible()) continue;
        const bounds = layer.get('dataExtent') || layer.getSource()?.getExtent?.() || layer.getExtent();
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
            open && h(Button, { size: 'small', onClick: open }, '展开地图')),
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
    const catalog = [...definitions.map(entry => ({ value: entry.id + '@' + entry.revision, label: entry.title,
        wkt: entry.definition.wkt, units: { degree: 'degrees', metre: 'm', meter: 'm' }[entry.definition.axes?.[0]?.unit] })),
        ...standard.filter(item => !definitions.some(entry => {
            const identifier = entry.definition.projjson?.id;
            return (entry.definition.authority || (identifier && identifier.authority + ':' + identifier.code)) === item.value;
        }))];
    const options = [
        ...['地理坐标系', '投影坐标系'].map(label => ({ label, options: catalog.filter(item => (item.kind ||
            (item.units === 'degrees' ? '地理坐标系' : '投影坐标系')) === label) })).filter(group => group.options.length),
    ];
    const [destination, setDestination] = useState(null);
    useEffect(() => setDestination(null), [layer]);
    const plans = entries.filter(entry => entry.kind === 'plan');
    return h('div', { className: 'map-coordinate' },
        h('span', null, layer?.crsName || '尚未添加图层'),
        h(Choice, { showSearch: true, filterOption: false, allowClear: true, onSearch: search,
            placeholder: '地图显示坐标系', 'aria-label': '地图显示坐标系',
            options: [...options, ...(display?.value && !catalog.some(item => item.value === display.value) ? [display] : [])],
            value: display?.value, onChange: (value, item) => setDisplay(value ? item : null) }),
        layer && h('div', { className: 'map-coordinate-actions' },
            h(Choice, { showSearch: true, filterOption: false, onSearch: search, options,
                value: destination?.value, placeholder: layer.crs ? '选择目标坐标系' : '选择图层已有坐标系',
                'aria-label': layer.crs ? '目标坐标系' : '图层已有坐标系', onChange: (value, item) => setDestination(item) }),
            h(Button, { disabled: !destination, onClick: () => request({ action: layer.crs ? 'reproject' : 'assign',
                ref: layer.ref, crs: destination.value }).then(onLayer).catch(fail) }, layer.crs ? '转换投影' : '定义坐标系')),
        layer?.crs && layer.kind !== 'raster' && plans.length > 0 && h('div', { className: 'map-coordinate-actions' },
            display && h(Choice, { value: displayPlan || undefined, placeholder: '为该图层选择显示转换',
                'aria-label': '显示转换方案', allowClear: true, onChange: setDisplayPlan,
                options: plans.map(entry => ({ value: entry.id + '@' + entry.revision, label: entry.title })) }),
            h(Choice, { value: plan || undefined, placeholder: '选择已保存转换方案', 'aria-label': '选择已保存转换方案',
                options: plans.map(entry => ({ value: entry.id + '@' + entry.revision, label: entry.title })), onChange: setPlan }),
            h(Button, { disabled: !plan, onClick: () => request({ action: 'transform', ref: layer.ref, plan }).then(onLayer).catch(fail) },
                '转换并保存')));

}

function WorkspaceMap({ layers, add, remove, selectLayer, active, hidden, toggle, entries, catalog, components,
    openComponents, display, setDisplay, background, setBackground, styleLayer, importFrame, fail, activity,
    requestedLayout, onLayout, mapActions, onMapActions, selection, setSelection, replace, editing }) {
    const node = useRef(null);
    const graph = useRef(null);
    const [properties, setProperties] = useState(null);
    const [mapProperties, setMapProperties] = useState(false);
    const [layout, setLayout] = useState(false);
    const [document, setDocument] = useState(null);
    const [cursor, setCursor] = useState(null);
    const [scale, setScale] = useState(null);
    const [viewReady, setViewReady] = useState(false);
    const [ioStatus, setIoStatus] = useState('');
    const [importIssues, setImportIssues] = useState([]);
    const [displayPlans, setDisplayPlans] = useState({});
    const [editLayer, setEditLayer] = useState(null);
    const [edit, setEdit] = useState('');
    const [ribbon, setRibbon] = useState('地图');
    const [layerSearch, setLayerSearch] = useState('');
    const [expandedLegends, setExpandedLegends] = useState({});
    const [analysis, setAnalysis] = useState(null);
    const [lastResult, setLastResult] = useState(null);
    const [count, setCount] = useState(0);
    const [fieldEdit, setFieldEdit] = useState(null);
    const edits = useRef([]);
    const drawingTool = useRef(null);
    const [drawingPoints, setDrawingPoints] = useState(0);
    const original = useRef(new globalThis.Map());
    const [busy, setBusy] = useState(false);
    const exportedLayout = useRef(null);
    const appliedActions = useRef(null);
    const [importing, setImporting] = useState(null);
    useEffect(() => api.onMapImportProgress(setImporting), []);
    useEffect(() => { editing(Boolean(edit) || busy); return () => editing(false); }, [Boolean(edit), busy]);
    const [issue, setIssue] = useState('');
    const [layerErrors, setLayerErrors] = useState([]);
    const [locateMxd, setLocateMxd] = useState(null);
    const [table, setTable] = useState(null);
    const [tableLayer, setTableLayer] = useState(null);
    const [page, setPage] = useState(0);
    const [sort, setSort] = useState({});
    const [limited, setLimited] = useState([]);
    const [viewRevision, setViewRevision] = useState(0);
    const selectionRef = useRef(selection);
    selectionRef.current = selection;
    const layer = layers.find(item => key(item) === active);
    const focused = item => [activity?.target, ...(activity?.targets || [])].filter(Boolean).some(name =>
        [item.name, item.sourceName?.split(/[\\/]/).at(-1)].includes(name));
    const native = editLayer || layers.find(item => item.crs && item.kind !== 'raster') || layers.find(item => item.crs) || layers[0];
    const target = display?.wkt || display?.value || native?.crs;
    const units = display ? display.units : native?.units;
    const metresPerUnit = display ? display.metresPerUnit : native?.metresPerUnit;
    const initialExtent = display?.bounds;
    const backgroundColor = background || defaultMapBackground;
    const activeRef = useRef(active);
    activeRef.current = active;
    async function exportImage(config, taskId) {
        setBusy(true);
        try {
            const legend = layers.flatMap((item, index) => {
                if (hidden.includes(key(item))) return [];
                const appearance = symbol(item.style, palette[index % palette.length]);
                const classification = item.style?.classification;
                if (classification) return classification.classes.map(entry => ({
                    name: entry.label || entry.value || '空值', color: entry.color,
                    fill: classFill(appearance, entry.color, entry.fillColor),
                }));
                return [{ name: item.name, color: appearance.color,
                    fill: /Polygon/.test(item.geometry || '') ? appearance.fill : appearance.color }];
            });
            const result = await api.mapLayout({ format: config.format,
                data: mapImage(graph.current.ol, node.current, backgroundColor, legend, config), taskId });
            if (result) setLayout(false);
        } catch (error) { if (taskId) await onLayout(taskId, window.GuizhiErrorText(error)); else fail(error); }
        finally { setBusy(false); }
    }
    useEffect(() => {
        if (!requestedLayout || mapActions || !viewReady || busy || !node.current?.clientWidth ||
                exportedLayout.current === requestedLayout.taskId) return;
        exportedLayout.current = requestedLayout.taskId;
        void exportImage(requestedLayout.layout, requestedLayout.taskId);
    }, [requestedLayout, mapActions, viewReady, busy, layers]);
    useEffect(() => {
        if (!mapActions || !viewReady || busy || edit || !node.current?.clientWidth || appliedActions.current === mapActions.id) return;
        appliedActions.current = mapActions.id;
        const map = graph.current;
        try {
            const actions = mapActions.actions.map(action => {
                const matches = action.layer ? layers.filter(item => item.name === action.layer) : [];
                if (action.layer && matches.length !== 1) throw new Error('请确认地图中的图层名称唯一：' + action.layer);
                const item = matches[0];
                if (action.action === 'table' && (item.kind === 'raster' || action.field && !item.fields.some(field => field.name === action.field))) {
                    throw new Error('图层或排序字段没有可用的属性表：' + action.layer);
                }
                if (action.action === 'symbol' && item.kind === 'raster') throw new Error('影像图层不能使用矢量符号');
                return { action, item };
            });
            const visibility = new Set(hidden);
            for (const { action, item } of actions) {
                const id = item && key(item);
                switch (action.action) {
                    case 'background': setBackground(action.color); break;
                    case 'visibility':
                        if (visibility.has(id) === action.visible) {
                            toggle(id);
                            if (action.visible) visibility.delete(id); else visibility.add(id);
                        }
                        break;
                    case 'symbol': {
                        const appearance = layerSymbol(item.style, palette[layers.indexOf(item) % palette.length]);
                        if (appearance.classification && (action.color || action.fill)) {
                            const classification = appearance.classification;
                            appearance.classification = { ...classification,
                                classes: classification.classes.map(entry => ({ ...entry, ...(action.color && { color: action.color }),
                                    ...(action.fill && { fillColor: action.fill }) })),
                                ...(action.color && { other: action.color }), ...(action.fill && { otherFill: action.fill }) };
                        }
                        styleLayer(id, { ...appearance, ...(action.color && { color: action.color }),
                            ...(action.fill && { fill: action.fill }), ...(action.fill_opacity !== undefined && { fillOpacity: action.fill_opacity }) });
                        break;
                    }
                    case 'table':
                        selectLayer(id);
                        setPage(0);
                        setSort(action.field ? { key: id, field: action.field,
                            direction: action.direction === 'descending' ? 'descend' : 'ascend' } : {});
                        setTable([]);
                        break;
                    case 'zoom': {
                        const drawing = item && map.byKey.get(id);
                        const extent = drawing?.get('dataExtent') || (item?.crs === target ? item?.bounds : null);
                        if (extent) map.view.fit(extent, { size: map.ol.getSize(), padding: [28, 28, 28, 28], maxZoom: 20 });
                        else if (!item) fit(map.view, [...map.byKey.values()], map.ol.getSize());
                        else throw new Error('图层尚无可定位的范围：' + action.layer);
                        break;
                    }
                }
            }
        } catch (error) { fail(error); }
        finally { onMapActions(); }
    }, [mapActions, viewReady, busy, edit, layers]);

    async function load(job) {
        setBusy(true);
        setImporting({ label: '正在读取数据' });
        setIoStatus('');
        setImportIssues([]);
        try {
            const { layers: items, issues } = await job;
            if (items.length) graph.current.fitNeeded = true;
            add(items);
            setImportIssues(issues);
        } catch (error) { setIoStatus(window.GuizhiErrorText(error)); }
        finally { setBusy(false); setImporting(null); }
    }

    async function mxd(job, accept) {
        setBusy(true);
        setIssue('');
        setIoStatus('');
        try {
            const result = await job;
            if (result) accept(result);
        } catch (error) {
            const text = String(error?.message || error).replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '');
            setIoStatus('地图工程操作失败。' + text);
        } finally { setBusy(false); }
    }

    useEffect(() => {
        const view = new View({ projection: projection(target, units), center: [0, 0], zoom: 0 });
        const ol = attach(node.current, view, []);
        setViewReady(false);
        setScale(null);
        const updateScale = () => {
            if (!target) { setScale(null); return; }
            const resolution = view.getResolution();
            const metres = units === 'degrees' ? getPointResolution(view.getProjection(), resolution, view.getCenter(), 'm')
                : resolution * (metresPerUnit || (units === 'm' ? 1 : NaN));
            setScale(Number.isFinite(metres) && metres > 0 ? Math.max(1, Math.round(metres / .0002645833333)) : null);
        };
        const track = event => { if (!event.dragging) setCursor(event.coordinate); };
        const clearCursor = () => setCursor(null);
        ol.on('pointermove', track);
        node.current.addEventListener('pointerleave', clearCursor);
        setCursor(null);
        if (units === 'm' || units === 'degrees') ol.addControl(new ScaleLine({ units: 'metric' }));
        const select = new Select({ condition: click, layers: candidate => candidate.get('key') === activeRef.current,
            style: style(palette[0], true) });
        ol.addInteraction(select);
        select.on('select', () => setSelection({ key: activeRef.current, ids: select.getFeatures().getArray().map(item => item.getId()) }));
        graph.current = { ol, view, select, byKey: new globalThis.Map(), fitNeeded: true, initialExtent, updateScale };
        const observer = new ResizeObserver(() => ol.updateSize());
        observer.observe(node.current);
        const moved = () => { if (!graph.current?.fitNeeded) updateScale(); setViewRevision(value => value + 1); };
        ol.on('moveend', moved);
        const viewport = node.current;
        return () => { viewport.removeEventListener('pointerleave', clearCursor); observer.disconnect(); ol.dispose(); graph.current = null; };
    }, [target, units, metresPerUnit, initialExtent]);

    useEffect(() => {
        const state = graph.current;
        if (!state || edit) return;
        let alive = true;
        const previous = state.byKey;
        const next = new globalThis.Map();
        const layerKeys = layers.map(key);
        for (const [id, existing] of previous) {
            if (layerKeys.includes(id)) continue;
            const fillLayer = existing.get('fillLayer');
            if (fillLayer) state.ol.removeLayer(fillLayer);
            state.ol.removeLayer(existing);
        }
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
            const order = 2 * (layers.length - index);
            const layerOpacity = opacity(item.style?.opacity ?? item.opacity) / 100;
            const visible = !hidden.includes(id);
            if (item.kind !== 'raster') {
                const color = palette[index % palette.length];
                const appearance = symbol(item.style, color);
                const classification = item.style?.classification;
                const field = classification?.field;
                const render = settings => {
                    if (!field) return style(color, false, settings);
                    const classStyle = (shade, fill) => style(shade, false, { ...settings, color: shade,
                        fill: classFill(settings, shade, fill) });
                    const byValue = new globalThis.Map(classification.classes.map(entry =>
                        [entry.value, classStyle(entry.color, entry.fillColor)]));
                    const other = classStyle(classification.other, classification.otherFill);
                    return feature => byValue.get(categoryValue(feature.get(field))) || other;
                };
                let fillLayer = drawing.get('fillLayer');
                if (/Polygon$/.test(item.geometry) && appearance.fill && appearance.fillOpacity < 100) {
                    // ArcGIS blends the polygon fill once per layer and draws outlines separately.
                    if (!fillLayer) {
                        fillLayer = new VectorLayer({ source: drawing.getSource() });
                        drawing.set('fillLayer', fillLayer);
                        state.ol.addLayer(fillLayer);
                    }
                    fillLayer.setStyle(render({ ...appearance, fillOpacity: 100, noOutline: true }));
                    fillLayer.setOpacity(layerOpacity * appearance.fillOpacity / 100);
                    fillLayer.setZIndex(order - 1);
                    fillLayer.setVisible(visible);
                    drawing.setStyle(render({ ...appearance, fillOpacity: 0 }));
                } else {
                    if (fillLayer) {
                        state.ol.removeLayer(fillLayer);
                        drawing.unset('fillLayer');
                    }
                    drawing.setStyle(render(appearance));
                }
            }
            drawing.setOpacity(layerOpacity);
            drawing.setZIndex(order);
            drawing.setVisible(visible);
            next.set(id, drawing);
            if (hidden.includes(id)) return null;
            if (item.kind === 'raster') {
                if (drawing.getSource()) return Promise.resolve({ id, drawing, raster: true });
                const nativeRaster = !target || target === item.crs;
                const request = nativeRaster ? api.map({ action: 'raster-preview', ref: item.ref }).then(image => ({ image, bounds: item.bounds }))
                    : api.map({ action: 'raster-display', ref: item.ref, target });
                return request.then(({ image, bounds }) => ({ id, drawing, raster: true, bounds,
                    image: new Static({ url: 'data:image/png;base64,' + image, imageExtent: bounds,
                        projection: state.view.getProjection() }) })).catch(error => ({ id, drawing, raster: true, error }));
            }
            if (!item.crs && target) {
                drawing.getSource().clear();
                return Promise.resolve({ id, drawing, error: new Error('请先定义图层坐标系') });
            }
            const plan = entries.find(entry => entry.kind === 'plan' &&
                (entry.plan.source_wkt === item.crs || entry.source_reference === item.crs) &&
                (entry.plan.target_wkt === target || entry.target_reference === display?.value));
            const overviewTolerance = item.bounds && Math.max(item.bounds[2] - item.bounds[0], item.bounds[3] - item.bounds[1]) / 2000;
            return api.map({ action: 'features', ref: item.ref, bbox, tolerance: item.count > 8000
                ? (bbox ? state.view.getResolution() * .5 : (!target || target === item.crs) ? overviewTolerance : undefined) : undefined,
                target: target || null, fields: item.style?.classification ? [item.style.classification.field] : [], plan: displayPlans[id] || (plan && plan.id + '@' + plan.revision) })
                .then(result => ({ id, drawing, result })).catch(error => ({ id, drawing, error }));
        }).filter(Boolean);
        state.byKey = next;
        Promise.all(jobs).then(items => {
            if (!alive) return;
            const errors = new globalThis.Map();
            for (const item of items.filter(item => item.error)) {
                const message = window.GuizhiErrorText(item.error);
                const affected = errors.get(message) || new Set();
                affected.add(layers.find(layer => key(layer) === item.id)?.name || '图层');
                errors.set(message, affected);
            }
            setLayerErrors([...errors].map(([message, affected]) => ({ message, names: [...affected] })));
            setLimited(items.filter(item => item.result?.limited).map(item => item.id));
            for (const item of items) {
                if (item.raster) {
                    if (item.image) {
                        item.drawing.setExtent(item.bounds);
                        item.drawing.set('dataExtent', item.bounds);
                        item.drawing.setSource(item.image);
                    }
                    continue;
                }
                item.drawing.getSource().clear();
                if (item.result) {
                    item.drawing.set('dataExtent', item.result.bounds);
                    item.drawing.getSource().addFeatures(item.result.features.map(feature));
                }
            }
            if (state.fitNeeded && items.length) {
                state.fitNeeded = false;
                const drawn = [...next.values()];
                if (state.initialExtent && state.ol.getSize()?.every(size => size > 0)) {
                    state.view.fit(state.initialExtent, { size: state.ol.getSize(), padding: [12, 12, 12, 12] });
                    state.initialExtent = null;
                } else if (drawn.some(item => item.getVisible() && (item instanceof ImageLayer ? Boolean(item.getSource()) : !isEmpty(item.getSource().getExtent())))) fit(state.view, drawn, state.ol.getSize());
                else if (native?.bounds && native.crs === target) state.view.fit(native.bounds, { maxZoom: 20, padding: [28, 28, 28, 28] });
                state.updateScale();
                setViewReady(true);
            }
            restoreSelection(state, selectionRef.current);
        });
        return () => { alive = false; };
    }, [layers, hidden, target, units, initialExtent, viewRevision, entries, displayPlans, Boolean(edit)]);

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
        const order = sort.key === key(layer) ? sort : {};
        api.map({ action: 'table', ref: layer.ref, page, sort: order.field, direction: order.direction }).then(result => {
            if (alive) { setTable(result.rows); setTableLayer(key(layer)); }
        }).catch(error => { if (alive) fail(error); });
        return () => { alive = false; };
    }, [layer, page, sort, Boolean(table)]);

    const picked = selection?.key === active ? selection.ids : [];
    const openTool = tool => setAnalysis({ tool });
    const skillGroups = [
        ['规划与用地', ['control-check', 'farmland-balance', 'annual-land', 'permit-history']],
        ['数据与影像', ['shared-delivery', 'cad-coordinates', 'imagery-change']],
    ];
    const customSkills = catalog.filter(item => !skillGroups.some(([, ids]) => ids.includes(item.id)));
    const menuGroup = (title, icon, items, onClick) => h('section', { className: 'ribbon-group ribbon-menu' }, h(antd.Dropdown, {
        trigger: ['click'], menu: { items: items.map(item => ({ key: item.id, label: item.title, icon: glyph(item.icon || icon) })),
            onClick: ({ key: id }) => onClick(id) },
    }, h(Button, { size: 'small', icon: glyph(icon), disabled: busy }, title, glyph('chevron', 13))));

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
        ...(layer?.fields || []).map(field => ({ title: field.name, key: field.name, sorter: true,
            sortOrder: sort.key === active && sort.field === field.name ? sort.direction : null,
            render: (_, row) => String(row.values[field.name] ?? '—') }))];
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
        group('要素编辑', ...[['select', '选择'], ['draw', '绘制'], ['modify', '编辑顶点'], ['move', '移动']].map(([mode, title]) =>
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
    function layerMenu(item, index) {
        const id = key(item);
        const raster = item.kind === 'raster';
        return { items: [
            { key: 'fit', label: '缩放至图层', icon: glyph('target') },
            { key: 'table', label: '打开属性表', icon: glyph('table'), disabled: raster },
            { key: 'properties', label: '图层属性', icon: glyph('layers') },
            { key: 'style', label: raster ? '显示' : '符号化', icon: glyph('pencil') },
            { key: 'export', label: '导出', icon: glyph('download'), children: [
                { key: 'export-data', label: '导出为…' },
                ...(!raster ? [{ key: 'export-table', label: '导出属性表' }] : []),
            ] },
            ...(id === active && picked.length && !raster ? [
                { key: 'selection', label: '选中要素另存为图层', icon: glyph('download') }] : []),
            { type: 'divider' },
            { key: 'remove', label: '从地图移除', icon: glyph('trash'), danger: true },
        ], onClick: ({ key: action }) => {
            if (action === 'remove') { remove(id); return; }
            if (action === 'export-data' || action === 'export-table') {
                api.mapExport(item.ref, action === 'export-table').catch(fail);
                return;
            }
            if (action === 'properties' || action === 'style') {
                setProperties({ item, color: palette[index % palette.length], start: action === 'style' ? 'style' : 'info' });
                return;
            }
            if (action === 'selection') { openTool('data.export_selection'); return; }
            if (action === 'table') {
                if (id !== active) selectLayer(id);
                setPage(0);
                setTable([]);
                return;
            }
            const g = graph.current;
            const drawing = g?.byKey.get(id);
            if (drawing) {
                if (hidden.includes(id)) toggle(id);
                const extent = drawing.get('dataExtent') || ((!target || item.crs === target) ? item.bounds : null);
                if (extent) g.view.fit(extent, { size: g.ol.getSize(), maxZoom: 20, padding: [28, 28, 28, 28] });
                else fit(g.view, [drawing], g.ol.getSize());
            }
        } };
    }
    const notices = new globalThis.Map(layerErrors.map(({ message, names }) => [message, names]));
    for (const message of [document ? '' : ioStatus, issue]) {
        if (message && !notices.has(message)) notices.set(message, []);
    }
    const coordinateTools = () => h(CoordinateTools, { layer, display,
        setDisplay: item => { setDisplayPlans({}); setDisplay(item); },
        displayPlan: displayPlans[active], setDisplayPlan: value => setDisplayPlans(old => ({ ...old, [active]: value })),
        entries, request: api.map, onLayer: item => replace(layer, item), fail });
    const commands = {
        地图: [group('导航', command('全图', () => position(false), { disabled: !layers.length }),
            command('定位图层', () => position(true), { disabled: !layer }),
            command('放大', () => graph.current.view.adjustZoom(1)), command('缩小', () => graph.current.view.adjustZoom(-1))),
            group('选择', command('清除选择', clear, { disabled: !picked.length }),
                command('按属性选择', () => openTool('data.select_by_attribute'), { disabled: !layer?.fields?.length }),
                command('按位置选择', () => openTool('data.select_by_location'), { disabled: !layer?.geometry || layers.filter(item => item.geometry).length < 2 })),
            group('显示', command('地图属性', () => setMapProperties(true), { disabled: busy }))],
        数据: [group('地图工程', command('打开', () => mxd(api.mxdRead(), value => { setLocateMxd(null); setDocument(value); }),
            { disabled: busy, 'aria-label': '打开地图工程' }),
            command('保存', () => mxd(api.mxdWrite({ crs: target, bounds: graph.current?.view.calculateExtent(graph.current.ol.getSize()),
                background: backgroundColor,
                layers: [...layers].reverse().map((item, index) => ({ ref: item.ref, name: item.name, visible: !hidden.includes(key(item)), opacity: opacity(item.style?.opacity ?? item.opacity),
                    style: item.kind === 'raster' ? null : layerSymbol(item.style, palette[(layers.length - index - 1) % palette.length]) })) }),
            result => setIoStatus('已保存 MXD 和配套数据 · ' + result.layers + ' 个图层')),
            { disabled: busy || !layers.length || !viewReady, 'aria-label': '保存地图工程' })),
            group('图层管理', command('添加图层', () => load(api.mapImport()), { disabled: Boolean(edit) || busy }),
                command('新建图层', () => openTool('geometry.create_dataset')),
                command('复制图层', () => openTool('data.copy'), { disabled: !layer?.geometry }),
                command('合并图层', () => openTool('data.merge'), { disabled: layers.filter(item => item.geometry).length < 2 })),
            group('成果', command('最近成果', () => setAnalysis(lastResult), { disabled: !lastResult }))],
        编辑: editCommands,
        制图: [group('出图', command('导出地图', () => setLayout(true), { icon: glyph('download'), disabled: busy || !viewReady }))],
        分析: TOOL_GROUPS.map(({ title, icon, tools }) => {
            const prominent = { '统计与选择': 2, '矢量处理': 2, '叠加分析': 2 }[title] || 0;
            return prominent ? group(title, ...tools.slice(0, prominent).map(item => command(item.title, () => openTool(item.id),
                { key: item.id, icon: glyph(item.icon), disabled: busy,
                    'data-ai-focus': activity?.operation === item.id ? 'true' : undefined })),
                menuGroup('更多', icon, tools.slice(prominent), openTool)) : menuGroup(title, icon, tools, openTool);
        }),
        业务: [...skillGroups.map(([title, ids]) => [title, catalog.filter(item => ids.includes(item.id))]),
            ['自定义业务', customSkills]].filter(([, items]) => items.length)
            .map(([title, items]) => menuGroup(title, 'analysis', items, id => setAnalysis({ skill: items.find(item => item.id === id) }))),
    };
    return h('section', { className: 'workspace-map', 'aria-label': '地图工作区', onDragOver: event => event.preventDefault(),
        onDrop: event => { event.preventDefault(); if (!busy && !edit) void load(api.mapDrop([...event.dataTransfer.files])); } },
        h('header', { className: 'map-ribbon' },
            h('div', { className: 'ribbon-tabs' },
                h('div', { role: 'tablist', 'aria-label': '地图工具' }, ...Object.keys(commands).map(tab => h(Button, {
                    key: tab, type: 'text', icon: glyph(tabIcons[tab], 16), role: 'tab', 'aria-selected': ribbon === tab,
                    disabled: busy || Boolean(edit) && tab !== '编辑', onClick: () => setRibbon(tab),
                    'data-ai-focus': activity?.operation && tab === (/^(data|table)\./.test(activity.operation) ? '数据' :
                        activity.operation.startsWith('presentation.') ? '制图' : '分析') ? 'true' : undefined,
                }, tab)))),
            h('div', { className: 'ribbon-tools', role: 'tabpanel', 'aria-label': ribbon }, ...commands[ribbon].map((item, i) =>
                window.React.cloneElement(item, { key: i })))),
        h('div', { className: 'map-workspace-body' },
            h('aside', { className: 'map-layers', 'aria-label': '图层和图例' },
                h('div', { className: 'map-layer-head' }, h('strong', null, glyph('layers'), '图层')),
                layers.length > 5 && h(Input, { size: 'small', placeholder: '查找图层', 'aria-label': '查找图层',
                    allowClear: true, value: layerSearch, onChange: event => setLayerSearch(event.target.value) }),
                ...layers.map((item, index) => (!layerSearch || item.name.toLowerCase().includes(layerSearch.toLowerCase())) &&
                    h('div', { className: 'map-layer-entry', key: key(item) },
                    h('div', { className: 'map-layer-row', 'data-ai-focus': focused(item) ? 'true' : undefined },
                    h('input', { type: 'checkbox', 'aria-label': '显示 ' + item.name, checked: !hidden.includes(key(item)), disabled: Boolean(edit) || busy,
                        onChange: () => toggle(key(item)) }),
                    h('span', { className: 'map-swatch', 'data-color': index % palette.length,
                        ref: node => { if (node) node.style.backgroundColor = symbol(item.style, palette[index % palette.length]).color; } }),
                    h(antd.Dropdown, { trigger: ['contextMenu'], menu: layerMenu(item, index), disabled: Boolean(edit) || busy },
                        h(Button, { type: 'text', className: key(item) === active ? 'map-layer-active' : '',
                            title: item.group ? item.group + ' / ' + item.name : item.name, disabled: Boolean(edit) || busy,
                            onClick: () => { setPage(0); setEditLayer(null); selectLayer(key(item)); } }, item.name)),
                    h(antd.Dropdown, { trigger: ['click'], menu: layerMenu(item, index) },
                        h(Button, { type: 'text', size: 'small', disabled: Boolean(edit) || busy,
                            'aria-label': item.name + '菜单', icon: glyph('more', 16) }))),
                    item.style?.classification && h('div', { className: 'map-class-legend' },
                        h(Button, { type: 'text', size: 'small', 'aria-expanded': Boolean(expandedLegends[key(item)]),
                            icon: glyph('chevron', 13), onClick: () => setExpandedLegends(old => ({ ...old, [key(item)]: !old[key(item)] })) },
                        item.style.classification.field),
                        expandedLegends[key(item)] && h('ul', null, ...[...item.style.classification.classes,
                            { label: '其他值', color: item.style.classification.other, fillColor: item.style.classification.otherFill }].map(entry => h('li', { key: JSON.stringify(entry.value) },
                            h('span', { className: 'map-swatch', style: classSwatch(item, entry) }),
                            entry.label || (entry.value ?? '空值')))))))),
            h('div', { className: 'map-viewport' },
                h('div', { className: 'geo-map', ref: node, tabIndex: 0, 'aria-label': '交互地图', style: { backgroundColor } }),
                activity && h('div', { className: 'map-ai-note', 'data-kind': activity.kind },
                    glyph(({ read: 'layers', transform: 'globe', calculate: 'analysis', publish: 'save', draw: 'pencil' })[activity.kind] || 'target'),
                    h('span', null, activity.label), activity.target && h('strong', null, activity.target)),
                importing && h('div', { className: 'map-import-progress', role: 'status' },
                    h('span', null, importing.label),
                    importing.total ? h('progress', { max: importing.total, value: importing.completed,
                        'aria-label': '图层导入进度' }) : h('progress', { 'aria-label': '图层导入进度' })),
                !layers.length && h(Button, { className: 'map-empty', type: 'default', icon: glyph('add', 30),
                    title: '添加或拖入空间数据', 'aria-label': '添加或拖入空间数据', disabled: busy, onClick: () => load(api.mapImport()) }),
                (notices.size > 0 || limited.length > 0 || busy) && h('div', { className: 'map-notice', role: 'status' },
                    busy ? '正在处理地图数据…' : notices.size > 0 ?
                        [...notices].map(([message, names]) => h('div', { key: message }, message,
                            names.length > 0 && h('span', { className: 'muted' }, '（' + names.join('、') + '）')))
                        : '当前视野仅显示部分要素，放大后可查看更多。'))),
        h('footer', { className: 'map-status' },
            h('span', null, layers.length + ' 个图层'),
            layer && h('span', null, layer.name),
            picked.length > 0 && h('span', null, '已选 ' + picked.length + ' 个要素'),
            h('span', { className: 'map-status-crs', title: display?.label || native?.crsName }, glyph('globe', 14),
                h('span', null, display?.label || native?.crsName || '未设置坐标系')),
            h('span', { className: 'map-status-measure', 'aria-label': '当前比例尺', title: '屏幕比例尺' },
                scale ? '1 : ' + scale.toLocaleString('zh-CN') : '1 : —'),
            h('span', { className: 'map-status-coordinates', 'aria-label': '鼠标坐标', title: '地图坐标 · X / Y' },
                cursor && viewReady ? cursor.map(value => value.toFixed(units === 'degrees' ? 6 : 2)).join(' , ') + (units === 'degrees' ? ' °' : units === 'm' ? ' m' : '') : 'X —　Y —')),
        table && layer && layer.kind !== 'raster' && h('section', { className: 'map-attribute-panel', 'aria-label': '属性表' },
            h('header', { className: 'map-attribute-head' }, h('strong', null, glyph('table'), '属性表'),
                h('span', null, layer.name),
                command('添加字段', () => openTool('table.add_field'), { disabled: busy }),
                command('删除字段', () => openTool('table.delete_field'), { disabled: busy || !layer.fields?.length }),
                command('字段计算', () => openTool('table.calculate_field'), { disabled: busy || !layer.fields?.length }),
                h(Button, { type: 'text', size: 'small', icon: glyph('close', 15),
                    'aria-label': '关闭属性表', onClick: () => setTable(null) })),
            h(Table, { className: 'map-attributes', size: 'small', columns, rowKey: 'id', dataSource: tableLayer === active ? table : [],
            onChange: (_, filters, sorter, { action }) => {
                if (action !== 'sort') return;
                setPage(0);
                setSort(sorter.order ? { key: active, field: sorter.columnKey, direction: sorter.order } : {});
            },
            scroll: { x: 'max-content', y: 130 }, rowSelection: { selectedRowKeys: picked, preserveSelectedRowKeys: true,
                onChange: ids => setSelection({ key: active, ids }) },
            pagination: { current: page + 1, pageSize: 50, total: layer.count,
                showSizeChanger: false, onChange: value => setPage(value - 1) } })),
        properties && h(LayerProperties, { ...properties, close: () => setProperties(null), glyph,
            save: value => styleLayer(key(properties.item), value) }),
        mapProperties && h(MapProperties, { background: backgroundColor, save: setBackground, coordinates: coordinateTools(),
            close: () => setMapProperties(false), glyph }),
        layout && h(MapLayout, { initial: { ...layoutDefaults, title: layer?.name || '小六地图' },
            busy, close: () => setLayout(false), exportImage }),
        document && h(MxdOpen, { document, busy, glyph, locateAvailable: locateMxd, close: () => setDocument(null),
            open: (frame, locate) => mxd(api.mxdImport(frame, locate), result => {
                if (result.layers.length) {
                    graph.current.fitNeeded = true;
                    graph.current.initialExtent = result.frame.bounds;
                    setDisplayPlans({});
                    setLayerErrors([]);
                    setExpandedLegends({});
                    setTable(null);
                    setLastResult(null);
                    importFrame(result);
                    setDocument(null);
                }
                setImportIssues(result.layers.length ? result.issues : []);
                if (!result.layers.length && result.issues.length) {
                    setLocateMxd(result.issues.some(item => item.missing) ? frame : null);
                    setIoStatus('未能加载图层。' + [...new Set(result.issues.map(item => window.GuizhiErrorText(item.message)))].join('。'));
                }
            }), issue: ioStatus }),
        importIssues.length > 0 && h(Modal, { open: true, centered: true, title: '未加载的图层', className: 'gis-dialog',
            rootClassName: 'gis-modal-root', onCancel: () => setImportIssues([]), footer: h(Button, {
                onClick: () => setImportIssues([]), type: 'primary' }, '关闭') },
            h('ul', { className: 'map-field-editor' }, ...importIssues.map((item, index) =>
                h('li', { key: index }, item.name + '：' + window.GuizhiErrorText(item.message))))),
        analysis && h(MapProcessing, { ...analysis, layers, active, picked, entries, components, openComponents, glyph, busy,
            setBusy, close: () => setAnalysis(null), Preview, onResult: result => {
                setLastResult({ tool: analysis.tool, skill: analysis.skill, initialResult: result });
                if (result.selection) {
                    selectLayer(result.input);
                    setSelection({ key: result.input, ids: result.selection.fids });
                }
                if (result.layers.length && ['table.add_field', 'table.delete_field', 'table.calculate_field'].includes(analysis.tool)) {
                    const input = layers.find(item => key(item) === result.input);
                    if (input) {
                        const output = result.layers[0];
                        const classification = input.style?.classification;
                        replace(classification && !output.fields.some(field => field.name === classification.field)
                            ? { ...input, style: { ...input.style, classification: undefined } } : input, output);
                    }
                } else if (result.layers.length) {
                    graph.current.fitNeeded = true;
                    add(result.layers);
                }
            } }),
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

window.GuizhiMap = { Preview, WorkspaceMap, key, roleLayerPicker };
