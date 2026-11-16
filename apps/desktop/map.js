import 'ol/ol.css';
import { MapProcessing, TOOL_GROUPS, toolIcon, roleLayerPicker, layerMatches, mapLayerMaterial } from './map-processing.js';
import { MapFlow } from './map-flow.js';
import { geometryProperties } from './map-attribute-tools.js';
import { AttributeSort, AttributeStatistics, AttributeCell } from './map-table-ui.js';
import { MapLayout, layoutDefaults, mapCanvas, mapImage } from './map-layout.js';
import { LayerProperties, MapProperties, MxdOpen, categoryValue, defaultMapBackground, layerSymbol, opacity, symbol } from './map-layer-dialog.js';
import Map from 'ol/Map.js';
import Overlay from 'ol/Overlay.js';
import View from 'ol/View.js';
import Projection from 'ol/proj/Projection.js';
import ImageLayer from 'ol/layer/Image.js';
import Static from 'ol/source/ImageStatic.js';
import VectorLayer from 'ol/layer/Vector.js';
import VectorSource from 'ol/source/Vector.js';
import GeoJSON from 'ol/format/GeoJSON.js';
import Select from 'ol/interaction/Select.js';
import Draw from 'ol/interaction/Draw.js';
import DragBox from 'ol/interaction/DragBox.js';
import Modify from 'ol/interaction/Modify.js';
import Translate from 'ol/interaction/Translate.js';
import Snap from 'ol/interaction/Snap.js';
import { never, primaryAction, singleClick, shiftKeyOnly, noModifierKeys } from 'ol/events/condition.js';
import ScaleLine from 'ol/control/ScaleLine.js';
import { Fill, Stroke, Style, Circle, RegularShape } from 'ol/style.js';
import { createEmpty, extend, isEmpty } from 'ol/extent.js';
import { getPointResolution } from 'ol/proj.js';

const h = window.React.createElement;
const { useEffect, useRef, useState } = window.React;
const { Button, Input, Select: Choice, Table, Modal } = window.antd;
const { icon: glyph, businessIcon } = window.GuizhiShell;
const api = window.guizhi;
const format = new GeoJSON();
const palette = ['#1785db', '#bf6f2b', '#17a489', '#8263ca', '#cc5785'];
const commandIcons = {
    全图: 'extent', 定位图层: 'target', 放大: 'zoomIn', 缩小: 'zoomOut', 清除选择: 'clear',
    按属性选择: toolIcon('data.select_by_attribute'), 按位置选择: toolIcon('data.select_by_location'), 添加图层: 'add', 最近成果: 'history',
    添加字段: toolIcon('table.add_field'), 删除字段: toolIcon('table.delete_field'), 新建图层: toolIcon('geometry.create_dataset'),
    复制图层: toolIcon('data.copy'), 合并图层: toolIcon('data.merge'), 地图属性: 'settings',
    编辑图层: 'pencil', 选择: 'select', 绘制: 'pencil', 编辑顶点: 'node', 移动: 'move',
    完成绘制: 'check', 撤销绘制点: 'undo', 编辑属性: 'table', 删除要素: 'trash',
    撤销: 'undo', 退出: 'close', 识别要素: 'info', 框选: 'box',
    合并要素: 'merge', 分割要素: 'split', 完成分割: 'check', 复制: 'copy', 剪切: 'cut',
    打开: 'folderOpen', 保存: 'save', 字段计算器: toolIcon('table.calculate_field'), 计算几何: toolIcon('table.calculate_geometry'),
};
const tabIcons = { 地图: 'map', 数据: 'layers', 编辑: 'pencil', 分析: 'analysis', 制图: 'layout', 业务: 'briefcase' };

const roles = { project: '项目范围', coverage: '重叠范围' };
const key = item => item.ref.run + ':' + item.ref.artifact;
const projection = (code, units) => new Projection({ code: code || 'local', units: units || 'm' });
const fillColor = (color, percent) => color + Math.round(percent * 255 / 100).toString(16).padStart(2, '0');
const classFill = (settings, color, fill) => settings.fill === null || fill === null ? null : fill || color;
const feature = item => format.readFeature({ type: 'Feature', ...item });
const style = (color, settings) => {
    const appearance = symbol(settings, color);
    const lineDash = appearance.pattern === 'dash' ? [9, 5] : appearance.pattern === 'dot' ? [2, 4] : undefined;
    const stroke = !appearance.noOutline ? new Stroke({ color: appearance.color, width: appearance.width, lineDash }) : undefined;
    const fill = new Fill({ color: appearance.fill ? fillColor(appearance.fill, appearance.fillOpacity) : 'rgba(0,0,0,0)' });
    const point = { radius: appearance.size / 2, fill: new Fill({ color: appearance.color }),
        stroke: new Stroke({ color: appearance.color, width: 1 }) };
    const shape = appearance.shape;
    const image = shape === 'circle' ? new Circle(point) : new RegularShape({ ...point,
        points: 4, radius2: shape === 'cross' ? 0 : undefined, angle: shape === 'square' ? Math.PI / 4 : 0 });
    return new Style({ stroke, fill, image });
};
const layerFor = (items, color) => new VectorLayer({
    source: new VectorSource({ features: items.map(feature) }), style: style(color),
});
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

function WorkspaceMap({ layers, add, remove, moveLayer, renameLayer, selectLayer, active, hidden, toggle, entries, catalog, components,
    openComponents, display, setDisplay, background, setBackground, styleLayer, importFrame, fail, activity,
    requestedLayout, onLayout, mapActions, onMapActions, selection, setSelection, replace, replaceLayers, editing }) {
    const node = useRef(null);
    const graph = useRef(null);
    const [properties, setProperties] = useState(null);
    const [mapProperties, setMapProperties] = useState(false);
    const [layout, setLayout] = useState(false);
    const [layoutDraft, setLayoutDraft] = useState(null);
    const [document, setDocument] = useState(null);
    const [cursor, setCursor] = useState(null);
    const [scale, setScale] = useState(null);
    const [viewReady, setViewReady] = useState(false);
    const [ioStatus, setIoStatus] = useState('');
    const [importIssues, setImportIssues] = useState([]);
    const [displayPlans, setDisplayPlans] = useState({});
    const [editLayer, setEditLayer] = useState(null);
    const [edit, setEdit] = useState('');
    const [boxSelect, setBoxSelect] = useState(false);
    const [pointSelect, setPointSelect] = useState(false);
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
    const [flowOpen, setFlowOpen] = useState(null);
    const [flowRequest, setFlowRequest] = useState(null);
    const [lastResult, setLastResult] = useState(null);
    const [count, setCount] = useState(0);
    const [fieldEdit, setFieldEdit] = useState(null);
    const edits = useRef([]);
    const drawingTool = useRef(null);
    const [drawingPoints, setDrawingPoints] = useState(0);
    const original = useRef(new globalThis.Map());
    const [busy, setBusy] = useState(false);
    selectionMode.current = { boxSelect, pointSelect, edit, busy };
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
    const [tableFilter, setTableFilter] = useState('all');
    const [tableDialog, setTableDialog] = useState(null);
    const [limited, setLimited] = useState([]);
    const [viewRevision, setViewRevision] = useState(0);
    const selectionRef = useRef(selection);
    selectionRef.current = selection;
    const layer = layers.find(item => key(item) === active);
    const geometryFamily = item => item?.geometry?.replace(/^Multi/, '');
    const pasteSource = clipboard && layers.find(item => key(item) === clipboard.key);
    const canPaste = Boolean(edit === 'select' && !busy && layer?.crs && pasteSource && active !== clipboard.key &&
        geometryFamily(layer) === clipboard.kind);
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
    function closeIdentification() {
        identification.current += 1;
        graph.current?.identify.setPosition(undefined);
        setIdentified(null);
    }
    function toggleIdentify(enabled) {
        identifying.current = enabled;
        setIdentify(enabled);
        if (!enabled) closeIdentification();
    }
    useEffect(() => {
        if (identified && (!layers.some(item => key(item) === identified.layer) || hidden.includes(identified.layer))) closeIdentification();
    }, [layers, hidden, identified]);
    const activeRef = useRef(active);
    activeRef.current = active;
    function drawLayout(config) {
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
        return mapCanvas(graph.current.ol, node.current, backgroundColor, legend, config);
    }
    async function exportImage(config, taskId) {
        setBusy(true);
        try {
            await api.mapLayout({ format: config.format,
                data: mapImage(drawLayout(config), config.format), name: config.fileName || config.title, taskId });
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
        const selectable = () => !identifying.current && !selectionMode.current.busy &&
            (!selectionMode.current.edit || selectionMode.current.edit === 'select') &&
            Boolean(graph.current?.byKey.get(activeRef.current) instanceof VectorLayer && graph.current.byKey.get(activeRef.current).getVisible());
        const subtract = event => event.originalEvent.ctrlKey && !event.originalEvent.shiftKey;
        const select = new Select({ condition: event => selectable() && !selectionMode.current.boxSelect &&
            (selectionMode.current.edit === 'select' || selectionMode.current.pointSelect) && singleClick(event),
            layers: candidate => candidate.get('key') === activeRef.current || candidate.get('pickKey') === activeRef.current,
            addCondition: shiftKeyOnly, removeCondition: subtract, toggleCondition: never, hitTolerance: 5, style: null });
        ol.addInteraction(select);
        const highlight = showSelection(ol, select);
        select.on('select', event => {
            const layer = activeRef.current;
            const changed = event.selected.map(item => item.getId());
            const previous = selectionRef.current?.key === layer ? selectionRef.current.ids : [];
            const added = shiftKeyOnly(event.mapBrowserEvent);
            const removed = subtract(event.mapBrowserEvent);
            const ids = removed ? previous.filter(id => !event.deselected.some(item => item.getId() === id))
                : added ? [...new Set([...previous, ...changed])] : select.getFeatures().getArray().map(item => item.getId());
            setSelection({ key: layer, ids });
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
        });
        ol.addInteraction(box);
        const host = globalThis.document.createElement('div');
        const overlay = new Overlay({ element: host, position: undefined, positioning: 'bottom-center', stopEvent: true });
        ol.addOverlay(overlay);
        setIdentifyHost(host);
        setIdentified(null);
        graph.current = { ol, view, select, highlight, identify: overlay, byKey: new globalThis.Map(), fitNeeded: true, initialExtent, updateScale };
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
                const values = await api.map({ action: 'table-feature', ref: hit.item.ref, fid: hit.fid });
                if (request === identification.current) setIdentified({ ...record, values });
            } catch (error) { if (request === identification.current) setIdentified({ ...record, error: window.GuizhiErrorText(error) }); }
        });
        const observer = new ResizeObserver(() => ol.updateSize());
        observer.observe(node.current);
        const moved = () => { if (!graph.current?.fitNeeded) updateScale(); setViewRevision(value => value + 1); };
        ol.on('moveend', moved);
        const viewport = node.current;
        return () => { identification.current += 1; viewport.removeEventListener('pointerleave', clearCursor); observer.disconnect(); ol.dispose(); graph.current = null; };
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
        const source = drawing instanceof VectorLayer && drawing.getVisible() && drawing.getSource();
        const collection = state.select.getFeatures();
        if (!collection.getLength() && !source) return;
        collection.clear();
        if (source && selected.ids.length) {
            const ids = new Set(selected.ids);
            const item = layerList.current.find(item => key(item) === selected.key);
            state.highlight.setStyle(selectionStyle(symbol(item?.style, palette[0]).size));
            collection.extend(source.getFeatures().filter(feature => ids.has(feature.getId())));
        }
    }
    useEffect(() => {
        if (graph.current) restoreSelection(graph.current, selection);
    }, [selection]);
    const picked = selection?.key === active ? selection.ids : [];
    const selectedRows = tableFilter === 'selected' && selection?.key === active ? selection.ids : undefined;
    const emptyFilter = tableFilter === 'selected' && !selectedRows;
    useEffect(() => { setPage(0); setTableFilter('all'); setTableDialog(null); }, [active]);
    useEffect(() => {
        if (!layer || layer.kind === 'raster' || !table) return;
        let alive = true;
        const order = sort.key === key(layer) ? sort.order : [];
        api.map({ action: 'table', ref: layer.ref, page, order, selection: emptyFilter ? [] : selectedRows }).then(result => {
            if (alive) { setTable(result.rows); setTableLayer(key(layer)); }
        }).catch(error => { if (alive) fail(error); });
        return () => { alive = false; };
    }, [layer, page, sort, selectedRows, emptyFilter, Boolean(table)]);
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
    async function saveAttribute(value) {
        if (!tableDialog?.cell || busy) return;
        const { fid, field } = tableDialog.cell;
        setBusy(true);
        try {
            const result = await api.map({ action: 'tool', operation: 'table.edit_cell', refs: [layer.ref],
                parameters: { fid, field, value } });
            replaceAttributes(layer, result.layers[0]);
            setTableDialog(null);
        } catch (error) { fail(error); }
        finally { setBusy(false); }
    }
    const openTool = operation => {
        setFlowRequest({ id: crypto.randomUUID(), operation, layer: active });
        setFlowOpen(true);
    };
    async function saveSelectedLayer() {
        if (busy || !layer || !picked.length || picked.length > 500) return;
        setBusy(true);
        try {
            const result = await api.map({ action: 'tool', operation: 'data.export_selection',
                refs: [layer.ref], parameters: { fids: picked } });
            add(result.layers);
            setIoStatus('已将所选要素另存为图层');
        } catch (error) { fail(error); }
        finally { setBusy(false); }
    }
    const skillGroups = [
        ['规划与用地', ['control-check', 'farmland-balance', 'annual-land', 'permit-history'], 'parcels'],
        ['数据与影像', ['shared-delivery', 'cad-coordinates', 'imagery-change'], 'image'],
    ];
    const customSkills = catalog.filter(item => !skillGroups.some(([, ids]) => ids.includes(item.id)));
    const menuGroup = (title, icon, items, onClick) => h('section', { className: 'ribbon-group ribbon-menu' }, h(antd.Dropdown, {
        trigger: ['click'], menu: { items: items.map(item => ({ key: item.id, label: item.title, icon: glyph(item.icon || icon) })),
            onClick: ({ key: id }) => onClick(id) },
    }, h(Button, { size: 'small', icon: glyph(icon), disabled: busy }, title, glyph('chevron', 13))));

    const editable = Boolean(layer?.crs && !hidden.includes(active) &&
        /^(Multi)?(Point|LineString|Polygon)$/.test(layer.geometry));
    function push(changes, transfer) {
        edits.current.push({ changes, selected: selectionRef.current, transfer });
        setCount(edits.current.length);
    }
    function applyDraft() {
        const source = graph.current?.byKey.get(active)?.getSource();
        if (!source) return;
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
        original.current = new globalThis.Map(source.getFeatures().map(item => {
            const properties = item.getProperties();
            delete properties[item.getGeometryName()];
            return [item.getId(), { id: item.getId(), geometry: format.writeGeometryObject(item.getGeometry()), properties }];
        }));
        edits.current = [];
        toggleIdentify(false);
        setEdit('select');
        setIssue('');
        setRibbon('编辑');
        node.current.focus();
    }
    function stopEdit() {
        edits.current = [];
        setCount(0);
        setEdit('');
        setFieldEdit(null);
        setIssue('');
    }
    function undoEdit() {
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
            if (!event.repeat) key === 'v' ? void pasteFeatures() : copyFeatures(key === 'x' ? 'cut' : 'copy');
            return;
        }
        if (!edit || key !== 'z' && key !== 's') return;
        event.preventDefault();
        if (busy || confirmExit || fieldEdit || event.repeat) return;
        if (key === 'z') undoEdit();
        else if (edits.current.length) void saveEdit();
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
        const merged = pendingChanges();
        if (!merged.size) { stopEdit(); return; }
        setBusy(true);
        try {
            const transfers = new globalThis.Map();
            for (const group of edits.current) {
                const cut = group.transfer;
                if (!cut) continue;
                const ids = transfers.get(cut.key) || new Set();
                group.changes.forEach((addition, index) => {
                    if (merged.has(addition.id)) ids.add(cut.fids[index]);
                });
                transfers.set(cut.key, ids);
            }
            const saved = await api.map({ action: 'edit', ref: layer.ref, revision: layer.revision, changes: [...merged.values()] });
            const pairs = [];
            for (const [sourceKey, fids] of transfers) {
                if (!fids.size) continue;
                const source = layers.find(item => key(item) === sourceKey);
                if (!source) throw new Error('剪切的源图层已移除');
                const next = await api.map({ action: 'edit', ref: source.ref, revision: source.revision,
                    changes: [...fids].map(fid => ({ action: 'delete', fid })) });
                pairs.push([source, next]);
            }
            pairs.push([layer, saved]);
            for (const [before, after] of pairs) if (key(before) === mapBasis.current) mapBasis.current = key(after);
            stopEdit();
            replaceLayers(pairs);
        } catch (error) { fail(error); }
        finally { setBusy(false); }
    }

    const pickedRef = useRef(picked);
    pickedRef.current = picked;
    const canCopy = (!edit || edit === 'select' && !count) && !busy && Boolean(layer?.crs) && /^(Point|LineString|Polygon)$/.test(geometryFamily(layer)) &&
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
        if (!canPaste) return;
        setIssue('');
        setBusy(true);
        try {
            const prepared = await api.map({ action: 'edit-transfer-plan', ref: pasteSource.ref, revision: pasteSource.revision,
                destination: layer.ref, destinationRevision: layer.revision, fids: clipboard.fids });
            if (pendingChanges().size + prepared.length > 500) throw new Error('一次最多保存 500 个要素的编辑，请分批保存');
            const changes = prepared.map(item => ({ action: 'add', id: 'new-' + crypto.randomUUID(),
                geometry: item.geometry, fields: item.properties }));
            push(changes, clipboard.mode === 'cut' ? { ...clipboard, fids: [...clipboard.fids].sort((a, b) => a - b) } : null);
            applyDraft();
            setSelection({ key: active, ids: changes.map(item => item.id) });
            if (clipboard.mode === 'cut') cancelClipboard();
            setIssue('已粘贴 ' + prepared.length + ' 个要素，可撤销或保存。');
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
            return h('span', { className: 'gis-cell', tabIndex: 0, title: '双击编辑或复制',
                onDoubleClick: () => setTableDialog({ cell: { value: raw, field: value, fid: row.id, readonly: !index || Boolean(edit), fail } }),
                onKeyDown: event => {
                    if (event.key !== 'F2' && event.key !== 'Enter') return;
                    event.preventDefault();
                    setTableDialog({ cell: { value: raw, field: value, fid: row.id, readonly: !index || Boolean(edit), fail } });
                } }, String(raw ?? '—'));
        },
    }));
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
            command(title, () => { setEdit(mode); if (mode === 'select') setBoxSelect(false); }, { key: mode, disabled: busy || (['modify', 'move'].includes(mode) && !picked.length),
                type: edit === mode ? 'primary' : 'default' })),
            command('合并要素', () => { setEditDialogError(''); setMergeTarget(picked[0]); }, { disabled: !canReshape || picked.length < 2 }),
            command('分割要素', () => { setIssue('绘制穿过所选要素的分割线，双击结束。'); setEdit('split'); node.current.focus(); },
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
    ] : [group('图层编辑', command('编辑图层', startEdit, { disabled: !editable || busy || limited.includes(active) }))];
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
            { key: 'style', label: raster ? '显示' : '符号化', icon: glyph(raster ? 'image' : 'swatches') },
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
            if (action.startsWith('export-')) {
                const table = action === 'export-table';
                const appearance = !table && !raster && layerSymbol({ ...item.style, opacity: item.style?.opacity ?? item.opacity },
                    palette[index % palette.length]);
                const pointIcon = appearance && /Point/.test(item.geometry) ?
                    style('#ffffff', { ...appearance, color: '#ffffff' }).getImage().getImage(1).toDataURL('image/png') : undefined;
                api.mapExport(item.ref, table, appearance || undefined, pointIcon).catch(fail);
                return;
            }
            if (action === 'properties' || action === 'style') {
                setProperties({ item, color: palette[index % palette.length], start: action === 'style' ? 'style' : 'info' });
                return;
            }
            if (action === 'selection') { void saveSelectedLayer(); return; }
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
    const coordinateTools = () => h(CoordinateTools, { layer, display,
        setDisplay: item => { setDisplayPlans({}); setDisplay(item); },
        displayPlan: displayPlans[active], setDisplayPlan: value => setDisplayPlans(old => ({ ...old, [active]: value })),
        entries, request: api.map, onLayer: item => replace(layer, item), fail });
    const commands = {
        地图: [group('导航',
            command('平移', () => { toggleIdentify(false); setPointSelect(false); setBoxSelect(false); },
                { icon: glyph('hand'), 'aria-pressed': !pointSelect && !boxSelect && !identify }),
            command('全图', () => position(false), { disabled: !layers.length }),
            command('定位图层', () => position(true), { disabled: !layer }),
            command('放大', () => graph.current.view.adjustZoom(1)), command('缩小', () => graph.current.view.adjustZoom(-1))),
            group('查询',
                command('点选', () => { toggleIdentify(false); setPointSelect(!pointSelect); setBoxSelect(false); },
                    { icon: glyph('select'), 'aria-pressed': pointSelect && !identify, disabled: busy || !layer?.geometry }),
                command('识别要素', () => toggleIdentify(!identify), { disabled: busy || !layers.some(item => item.geometry),
                type: identify ? 'primary' : 'default', 'aria-pressed': identify }),
                command('按属性选择', () => { toggleIdentify(false); openTool('data.select_by_attribute'); },
                    { disabled: busy || !layer?.geometry }),
                command('按位置选择', () => { toggleIdentify(false); openTool('data.select_by_location'); },
                    { disabled: busy || !layer?.geometry || layers.filter(item => item.geometry).length < 2 })),
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
        编辑: [group('选择',
            command('框选', () => { toggleIdentify(false); setBoxSelect(!boxSelect); node.current.focus(); },
                { title: '向右拖动选中相交要素，向左拖动仅选中完全框住的要素。Shift 追加，Ctrl 减选。', 'aria-pressed': boxSelect, type: boxSelect ? 'primary' : 'default',
                    disabled: busy || edit && edit !== 'select' || !layer?.geometry || hidden.includes(active) }),
            command('清除选择', clear, { disabled: busy || edit === 'split' || !picked.length })),
            group('剪贴板', command('复制', () => copyFeatures('copy'), { disabled: !canCopy, title: 'Ctrl+C', 'aria-keyshortcuts': 'Control+C' }),
                command('剪切', () => copyFeatures('cut'), { disabled: !canCopy, title: 'Ctrl+X', 'aria-keyshortcuts': 'Control+X' }),
                command('粘贴', () => void pasteFeatures(), { icon: glyph('paste'), disabled: !canPaste,
                    title: clipboard ? '编辑兼容图层后按 Ctrl+V 粘贴' : '请先复制或剪切要素', 'aria-keyshortcuts': 'Control+V' }),
                clipboard && command('清空剪贴板', cancelClipboard, { icon: glyph('close'), disabled: busy })),
            ...editCommands],
        制图: [group('图件', command('版面布局', () => setLayout(true), { icon: glyph('layout'), disabled: busy || !viewReady || !layers.length }))],
        分析: [group('流程', command('流程编辑器', () => setFlowOpen(true), {
            icon: glyph('workflow'), disabled: Boolean(edit),
        })), ...TOOL_GROUPS.map(({ title, icon, tools }) => {
            const prominent = { '统计': 2, '矢量处理': 2, '叠加分析': 2 }[title] || 0;
            return prominent ? group(title, ...tools.slice(0, prominent).map(item => command(item.title, () => openTool(item.id),
                { key: item.id, icon: glyph(item.icon), disabled: busy,
                    'data-ai-focus': activity?.operation === item.id ? 'true' : undefined })),
                ...(tools.length > prominent ? [menuGroup('更多', icon, tools.slice(prominent), openTool)] : []))
                : menuGroup(title, icon, tools, openTool);
        })],
        业务: [...skillGroups.map(([title, ids, icon]) => [title, catalog.filter(item => ids.includes(item.id)), icon]),
            ['自定义业务', customSkills, 'briefcase']].filter(([, items]) => items.length)
            .map(([title, items, icon]) => menuGroup(title, icon, items.map(item => ({ ...item, icon: businessIcon(item.id) })),
                id => setAnalysis({ skill: items.find(item => item.id === id) }))),
    };
    return h('section', { className: 'workspace-map', 'aria-label': '地图工作区', onKeyDown: mapKeys,
        'data-identify': identify ? 'true' : undefined, 'data-box-select': boxSelect && (!edit || edit === 'select') ? 'true' : undefined,
        'data-edit-mode': edit || undefined,
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
                identifyHost && identified && window.ReactDOM.createPortal(h('section', { className: 'map-identify-popup',
                    'aria-label': '要素属性', 'data-above': identified.above, 'data-align': identified.align,
                    style: { width: identified.width, maxHeight: Math.min(252, identified.height) } },
                    h('header', null, h('strong', { title: identified.name }, identified.name),
                        h(Button, { type: 'text', size: 'small', icon: glyph('close', 13), 'aria-label': '关闭要素属性', onClick: closeIdentification })),
                    identified.values ? h('dl', null, ...Object.entries({ FID: identified.fid, ...identified.values }).map(([name, value]) =>
                        h('div', { key: name }, h('dt', { title: name }, name), h('dd', null, value === null ? '空值' : String(value))))) :
                        h('p', { role: identified.error ? 'alert' : 'status' }, identified.error || '正在读取属性…')), identifyHost),
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
                command('添加字段', () => openTool('table.add_field'), { disabled: busy || Boolean(edit) }),
                command('删除字段', () => openTool('table.delete_field'), { disabled: busy || Boolean(edit) || !layer.fields?.length }),
                command('字段计算器', () => openTool('table.calculate_field'), { disabled: busy || Boolean(edit) || !layer.fields?.length }),
                command('计算几何', () => openTool('table.calculate_geometry'), {
                    disabled: busy || Boolean(edit) || !Object.keys(geometryProperties(layer)).length }),
                command('高级排序', () => setTableDialog({ kind: 'sort' }), { icon: glyph('sort'), disabled: busy }),
                command('统计', () => setTableDialog({ kind: 'stats' }), { icon: glyph('statistics'), disabled: busy || !tableFields.length }),
                command('切换选择', invertSelection, { icon: glyph('select'), disabled: busy }),
                h(Button, { type: 'text', size: 'small', icon: glyph('close', 15),
                    'aria-label': '关闭属性表', onClick: () => { setTable(null); setTableDialog(null); } })),
            h(Table, { className: 'map-attributes', size: 'small', columns, rowKey: 'id', dataSource: tableLayer === active ? table : [],
                onChange: (_, filters, sorter, { action }) => {
                    if (action !== 'sort') return;
                    setPage(0);
                    setSort({ key: active, order: sorter.order ? [{ field: sorter.columnKey, direction: sorter.order }] : [] });
                },
                scroll: { x: 'max-content', y: 146 }, rowSelection: { selectedRowKeys: picked, preserveSelectedRowKeys: true,
                    onChange: ids => { if (tableFilter === 'selected') setPage(0); setSelection({ key: active, ids }); } },
                pagination: { current: page + 1, pageSize: 50,
                    total: tableFilter === 'selected' ? picked.length : layer.count,
                    showSizeChanger: false, onChange: value => setPage(value - 1) } }),
            h('div', { className: 'map-table-status' },
                h('span', { role: 'status' }, '已选 ' + picked.length.toLocaleString('zh-CN') + ' / ' + layer.count.toLocaleString('zh-CN') + ' 个要素'),
                h(antd.Segmented, { size: 'small', 'aria-label': '显示要素', value: tableFilter,
                    options: [{ label: '全部', value: 'all' }, { label: '已选', value: 'selected' }],
                    onChange: value => { setTableFilter(value); setPage(0); } }))),
        tableDialog?.kind === 'sort' && h(AttributeSort, { columns: tableFields, order: cellOrder,
            close: () => setTableDialog(null), apply: order => { setPage(0); setSort({ key: active, order }); } }),
        tableDialog?.kind === 'stats' && h(AttributeStatistics, { columns: tableFields, source: layer.ref,
            selected: picked, close: () => setTableDialog(null) }),
        tableDialog?.cell && h(AttributeCell, { cell: tableDialog.cell, busy, close: () => setTableDialog(null), save: saveAttribute }),
        properties && h(LayerProperties, { ...properties, close: () => setProperties(null), glyph,
            save: value => styleLayer(key(properties.item), value) }),
        mapProperties && h(MapProperties, { background: backgroundColor, save: setBackground, coordinates: coordinateTools(),
            close: () => setMapProperties(false), glyph }),
        layout && h(MapLayout, { initial: layoutDraft || { ...layoutDefaults, title: layer?.name || '小六地图' },
            change: setLayoutDraft, draw: drawLayout,
            busy, close: () => setLayout(false), exportImage }),
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
        flowOpen !== null && h(MapFlow, { open: flowOpen, layers, busy, setBusy, glyph, request: flowRequest, components, openComponents,
            onApply: replaceAttributes,
            close: () => setFlowOpen(false), onLayers: items => {
                graph.current.fitNeeded = true;
                add(items.filter(item => !layers.some(existing => key(existing) === key(item))));
            }, onHighlight: (layer, ids) => {
                setPointSelect(false);
                setBoxSelect(false);
                toggleIdentify(false);
                selectLayer(key(layer));
                setSelection({ key: key(layer), ids });
            } }),
        analysis && h(MapProcessing, { ...analysis, layers, entries, components, openComponents, glyph, busy,
            setBusy, close: () => setAnalysis(null), Preview, onResult: result => {
                setLastResult({ skill: analysis.skill, initialResult: result });
                if (result.layers.length) {
                    graph.current.fitNeeded = true;
                    add(result.layers);
                }
            } }),
        mergeTarget !== null && h(Modal, { open: true, centered: true, title: '合并要素', className: 'gis-dialog',
            rootClassName: 'gis-modal-root', okText: '合并', cancelText: '取消', confirmLoading: busy,
            onOk: () => void shapeEdit('merge', picked, mergeTarget), onCancel: () => { if (!busy) setMergeTarget(null); } },
            h('div', { className: 'map-field-editor' },
                h('label', null, '保留属性', h(Choice, { value: mergeTarget, 'aria-label': '保留属性', disabled: busy,
                    options: picked.map((id, index) => ({ value: id, label: typeof id === 'number' ? 'FID ' + id : '新建要素 ' + (index + 1) })),
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
