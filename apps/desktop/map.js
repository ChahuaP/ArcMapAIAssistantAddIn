import 'ol/ol.css';
import { MapSourceDialog } from './map-source-dialog.js';
import { attachMeasurement } from './map-measure.js';
import { MapProcessing, MapResultDialog, MapTool, TOOL_GROUPS, toolError, toolIcon,
    roleLayerPicker, layerMatches, mapLayerMaterial } from './map-processing.js';
import { geometryProperties } from './map-attribute-tools.js';
import { useTableWindow } from './map-table-window.js';
import { AttributeSort, AttributeStatistics, AttributeCell } from './map-table-ui.js';
import { MapLayout, amendLayout, drawMapLayers, mapCanvas, mapImage, sheetSize } from './map-layout.js';
import { LayerProperties, MapProperties, MxdOpen, categoryValue, classifyLayer, defaultMapBackground, layerSymbol, opacity, symbol } from './map-layer-dialog.js';
import Map from 'ol/Map.js';
import { defaults as defaultInteractions } from 'ol/interaction/defaults.js';
import MouseWheelZoom from 'ol/interaction/MouseWheelZoom.js';
import { unByKey } from 'ol/Observable.js';
import Overlay from 'ol/Overlay.js';
import View from 'ol/View.js';
import Projection from 'ol/proj/Projection.js';
import ImageLayer from 'ol/layer/Image.js';
import Static from 'ol/source/ImageStatic.js';
import VectorLayer from 'ol/layer/Vector.js';
import VectorSource from 'ol/source/Vector.js';
import GeoJSON from 'ol/format/GeoJSON.js';
import WKB from 'ol/format/WKB.js';
import Feature from 'ol/Feature.js';
import Select from 'ol/interaction/Select.js';
import Draw from 'ol/interaction/Draw.js';
import DragBox from 'ol/interaction/DragBox.js';
import Modify from 'ol/interaction/Modify.js';
import Translate from 'ol/interaction/Translate.js';
import Snap from 'ol/interaction/Snap.js';
import { never, primaryAction, singleClick, shiftKeyOnly, noModifierKeys } from 'ol/events/condition.js';
import ScaleLine from 'ol/control/ScaleLine.js';
import { Fill, Stroke, Style, Circle, RegularShape, Text } from 'ol/style.js';
import { createEmpty, extend, isEmpty } from 'ol/extent.js';
import { getPointResolution } from 'ol/proj.js';

const h = window.React.createElement;
const { useEffect, useRef, useState } = window.React;
const { Button, Input, Select: Choice, Table, Modal } = window.antd;
const { icon: glyph, businessIcon } = window.GuizhiShell;
const api = window.guizhi;
const format = new GeoJSON();
const viewportGeometry = new WKB();
const palette = ['#1785db', '#bf6f2b', '#17a489', '#8263ca', '#cc5785'];
const commandIcons = {
    全图: 'extent', 定位图层: 'target', 放大: 'zoomIn', 缩小: 'zoomOut', 清除选择: 'clear',
    按属性选择: toolIcon('data.select_by_attribute'), 按位置选择: toolIcon('data.select_by_location'), 添加图层: 'add',
    添加字段: toolIcon('table.add_field'), 删除字段: toolIcon('table.delete_field'), 新建图层: toolIcon('geometry.create_dataset'),
    复制图层: toolIcon('data.copy'), 合并图层: toolIcon('data.merge'), 地图属性: 'settings',
    编辑图层: 'pencil', 选择: 'select', 绘制: 'pencil', 编辑顶点: 'node', 移动: 'move',
    完成绘制: 'check', 撤销绘制点: 'undo', 编辑属性: 'table', 删除要素: 'trash',
    撤销: 'undo', 退出: 'close', 识别要素: 'info', 框选: 'box',
    合并要素: 'merge', 按线分割: 'split', 完成分割: 'check', 复制: 'copy', 剪切: 'cut',
    合并为多部件: toolIcon('geometry.multipart'), 拆分多部件: toolIcon('geometry.explode'),
    打开: 'folderOpen', 保存: 'save', 字段计算器: toolIcon('table.calculate_field'), 计算几何: toolIcon('table.calculate_geometry'),
};
const tabIcons = { 地图: 'map', 数据: 'layers', 编辑: 'pencil', 分析: 'analysis', 制图: 'layout', 业务: 'briefcase' };

const roles = { project: '项目范围', coverage: '重叠范围' };
const key = item => item.ref.run + ':' + item.ref.artifact;
function rasterDetailBounds(item, view) {
    if (!view || !item.bounds || Math.max(item.width, item.height) <= 960) return null;
    const [x1, y1, x2, y2] = item.bounds;
    const bounds = [Math.max(x1, view[0]), Math.max(y1, view[1]), Math.min(x2, view[2]), Math.min(y2, view[3])];
    if (bounds[0] >= bounds[2] || bounds[1] >= bounds[3]) return null;
    const fraction = Math.max((bounds[2] - bounds[0]) / (x2 - x1), (bounds[3] - bounds[1]) / (y2 - y1));
    return fraction <= .5 ? bounds : null;
}
const projection = (code, units) => new Projection({ code: code || 'local', units: units || 'm' });
const fillColor = (color, percent) => color + Math.round(percent * 255 / 100).toString(16).padStart(2, '0');
const classFill = (settings, color, fill) => settings.fill === null || fill === null ? null : fill || color;
const feature = item => format.readFeature({ type: 'Feature', ...item });
function viewportFeature(item) {
    const bytes = atob(item.wkb);
    const buffer = new Uint8Array(bytes.length);
    for (let i = 0; i < bytes.length; i++) buffer[i] = bytes.charCodeAt(i);
    const geometry = viewportGeometry.readGeometry(buffer);
    const rendered = new Feature();
    rendered.setProperties(item.properties);
    rendered.setGeometry(geometry);
    rendered.setId(item.id);
    return rendered;
}
const style = (color, settings) => {
    const appearance = symbol(settings, color);
    const lineDash = appearance.pattern === 'dash' ? [9, 5] : appearance.pattern === 'dot' ? [2, 4] : undefined;
    const stroke = !appearance.noOutline ? new Stroke({ color: appearance.color, width: appearance.width, lineDash }) : undefined;
    const fill = new Fill({ color: appearance.fill ? fillColor(appearance.fill, appearance.fillOpacity) : 'rgba(0,0,0,0)' });
    const point = { radius: appearance.size / 2, declutterMode: 'none', fill: new Fill({ color: appearance.color }),
        stroke: new Stroke({ color: appearance.color, width: 1 }) };
    const shape = appearance.shape;
    const image = shape === 'circle' ? new Circle(point) : new RegularShape({ ...point,
        points: 4, radius2: shape === 'cross' ? 0 : undefined, angle: shape === 'square' ? Math.PI / 4 : 0 });
    return new Style({ stroke, fill, image });
};
const layerFor = (items, color) => new VectorLayer({
    declutter: 'map-labels', source: new VectorSource({ features: items.map(feature) }), style: style(color),
});
function coverageLayer(geometries, color, settings = {}) {
    const source = new VectorSource({ features: geometries.map(geometry => feature({ geometry })) });
    return new VectorLayer({ ...settings, source, style: coverageStyle(color) });
}

function coverageStyle(color) {
    return new Style({ stroke: new Stroke({ color, width: 1.4 }), fill: new Fill({ color: fillColor(color, 27) }) });
}
const displayFields = item => [...new Set([item.style?.classification?.field, item.style?.labelField].filter(Boolean))];
function withLabels(render, labelField, geometry) {
    if (!labelField) return render;
    const text = new Text({ font: '13px "Microsoft YaHei", sans-serif', offsetY: /Point/.test(geometry) ? -15 : 0,
        padding: [2, 3, 2, 3], fill: new Fill({ color: '#172536' }), stroke: new Stroke({ color: '#fff', width: 3 }),
        overflow: true });
    const labels = new Style({ text, zIndex: 10 });
    return feature => {
        const base = typeof render === 'function' ? render(feature) : render;
        const value = feature.get(labelField);
        if (value == null || value === '') return base;
        text.setText(String(value).slice(0, 160));
        return [base, labels];
    };
}
function selectionStyle(size = 10) {
    return [['#312a05', 5.5], ['#fff000', 3]].map(([color, width], zIndex) => {
        const stroke = new Stroke({ color, width });
        return new Style({ stroke, image: new Circle({ radius: size / 2 + 3, stroke }), zIndex });
    });
}
function showSelection(map, select) {
    const layer = new VectorLayer({ source: new VectorSource({ features: select.getFeatures(), useSpatialIndex: false }),
        style: selectionStyle(), zIndex: 100001 });
    map.addLayer(layer);
    return layer;
}
function classSwatch(item, entry) {
    if (!/Polygon$/.test(item.geometry)) return { backgroundColor: entry.color };
    const fill = classFill(item.style, entry.color, entry.fillColor);
    return { backgroundColor: fill ? fillColor(fill, opacity(item.style.fillOpacity)) : 'transparent',
        borderColor: item.style.noOutline ? 'transparent' : entry.color };
}

function attach(node, view, layers) {
    const interactions = defaultInteractions({ mouseWheelZoom: false });
    interactions.push(new MouseWheelZoom({ timeout: 35, duration: 120 }));
    return new Map({ target: node, view, layers, controls: [], interactions, pixelRatio: Math.min(devicePixelRatio, 2) });
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
        const selection = new Select({ condition: singleClick, layers: [layers[0]], style: null, hitTolerance: 5 });
        ol.addInteraction(selection);
        showSelection(ol, selection);
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

function CoordinateTools({ layer, display, setDisplay, entries, openTool, fail, displayPlan, setDisplayPlan }) {
    const [query, search] = useState('');
    const [standard, setStandard] = useState([]);
    useEffect(() => {
        let current = true;
        const timer = setTimeout(() => api.map({ action: 'crs-search', query }).then(items => {
            if (current) setStandard(items);
        }).catch(error => { if (current) fail(error); }), 180);
        return () => { current = false; clearTimeout(timer); };
    }, [query]);
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
    const plans = entries.filter(entry => entry.kind === 'plan');
    return h('div', { className: 'map-coordinate' },
        h('span', null, layer ? layer.name + ' · ' + (layer.crsName || '未定义坐标系') : '暂无图层'),
        h(Choice, { showSearch: true, filterOption: false, allowClear: true, onSearch: search,
            placeholder: '地图显示坐标系', 'aria-label': '地图显示坐标系',
            options: [...options, ...(display?.value && !catalog.some(item => item.value === display.value) ? [display] : [])],
            value: display?.value, onChange: (value, item) => setDisplay(value ? item : null) }),
        layer && h('div', { className: 'map-coordinate-actions' },
            h(Button, { onClick: () => openTool('crs.assign') }, '定义坐标系'),
            h(Button, { disabled: !layer.crs, onClick: () => openTool('crs.reproject') }, '投影转换')),
        display && layer?.crs && layer.kind !== 'raster' && plans.length > 0 &&
            h(Choice, { value: displayPlan || undefined, placeholder: '为该图层选择显示转换',
                'aria-label': '显示转换方案', allowClear: true, onChange: setDisplayPlan,
                options: plans.map(entry => ({ value: entry.id + '@' + entry.revision, label: entry.title })) }));
}

function WorkspaceMap({ layers, add, remove, moveLayer, renameLayer, selectLayer, active, hidden, toggle, entries, catalog, components,
    openComponents, display, setDisplay, background, setBackground, styleLayer, importFrame, fail, activity,
    requestedLayout, openLayout, onLayout, onLayoutSettled, mapActions, onMapActions, selection, setSelection, replace, replaceLayers, editing }) {
    const node = useRef(null);
    const graph = useRef(null);
    const [properties, setProperties] = useState(null);
    const [mapProperties, setMapProperties] = useState(null);
    const [layout, setLayout] = useState(null);
    const [layoutDraft, setLayoutDraft] = useState(null);
    const [document, setDocument] = useState(null);
    const cursorLabel = useRef(null);
    const scaleLabel = useRef(null);
    const [viewReady, setViewReady] = useState(false);
    const [ioStatus, setIoStatus] = useState('');
    const [importIssues, setImportIssues] = useState([]);
    const [displayPlans, setDisplayPlans] = useState({});
    const [editLayer, setEditLayer] = useState(null);
    const [pendingEdit, setPendingEdit] = useState(null);
    const [edit, setEdit] = useState('');
    const [boxSelect, setBoxSelect] = useState(false);
    const [pointSelect, setPointSelect] = useState(false);
    const [measure, setMeasure] = useState(null);
    const [measurement, setMeasurement] = useState('');
    const selectionMode = useRef(null);
    const [mergeTarget, setMergeTarget] = useState(null);
    const [clipboard, setClipboard] = useState(null);
    const [editDialogError, setEditDialogError] = useState('');
    const [confirmExit, setConfirmExit] = useState(false);
    const [identify, setIdentify] = useState(false);
    const [identifyHost, setIdentifyHost] = useState(null);
    const [identified, setIdentified] = useState(null);
    const identifying = useRef(false);
    const identification = useRef(0);
    const layerList = useRef(layers);
    layerList.current = layers;
    const [ribbon, setRibbon] = useState('地图');
    const [layerSearch, setLayerSearch] = useState('');
    const [renamingLayer, setRenamingLayer] = useState(null);
    const draggedLayer = useRef(null);
    const [dropEdge, setDropEdge] = useState(null);
    const mapBasis = useRef(null);
    const [expandedLegends, setExpandedLegends] = useState({});
    const [analysis, setAnalysis] = useState(null);
    const [tool, setTool] = useState(null);
    const [toolResult, setToolResult] = useState(null);
    const [count, setCount] = useState(0);
    const partsDraft = useRef(null);
    const [fieldEdit, setFieldEdit] = useState(null);
    const [pasteTarget, setPasteTarget] = useState(null);
    const edits = useRef([]);
    const drawingTool = useRef(null);
    const [drawingPoints, setDrawingPoints] = useState(0);
    const original = useRef(new globalThis.Map());
    const [busy, setBusy] = useState(false);
    selectionMode.current = { boxSelect, pointSelect, edit, busy, measure };
    const exportedLayout = useRef(null);
    const appliedActions = useRef(null);
    const [importing, setImporting] = useState(null);
    const [sourceDialog, setSourceDialog] = useState(null);
    useEffect(() => api.onMapImportProgress(setImporting), []);
    useEffect(() => { editing(Boolean(edit) || busy); return () => editing(false); }, [Boolean(edit), busy]);
    const [issue, setIssue] = useState('');
    const [layerErrors, setLayerErrors] = useState([]);
    const [locateMxd, setLocateMxd] = useState(null);
    const [table, setTable] = useState(null);
    const [tableLayer, setTableLayer] = useState(null);
    const [tableRequest, requestTable] = useState({ page: 0 });
    const [resolvedPage, setResolvedPage] = useState(0);
    const tablePanel = useRef(null);
    const zoomRequest = useRef(0);
    const focusFid = tableRequest.layer === active ? tableRequest.fid : undefined;
    const page = focusFid == null ? tableRequest.page : resolvedPage;
    function setPage(page) { requestTable({ page }); }
    function focusRow(layer, fid) { if (fid != null) requestTable({ page: 0, layer, fid }); }
    const [sort, setSort] = useState({});
    const [tableFilter, setTableFilter] = useState('all');
    const [tableDialog, setTableDialog] = useState(null);
    const [limited, setLimited] = useState([]);
    const [viewRevision, setViewRevision] = useState(0);
    const [viewLoading, setViewLoading] = useState(false);
    const selectionRef = useRef(selection);
    selectionRef.current = selection;
    const layer = layers.find(item => key(item) === active);
    function closeTable() { setTable(null); setTableDialog(null); }
    const tableVisible = Boolean(table && layer && layer.kind !== 'raster');
    const tableWindow = useTableWindow(tableVisible, layer?.name, closeTable, fail);
    const geometryFamily = item => item?.geometry?.replace(/^Multi/, '');
    const pasteSource = clipboard && layers.find(item => key(item) === clipboard.key);
    const pasteTargets = pasteSource ? layers.filter(item => key(item) !== clipboard.key && item.crs &&
        geometryFamily(item) === clipboard.kind) : [];
    const canPaste = !busy && !count && edit === 'select' && pasteTargets.length > 0;
    const openPaste = () => {
        if (!canPaste) return;
        setIssue('');
        setPasteTarget(pasteTargets.some(item => key(item) === active) ? active : key(pasteTargets[0]));
    };
    useEffect(() => {
        if (clipboard && !pasteSource) setClipboard(null);
    }, [layers, clipboard]);
    const focused = item => [activity?.target, ...(activity?.targets || [])].filter(Boolean).some(name =>
        [item.name, item.sourceName?.split(/[\\/]/).at(-1)].includes(name));
    const native = editLayer || layers.find(item => key(item) === mapBasis.current && item.crs) ||
        layers.find(item => item.crs && item.kind !== 'raster') || layers.find(item => item.crs) || layers[0];
    const target = display?.wkt || display?.value || native?.crs;
    const units = display ? display.units : native?.units;
    const metresPerUnit = display ? display.metresPerUnit : native?.metresPerUnit;
    const initialExtent = display?.bounds;
    const backgroundColor = background || defaultMapBackground;
    const canMeasure = Boolean(target) && (units === 'degrees' || metresPerUnit > 0 || units === 'm');
    function startMeasure(type) {
        toggleIdentify(false);
        setPointSelect(false);
        setBoxSelect(false);
        setMeasurement('');
        setMeasure(measure === type ? null : type);
        node.current?.focus();
    }
    function closeIdentification() {
        identification.current += 1;
        graph.current?.identify.setPosition(undefined);
        setIdentified(null);
    }
    function toggleIdentify(enabled) {
        setMeasure(null);
        identifying.current = enabled;
        setIdentify(enabled);
        if (!enabled) closeIdentification();
    }
    useEffect(() => {
        if (identified && (!layers.some(item => key(item) === identified.layer) || hidden.includes(identified.layer))) closeIdentification();
    }, [layers, hidden, identified]);
    const activeRef = useRef(active);
    activeRef.current = active;
    const frameCache = useRef(new globalThis.Map());
    const mapRoles = Object.fromEntries(layers.map((item, index) => ['map-' + (index + 1), key(item)]));
    const layoutSource = JSON.stringify([layers, hidden, target, backgroundColor, displayPlans]);
    useEffect(() => { frameCache.current.clear(); }, [layoutSource]);

    function frameLayer(identifier) {
        const stable = layers.find(item => key(item) === identifier || key(item) === mapRoles[identifier]);
        if (stable) return stable;
        const matches = layers.filter(item => item.name === identifier);
        if (matches.length !== 1) throw new Error('请确认地图框所用图层：' + identifier);
        return matches[0];
    }

    useEffect(() => {
        if (openLayout) openManualLayout(openLayout);
    }, [openLayout]);

    function openManualLayout(id) {
        const size = graph.current?.ol.getSize();
        const config = amendLayout(null, layoutDraft || { title: layer?.name || '地图' });
        config.viewBounds = viewReady && size?.every(value => value > 0) ? graph.current.view.calculateExtent(size) : undefined;
        setLayout({ key: id, config });
    }

    function prepareLayout(config) {
        const size = graph.current.ol.getSize();
        if (!size?.every(value => value > 0)) throw new Error('请打开地图并等待图层显示');
        return { ...config, viewBounds: config.viewBounds || graph.current.view.calculateExtent(size),
            frames: Object.fromEntries(Object.entries(config.frames).map(([id, frame]) =>
                [id, { ...frame, layers: frame.layers?.map(layer => key(frameLayer(layer))) }])) };
    }

    function frameLegend(items) {
        return items.flatMap(item => {
            const appearance = symbol(item.style, palette[layers.indexOf(item) % palette.length]);
            const classification = item.style?.classification;
            if (classification) return classification.classes.map(entry => ({
                name: entry.label || entry.value || '空值', color: entry.color,
                fill: classFill(appearance, entry.color, entry.fillColor),
            }));
            return [{ name: item.name, color: appearance.color,
                fill: /Polygon/.test(item.geometry || '') ? appearance.fill : appearance.color }];
        });
    }

    async function renderFrame(items, bounds, size, zoom) {
        if (!items.length) throw new Error('请先在地图工作区添加并显示图层，再预览或导出图件');
        const view = new View({ projection: projection(target, units), center: [0, 0], zoom: 0 });
        const offscreen = globalThis.document.createElement('div');
        Object.assign(offscreen.style, { position: 'fixed', left: '-20000px', top: '0',
            width: size[0] + 'px', height: size[1] + 'px', pointerEvents: 'none' });
        globalThis.document.body.append(offscreen);
        const ol = new Map({ target: offscreen, view, controls: [], interactions: [], pixelRatio: 1 });
        ol.setSize(size);
        try {
            const drawings = await Promise.all(items.map(async item => {
                const previous = graph.current?.byKey.get(key(item));
                const appearance = symbol(item.style, palette[layers.indexOf(item) % palette.length]);
                const options = { opacity: opacity(item.style?.opacity ?? item.opacity) / 100 };
                if (item.kind === 'raster') {
                    const stamp = JSON.stringify([target, item.style?.rasterBands]);
                    const windowed = options.opacity === 1 && item.byteImagery && (!target || target === item.crs) && rasterDetailBounds(item, bounds);
                    const oldSource = previous?.get('rasterStamp') === stamp && previous.getSource();
                    if (oldSource && !windowed) return [new ImageLayer({ ...options, source: oldSource })];
                    if (oldSource && windowed) options.extent = item.bounds;
                    const image = await api.map({ action: 'raster-display', ref: item.ref, target, bands: item.style?.rasterBands,
                        bbox: windowed || undefined });
                    if (oldSource && windowed) return [new ImageLayer({ ...options, source: oldSource }),
                        new ImageLayer({ ...options, source: new Static({ url: 'data:image/png;base64,' + image.image,
                            imageExtent: image.bounds, projection: view.getProjection() }) })];
                    return [new ImageLayer({ ...options, source: new Static({
                        url: 'data:image/png;base64,' + image.image, imageExtent: image.bounds, projection: view.getProjection(),
                    }) })];
                }
                if (!item.crs && target) throw new Error('请先定义图层坐标系：' + item.name);
                const plan = entries.find(entry => entry.kind === 'plan' &&
                    (entry.plan.source_wkt === item.crs || entry.source_reference === item.crs) &&
                    (entry.plan.target_wkt === target || entry.target_reference === display?.value));
                const reply = await api.map({ action: 'features', ref: item.ref, bbox: bounds, target: target || null,
                    outlines: item.style?.outlines === true,
                    fields: displayFields(item),
                    plan: displayPlans[key(item)] || (plan && plan.id + '@' + plan.revision) });
                if (reply.overview) {
                    const summary = coverageLayer(reply.overview, appearance.color, options);
                    summary.set('dataExtent', reply.bounds);
                    summary.set('limited', true);
                    return [summary];
                }
                const source = new VectorSource({ features: reply.features.map(feature) });
                const fillLayer = previous?.get('fillLayer');
                const layersForItem = fillLayer ? [new VectorLayer({ source, opacity: fillLayer.getOpacity(),
                    style: fillLayer.getStyle() })] : [];
                const drawing = new VectorLayer({ ...options, source, declutter: 'layout-labels',
                    style: previous?.getStyle() || withLabels(style(appearance.color, appearance), item.style?.labelField, item.geometry) });
                drawing.set('dataExtent', reply.bounds);
                drawing.set('limited', reply.limited);
                return [...layersForItem, drawing];
            }));
            const draw = drawings.reverse().flat();
            draw.forEach(layer => {
                if (layer instanceof ImageLayer) layer.set('dataExtent', layer.getSource().getImageExtent());
                ol.addLayer(layer);
            });
            if (bounds) view.fit(bounds, { size, maxZoom: 25 });
            else { fit(view, draw, size); if (zoom !== 1) view.setResolution(view.getResolution() / zoom); }
            await new Promise((resolve, reject) => {
                const timer = setTimeout(() => reject(new Error('等待地图框渲染超时')), 10000);
                ol.once('rendercomplete', () => { clearTimeout(timer); resolve(); });
                ol.renderSync();
            });
            const image = globalThis.document.createElement('canvas');
            [image.width, image.height] = size;
            const context = image.getContext('2d');
            context.fillStyle = backgroundColor;
            context.fillRect(0, 0, ...size);
            drawMapLayers(context, offscreen);
            const resolution = view.getResolution();
            const metresPerPixel = units === 'degrees'
                ? getPointResolution(view.getProjection(), resolution, view.getCenter(), 'm')
                : resolution * (metresPerUnit || (units === 'm' ? 1 : NaN));
            return { image, metresPerPixel, legend: frameLegend(items), limited: draw.some(item => item.get('limited')) };
        } finally { ol.dispose(); offscreen.remove(); }
    }

    async function drawLayout(config) {
        const [width, height] = sheetSize(config);
        const entries = await Promise.all(Object.entries(config.frames).map(async ([id, frame]) => {
            const items = frame.layers?.map(frameLayer) || layers.filter(item => !hidden.includes(key(item)));
            const position = config.positions[id];
            const size = [Math.round(width * position.width), Math.round(height * position.height)];
            const zoom = frame.zoom || 1;
            const bounds = frame.extent === 'layers' ? null : config.viewBounds?.map((value, index, original) => {
                const center = (value + original[(index + 2) % 4]) / 2;
                return center + (value - center) / zoom;
            });
            const stamp = JSON.stringify([items.map(item => [key(item), item.style]), bounds, size, zoom]);
            let rendering = frameCache.current.get(stamp);
            if (!rendering) {
                if (frameCache.current.size >= 12) frameCache.current.delete(frameCache.current.keys().next().value);
                rendering = renderFrame(items, bounds, size, zoom);
                frameCache.current.set(stamp, rendering);
                void rendering.catch(() => { if (frameCache.current.get(stamp) === rendering) frameCache.current.delete(stamp); });
            }
            return [id, await rendering];
        }));
        return mapCanvas(Object.fromEntries(entries), config);
    }

    async function exportImage(config, taskId, signal) {
        setBusy(true);
        try {
            const image = await drawLayout(config);
            if (signal?.aborted) return;
            await api.mapLayout({ format: config.format, paper: config.paper, orientation: config.orientation,
                data: mapImage(image, config.format), name: config.fileName || config.title, taskId });
        } catch (error) {
            if (!signal?.aborted) {
                if (taskId) await onLayout(taskId, window.GuizhiErrorText(error));
                else fail(error);
            }
        } finally {
            setBusy(false);
            if (taskId && !signal?.aborted) onLayoutSettled(taskId);
        }
    }

    function saveLayout(config) {
        setLayoutDraft(config);
        void api.mapLayoutDraft(config).catch(fail);
    }
    useEffect(() => {
        let mounted = true;
        api.mapLayoutDraft().then(draft => { if (mounted) setLayoutDraft(draft); }).catch(fail);
        return () => { mounted = false; };
    }, []);
    useEffect(() => {
        if (!requestedLayout || requestedLayout.signal.aborted || mapActions ||
                (!viewReady && layers.length > hidden.length) || busy || !node.current?.clientWidth ||
                exportedLayout.current === requestedLayout.taskId) return;
        const { taskId, signal } = requestedLayout;
        exportedLayout.current = taskId;
        try {
            const config = prepareLayout(amendLayout(layoutDraft, requestedLayout.layout));
            saveLayout(config);
            if (requestedLayout.layout.export) void exportImage(config, taskId, signal);
            else {
                setLayout({ key: taskId, taskId, config, signal });
            }
        } catch (error) {
            void onLayout(taskId, window.GuizhiErrorText(error)).finally(() => onLayoutSettled(taskId));
        }
    }, [requestedLayout, mapActions, viewReady, busy, layers, hidden]);
    useEffect(() => {
        if (!layout?.taskId || !layout.signal) return;
        const abort = () => setLayout(current => current?.taskId === layout.taskId ? null : current);
        if (layout.signal.aborted) abort();
        else layout.signal.addEventListener('abort', abort, { once: true });
        return () => layout.signal.removeEventListener('abort', abort);
    }, [layout?.taskId]);
    useEffect(() => {
        if (!mapActions || mapActions.signal.aborted || (!viewReady && layers.length > hidden.length) || busy ||
                edit || !node.current?.clientWidth || appliedActions.current === mapActions.id) return;
        appliedActions.current = mapActions.id;
        const map = graph.current;
        async function apply() {
            const actions = await Promise.all(mapActions.actions.map(async action => {
                const matches = action.layer ? layers.filter(item => action.layerRef
                    ? key(item) === key({ ref: action.layerRef }) : item.name === action.layer) : [];
                if (action.layer && matches.length !== 1) throw new Error('请确认地图中的图层名称唯一：' + action.layer);
                const item = matches[0];
                if (action.action === 'table' && (item.kind === 'raster' || action.field && !item.fields.some(field => field.name === action.field))) {
                    throw new Error('图层或排序字段没有可用的属性表：' + action.layer);
                }
                if (['symbol', 'classify'].includes(action.action) && item.kind === 'raster') throw new Error('影像图层不能使用矢量符号');
                if (action.action === 'classify' && !item.fields?.some(field => field.name === action.field)) {
                    throw new Error('分类字段不存在：' + action.field);
                }
                return { action, item, classification: action.action === 'classify'
                    ? await classifyLayer(item, action.field, item.style?.classification) : null };
            }));
            if (mapActions.signal.aborted) return;
            const visibility = new Set(hidden);
            for (const { action, item, classification } of actions) {
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
                    case 'classify':
                        styleLayer(id, { ...layerSymbol(item.style, palette[layers.indexOf(item) % palette.length]), classification });
                        break;
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
        }
        void apply().then(() => { if (!mapActions.signal.aborted) onMapActions(mapActions.id); },
            error => { if (!mapActions.signal.aborted) onMapActions(mapActions.id, window.GuizhiErrorText(error)); });
    }, [mapActions, viewReady, busy, edit, layers]);

    async function load(job) {
        setBusy(true);
        setImporting({ label: '正在读取数据' });
        setIoStatus('');
        setImportIssues([]);
        try {
            const { layers: items, issues, source } = await job;
            if (source) setSourceDialog(source);
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
        const updateScale = () => {
            if (!scaleLabel.current) return;
            const resolution = view.getResolution();
            const metres = !target ? NaN : units === 'degrees'
                ? getPointResolution(view.getProjection(), resolution, view.getCenter(), 'm')
                : resolution * (metresPerUnit || (units === 'm' ? 1 : NaN));
            const ratio = Number.isFinite(metres) && metres > 0 ? Math.max(1, Math.round(metres / .0002645833333)) : null;
            scaleLabel.current.textContent = ratio ? '1 : ' + ratio.toLocaleString('zh-CN') : '1 : —';
        };
        const scaleChange = view.on('change:resolution', updateScale);
        const clearCursor = () => {
            if (cursorLabel.current) cursorLabel.current.textContent = 'X —　Y —';
        };
        ol.on('pointermove', event => {
            if (event.dragging || !cursorLabel.current || graph.current?.fitNeeded) return;
            const suffix = units === 'degrees' ? ' °' : units === 'm' ? ' m' : '';
            cursorLabel.current.textContent = event.coordinate.map(value => value.toFixed(units === 'degrees' ? 6 : 2)).join(' , ') + suffix;
        });
        node.current.addEventListener('pointerleave', clearCursor);
        clearCursor();
        if (scaleLabel.current) scaleLabel.current.textContent = '1 : —';
        if (units === 'm' || units === 'degrees') ol.addControl(new ScaleLine({ units: 'metric' }));
        const selectable = () => !identifying.current && !selectionMode.current.measure && !selectionMode.current.busy &&
            (!selectionMode.current.edit || selectionMode.current.edit === 'select') &&
            Boolean(graph.current?.byKey.get(activeRef.current) instanceof VectorLayer && graph.current.byKey.get(activeRef.current).getVisible() && !graph.current.byKey.get(activeRef.current).get('coverage'));
        const subtract = event => event.originalEvent.ctrlKey && !event.originalEvent.shiftKey;
        const select = new Select({ condition: event => selectable() && !selectionMode.current.boxSelect &&
            (selectionMode.current.edit === 'select' || selectionMode.current.pointSelect) && singleClick(event),
            layers: candidate => candidate.get('key') === activeRef.current || candidate.get('pickKey') === activeRef.current,
            addCondition: shiftKeyOnly, removeCondition: subtract, toggleCondition: never, hitTolerance: 5, style: null });
        ol.addInteraction(select);
        const highlight = showSelection(ol, select);
        const summaryHighlight = new VectorLayer({ source: new VectorSource(), style: selectionStyle(), zIndex: 100002 });
        ol.addLayer(summaryHighlight);
        select.on('select', event => {
            const layer = activeRef.current;
            const changed = event.selected.map(item => item.getId());
            const previous = selectionRef.current?.key === layer ? selectionRef.current.ids : [];
            const added = shiftKeyOnly(event.mapBrowserEvent);
            const removed = subtract(event.mapBrowserEvent);
            const ids = removed ? previous.filter(id => !event.deselected.some(item => item.getId() === id))
                : added ? [...new Set([...previous, ...changed])] : select.getFeatures().getArray().map(item => item.getId());
            setSelection({ key: layer, ids });
            if (!removed) focusRow(layer, changed.at(-1));
        });
        const box = new DragBox({ className: 'ol-box map-selection-box', condition: event => selectable() && primaryAction(event) &&
            (shiftKeyOnly(event) || selectionMode.current.boxSelect && (noModifierKeys(event) || subtract(event))) });
        let boxStart;
        box.on('boxstart', event => { boxStart = event.mapBrowserEvent.pixel; });
        box.on('boxend', event => {
            const layer = activeRef.current;
            const drawing = graph.current?.byKey.get(layer);
            if (!(drawing instanceof VectorLayer) || !drawing.getVisible()) return;
            const extent = box.getGeometry().getExtent();
            const end = event.mapBrowserEvent.pixel;
            const full = boxStart && (end[0] < boxStart[0] || (end[0] === boxStart[0] && end[1] < boxStart[1]));
            const hits = new Set(drawing.getSource().getFeaturesInExtent(extent)
                .filter(item => {
                    const geometry = item.getGeometry();
                    if (!full) return geometry.intersectsExtent(extent);
                    const bounds = geometry.getExtent();
                    return bounds[0] >= extent[0] && bounds[1] >= extent[1] && bounds[2] <= extent[2] && bounds[3] <= extent[3];
                }).map(item => item.getId()));
            const previous = selectionRef.current?.key === layer ? selectionRef.current.ids : [];
            const ids = subtract(event.mapBrowserEvent) ? previous.filter(id => !hits.has(id)) :
                shiftKeyOnly(event.mapBrowserEvent) ? [...new Set([...previous, ...hits])] : [...hits];
            setSelection({ key: layer, ids });
            if (!subtract(event.mapBrowserEvent)) focusRow(layer, hits.values().next().value);
        });
        ol.addInteraction(box);
        const host = globalThis.document.createElement('div');
        const overlay = new Overlay({ element: host, position: undefined, positioning: 'bottom-center', stopEvent: true });
        ol.addOverlay(overlay);
        setIdentifyHost(host);
        setIdentified(null);
        graph.current = { ol, view, select, highlight, summaryHighlight, identify: overlay, byKey: new globalThis.Map(), fitNeeded: true, initialExtent, updateScale };
        ol.on('singleclick', async event => {
            if (!identifying.current) return;
            const hit = ol.forEachFeatureAtPixel(event.pixel, (feature, drawing) => {
                const id = drawing?.get('key') || drawing?.get('identifyKey');
                const item = id && layerList.current.find(item => key(item) === id);
                const fid = feature.getId();
                if (item && Number.isSafeInteger(fid)) return { item, fid };
            }, { hitTolerance: 5 });
            if (!hit) { closeIdentification(); return; }
            const request = ++identification.current;
            const layer = key(hit.item);
            const [width, height] = ol.getSize();
            const popupWidth = Math.min(306, width - 22);
            const above = event.pixel[1] >= Math.min(174, height / 2);
            const align = event.pixel[0] < popupWidth / 2 ? 'left' : width - event.pixel[0] < popupWidth / 2 ? 'right' : 'center';
            overlay.setPositioning(`${above ? 'bottom' : 'top'}-${align}`);
            overlay.setOffset([align === 'left' ? 6 : align === 'right' ? -6 : 0, above ? -10 : 10]);
            overlay.setPosition(event.coordinate);
            const record = { layer, fid: hit.fid, name: hit.item.name, width: popupWidth, above, align,
                height: Math.max(82, (above ? event.pixel[1] : height - event.pixel[1]) - 18) };
            setIdentified(record);
            try {
                const { attributes, geometryType } = await api.map({ action: 'table-feature', ref: hit.item.ref, fid: hit.fid });
                if (request === identification.current) setIdentified({ ...record, values: attributes, geometryType });
            } catch (error) { if (request === identification.current) setIdentified({ ...record, error: window.GuizhiErrorText(error) }); }
        });
        const observer = new ResizeObserver(() => ol.updateSize());
        observer.observe(node.current);
        const moved = () => { if (!graph.current?.fitNeeded) updateScale(); setViewRevision(value => value + 1); };
        ol.on('moveend', moved);
        const viewport = node.current;
        return () => {
            identification.current += 1;
            viewport.removeEventListener('pointerleave', clearCursor);
            unByKey(scaleChange);
            observer.disconnect();
            ol.dispose();
            graph.current = null;
            void api.mapSnapshot(null).catch(fail);
        };
    }, [target, units, metresPerUnit, initialExtent]);
    useEffect(() => {
        setMeasurement('');
        if (!measure || !canMeasure || !graph.current || busy || edit) return;
        return attachMeasurement(graph.current.ol, measure, units === 'degrees', metresPerUnit || 1, setMeasurement);
    }, [target, units, metresPerUnit, initialExtent, measure, canMeasure, busy, edit]);

    useEffect(() => {
        const state = graph.current;
        if (!state || edit) return;
        let alive = true;
        let snapshotTimer;
        let snapshotListener;
        const previous = state.byKey;
        const next = new globalThis.Map();
        const layerKeys = layers.map(key);
        for (const [id, existing] of previous) {
            if (layerKeys.includes(id)) continue;
            const fillLayer = existing.get('fillLayer');
            if (fillLayer) state.ol.removeLayer(fillLayer);
            if (existing.get('rasterDetail')) state.ol.removeLayer(existing.get('rasterDetail'));
            if (existing.get('coverage')) state.ol.removeLayer(existing.get('coverage'));
            state.ol.removeLayer(existing);
        }
        const bbox = !state.fitNeeded && state.ol.getSize()?.every(size => size > 0)
            ? state.view.calculateExtent(state.ol.getSize()) : null;
        let hasNewView = false;
        const jobs = layers.map((item, index) => {
            const id = key(item);
            const drawing = previous.get(id) || (item.kind === 'raster'
                ? new ImageLayer({ extent: item.bounds, zIndex: -1 }) : layerFor([], palette[index % palette.length]));
            if (!previous.has(id)) {
                drawing.set('key', id);
                state.ol.addLayer(drawing);
                if (item.ref.artifact === 'shape_source' && item.count > 8000 && item.bounds &&
                        (!target || target === item.crs)) {
                    const [west, south, east, north] = item.bounds;
                    if (west < east && south < north) {
                        const geometry = { type: 'Polygon', coordinates: [[
                            [west, south], [east, south], [east, north], [west, north], [west, south],
                        ]] };
                        const extentLayer = coverageLayer([geometry], palette[index % palette.length]);
                        drawing.set('coverage', extentLayer);
                        drawing.set('dataExtent', item.bounds);
                        state.ol.addLayer(extentLayer);
                        if (state.fitNeeded && !state.initialExtent && !hidden.includes(id))
                            fit(state.view, [drawing], state.ol.getSize());
                    }
                }
            }
            const order = 2 * (layers.length - index);
            const layerOpacity = opacity(item.style?.opacity ?? item.opacity) / 100;
            const visible = !hidden.includes(id);
            const displayStyle = JSON.stringify([index, order, item.style, layerOpacity, visible]);
            if (item.kind !== 'raster' && drawing.get('displayStyle') !== displayStyle) {
                drawing.set('displayStyle', displayStyle);
                const color = palette[index % palette.length];
                const appearance = symbol(item.style, color);
                const coverage = drawing.get('coverage');
                if (coverage) {
                    coverage.setStyle(coverageStyle(appearance.color));
                    coverage.setOpacity(layerOpacity);
                    coverage.setZIndex(order);
                }
                const classification = item.style?.classification;
                const field = classification?.field;
                const render = settings => {
                    if (!field) return style(color, settings);
                    const classStyle = (shade, fill) => style(shade, { ...settings, color: shade,
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
                        fillLayer.set('identifyKey', id);
                        fillLayer.set('pickKey', id);
                        drawing.set('fillLayer', fillLayer);
                        state.ol.addLayer(fillLayer);
                    }
                    fillLayer.setStyle(render({ ...appearance, fillOpacity: 100, noOutline: true }));
                    fillLayer.setOpacity(layerOpacity * appearance.fillOpacity / 100);
                    fillLayer.setZIndex(order - 1);
                    fillLayer.setVisible(visible);
                    drawing.setStyle(withLabels(render({ ...appearance, fillOpacity: 0 }), item.style?.labelField, item.geometry));
                } else {
                    if (fillLayer) {
                        state.ol.removeLayer(fillLayer);
                        drawing.unset('fillLayer');
                    }
                    drawing.setStyle(withLabels(render(appearance), item.style?.labelField, item.geometry));
                }
            }
            drawing.setOpacity(layerOpacity);
            drawing.setZIndex(order);
            drawing.setVisible(visible);
            drawing.get('coverage')?.setVisible(visible);
            drawing.get('rasterDetail')?.setVisible(visible);
            next.set(id, drawing);
            if (hidden.includes(id)) return null;
            if (item.kind === 'raster') {
                if (!item.crs && target) {
                    return Promise.resolve({ id, drawing, raster: true,
                        error: new Error('此影像没有地理参考。可单独按像素浏览，或补齐地理参考后叠加。') });
                }
                const bands = item.style?.rasterBands;
                const rasterStamp = JSON.stringify([target, bands]);
                let detail = drawing.get('rasterDetail');
                if (drawing.get('rasterStamp') === rasterStamp) {
                    const extent = layerOpacity === 1 && item.byteImagery && (!target || target === item.crs)
                        && rasterDetailBounds(item, bbox);
                    if (!extent) {
                        detail?.setVisible(false);
                        return Promise.resolve({ id, drawing, raster: true });
                    }
                    if (!detail) {
                        detail = new ImageLayer();
                        drawing.set('rasterDetail', detail);
                        state.ol.addLayer(detail);
                    }
                    detail.setZIndex(order + 1);
                    const detailStamp = JSON.stringify([rasterStamp, extent]);
                    if (detail.get('stamp') === detailStamp) return Promise.resolve({ id, drawing, raster: true });
                    return api.map({ action: 'raster-display', ref: item.ref, target, bands, bbox: extent })
                        .then(({ image, bounds }) => ({ id, drawing, raster: true, detail, detailStamp,
                            detailImage: new Static({ url: 'data:image/png;base64,' + image, imageExtent: bounds,
                                projection: state.view.getProjection() }) }))
                        .catch(error => ({ id, drawing, raster: true, error }));
                }
                detail?.setVisible(false);
                return api.map({ action: 'raster-display', ref: item.ref, target, bands })
                    .then(({ image, bounds }) => ({ id, drawing, raster: true, bounds, rasterStamp,
                    image: new Static({ url: 'data:image/png;base64,' + image, imageExtent: bounds,
                        projection: state.view.getProjection() }) })).catch(error => ({ id, drawing, raster: true, error }));
            }
            if (!item.crs && target) {
                return Promise.resolve({ id, drawing, error: new Error('请先定义图层坐标系') });
            }
            const plan = entries.find(entry => entry.kind === 'plan' &&
                (entry.plan.source_wkt === item.crs || entry.source_reference === item.crs) &&
                (entry.plan.target_wkt === target || entry.target_reference === display?.value));
            const planId = displayPlans[id] || (plan && plan.id + '@' + plan.revision);
            drawing.set('displayPlan', planId);
            const stamp = JSON.stringify([target, displayFields(item), planId, item.style?.outlines === true]);
            const viewportStamp = JSON.stringify([stamp, bbox]);
            if (drawing.get('completeFeatures') === stamp || drawing.get('viewportStamp') === viewportStamp)
                return Promise.resolve({ id, drawing });
            hasNewView = true;
            // Only one query per layer can be in flight. Queued views superseded by a new gesture do no work.
            const pending = (drawing.get('viewportRequest') || Promise.resolve()).then(async () => {
                if (!alive) return { id, drawing };
                try {
                    const result = await api.map({ action: 'features', ref: item.ref, bbox, interactive: true,
                        knownOverview: drawing.get('overviewKey'), target: target || null, fields: displayFields(item), plan: planId,
                        outlines: item.style?.outlines === true });
                    return { id, drawing, result, viewportStamp, full: !bbox && !result.limited ? stamp : null };
                } catch (error) {
                    return { id, drawing, error };
                }
            });
            drawing.set('viewportRequest', pending, true);
            return pending.finally(() => {
                if (drawing.get('viewportRequest') === pending) drawing.unset('viewportRequest', true);
            });
        }).filter(Boolean);
        state.byKey = next;
        setViewLoading(hasNewView);
        const updated = new Set();
        function apply(item) {
            if (!alive || item.error) return item;
            const { drawing, result } = item;
            if (item.detailImage) {
                item.detail.setSource(item.detailImage);
                item.detail.set('stamp', item.detailStamp);
                item.detail.setVisible(true);
            }
            if (item.image) {
                drawing.set('rasterStamp', item.rasterStamp);
                drawing.setExtent(item.bounds);
                drawing.set('dataExtent', item.bounds);
                drawing.setSource(item.image);
            }
            if (!result || state.view.getAnimating() || state.view.getInteracting()) return item;
            if (bbox) {
                const latest = state.view.calculateExtent(state.ol.getSize());
                if (!bbox.every((coordinate, i) => coordinate === latest[i])) return item;
            }
            if (item.full) drawing.set('completeFeatures', item.full);
            else drawing.unset('completeFeatures');
            drawing.set('viewportStamp', item.viewportStamp);
            drawing.set('clipped', Boolean(result.clipped));
            drawing.set('dataExtent', result.bounds);
            if (result.overviewKey && result.overviewKey === drawing.get('overviewKey')) return item;
            updated.add(item.id);
            // Prepare the new geometries before replacing anything visible.
            const details = result.features.map(viewportFeature);
            const old = drawing.get('coverage');
            let coverage;
            if (result.overview) {
                const index = layerKeys.indexOf(item.id);
                const color = symbol(layers[index].style, palette[index % palette.length]).color;
                coverage = coverageLayer(result.overview, color, {
                    opacity: drawing.getOpacity(), zIndex: drawing.getZIndex(),
                });
                state.ol.addLayer(coverage);
            }
            drawing.getSource().clear(true);
            drawing.getSource().addFeatures(details);
            if (old) state.ol.removeLayer(old);
            drawing.set('coverage', coverage);
            drawing.set('overviewKey', result.overviewKey);
            return item;
        }
        Promise.all(jobs.map(job => job.then(apply))).then(async items => {
            if (!alive) return;
            const errors = new globalThis.Map();
            for (const item of items.filter(item => item.error)) {
                const message = window.GuizhiErrorText(item.error);
                const affected = errors.get(message) || new Set();
                affected.add(layers.find(layer => key(layer) === item.id)?.name || '图层');
                errors.set(message, affected);
            }
            setLayerErrors([...errors].map(([message, affected]) => ({ message, names: [...affected] })));
            const overviewKeys = items.filter(item => item.drawing.get('coverage')).map(item => item.id);
            setLimited(previous => previous.length === overviewKeys.length &&
                previous.every((id, index) => id === overviewKeys[index]) ? previous : overviewKeys);
            setViewLoading(false);
            if (state.fitNeeded && items.length) {
                state.fitNeeded = false;
                const drawn = [...next.values()];
                if (state.initialExtent && state.ol.getSize()?.every(size => size > 0)) {
                    state.view.fit(state.initialExtent, { size: state.ol.getSize(), padding: [12, 12, 12, 12] });
                    state.initialExtent = null;
                } else if (drawn.some(item => item.getVisible() && (item instanceof ImageLayer ? Boolean(item.getSource()) : !!item.get('dataExtent')))) fit(state.view, drawn, state.ol.getSize());
                else if (native?.bounds && native.crs === target) state.view.fit(native.bounds, { maxZoom: 20, padding: [28, 28, 28, 28] });
                state.updateScale();
                setViewReady(true);
            }
            const selected = selectionRef.current;
            if (selected && (updated.has(selected.key) || next.get(selected.key)?.get('coverage') ||
                    !next.get(selected.key)?.getVisible())) await restoreSelection(state, selected);
            if (!alive || !items.length) return;
            // Use the next rendered frame, away from wheel/drag input, for the agent's map snapshot.
            snapshotTimer = setTimeout(() => {
                if (!alive || state.view.getAnimating() || state.view.getInteracting()) return;
                snapshotListener = state.ol.once('postrender', capture);
                state.ol.render();
            }, 180);
            function capture() {
                const size = state.ol.getSize();
                if (!alive || !size?.every(value => value > 0) || state.view.getAnimating() || state.view.getInteracting()) return;
                const factor = Math.min(1, 880 / Math.max(...size));
                const canvas = globalThis.document.createElement('canvas');
                canvas.width = Math.round(size[0] * factor);
                canvas.height = Math.round(size[1] * factor);
                const context = canvas.getContext('2d');
                context.fillStyle = backgroundColor;
                context.fillRect(0, 0, canvas.width, canvas.height);
                context.scale(factor, factor);
                drawMapLayers(context, node.current);
                void api.mapSnapshot({ keys: layerKeys.filter(id => !hidden.includes(id)),
                    bounds: state.view.calculateExtent(size), crs: target, limited: overviewKeys.length > 0,
                    scale: node.current.querySelector('.ol-scale-line-inner')?.textContent,
                    image: canvas.toDataURL('image/jpeg', .78) }).catch(fail);
            }
        }).catch(error => { if (alive) { setViewLoading(false); fail(error); } });
        return () => {
            alive = false;
            clearTimeout(snapshotTimer);
            if (snapshotListener) unByKey(snapshotListener);
        };
    }, [layers, hidden, target, units, initialExtent, viewRevision, entries, displayPlans, backgroundColor, Boolean(edit)]);

    async function restoreSelection(state, selected) {
        const request = state.selectionRequest = (state.selectionRequest || 0) + 1;
        const summarySource = state.summaryHighlight.getSource();
        summarySource.clear();
        const drawing = selected && state.byKey.get(selected.key);
        const source = drawing instanceof VectorLayer && drawing.getVisible() && drawing.getSource();
        const collection = state.select.getFeatures();
        if (!collection.getLength() && !source) return;
        collection.clear();
        if (source && selected.ids.length) {
            const ids = new Set(selected.ids);
            const item = layerList.current.find(item => key(item) === selected.key);
            state.highlight.setStyle(selectionStyle(symbol(item?.style, palette[0]).size));
            if (drawing.get('coverage') && item) {
                const result = await api.map({ action: 'features', ref: item.ref, selectionFids: selected.ids,
                    bbox: state.view.calculateExtent(state.ol.getSize()), target, plan: drawing.get('displayPlan') });
                if (graph.current !== state || state.selectionRequest !== request) return;
                summarySource.addFeatures(result.overview ? result.overview.map(geometry => feature({ geometry }))
                    : result.features.map(feature));
            } else {
                collection.extend(source.getFeatures().filter(feature => ids.has(feature.getId())));
            }
        }
    }
    useEffect(() => {
        if (graph.current) void restoreSelection(graph.current, selection).catch(fail);
    }, [selection]);
    const picked = selection?.key === active ? selection.ids : [];
    const selectedRows = tableFilter === 'selected' && selection?.key === active ? selection.ids : undefined;
    const emptyFilter = tableFilter === 'selected' && !selectedRows;
    useEffect(() => { setPage(0); setTableFilter('all'); setTableDialog(null); }, [active]);
    useEffect(() => {
        if (!layer || layer.kind === 'raster' || !table) return;
        let alive = true;
        const order = sort.key === key(layer) ? sort.order : [];
        api.map({ action: 'table', ref: layer.ref, page: tableRequest.page, order,
            focusFid: Number.isSafeInteger(focusFid) ? focusFid : undefined,
            selection: emptyFilter ? [] : selectedRows?.filter(Number.isSafeInteger) }).then(result => {
            if (alive) { setTable(result.rows); setTableLayer(key(layer)); setResolvedPage(result.page); }
        }).catch(error => { if (alive) fail(error); });
        return () => { alive = false; };
    }, [layer, tableRequest, sort, selectedRows, emptyFilter, Boolean(table)]);
    useEffect(() => {
        if (focusFid == null || !tablePanel.current) return;
        const row = [...tablePanel.current.querySelectorAll('tr[data-row-key]')].find(
            element => element.dataset.rowKey === String(focusFid));
        row?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }, [table, focusFid, tableWindow.detached]);
    async function zoomToRow(row) {
        const g = graph.current;
        if (!g || !layer || busy) return;
        const request = ++zoomRequest.current;
        try {
            const drawing = g.byKey.get(active);
            // A rendered feature can be clipped to the viewport. Use the complete stored geometry outside edit mode.
            let geometry = edit && drawing?.getSource().getFeatureById(row.id)?.getGeometry();
            if (!geometry && Number.isSafeInteger(row.id)) {
                const slice = await api.map({ action: 'features', ref: layer.ref, fids: [row.id], target, plan: drawing?.get('displayPlan') });
                const match = slice.features.find(item => item.id === row.id);
                geometry = match?.geometry && format.readGeometry(match.geometry);
            }
            if (g !== graph.current || active !== activeRef.current || request !== zoomRequest.current) return;
            if (!geometry) throw new Error('该要素没有可定位的几何');
            if (hidden.includes(active)) toggle(active);
            setSelection({ key: active, ids: [row.id] });
            const size = g.ol.getSize();
            const margin = Math.min(32, Math.floor(Math.min(...size) / 5));
            g.view.fit(geometry, { size, padding: [margin, margin, margin, margin], maxZoom: 18 });
        } catch (error) { if (request === zoomRequest.current) fail(error); }
    }
    async function invertSelection() {
        setBusy(true);
        try {
            const ids = await api.map({ action: 'table-complement', ref: layer.ref, fids: picked });
            setPage(0);
            setSelection({ key: active, ids });
        } catch (error) { fail(error); }
        finally { setBusy(false); }
    }
    function replaceAttributes(input, output) {
        const id = key(input);
        const classification = input.style?.classification;
        replace(classification && !output.fields.some(field => field.name === classification.field)
            ? { ...input, style: { ...input.style, classification: undefined } } : input, output);
        const changed = key(output);
        if (id === active && picked.length) setSelection({ key: changed, ids: picked });
        if (sort.key === id) {
            setSort({ key: changed, order: (sort.order || []).filter(item => item.field === output.fidField ||
                output.fields.some(field => field.name === item.field)) });
        }
    }
    async function saveAttribute(fid, field, value) {
        if (busy || partsDraft.current) return;
        if (edit) {
            push([{ action: 'update', id: fid, fields: { [field]: value } }]);
            applyDraft();
            return;
        }
        setBusy(true);
        try {
            const result = await api.map({ action: 'tool', operation: 'table.edit_cell', refs: [layer.ref],
                parameters: { fid, field, value } });
            const error = toolError(result);
            if (error) { fail(error); return; }
            replaceAttributes(layer, result.layers[0]);
        } catch (error) { fail(error); }
        finally { setBusy(false); }
    }
    const openTool = (operation, source = active, fromTable = false) => {
        const query = ['data.select_by_attribute', 'data.select_by_location'].includes(operation);
        if (busy || edit && !(query && edit === 'select' && !count && source === active)) return;
        setTool({ operation, active: source, lockInput: Boolean(edit), fromTable });
    };
    function addResultLayers(items) {
        const fresh = items.filter(item => !layers.some(existing => key(existing) === key(item)));
        if (!fresh.length) return;
        graph.current.fitNeeded = true;
        add(fresh);
    }
    function showToolResult(result, input, destination) {
        if (destination && ['geometry.multipart', 'geometry.explode'].includes(tool?.operation)) {
            const [output] = result.layers;
            if (!output || result.layers.length !== 1) throw new Error('没有取得完整的编辑结果');
            partsDraft.current = { input, output, destination };
            setCount(1);
            setTool(null);
            return;
        }
        if (result.selection) {
            setPointSelect(false);
            setBoxSelect(false);
            toggleIdentify(false);
            selectLayer(key(input));
            setSelection({ key: key(input), ids: result.selection.fids });
        }
        if (!result.layers.length) return;
        const operation = tool?.operation;
        const attributes = ['table.add_field', 'table.delete_field', 'table.calculate_field', 'table.calculate_geometry'].includes(operation);
        const replaceLayer = input && result.layers.length === 1 && (attributes || operation === 'crs.assign')
            ? () => (attributes ? replaceAttributes : replace)(input, result.layers[0]) : undefined;
        setTool(null);
        setToolResult({ result, replaceLayer, fromTable: tool?.fromTable });
    }
    async function saveSelectedLayer() {
        if (busy || !layer || !picked.length) return;
        setBusy(true);
        try {
            const result = await api.map({ action: 'tool', operation: 'data.export_selection',
                refs: [layer.ref], parameters: { fids: picked } });
            const error = toolError(result);
            if (error) { fail(error); return; }
            showToolResult(result);
        } catch (error) { fail(error); }
        finally { setBusy(false); }
    }
    const skillGroups = [
        ['规划与用地', ['control-check', 'farmland-balance', 'annual-land', 'permit-history'], 'parcels'],
        ['数据与影像', ['shared-delivery', 'cad-coordinates', 'imagery-change'], 'image'],
    ];
    const customSkills = catalog.filter(item => !skillGroups.some(([, ids]) => ids.includes(item.id)));
    const menuGroup = (title, icon, items, onClick, accessibleName = title) => h('section', { className: 'ribbon-group ribbon-menu' }, h(antd.Dropdown, {
        trigger: ['click'], menu: { items: items.map(item => ({ key: item.id, label: item.title, icon: glyph(item.icon || icon) })),
            onClick: ({ key: id }) => onClick(id) },
    }, h(Button, { size: 'small', icon: glyph(icon), disabled: busy,
        'aria-label': accessibleName, title: accessibleName }, title, glyph('chevron', 13))));
    const addLayer = trigger => h(antd.Dropdown, { trigger: ['click'], disabled: busy,
        menu: { items: [
            { key: 'file', label: '本地文件', icon: glyph('folderOpen') },
            { key: 'gdb', label: '文件地理数据库', icon: glyph('layers') },
            { key: 'xy', label: '坐标表（XY）', icon: glyph('target') },
        ], onClick: ({ key: mode }) => load(mode === 'file' ? api.mapImport() : api.mapImport(null, mode)) },
    }, trigger);

    const editable = Boolean(layer?.crs && !hidden.includes(active) &&
        /^(Multi)?(Point|LineString|Polygon)$/.test(layer.geometry));
    function push(changes) {
        edits.current.push({ changes, selected: selectionRef.current });
        setCount(edits.current.length);
    }
    function applyDraft() {
        const drawing = graph.current?.byKey.get(active);
        const source = drawing?.getSource();
        if (!source) return;
        drawing.unset('completeFeatures');
        const items = new globalThis.Map(original.current);
        for (const group of edits.current) for (const change of group.changes) {
            const id = change.id ?? change.fid;
            if (change.action === 'delete') items.delete(id);
            else {
                const previous = items.get(id) || {};
                items.set(id, { id, geometry: change.geometry || previous.geometry,
                    properties: { ...previous.properties, ...change.fields } });
            }
        }
        source.clear();
        source.addFeatures([...items.values()].map(feature));
        void restoreSelection(graph.current, selectionRef.current).catch(fail);
    }
    async function startEdit() {
        if (display || target !== layer.crs) {
            setDisplay(null);
            setEditLayer(layer);
            setPendingEdit(active);
            setIssue('正在切换到图层坐标系…');
            return;
        }
        const state = graph.current;
        const drawing = state.byKey.get(active);
        const source = drawing?.getSource();
        if (!source || state.fitNeeded || drawing.get('coverage')) return;
        if (drawing.get('clipped')) {
            setBusy(true);
            try {
                const ids = source.getFeatures().map(item => item.getId());
                const complete = [];
                for (let index = 0; index < ids.length; index += 500) {
                    const result = await api.map({ action: 'features', ref: layer.ref, fids: ids.slice(index, index + 500) });
                    complete.push(...result.features.map(feature));
                }
                if (graph.current !== state || activeRef.current !== active) return;
                source.clear();
                source.addFeatures(complete);
                drawing.unset('clipped');
            } catch (error) {
                fail(error);
                return;
            } finally { setBusy(false); }
        }
        original.current = new globalThis.Map(source.getFeatures().map(item => {
            const properties = item.getProperties();
            delete properties[item.getGeometryName()];
            return [item.getId(), { id: item.getId(), geometry: format.writeGeometryObject(item.getGeometry()), properties }];
        }));
        edits.current = [];
        toggleIdentify(false);
        setEdit('select');
        setBoxSelect(false);
        setIssue('');
        setRibbon('编辑');
        node.current.focus();
    }
    useEffect(() => {
        if (!pendingEdit) return;
        if (pendingEdit !== active) {
            setPendingEdit(null);
            setIssue('');
        } else if (!busy && !tool && !toolResult && viewReady && !graph.current?.fitNeeded && target === layer?.crs) {
            setPendingEdit(null);
            if (limited.includes(active) || layerErrors.some(error => error.names.includes(layer.name))) {
                setIssue('完整图层尚未加载，无法开始编辑。');
                return;
            }
            startEdit();
        }
    }, [pendingEdit, active, layer, target, busy, tool, toolResult, viewReady, limited, layerErrors]);
    function stopEdit() {
        partsDraft.current = null;
        edits.current = [];
        setCount(0);
        setEdit('');
        setFieldEdit(null);
        setIssue('');
    }
    function undoEdit() {
        if (partsDraft.current) {
            partsDraft.current = null;
            setCount(0);
            return;
        }
        if (['draw', 'split'].includes(edit) && drawingPoints > 0) { drawingTool.current?.removeLastPoint(); return; }
        if (!edits.current.length) return;
        const reverted = edits.current.pop();
        setIssue('');
        setCount(edits.current.length);
        applyDraft();
        setSelection(reverted.selected);
    }
    function leaveEdit() {
        edits.current = [];
        applyDraft();
        stopEdit();
        setConfirmExit(false);
    }
    function mapKeys(event) {
        if (event.key === 'Escape' && !busy && !event.target.closest('input, textarea, [role="dialog"]')) {
            if (measure) { setMeasure(null); return; }
            if (identify) { toggleIdentify(false); return; }
            if (edit && edit !== 'select') { setEdit('select'); setIssue(''); return; }
            if (clipboard) { cancelClipboard(); return; }
        }
        if (event.defaultPrevented || event.isComposing || !event.ctrlKey || event.altKey || event.metaKey || event.shiftKey ||
                event.target.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]')) return;
        const key = event.key.toLowerCase();
        if (['c', 'x', 'v'].includes(key) && !busy && (key === 'v' ? canPaste : canCopy)) {
            if (key === 'c' && globalThis.getSelection()?.toString()) return;
            event.preventDefault();
            if (!event.repeat) key === 'v' ? openPaste() : copyFeatures(key === 'x' ? 'cut' : 'copy');
            return;
        }
        if (!edit || key !== 'z' && key !== 's') return;
        event.preventDefault();
        if (busy || confirmExit || fieldEdit || event.repeat) return;
        if (key === 'z') undoEdit();
        else if (edits.current.length || partsDraft.current) void saveEdit();
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
            if (changes.length) {
                push(changes);
                node.current.focus({ preventScroll: true });
            }
        };
        if (edit === 'draw' || edit === 'split') {
            const splitting = edit === 'split';
            const draw = new Draw({ type: splitting ? 'LineString' : geometryFamily(layer) });
            drawingTool.current = draw;
            draw.on('drawstart', event => {
                const geometry = event.feature.getGeometry();
                const measure = () => {
                    const coordinates = geometry.getCoordinates();
                    const vertices = !splitting && layer.geometry.endsWith('Polygon') ? coordinates[0] : coordinates;
                    setDrawingPoints(Math.max(0, vertices.length - 1));
                };
                geometry.on('change', measure);
                measure();
            });
            draw.on('drawabort', () => setDrawingPoints(0));
            draw.on('drawend', event => {
                setDrawingPoints(0);
                const geometry = format.writeGeometryObject(event.feature.getGeometry());
                if (splitting) { void shapeEdit('split', pickedRef.current, geometry); return; }
                const id = 'new-' + crypto.randomUUID();
                push([{ action: 'add', id, geometry }]);
                applyDraft();
                setSelection({ key: active, ids: [id] });
                if (table) { setPage(0); setTableFilter('selected'); }
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
        if (edit !== 'select' && edit !== 'pan') {
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

    function pendingChanges() {
        const merged = new globalThis.Map();
        for (const group of edits.current) for (const change of group.changes) {
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
        return merged;
    }
    async function saveEdit() {
        if (partsDraft.current) {
            const { input, output, destination } = partsDraft.current;
            stopEdit();
            if (destination === 'current') {
                if (mapBasis.current === key(input)) mapBasis.current = key(output);
                replace(input, output);
            } else addResultLayers([output]);
            return;
        }
        const merged = pendingChanges();
        if (!merged.size) { stopEdit(); return; }
        setBusy(true);
        try {
            const saved = await api.map({ action: 'edit', ref: layer.ref, revision: layer.revision, changes: [...merged.values()] });
            if (active === mapBasis.current) mapBasis.current = key(saved);
            stopEdit();
            replace(layer, saved);
        } catch (error) { fail(error); }
        finally { setBusy(false); }
    }

    const pickedRef = useRef(picked);
    pickedRef.current = picked;
    const canCopy = edit === 'select' && !count && !busy && Boolean(layer?.crs) && /^(Point|LineString|Polygon)$/.test(geometryFamily(layer)) &&
        picked.length >= 1 && picked.length <= 500 && picked.every(Number.isSafeInteger);
    const canReshape = Boolean(edit) && /^(LineString|Polygon)$/.test(geometryFamily(layer)) && !busy && picked.length <= 500;
    async function shapeEdit(mode, ids, value) {
        setBusy(true);
        setIssue('');
        setEditDialogError('');
        try {
            const fids = ids.filter(Number.isSafeInteger);
            const originals = fids.length ? (await api.map({ action: 'features', ref: layer.ref, fids })).features : [];
            const raw = new globalThis.Map(originals.map(item => [item.id, item]));
            const pending = pendingChanges();
            const features = ids.map(id => {
                const item = raw.get(id);
                const change = pending.get(id);
                const geometry = change?.geometry || item?.geometry;
                if (!geometry || change?.action === 'delete') throw new Error('选中的要素已失效，请重新选择');
                return { geometry, fields: { ...item?.properties, ...change?.fields } };
            });
            const output = await api.map({ action: 'edit-sketch', mode, geometries: features.map(item => item.geometry),
                ...(mode === 'split' && { line: value }) });
            const proposal = mode === 'merge' ? [{ action: 'update', id: value, geometry: output.geometry },
                ...ids.filter(id => id !== value).map(id => ({ action: 'delete', id }))] : output.parts.flatMap((parts, index) =>
                parts.map((geometry, section) => section ? { action: 'add', id: 'new-' + crypto.randomUUID(), geometry,
                    fields: features[index].fields } : { action: 'update', id: ids[index], geometry }));
            if (new Set([...pending.keys(), ...proposal.map(item => item.id)]).size > 500) throw new Error('一次最多保存 500 个要素的编辑，请分批保存');
            push(proposal);
            applyDraft();
            setSelection({ key: active, ids: proposal.filter(item => item.action !== 'delete').map(item => item.id) });
            setMergeTarget(null);
            setEdit('select');
        } catch (error) {
            const message = window.GuizhiErrorText(error);
            if (mode === 'split') setIssue(message);
            else setEditDialogError(message);
        } finally { setBusy(false); }
    }
    function copyFeatures(mode) {
        setClipboard({ key: active, mode, kind: geometryFamily(layer), fids: [...picked] });
        setIoStatus((mode === 'cut' ? '待剪切 ' : '已复制 ') + picked.length + ' 个要素');
    }
    function cancelClipboard() {
        setClipboard(null);
        setIoStatus('');
    }
    async function pasteFeatures() {
        const destination = pasteTargets.find(item => key(item) === pasteTarget);
        if (!canPaste || !destination) return;
        setBusy(true);
        try {
            const result = await api.map({ action: 'edit-transfer', ref: pasteSource.ref, revision: pasteSource.revision,
                destination: destination.ref, destinationRevision: destination.revision, fids: clipboard.fids, mode: clipboard.mode });
            const output = result.find(item => item.ref.artifact === 'export');
            const moved = result.find(item => item.ref.artifact === 'transfer-source');
            if (!output || clipboard.mode === 'cut' && !moved) throw new Error('未找到粘贴后的图层');
            const replacements = moved ? [[pasteSource, moved], [destination, output]] : [[destination, output]];
            for (const [before, after] of replacements) if (key(before) === mapBasis.current) mapBasis.current = key(after);
            if (edit) stopEdit();
            setPasteTarget(null);
            replaceLayers(replacements);
            if (moved) cancelClipboard();
            setIoStatus('已粘贴 ' + clipboard.fids.length + ' 个要素到「' + destination.name + '」');
        } catch (error) { setIssue(window.GuizhiErrorText(error)); }
        finally { setBusy(false); }
    }

    async function openFields() {
        const fid = picked[0];
        const pending = pendingChanges().get(fid)?.fields;
        try {
            const fields = typeof fid === 'number' ? (await api.map({ action: 'features', ref: layer.ref, fids: [fid] })).features[0].properties : {};
            setFieldEdit({ fid, values: { ...fields, ...pending }, changed: {} });
        } catch (error) { fail(error); }
    }

    const tableFields = layer?.fidField ? [{ label: 'FID', value: layer.fidField },
        ...layer.fields.map(field => ({ label: field.name, value: field.name }))] : [];
    const cellOrder = sort.key === active ? sort.order : [];
    const columns = tableFields.map(({ label, value }, index) => ({
        title: label, key: value, sorter: true, width: index ? undefined : 75,
        sortOrder: cellOrder[0]?.field === value ? cellOrder[0].direction : null,
        render: (_, row) => {
            const raw = index ? row.values[value] : row.id;
            return h(AttributeCell, { value: raw, field: value, fid: row.id,
                disabled: !index || busy || Boolean(partsDraft.current), fail, commit: saveAttribute });
        },
    }));
    const draftChanges = edit ? pendingChanges() : new globalThis.Map();
    const draftRows = [...draftChanges].filter(([id, change]) => change.action === 'add' &&
        (tableFilter !== 'selected' || picked.includes(id))).map(([id, change]) => ({ id, values: change.fields || {} }));
    const persistedRows = tableLayer === active ? (table || []).filter(row => draftChanges.get(row.id)?.action !== 'delete')
        .map(row => ({ ...row, values: { ...row.values, ...draftChanges.get(row.id)?.fields } })) : [];
    const countDraft = layer?.count + [...draftChanges.values()].reduce((total, change) =>
        total + Number(change.action === 'add') - Number(change.action === 'delete'), 0);
    const command = (label, onClick, props = {}) => h(Button, { size: 'small', icon: glyph(commandIcons[label]),
        onClick, ...props, disabled: props.disabled || (Boolean(partsDraft.current) && !['撤销', '保存', '退出'].includes(label)) }, label);
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
            command(title, () => { setEdit(mode); if (mode === 'select') setBoxSelect(false); }, { key: mode, disabled: busy || (['modify', 'move'].includes(mode) && !picked.length),
                type: edit === mode ? 'primary' : 'default' })),
            command('合并要素', () => { setEditDialogError(''); setMergeTarget(picked[0]); }, { disabled: !canReshape || picked.length < 2 }),
            command('按线分割', () => { setIssue('绘制穿过所选要素的分割线，双击结束。'); setBoxSelect(false); setEdit('split'); node.current.focus(); },
                { type: edit === 'split' ? 'primary' : 'default', disabled: !canReshape || !picked.length }),
            edit === 'draw' && drawingPoints > 0 && command('完成绘制', () => drawingTool.current?.finishDrawing(),
                { disabled: drawingPoints < (layer.geometry.endsWith('Polygon') ? 3 : 2) }),
            edit === 'split' && drawingPoints >= 2 && command('完成分割', () => drawingTool.current?.finishDrawing(), { disabled: busy }),
            edit === 'draw' && drawingPoints > 0 && command('撤销绘制点', () => drawingTool.current?.removeLastPoint())),
        group('属性与删除', command('编辑属性', openFields, { disabled: busy || picked.length !== 1 || !layer.fields.length }),
            command('删除要素', () => {
                push(picked.map(id => ({ action: 'delete', id }))); applyDraft(); clear(); setEdit('select');
            }, { disabled: busy || !picked.length })),
        group('提交', command('撤销', undoEdit, { disabled: busy || !(count || drawingPoints), title: 'Ctrl+Z', 'aria-keyshortcuts': 'Control+Z' }),
        command('保存', saveEdit, { disabled: busy || !count, loading: busy, type: 'primary', title: 'Ctrl+S', 'aria-keyshortcuts': 'Control+S' }),
        command('退出', () => count ? setConfirmExit(true) : leaveEdit(), { disabled: busy })),
    ] : [group('图层编辑', command('编辑图层', startEdit, { disabled: !editable || busy || viewLoading || !viewReady || Boolean(pendingEdit) || limited.includes(active) }))];
    const partsReady = !busy && !count && edit === 'select';
    function openPartsTool(operation) {
        if (!partsReady) return;
        setTool({ operation, active, lockInput: true });
    }
    const showLayer = item => item.name.toLowerCase().includes(layerSearch.toLowerCase());
    const visibleLayers = layers.filter(showLayer);
    const canReorder = !edit && !busy && visibleLayers.length > 1;
    function reorder(source, target, before) {
        if (!display && native?.crs) mapBasis.current = key(native);
        moveLayer(source, target, before);
    }
    function endLayerDrag() {
        draggedLayer.current = null;
        setDropEdge(null);
    }
    function layerDrop(event, id, drop) {
        const source = draggedLayer.current;
        if (!source) return;
        event.preventDefault();
        event.stopPropagation();
        if (!canReorder || source === id) { if (drop) endLayerDrag(); else setDropEdge(null); return; }
        const bounds = event.currentTarget.getBoundingClientRect();
        const before = event.clientY < bounds.top + bounds.height / 2;
        if (drop) {
            reorder(source, id, before);
            endLayerDrag();
        } else {
            event.dataTransfer.dropEffect = 'move';
            setDropEdge(old => old?.id === id && old.before === before ? old : { id, before });
        }
    }
    function layerHandle(item) {
        const id = key(item);
        return h('button', { type: 'button', className: 'map-layer-grip', disabled: !canReorder, draggable: canReorder,
            title: '拖动排序，或按上下方向键', 'aria-label': '调整' + item.name + '的顺序', 'aria-keyshortcuts': 'ArrowUp ArrowDown',
            onDragStart: event => {
                draggedLayer.current = id;
                event.dataTransfer.effectAllowed = 'move';
                event.dataTransfer.setData('application/x-guizhi-layer', id);
                const row = event.currentTarget.closest('.map-layer-row');
                event.dataTransfer.setDragImage(row, 12, event.clientY - row.getBoundingClientRect().top);
            }, onDragEnd: endLayerDrag,
            onKeyDown: event => {
                if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
                event.preventDefault();
                const before = event.key === 'ArrowUp';
                const index = visibleLayers.findIndex(layer => key(layer) === id);
                const target = visibleLayers[index + (before ? -1 : 1)];
                if (target) {
                    const handle = event.currentTarget;
                    reorder(id, key(target), before);
                    requestAnimationFrame(() => { if (handle.isConnected) handle.focus(); });
                }
            },
        }, glyph('grip', 14));
    }
    function layerMenu(item, index) {
        const id = key(item);
        const raster = item.kind === 'raster';
        return { items: [
            { key: 'fit', label: '缩放至图层', icon: glyph('target') },
            { key: 'table', label: '打开属性表', icon: glyph('table'), disabled: raster },
            { key: 'rename', label: '重命名', icon: glyph('pencil') },
            { key: 'style', label: raster ? '影像显示' : '符号化', icon: glyph(raster ? 'image' : 'swatches') },
            ...(!raster && item.geometry && item.count > 8000 ? [{ key: 'outlines', icon: glyph('layers'),
                label: item.style?.outlines ? '切换为自动概览' : '全图显示要素轮廓' }] : []),
            ...(!raster ? [{ key: 'labels', label: '标注', icon: glyph('label') }] : []),
            { key: 'export', label: '导出', icon: glyph('download'), children: [
                { key: 'export-data', label: '导出为…' },
                ...(!raster ? [{ key: 'export-table', label: '导出属性表' }] : []),
            ] },
            ...(id === active && picked.length && !raster ? [
                { key: 'selection', label: '选中要素另存为图层', icon: glyph('download') }] : []),
            { type: 'divider' },
            { key: 'remove', label: '从地图移除', icon: glyph('removeLayer'), danger: true },
            { type: 'divider' },
            { key: 'properties', label: '图层属性', icon: glyph('settings') },
        ], onClick: ({ key: action }) => {
            if (action === 'remove') { remove(id); return; }
            if (action === 'rename') { setRenamingLayer(id); return; }
            if (action === 'outlines') {
                styleLayer(id, { ...item.style, outlines: !item.style?.outlines });
                return;
            }
            if (action === 'export-table') { openTool('presentation.export_table', id); return; }
            if (action === 'export-data') {
                const appearance = !raster && layerSymbol({ ...item.style, opacity: item.style?.opacity ?? item.opacity },
                    palette[index % palette.length]);
                const pointIcon = appearance && /Point/.test(item.geometry) ?
                    style('#ffffff', { ...appearance, color: '#ffffff' }).getImage().getImage(1).toDataURL('image/png') : undefined;
                api.mapExport(item.ref, false, appearance || undefined, pointIcon).catch(fail);
                return;
            }
            if (['properties', 'style', 'labels'].includes(action)) {
                setProperties({ item, color: palette[index % palette.length], start: action === 'properties' ? 'info' : action });
                return;
            }
            if (action === 'selection') { void saveSelectedLayer(); return; }
            if (action === 'table') {
                if (id !== active) selectLayer(id);
                if (selection?.key === id && selection.ids.length) focusRow(id, selection.ids[0]);
                else setPage(0);
                setTableFilter('all');
                setTable(current => current || []);
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
    function layerName(item, index) {
        const id = key(item);
        if (renamingLayer === id) {
            const confirm = event => {
                const name = event.currentTarget.value.trim();
                if (name && name !== item.name) renameLayer(id, name);
                setRenamingLayer(null);
            };
            return h(Input, { className: 'map-layer-name', autoFocus: true, size: 'small', defaultValue: item.name,
                maxLength: 120, 'aria-label': '图层名称', onFocus: event => event.currentTarget.select(), onBlur: confirm,
                onPressEnter: confirm, onKeyDown: event => {
                    if (event.key === 'Escape') {
                        event.stopPropagation();
                        event.currentTarget.value = item.name;
                        setRenamingLayer(null);
                    }
                } });
        }
        return h(antd.Dropdown, { trigger: ['contextMenu'], menu: layerMenu(item, index), disabled: Boolean(edit) || busy },
            h(Button, { type: 'text', className: id === active ? 'map-layer-active' : '',
                title: item.group ? item.group + ' / ' + item.name : item.name, disabled: Boolean(edit) || busy,
                onClick: () => { setPage(0); setEditLayer(null); selectLayer(id); },
                onDoubleClick: () => setRenamingLayer(id), onKeyDown: event => {
                    if (event.key === 'F2') { event.preventDefault(); setRenamingLayer(id); }
                } }, item.name));
    }
    const notices = new globalThis.Map(layerErrors.map(({ message, names }) => [message, names]));
    for (const message of [document ? '' : ioStatus, issue]) {
        if (message && !notices.has(message)) notices.set(message, []);
    }
    const coordinateTools = () => h(CoordinateTools, { layer, display: mapProperties.display,
        setDisplay: item => setMapProperties({ display: item, plan: undefined }),
        displayPlan: mapProperties.plan, setDisplayPlan: value => setMapProperties(old => ({ ...old, plan: value })),
        entries, openTool: operation => { setMapProperties(null); openTool(operation); }, fail });
    function applyMapProperties(color) {
        const sameDisplay = mapProperties.display === display;
        setBackground(color);
        if (!sameDisplay) setDisplay(mapProperties.display);
        if (!sameDisplay || displayPlans[active] !== mapProperties.plan) {
            setDisplayPlans(old => ({ ...(sameDisplay ? old : {}), ...(active && mapProperties.display && { [active]: mapProperties.plan }) }));
        }
    }
    const commands = {
        地图: [group('导航',
            command('平移', () => { toggleIdentify(false); setPointSelect(false); setBoxSelect(false); if (edit) setEdit('pan'); node.current?.focus(); },
                { icon: glyph('hand'), title: '按住鼠标左键拖动地图', 'aria-pressed': !pointSelect && !boxSelect && !identify && !measure && (!edit || edit === 'pan') }),
            command('全图', () => position(false), { disabled: !layers.length }),
            command('定位图层', () => position(true), { disabled: !layer }),
            command('放大', () => graph.current.view.adjustZoom(1)), command('缩小', () => graph.current.view.adjustZoom(-1))),
            group('量测', ...[['测距', 'LineString', 'measure'], ['测面', 'Polygon', 'area']].map(([label, type, icon]) =>
                command(label, () => startMeasure(type), { icon: glyph(icon), disabled: busy || Boolean(edit) || !canMeasure,
                    'aria-pressed': measure === type }))),
            group('查询', command('识别要素', () => toggleIdentify(!identify), { disabled: busy || !layers.some(item => item.geometry),
                type: identify ? 'primary' : 'default', 'aria-pressed': identify })),
            group('显示', command('地图属性', () => setMapProperties({ display, plan: displayPlans[active] }), { disabled: busy }))],
        数据: [...(!edit ? [group('地图工程', command('打开', () => mxd(api.mxdRead(), value => { setLocateMxd(null); setDocument(value); }),
            { disabled: busy, 'aria-label': '打开地图工程' }),
            command('保存', () => mxd(api.mxdWrite({ crs: target, bounds: graph.current?.view.calculateExtent(graph.current.ol.getSize()),
                background: backgroundColor,
                layers: [...layers].reverse().map((item, index) => ({ ref: item.ref, name: item.name, visible: !hidden.includes(key(item)), opacity: opacity(item.style?.opacity ?? item.opacity),
                    style: item.kind === 'raster' ? null : layerSymbol(item.style, palette[(layers.length - index - 1) % palette.length]) })) }),
            result => setIoStatus('已保存 MXD 和配套数据 · ' + result.layers + ' 个图层')),
            { disabled: busy || !layers.length || !viewReady, 'aria-label': '保存地图工程' })),
            group('图层管理', addLayer(command('添加数据', undefined, { icon: glyph('add'), disabled: busy, 'aria-haspopup': 'menu' })),
                command('新建图层', () => openTool('geometry.create_dataset')),
                command('复制图层', () => openTool('data.copy'), { disabled: !layer?.geometry }),
                command('合并图层', () => openTool('data.merge'), { disabled: layers.filter(item => item.geometry).length < 2 }))] : []),
            group('选择',
                command('点选', () => { toggleIdentify(false); setPointSelect(!pointSelect); setBoxSelect(false); },
                    { icon: glyph('select'), 'aria-pressed': !identify && !boxSelect && (pointSelect || edit === 'select'),
                        disabled: busy || Boolean(edit) && edit !== 'select' || !layer?.geometry || hidden.includes(active) }),
                command('框选', () => { toggleIdentify(false); setBoxSelect(!boxSelect); setPointSelect(false); node.current.focus(); },
                    { title: '向右拖动选中相交要素，向左拖动仅选中完全框住的要素。Shift 追加，Ctrl 减选。', 'aria-pressed': boxSelect, type: boxSelect ? 'primary' : 'default',
                        disabled: busy || Boolean(edit) && edit !== 'select' || !layer?.geometry || hidden.includes(active) }),
                command('按属性选择', () => { toggleIdentify(false); openTool('data.select_by_attribute'); },
                    { disabled: busy || Boolean(edit) && (edit !== 'select' || count > 0) || !layer?.geometry }),
                command('按位置选择', () => { toggleIdentify(false); openTool('data.select_by_location'); },
                    { disabled: busy || Boolean(edit) && (edit !== 'select' || count > 0) || !layer?.geometry || layers.filter(item => item.geometry).length < 2 }),
                command('清除选择', clear, { disabled: busy || edit === 'split' || !picked.length }))],
        编辑: [...(edit ? [
            group('剪贴板', command('复制', () => copyFeatures('copy'), { disabled: !canCopy, title: 'Ctrl+C', 'aria-keyshortcuts': 'Control+C' }),
                command('剪切', () => copyFeatures('cut'), { disabled: !canCopy, title: 'Ctrl+X', 'aria-keyshortcuts': 'Control+X' }),
                command('粘贴', openPaste, { icon: glyph('paste'), disabled: !canPaste,
                    title: !clipboard ? '请先复制或剪切要素' : count ? '请先保存或放弃当前编辑' :
                        !pasteTargets.length ? '请先添加同类型的目标图层' : '选择粘贴到哪个图层 · Ctrl+V', 'aria-keyshortcuts': 'Control+V' }),
                clipboard && command('清空剪贴板', cancelClipboard, { icon: glyph('close'), disabled: busy })),
            group('多部件',
                command('合并为多部件', () => openPartsTool('geometry.multipart'),
                    { disabled: !partsReady || !canCopy || picked.length < 2, title: '选择两个或更多同图层要素' }),
                command('拆分多部件', () => openPartsTool('geometry.explode'),
                    { disabled: !partsReady || !picked.length || !picked.every(Number.isSafeInteger) })),
        ] : []), ...editCommands],
        制图: [group('图件', command('版面布局', () => openManualLayout('manual'),
        { icon: glyph('layout'), disabled: busy }))],
        分析: TOOL_GROUPS.map(({ title, icon, tools }) => {
            const prominent = { '统计': 2, '矢量处理': 2, '叠加分析': 3 }[title] || 0;
            return prominent ? group(title, ...tools.slice(0, prominent).map(item => command(item.title, () => openTool(item.id),
                { key: item.id, icon: glyph(item.icon), disabled: busy,
                    'data-ai-focus': activity?.operation === item.id ? 'true' : undefined })),
                ...(tools.length > prominent ? [menuGroup('更多', icon, tools.slice(prominent), openTool, '更多' + title + '工具')] : []))
                : menuGroup(title, icon, tools, openTool);
        }),
        业务: [...skillGroups.map(([title, ids, icon]) => [title, catalog.filter(item => ids.includes(item.id)), icon]),
            ['自定义业务', customSkills, 'briefcase']].filter(([, items]) => items.length)
            .map(([title, items, icon]) => menuGroup(title, icon, items.map(item => ({ ...item, icon: businessIcon(item.id) })),
                id => setAnalysis({ skill: items.find(item => item.id === id) }))),
    };
    return h('section', { className: 'workspace-map', 'aria-label': '地图工作区', onKeyDown: mapKeys,
        'data-identify': identify ? 'true' : undefined, 'data-box-select': boxSelect && (!edit || edit === 'select') ? 'true' : undefined,
        'data-edit-mode': edit || undefined,
        'data-map-mode': measure || identify || pointSelect || boxSelect || (edit && edit !== 'pan') ? 'tool' : 'pan',
        onDragOver: event => { if (event.dataTransfer.types.includes('Files')) event.preventDefault(); },
        onDrop: event => {
            if (!event.dataTransfer.files.length) return;
            event.preventDefault();
            if (!busy && !edit) void load(api.mapDrop([...event.dataTransfer.files]));
        } },
        h('header', { className: 'map-ribbon' },
            h('div', { className: 'ribbon-tabs' },
                h('div', { role: 'tablist', 'aria-label': '地图工具' }, ...Object.keys(commands).map(tab => h(Button, {
                    key: tab, type: 'text', icon: glyph(tabIcons[tab], 16), role: 'tab', 'aria-selected': ribbon === tab,
                    disabled: busy || Boolean(edit) && !['编辑', '数据'].includes(tab), onClick: () => setRibbon(tab),
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
                ...layers.map((item, index) => showLayer(item) &&
                    h('div', { className: 'map-layer-entry', key: key(item),
                        'data-drop-edge': dropEdge?.id === key(item) ? (dropEdge.before ? 'before' : 'after') : undefined,
                        onDragOver: event => layerDrop(event, key(item), false), onDrop: event => layerDrop(event, key(item), true),
                        onDragLeave: event => {
                            if (draggedLayer.current && !event.currentTarget.contains(event.relatedTarget)) {
                                setDropEdge(old => old?.id === key(item) ? null : old);
                            }
                        } },
                    h('div', { className: 'map-layer-row', 'data-ai-focus': focused(item) ? 'true' : undefined },
                    layerHandle(item),
                    h('input', { type: 'checkbox', 'aria-label': '显示 ' + item.name, checked: !hidden.includes(key(item)), disabled: Boolean(edit) || busy,
                        onChange: () => toggle(key(item)) }),
                    h('span', { className: 'map-swatch', 'data-color': index % palette.length,
                        ref: node => { if (node) node.style.backgroundColor = symbol(item.style, palette[index % palette.length]).color; } }),
                    layerName(item, index),
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
                measure && h('div', { className: 'map-measure-readout', role: 'status' },
                    h('strong', null, measure === 'Polygon' ? '面积' : '距离'), measurement && h('output', null, measurement),
                    h('span', null, '单击添加顶点，双击完成'), h(Button, { size: 'small', onClick: () => setMeasure(null) }, '结束量测')),
                identifyHost && identified && window.ReactDOM.createPortal(h('section', { className: 'map-identify-popup',
                    'aria-label': '要素属性', 'data-above': identified.above, 'data-align': identified.align,
                    style: { width: identified.width, maxHeight: Math.min(252, identified.height) } },
                    h('header', null, h('strong', { title: identified.name }, identified.name),
                        h(Button, { type: 'text', size: 'small', icon: glyph('close', 13), 'aria-label': '关闭要素属性', onClick: closeIdentification })),
                    identified.values ? h('dl', null, ...[['FID', identified.fid], ['要素类型', ({
                        Point: '点', MultiPoint: '多点', LineString: '线', MultiLineString: '多线',
                        Polygon: '面', MultiPolygon: '多面',
                    })[identified.geometryType] || identified.geometryType || '空几何'], ...Object.entries(identified.values)].map(([name, value], i) =>
                        h('div', { key: i }, h('dt', { title: name }, name), h('dd', null, value === null ? '空值' : String(value))))) :
                        h('p', { role: identified.error ? 'alert' : 'status' }, identified.error || '正在读取属性…')), identifyHost),
                activity && h('div', { className: 'map-ai-note', 'data-kind': activity.kind },
                    glyph(({ read: 'layers', transform: 'globe', calculate: 'analysis', publish: 'save', draw: 'pencil' })[activity.kind] || 'target'),
                    h('span', null, activity.label), activity.target && h('strong', null, activity.target)),
                importing && h('div', { className: 'map-import-progress', role: 'status' },
                    h('span', null, importing.label),
                    importing.total ? h('progress', { max: importing.total, value: importing.completed,
                        'aria-label': '图层导入进度' }) : h('progress', { 'aria-label': '图层导入进度' })),
                !layers.length && addLayer(h(Button, { className: 'map-empty', type: 'default', icon: glyph('add', 30),
                    title: '添加或拖入空间数据', 'aria-label': '添加或拖入空间数据', disabled: busy })),
                (notices.size > 0 || busy) && h('div', { className: 'map-notice', role: 'status' },
                    busy ? '正在处理地图数据…' :
                        [...notices].map(([message, names]) => h('div', { key: message }, message,
                            names.length > 0 && h('span', { className: 'muted' }, '（' + names.join('、') + '）')))))),
        h('footer', { className: 'map-status' },
            h('span', null, layers.length ? layers.length + ' 个图层' : '暂无图层'),
            layer && h('span', null, layer.name),
            picked.length > 0 && h('span', null, '已选 ' + picked.length + ' 个要素'),
            h('span', { className: 'map-status-crs', title: display?.label || native?.crsName }, glyph('globe', 14),
                h('span', null, display?.label || native?.crsName || '未设置坐标系')),
            h('span', { ref: scaleLabel, className: 'map-status-measure', 'aria-label': '当前比例尺', title: '屏幕比例尺' }, '1 : —'),
            h('span', { ref: cursorLabel, className: 'map-status-coordinates', 'aria-label': '鼠标坐标', title: '地图坐标 · X / Y' }, 'X —　Y —')),
        tableVisible && tableWindow.present(h('section', { ref: tablePanel, className: 'map-attribute-panel', 'aria-label': '属性表' },
            h('header', { className: 'map-attribute-head' }, h('strong', {
                className: 'map-table-grip', title: tableWindow.detached ? undefined : '拖出属性表',
                ...tableWindow.dragHandle }, glyph(tableWindow.detached ? 'table' : 'grip'), '属性表',
                h('span', { className: 'map-table-name' }, layer.name)),
                command('添加字段', () => openTool('table.add_field', active, true), { disabled: busy || Boolean(edit) }),
                command('删除字段', () => openTool('table.delete_field', active, true), { disabled: busy || Boolean(edit) || !layer.fields?.length }),
                command('字段计算器', () => openTool('table.calculate_field', active, true), { disabled: busy || Boolean(edit) || !layer.fields?.length }),
                command('计算几何', () => openTool('table.calculate_geometry', active, true), {
                    disabled: busy || Boolean(edit) || !layer.crs || !Object.keys(geometryProperties(layer)).length }),
                command('高级排序', () => setTableDialog({ kind: 'sort' }), { icon: glyph('sort'), disabled: busy }),
                command('统计', () => setTableDialog({ kind: 'stats' }), { icon: glyph('statistics'), disabled: busy || !tableFields.length || Boolean(count) }),
                command('切换选择', invertSelection, { icon: glyph('select'), disabled: busy || Boolean(count) }),
                h(Button, { type: 'text', size: 'small', className: 'map-table-detach',
                    icon: glyph(tableWindow.detached ? 'dockTable' : 'floatTable', 16),
                    title: tableWindow.detached ? '停靠属性表' : '分离属性表',
                    'aria-label': tableWindow.detached ? '停靠属性表' : '分离属性表',
                    onClick: () => tableWindow.detached ? tableWindow.dock() : tableWindow.detach() }),
                h(Button, { type: 'text', size: 'small', icon: glyph('close', 15),
                    'aria-label': '关闭属性表', onClick: closeTable })),
            h(Table, { className: 'map-attributes', size: 'small', columns, rowKey: 'id', dataSource: [...(page === 0 ? draftRows : []), ...persistedRows],
                onRow: row => ({ onDoubleClick: event => {
                    if (!event.target.closest('input, button, label, textarea')) void zoomToRow(row);
                } }),
                onChange: (_, filters, sorter, { action }) => {
                    if (action !== 'sort') return;
                    setPage(0);
                    setSort({ key: active, order: sorter.order ? [{ field: sorter.columnKey, direction: sorter.order }] : [] });
                },
                scroll: tableWindow.detached ? undefined : { x: 'max-content', y: 146 }, rowSelection: { selectedRowKeys: picked, preserveSelectedRowKeys: true,
                    onChange: ids => { if (tableFilter === 'selected') setPage(0); setSelection({ key: active, ids }); } },
                pagination: { current: page + 1, pageSize: 50,
                    total: tableFilter === 'selected' ? picked.length : countDraft,
                    showSizeChanger: false, onChange: value => setPage(value - 1) } }),
            h('div', { className: 'map-table-status' },
                h('span', { role: 'status' }, '已选 ' + picked.length.toLocaleString('zh-CN') + ' / ' + countDraft.toLocaleString('zh-CN') + ' 个要素'),
                h(antd.Segmented, { size: 'small', 'aria-label': '显示要素', value: tableFilter,
                    options: [{ label: '全部', value: 'all' }, { label: '已选', value: 'selected' }],
                    onChange: value => { setTableFilter(value); setPage(0); } })))),
        tableDialog?.kind === 'sort' && tableWindow.present(h(AttributeSort, { getContainer: tableWindow.container, columns: tableFields, order: cellOrder,
            close: () => setTableDialog(null), apply: order => { setPage(0); setSort({ key: active, order }); } })),
        tableDialog?.kind === 'stats' && tableWindow.present(h(AttributeStatistics, { getContainer: tableWindow.container, columns: tableFields, source: layer.ref,
            selected: picked, close: () => setTableDialog(null) })),
        pasteTarget !== null && h(Modal, { open: true, centered: true, title: '粘贴要素', className: 'gis-dialog',
            rootClassName: 'gis-modal-root', okText: '粘贴', cancelText: '取消', confirmLoading: busy,
            onOk: pasteFeatures, onCancel: () => { if (!busy) setPasteTarget(null); } },
            h('div', { className: 'map-field-editor' },
                h('label', null, '目标图层', h(Choice, { value: pasteTarget, 'aria-label': '目标图层', disabled: busy,
                    options: pasteTargets.map(item => ({ value: key(item), label: item.name })), onChange: setPasteTarget })),
                issue && h('p', { className: 'error', role: 'alert' }, issue))),
        sourceDialog && h(MapSourceDialog, { key: sourceDialog.id, source: sourceDialog, close: () => setSourceDialog(null),
            add: result => load(Promise.resolve(result)) }),
        properties && h(LayerProperties, { ...properties, close: () => setProperties(null), glyph,
            save: value => styleLayer(key(properties.item), value) }),
        mapProperties && h(MapProperties, { background: backgroundColor, save: applyMapProperties, coordinates: coordinateTools(),
            close: () => setMapProperties(null), glyph }),
        layout && !layout.signal?.aborted && h(MapLayout, { key: layout.key, initial: layout.config,
            change: saveLayout, draw: drawLayout, sourceRevision: layoutSource, layerChoices: layers.map(item => ({ value: key(item), label: item.name })),
            onRendered: layout.taskId ? issue => {
                if (layout.signal?.aborted) return;
                const jobId = layout.taskId;
                setLayout(previous => previous?.taskId === jobId ? { ...previous, taskId: null } : previous);
                void (issue ? onLayout(jobId, issue) : api.mapLayoutReady(jobId)).catch(fail)
                    .finally(() => onLayoutSettled(jobId));
            } : undefined,
            busy, close: () => setLayout(null), exportImage }),
        document && h(MxdOpen, { document, busy, glyph, locateAvailable: locateMxd, close: () => setDocument(null),
            open: (frame, locate) => mxd(api.mxdImport(frame, locate), result => {
                if (result.layers.length) {
                    graph.current.fitNeeded = true;
                    graph.current.initialExtent = result.frame.bounds;
                    mapBasis.current = null;
                    setDisplayPlans({});
                    setLayerErrors([]);
                    setExpandedLegends({});
                    setTable(null);
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
        tool && tableWindow.present(h(MapTool, { getContainer: tool.fromTable ? tableWindow.container : undefined, ...tool, layers, selection, components, openComponents: () => { setTool(null); openComponents(); },
            glyph, busy, setBusy, close: () => setTool(null), onResult: showToolResult }), Boolean(tool.fromTable)),
        toolResult && tableWindow.present(h(MapResultDialog, { getContainer: toolResult.fromTable ? tableWindow.container : undefined, result: toolResult.result,
            close: () => setToolResult(null),
            add: () => { if (edit) stopEdit(); addResultLayers(toolResult.result.layers); setToolResult(null); },
            replace: toolResult.replaceLayer && (() => { toolResult.replaceLayer(); setToolResult(null); }) }), Boolean(toolResult.fromTable)),
        analysis && h(MapProcessing, { ...analysis, layers, entries, components, openComponents, glyph, busy,
            setBusy, close: () => setAnalysis(null), Preview, onAddLayers: addResultLayers }),
        mergeTarget !== null && h(Modal, { open: true, centered: true, title: '合并要素', className: 'gis-dialog',
            rootClassName: 'gis-modal-root', okText: '合并', cancelText: '取消', confirmLoading: busy,
            onOk: () => void shapeEdit('merge', picked, mergeTarget), onCancel: () => { if (!busy) setMergeTarget(null); } },
            h('div', { className: 'map-field-editor' },
                h('label', null, '属性来源', h(Choice, { value: mergeTarget, 'aria-label': '属性来源', disabled: busy,
                    options: picked.map((id, index) => ({ value: id, label: typeof id === 'number' ? '要素 ' + id : '新建要素 ' + (index + 1) })),
                    onChange: setMergeTarget })), editDialogError && h('p', { role: 'alert', className: 'error' }, editDialogError))),
        confirmExit && h(Modal, { open: true, centered: true, title: '放弃未保存的编辑？', className: 'gis-dialog',
            rootClassName: 'gis-modal-root', okText: '放弃', cancelText: '继续编辑', okButtonProps: { danger: true },
            onOk: leaveEdit, onCancel: () => { setConfirmExit(false); node.current?.focus(); } }),
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

window.GuizhiMap = { Preview, WorkspaceMap, key, roleLayerPicker, layerMatches, mapLayerMaterial, initialStyle: index => layerSymbol(undefined, palette[index % palette.length]) };
