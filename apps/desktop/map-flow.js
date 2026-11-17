import { ReactFlow, Background, Controls, Handle, Position, applyEdgeChanges, applyNodeChanges, useUpdateNodeInternals } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { acceptsFlowLayer, flowForm, flowInputIssue, toolIcon, toolTitle } from './map-processing.js';
import { AttributePreview } from './map-pickers.js';

const h = window.React.createElement;
const { useEffect, useState } = window.React;
const { Button, Input, Modal, Popover } = window.antd;
const api = window.guizhi;
const layerKey = layer => layer.ref.run + ':' + layer.ref.artifact;
const dragType = 'application/x-guizhi-flow';
const outputs = { layer: '图层', selection: '筛选结果', statistics: '统计结果', table: '属性表',
    values: '唯一值', condition: '查询条件', number: '数值' };
const headings = { area_m2: '合并面积（平方米）', feature_count: '要素数', selected_count: '选中要素数',
    updated: '计算要素数', row_count: '记录数', distinct_count: '唯一值数', width: '宽度（像元）',
    height: '高度（像元）', band_count: '波段数' };
const writesFields = new Set(['table.add_field', 'table.delete_field', 'table.calculate_geometry', 'table.calculate_field']);
const inputTool = { title: '图层来源', inputs: [], inputTypes: [], output: 'layer' };
const graphNode = (node, definition, result, failed) => ({ id: node.id, type: 'operation', position: { x: node.x, y: node.y },
    data: { ...node, definition, result, failed }, ariaLabel: node.label || definition.title });
const edge = (source, target, port) => ({ id: [source, target, port].join(':'), source, target, targetHandle: String(port) });
const connection = link => ({ source: link.source, target: link.target, port: Number(link.targetHandle) });
const cleanGraph = (nodes, edges, forms, layerFor) => ({ nodes: nodes.map(node => ({
    id: node.id, operation: node.data.operation,
    label: layerFor(node.id)?.name.slice(0, 100) || node.data.label?.trim() || node.data.definition.title, x: Math.round(node.position.x),
    y: Math.round(node.position.y), settings: node.data.operation === 'input'
        ? { layerRef: layerFor(node.id)?.ref || node.data.settings.layerRef } : node.data.settings,
    parameters: forms.get(node.id)?.parameters || {},
})), connections: edges.map(connection) });

function OperationNode({ id, data }) {
    const { definition, failed, result, editor, name, lock, actions, showOutput } = data;
    const updateHandles = useUpdateNodeInternals();
    const [preview, showPreview] = useState(false);
    const [renaming, rename] = useState(false);
    useEffect(() => { updateHandles(id); }, [id, showOutput, updateHandles]);
    const layer = result?.layers[0];
    return h('div', { className: 'gis-flow-node', 'data-kind': definition.output,
        'data-state': failed ? 'error' : result ? 'done' : undefined },
        renaming ? h(Input, { className: 'nodrag', size: 'small', autoFocus: true, maxLength: 100, 'aria-label': '节点名称',
            value: data.label ?? '', onChange: event => name(event.target.value), onBlur: () => rename(false),
            onPressEnter: () => rename(false) })
            : h('strong', { title: '双击重命名', onDoubleClick: () => { if (!lock && definition !== inputTool) rename(true); } }, data.label || definition.title),
        [...definition.inputs, ...definition.valuePorts || []].map((label, index) =>
            h('div', { key: index, className: 'gis-flow-port', 'data-port-type': definition.inputTypes[index] },
                h(Handle, { id: String(index), type: 'target', position: Position.Left,
                    title: label, 'aria-label': label }), label)),
        editor.length > 0 && h('fieldset', { className: 'gis-flow-fields nodrag nopan nowheel', disabled: lock },
            editor.map((control, index) => h(window.React.Fragment, { key: index }, control))),
        result && h('div', { className: 'gis-flow-result nodrag nopan nowheel' },
            Object.entries(result.statistics || {}).filter(([name, value]) => headings[name] && typeof value === 'number').map(([name, value]) =>
                h('div', { key: name }, headings[name] + ' ' + value.toLocaleString('zh-CN', { maximumFractionDigits: 3 }))),
            definition.output !== 'condition' && Object.hasOwn(result, 'value') && (Array.isArray(result.value)
                ? h('details', null, h('summary', null, '查看唯一值'), h('div', { className: 'gis-flow-values' },
                    result.value.map((value, i) => h('div', { key: i }, value == null ? '空值' : value === '' ? '空字符串' : String(value)))))
                : h('output', null, String(result.value))),
            result.statistics?.band_statistics && h('details', null, h('summary', null, '波段统计'),
                result.statistics.band_statistics.map(band => h('p', { key: band.band },
                    '波段 ' + band.band + ' · 有效像元 ' + band.valid_count + ' · 平均值 ' + (band.mean?.toFixed(3) ?? '—')))),
            h('div', { className: 'gis-flow-result-actions' },
                layer && h(Button, { size: 'small', disabled: lock, onClick: () => actions.add() }, '加入地图'),
                layer?.geometry && h(Button, { size: 'small', onClick: () => showPreview(!preview) }, preview ? '收起' : '预览'),
                actions.apply && h(Button, { size: 'small', disabled: lock, onClick: actions.apply }, '应用到原图层'),
                actions.highlight && h(Button, { size: 'small', disabled: lock, onClick: actions.highlight }, '高亮原图层'),
                result.table && h(Button, { size: 'small', disabled: lock, onClick: actions.table }, '导出 CSV')),
            preview && layer?.geometry && h(AttributePreview, { key: layerKey(layer), layer, field: data.field })),
        showOutput && h('div', { className: 'gis-flow-output' }, outputs[definition.output],
            h(Handle, { type: 'source', position: Position.Right, title: '连接到下游节点', 'aria-label': outputs[definition.output] + '输出' })));
}
const nodeTypes = { operation: OperationNode };

export function MapFlow({ open, layers, busy, setBusy, close, glyph, onLayers, onHighlight, onApply, request, components }) {
    const [catalog, setCatalog] = useState([]);
    const [nodes, setNodes] = useState([]);
    const [edges, setEdges] = useState([]);
    const [bindings, setBindings] = useState({});
    const [selected, select] = useState(null);
    const [diagram, setDiagram] = useState(null);
    const [search, setSearch] = useState('');
    const [savedFlows, setSavedFlows] = useState(null);
    const [name, setName] = useState('');
    const [flowId, setFlowId] = useState(null);
    const [saveOpen, setSaveOpen] = useState(false);
    const [listOpen, setListOpen] = useState(false);
    const [deleting, setDeleting] = useState(null);
    const [lastRequest, setLastRequest] = useState(null);
    const [pending, setPending] = useState(false);
    const [running, setRunning] = useState(false);
    const [stopping, setStopping] = useState(false);
    const [progress, setProgress] = useState('');
    const [results, setResults] = useState({});
    const [failed, setFailed] = useState(null);
    const [issues, setIssues] = useState([]);
    const [message, setMessage] = useState('');
    const [error, setError] = useState('');
    const definition = id => id === 'input' ? inputTool : catalog.find(item => item.operation === id);
    const reportError = problem => setError(window.GuizhiErrorText(problem));
    useEffect(() => {
        let current = true;
        api.map({ action: 'flow-tools' }).then(items => {
            if (current) setCatalog(items.map(item => ({ ...item, title: toolTitle(item.operation) || item.title })));
        })
            .catch(problem => { if (current) reportError(problem); });
        return () => { current = false; };
    }, []);
    useEffect(() => {
        if (!running) return;
        let current = true, timer;
        async function check() {
            try {
                const next = await api.mapFlowProgress();
                if (current && next) setProgress(next);
            } catch { /* Completion and errors are returned by the run request. */ }
            if (current) timer = setTimeout(check, 550);
        }
        void check();
        return () => { current = false; clearTimeout(timer); };
    }, [running]);
    const choices = layers.filter(layer => layer.geometry || layer.kind === 'raster')
        .map(layer => ({ value: layerKey(layer), label: layer.name, layer }));
    const layerFor = id => choices.find(item => item.value === bindings[id])?.layer;
    const lock = running || pending || busy;
    const missingRaster = components?.raster?.installed === false;

    function clearResults(ids) {
        if (ids.length) {
            const stale = new Set(ids);
            let count = 0;
            while (count !== stale.size) {
                count = stale.size;
                edges.forEach(link => { if (stale.has(link.source)) stale.add(link.target); });
            }
            setResults(old => Object.fromEntries(Object.entries(old).filter(([id]) => !stale.has(id))));
        }
        setMessage('');
        setError('');
        setFailed(null);
        setIssues([]);
    }
    function patchSettings(id, name, value) {
        clearResults([id]);
        setNodes(old => old.map(node => node.id === id ? { ...node, data: { ...node.data,
            settings: { ...node.data.settings, [name]: value } } } : node));
    }
    function bindLayer(id, ref) {
        clearResults([id]);
        setBindings(old => ({ ...old, [id]: ref }));
        const layer = choices.find(item => item.value === ref)?.layer;
        if (layer) setNodes(old => old.map(node => node.id === id ? {
            ...node, data: { ...node.data, label: layer.name.slice(0, 100), settings: { layerRef: layer.ref } },
        } : node));
    }
    const forms = new Map();
    const descriptors = new Map();
    function describe(id) {
        if (descriptors.has(id)) return descriptors.get(id);
        const node = nodes.find(item => item.id === id);
        if (!node) return;
        const tool = node.data.definition;
        const metadata = node.data.operation === 'input' ? layerFor(id) : undefined;
        descriptors.set(id, metadata);
        if (tool === inputTool) return metadata;
        const predecessors = Object.fromEntries(edges.filter(link => link.target === id).map(link => [Number(link.targetHandle), link.source]));
        const inputs = tool.inputs.map((_, port) => describe(predecessors[port]));
        const operation = node.data.operation;
        const form = flowForm(operation, inputs, node.data.settings, (name, value) => patchSettings(id, name, value), predecessors);
        form.input = inputs[0];
        form.valid = form.valid && inputs.every(input => input !== undefined);
        form.issue = flowInputIssue(operation, tool.inputs, inputs, form.parameters, predecessors);
        form.missing = tool.inputs.filter((_, port) => !predecessors[port]);
        form.waitingForInput = inputs.some(input => input === undefined);
        forms.set(id, form);
        const layer = results[id]?.layers[0];
        if (layer) descriptors.set(id, layer);
        else if (operation === 'geometry.create_dataset' && form.valid) descriptors.set(id, {
            geometry: { point: 'Point', polyline: 'LineString', polygon: 'Polygon' }[form.parameters.geometry_type],
            crs: 'EPSG:' + form.parameters.epsg, units: node.data.settings.coordinate?.units, fields: [], unavailable: true,
        });
        else if (inputs[0] && ['layer', 'selection'].includes(tool.output)) {
            const inherited = { ...inputs[0], unavailable: true };
            if (['crs.reproject', 'crs.assign', 'raster.reproject'].includes(operation)) {
                inherited.crs = form.parameters.definition || form.parameters.target_crs;
                inherited.units = node.data.settings.coordinate?.units;
            }
            const field = operation === 'table.calculate_geometry' && form.parameters.field;
            if (field && !inherited.fields?.some(item => item.name === field)) {
                inherited.fields = [...inherited.fields, { name: field, type: 'float64' }];
            }
            if (operation === 'table.add_field' && form.valid) {
                const { name, type } = form.parameters.field;
                inherited.fields = [...inherited.fields, { name, type: { string: 'object', integer: 'int32', double: 'float64',
                    date: 'datetime64' }[type] }];
            }
            if (operation === 'table.delete_field' && form.valid) {
                inherited.fields = inherited.fields.filter(item => item.name !== form.parameters.field);
            }
            if (operation === 'vector.buffer') inherited.geometry = 'MultiPolygon';
            descriptors.set(id, inherited);
        }
        return descriptors.get(id);
    }
    nodes.forEach(node => describe(node.id));
    const visibleNodes = nodes.map(node => {
        const id = node.id;
        const form = forms.get(id);
        const result = results[id];
        const isInput = node.data.operation === 'input';
        const currentInput = form?.input?.ref && !form.input.unavailable && choices.some(item => item.value === layerKey(form.input));
        const showOutput = edges.some(link => link.source === id) || nodes.some(next =>
            next.data.definition.inputTypes.some((_, port) => canConnect(id, next, String(port))));
        return { ...node, selected: selected === id,
            data: { ...node.data, result, failed: failed === id || issues.some(issue => issue.node === id),
                lock, showOutput, field: form?.parameters.field,
            name: label => setNodes(old => old.map(item => item.id === id ? { ...item,
                ariaLabel: label || item.data.definition.title, data: { ...item.data, label } } : item)),
            label: isInput ? layerFor(id)?.name || node.data.label : node.data.label,
            editor: isInput ? layerFor(id) ? [] : [h('small', { key: 'source' }, '从左侧拖入图层')] : form?.controls || [],
            actions: {
                add: () => perform(async () => { await onLayers(result.layers); setMessage('已加入地图'); }),
                highlight: result?.selection && currentInput &&
                    (() => { onHighlight(form.input, result.selection.fids); close(); }),
                apply: result?.layers[0] && currentInput && writesFields.has(node.data.operation) &&
                    (() => { onApply(form.input, result.layers[0]); close(); }),
                table: () => perform(() => api.mapFlowTable(result.flow, result.run)),
            },
        } };
    });
    async function perform(action) {
        if (lock) return;
        setPending(true);
        setError('');
        try { await action(); }
        catch (problem) { reportError(problem); }
        finally { setPending(false); }
    }
    useEffect(() => {
        if (!request || !catalog.length || request.id === lastRequest || lock) return;
        setLastRequest(request.id);
        const tool = definition(request.operation);
        if (!tool) { setError('该工具尚未注册到流程'); return; }
        const layer = choices.find(item => item.value === request.layer)?.layer;
        const bound = layer && tool.inputs.length &&
            (!request.operation.startsWith('raster.') ? Boolean(layer.geometry) : layer.kind === 'raster') ? layer : null;
        if (nodes.length + 1 + Boolean(bound) > 16) { setError('请先为新工具留出流程节点'); return; }
        const row = nodes.length ? Math.max(...nodes.map(node => node.position.y + (node.measured?.height || 160))) + 44 : 36;
        const id = 'node-' + crypto.randomUUID().slice(0, 8);
        const upstream = 'node-' + crypto.randomUUID().slice(0, 8);
        const input = bound ? [graphNode({ id: upstream, operation: 'input', x: 48, y: row, settings: {}, label: bound.name.slice(0, 100) }, inputTool)] : [];
        setNodes(old => [...old.map(node => ({ ...node, selected: false })), ...input, {
            ...graphNode({ id, operation: request.operation, x: bound ? 365 : 48, y: row, settings: {} }, tool), selected: true,
        }]);
        if (bound) {
            setBindings(old => ({ ...old, [upstream]: layerKey(bound) }));
            setEdges(old => [...old, edge(upstream, id, 0)]);
        }
        select(id);
        clearResults([]);
        setTimeout(() => diagram?.fitView({ maxZoom: 1, padding: .15 }), 90);
    }, [request, catalog, lock, lastRequest, diagram]);

    function addNode(operation, layer, position) {
        if (lock || nodes.length >= 16) return;
        const tool = definition(operation);
        if (!tool || operation === 'input' && !layer) return;
        const id = 'node-' + crypto.randomUUID().slice(0, 8);
        const location = position || { x: 36 + nodes.length % 3 * 305,
            y: nodes.reduce((bottom, node) => Math.max(bottom, node.position.y + (node.measured?.height || 120)), -10) + 40 };
        const label = layer?.name.slice(0, 100) || undefined;
        setNodes(old => [...old.map(node => ({ ...node, selected: false })), {
            ...graphNode({ id, operation, label, settings: {} }, tool),
            position: location, selected: true,
        }]);
        if (layer) bindLayer(id, layerKey(layer));
        select(id);
        if (!position) setTimeout(() => diagram?.fitView({ maxZoom: 1, padding: .15 }), 90);
    }
    function canConnect(sourceId, node, handle) {
        if (!sourceId || !node || sourceId === node.id ||
                edges.some(item => item.target === node.id && item.targetHandle === handle)) return false;
        const source = nodes.find(item => item.id === sourceId)?.data.definition.output;
        const port = Number(handle);
        const target = node.data.definition.inputTypes[port];
        if (!target || (source === 'selection' ? 'layer' : source) !== target) return false;
        const layer = descriptors.get(sourceId);
        if (target === 'layer' && layer && !acceptsFlowLayer(node.data.operation, port, layer)) return false;
        if (target !== 'layer' && node.data.operation !== 'flow.calculate' &&
            edges.some(item => item.target === node.id && Number(item.targetHandle) >= node.data.definition.inputs.length)) return false;
        const seen = new Set([node.id]);
        for (const id of seen) edges.filter(item => item.source === id).forEach(item => seen.add(item.target));
        return !seen.has(sourceId);
    }
    function validConnection(link) {
        return !lock && canConnect(link.source, nodes.find(item => item.id === link.target), link.targetHandle);
    }
    function connect(link) {
        if (!validConnection(link)) return;
        setEdges(old => [...old, edge(link.source, link.target, link.targetHandle)]);
        clearResults([link.target]);
    }
    function changeNodes(changes) {
        setNodes(old => applyNodeChanges(changes.filter(change => !lock || change.type === 'dimensions'), old));
        if (lock) return;
        const removed = changes.filter(change => change.type === 'remove').map(change => change.id);
        const next = changes.find(change => change.type === 'select' && change.selected);
        if (next) select(next.id);
        else if (removed.includes(selected) || changes.some(change =>
            change.type === 'select' && change.id === selected && !change.selected)) select(null);
        if (removed.length) {
            setEdges(old => old.filter(link => !removed.includes(link.source) && !removed.includes(link.target)));
            setBindings(old => Object.fromEntries(Object.entries(old).filter(([id]) => !removed.includes(id))));
            clearResults(removed);
        }
    }
    function changeEdges(changes) {
        if (lock) return;
        setEdges(old => applyEdgeChanges(changes, old));
        const targets = changes.filter(change => change.type === 'remove')
            .map(change => edges.find(link => link.id === change.id)?.target).filter(Boolean);
        if (targets.length) clearResults(targets);
    }
    function required(target) {
        if (!target) return new Set(nodes.map(node => node.id));
        const ids = new Set([target]);
        for (const id of ids) edges.filter(link => link.target === id).forEach(link => ids.add(link.source));
        return ids;
    }
    function focusNode(id) {
        if (!id) return;
        select(id);
        void diagram?.fitView({ nodes: [{ id }], maxZoom: 1, padding: .25, duration: 150 });
    }
    async function verify(target) {
        const ids = required(target);
        clearResults([]);
        const model = cleanGraph(nodes, edges, forms, layerFor);
        model.nodes = model.nodes.filter(node => ids.has(node.id));
        model.connections = model.connections.filter(link => ids.has(link.source) && ids.has(link.target));
        const bindings = Object.fromEntries([...ids].flatMap(id => layerFor(id) ? [[id, layerFor(id).ref]] : []));
        const problems = [];
        const used = new Set(model.connections.map(link => link.source));
        for (const { id, operation } of model.nodes) {
            const form = forms.get(id);
            const add = message => problems.push({ node: id, message });
            if (operation === 'input') {
                if (!bindings[id]) add('从左侧拖入图层以替换此来源');
                if (!used.has(id)) add('请把图层连到工具节点，或移除此节点');
            } else if (form.missing.length) add('请连接' + form.missing.join('、'));
            else if (missingRaster && operation.startsWith('raster.'))
                add('请在「设置 → 按需组件」安装栅格处理组件');
            else if (form.issue) add(form.issue);
            else if (!form.valid && !form.waitingForInput) add('请补齐节点参数，或检查填写的值');
        }
        if (!model.nodes.some(node => node.operation !== 'input') && !problems.length)
            problems.push({ message: '请添加一个要运行的工具节点' });
        const payload = { model, bindings, target };
        if (!problems.length) problems.push(...await api.map({ action: 'flow-check', ...payload }));
        if (!problems.length) return payload;
        setIssues(problems);
        focusNode(problems.find(issue => issue.node)?.node);
    }
    async function check() {
        await perform(async () => {
            if (await verify()) setMessage('检查通过，可以运行流程');
        });
    }
    async function run(target) {
        if (lock) return;
        setPending(true);
        let payload;
        try { payload = await verify(target); }
        catch (problem) { reportError(problem); }
        finally { setPending(false); }
        if (!payload) return;
        setBusy(true);
        setProgress('正在读取图层…');
        setRunning(true);
        try {
            const result = await api.mapFlowRun(payload);
            if (result.status === 'cancelled') setMessage('流程已停止');
            else if (result.status !== 'succeeded') {
                if (result.failedNode) { focusNode(result.failedNode); setFailed(result.failedNode); }
                reportError(result.message);
            } else {
                setMessage('已完成 ' + result.steps.length + ' 个节点');
                setResults(old => ({ ...old, ...Object.fromEntries(result.steps.map(step => [step.node, { ...step, flow: result.run }])) }));
                select(target || result.steps.at(-1)?.node);
            }
        } catch (problem) { reportError(problem); }
        finally { setStopping(false); setRunning(false); setBusy(false); }
    }
    async function stop() {
        setStopping(true);
        try { await api.mapFlowCancel(); }
        catch (problem) { setStopping(false); reportError(problem); }
    }
    function load(item) {
        if (lock) return;
        if (item.model.nodes.some(node => !definition(node.operation) ||
            node.operation === 'table.calculate_field' && node.settings?.operator === 'sql' ||
            node.operation === 'data.select_by_attribute' && node.settings?.method === 'sql')) {
            setListOpen(false);
            setError('此流程含有已停用的手写表达式，请用「查询条件」或「计算字段」重新配置。');
            return;
        }
        setNodes(item.model.nodes.map(node => graphNode(node, definition(node.operation))));
        setEdges(item.model.connections.map(link => edge(link.source, link.target, link.port)));
        setFlowId(item.id);
        setName(item.title);
        setBindings(Object.fromEntries(item.model.nodes.flatMap(node => node.operation === 'input' && node.settings?.layerRef
            ? [[node.id, layerKey({ ref: node.settings.layerRef })]] : [])));
        setResults({});
        select(null);
        clearResults([]);
        setListOpen(false);
        requestAnimationFrame(() => diagram?.fitView());
    }
    async function save() {
        const title = name.trim();
        if (lock || !title) return;
        setPending(true);
        setError('');
        try {
            const item = await api.map({ action: 'flow-save', title, id: flowId,
                model: cleanGraph(nodes, edges, forms, layerFor) });
            setFlowId(item.id);
            setSaveOpen(false);
            setMessage('已保存「' + item.title + '」');
        } catch (problem) { reportError(problem); }
        finally { setPending(false); }
    }
    async function showSavedFlows(open) {
        setListOpen(open);
        if (!open) return;
        setSavedFlows(null);
        try { setSavedFlows(await api.map({ action: 'flow-list' })); }
        catch (problem) { setListOpen(false); reportError(problem); }
    }
    async function removeFlow(item) {
        await perform(async () => {
            await api.map({ action: 'flow-delete', id: item.id });
            setSavedFlows(list => list?.filter(value => value.id !== item.id));
            if (flowId === item.id) setFlowId(null);
            setDeleting(null);
            setMessage('已删除「' + item.title + '」');
        });
    }
    function reset() {
        setNodes([]);
        setEdges([]);
        setBindings({});
        setResults({});
        setFlowId(null);
        setName('');
        select(null);
        clearResults([]);
    }
    const match = title => title.toLowerCase().includes(search.trim().toLowerCase());
    const libraryButton = (title, icon, details) => h(Button, { key: details.operation + (details.layer || ''), type: 'text',
        icon: glyph(icon), disabled: lock || nodes.length >= 16, draggable: !lock && nodes.length < 16,
        onDragStart: event => { event.dataTransfer.effectAllowed = 'copy'; event.dataTransfer.setData(dragType, JSON.stringify(details)); },
        onClick: () => addNode(details.operation, choices.find(item => item.value === details.layer)?.layer) }, title);
    const graph = h(ReactFlow, {
        nodes: visibleNodes, edges, nodeTypes, isValidConnection: validConnection, onConnect: connect, onInit: setDiagram,
        onNodesChange: changeNodes, onEdgesChange: changeEdges, onNodeClick: (_, node) => select(node.id),
        onPaneClick: () => select(null), nodesDraggable: !lock, nodesConnectable: !lock,
        minZoom: 0.2, maxZoom: 2, deleteKeyCode: lock ? null : ['Backspace', 'Delete'],
        onDragOver: event => { if (event.dataTransfer.types.includes(dragType)) event.preventDefault(); },
        onDrop: event => {
            const value = event.dataTransfer.getData(dragType);
            if (!value || !diagram) return;
            event.preventDefault();
            const item = JSON.parse(value);
            const source = choices.find(choice => choice.value === item.layer)?.layer;
            const target = nodes.find(node => node.id === event.target.closest('.react-flow__node')?.dataset.id);
            if (source && target?.data.operation === 'input' && !lock) { bindLayer(target.id, layerKey(source)); return; }
            addNode(item.operation, source,
                diagram.screenToFlowPosition({ x: event.clientX, y: event.clientY }));
        },
    }, h(Background, { gap: 20, size: 1.4 }), h(Controls, { showInteractive: false }));
    return h(Modal, { title: '流程编辑器', open, width: 'min(1400px, calc(100vw - 32px))', centered: true,
        className: 'gis-dialog gis-flow-dialog', rootClassName: 'gis-modal-root', footer: null, maskClosable: false,
        onCancel: close },
    h('div', { className: 'gis-flow-toolbar' },
        h('div', { className: 'gis-flow-toolgroup', role: 'group', 'aria-label': '流程文件' },
            h(Button, { icon: glyph('add'), 'aria-label': '新建流程', disabled: lock || !nodes.length, onClick: reset }, '新建'),
            h(Popover, { open: listOpen, onOpenChange: showSavedFlows, trigger: 'click', placement: 'bottomLeft',
                content: h('div', { className: 'gis-choice-list gis-flow-saved' },
                    savedFlows?.length ? savedFlows.map(item => h('div', { className: 'gis-flow-saved-row', key: item.id },
                        h(Button, { type: 'text', title: item.title, onClick: () => load(item) }, item.title),
                        h(Button, { type: 'text', danger: true, icon: glyph('trash'), 'aria-label': '删除流程 ' + item.title,
                            title: '删除流程', onClick: () => { setListOpen(false); setDeleting(item); } })))
                        : h('span', { className: 'muted', role: 'status' }, savedFlows ? '暂无已保存流程' : '正在读取…')) },
                h(Button, { icon: glyph('folder'), 'aria-label': '打开流程', 'aria-expanded': listOpen,
                    disabled: lock || !catalog.length }, '打开')),
            h(Popover, { open: saveOpen, onOpenChange: setSaveOpen, trigger: 'click', placement: 'bottomLeft',
                content: h('div', { className: 'gis-flow-save' }, h(Input, { value: name, 'aria-label': '流程名称',
                    maxLength: 100, placeholder: '流程名称', onChange: event => setName(event.target.value), onPressEnter: save }),
                h(Button, { disabled: !name.trim() || pending, onClick: save }, '保存')) },
                h(Button, { icon: glyph('save'), 'aria-label': '保存流程', 'aria-expanded': saveOpen,
                    disabled: lock || !nodes.length }, '保存'))),
        h('div', { className: 'gis-flow-toolgroup', role: 'group', 'aria-label': '检查和运行' },
            h(Button, { disabled: lock || !nodes.length, onClick: check }, '检查流程'),
            h(Button, { type: 'primary', disabled: lock || !forms.size, onClick: () => run() }, '运行流程'),
            h(Button, { disabled: lock || !forms.has(selected), onClick: () => run(selected) }, '运行至选中节点'),
            running && h(Button, { disabled: stopping, onClick: stop }, stopping ? '正在停止' : '停止'))),
    h('div', { className: 'gis-flow-layout' },
        h('aside', { className: 'gis-flow-library', 'aria-label': '流程节点' },
            h(Input, { placeholder: '搜索图层和工具', 'aria-label': '搜索图层和工具', value: search, onChange: event => setSearch(event.target.value) }),
            h('h3', null, '图层'),
            choices.filter(item => match(item.label)).map(item => libraryButton(item.label, 'layers', { operation: 'input', layer: item.value })),
            h('h3', null, '工具'),
            catalog.filter(item => match(item.title)).map(item =>
                libraryButton(item.title, toolIcon(item.operation), { operation: item.operation }))),
        h('div', { className: 'gis-flow-canvas', 'aria-label': '流程画布' }, graph,
            !nodes.length && h('p', { className: 'gis-flow-empty' }, '从左侧添加图层和工具，再拖动端口连接处理步骤。'))),
    deleting && h(Modal, { open: true, centered: true, title: '删除流程', okText: '删除', cancelText: '取消',
        confirmLoading: pending, okButtonProps: { danger: true }, onOk: () => removeFlow(deleting),
        onCancel: () => { if (!pending) setDeleting(null); } }, '删除「' + deleting.title + '」？'),
    h('div', { className: 'gis-flow-status', role: error || issues.length ? 'alert' : 'status' },
        issues.length > 0 ? h('ul', { className: 'gis-flow-issues' }, issues.map(({ node, message }) => {
            const item = nodes.find(value => value.id === node);
            const text = item ? (item.data.label || item.data.definition.title) + ' · ' + message : message;
            return h('li', { key: (node || '') + message },
                item ? h('button', { type: 'button', onClick: () => focusNode(node) }, text) : text);
        })) : error ? h('span', { className: 'error' }, error) : running ? progress : message || ''));
}
