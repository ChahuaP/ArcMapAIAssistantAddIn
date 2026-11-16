import { ReactFlow, Background, Controls, Handle, Position, applyEdgeChanges, applyNodeChanges } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { flowForm, toolIcon, toolTitle } from './map-processing.js';
import { AttributePreview, TokenPicker } from './map-pickers.js';

const h = window.React.createElement;
const { useEffect, useState } = window.React;
const { Button, Input, Modal, Popover } = window.antd;
const api = window.guizhi;
const layerKey = layer => layer.ref.run + ':' + layer.ref.artifact;
const dragType = 'application/x-guizhi-flow';
const outputs = { layer: '图层', selection: '筛选结果', statistics: '统计结果', table: '属性表',
    values: '唯一值', expression: 'SQL', number: '数值' };
const headings = { area_m2: '合并面积（平方米）', feature_count: '要素数', selected_count: '选中要素数',
    updated: '计算要素数', row_count: '记录数', distinct_count: '唯一值数', width: '宽度（像元）',
    height: '高度（像元）', band_count: '波段数' };
const writesFields = new Set(['table.add_field', 'table.delete_field', 'table.calculate_geometry', 'table.calculate_field']);
const inputTool = { title: '输入图层', inputs: [], output: 'layer' };
const graphNode = (node, definition, result, failed) => ({ id: node.id, type: 'operation', position: { x: node.x, y: node.y },
    data: { ...node, definition, result, failed }, ariaLabel: node.label || definition.title });
const edge = (source, target, port) => ({ id: [source, target, port].join(':'), source, target, targetHandle: String(port) });
const connection = link => ({ source: link.source, target: link.target, port: Number(link.targetHandle) });
const cleanGraph = (nodes, edges, forms) => ({ nodes: nodes.map(node => ({
    id: node.id, operation: node.data.operation, label: node.data.label?.trim() || node.data.definition.title, x: Math.round(node.position.x),
    y: Math.round(node.position.y), settings: node.data.settings, parameters: forms.get(node.id)?.parameters || {},
})), connections: edges.map(connection) });

function OperationNode({ data }) {
    const { definition, failed, result, editor, name, lock, actions } = data;
    const [preview, showPreview] = useState(false);
    const [renaming, rename] = useState(false);
    const layer = result?.layers[0];
    return h('div', { className: 'gis-flow-node', 'data-kind': definition.output,
        'data-state': failed ? 'error' : result ? 'done' : undefined },
        renaming ? h(Input, { className: 'nodrag', size: 'small', autoFocus: true, maxLength: 100, 'aria-label': '节点名称',
            value: data.label ?? '', onChange: event => name(event.target.value), onBlur: () => rename(false),
            onPressEnter: () => rename(false) })
            : h('strong', { title: '双击重命名', onDoubleClick: () => { if (!lock) rename(true); } }, data.label || definition.title),
        [...definition.inputs, ...definition.valuePorts || []].map((label, index) =>
            h('div', { key: index, className: 'gis-flow-port', 'data-port-type': definition.inputTypes[index] },
                h(Handle, { id: String(index), type: 'target', position: Position.Left,
                    title: label, 'aria-label': label }), label)),
        editor.length > 0 && h('fieldset', { className: 'gis-flow-fields nodrag nopan nowheel', disabled: lock },
            editor.map((control, index) => h(window.React.Fragment, { key: index }, control))),
        result && h('div', { className: 'gis-flow-result nodrag nopan nowheel' },
            Object.entries(result.statistics || {}).filter(([name, value]) => headings[name] && typeof value === 'number').map(([name, value]) =>
                h('div', { key: name }, headings[name] + ' ' + value.toLocaleString('zh-CN', { maximumFractionDigits: 3 }))),
            Object.hasOwn(result, 'value') && (Array.isArray(result.value)
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
        h('div', { className: 'gis-flow-output' }, outputs[definition.output],
            !['statistics', 'table'].includes(definition.output) &&
                h(Handle, { type: 'source', position: Position.Right, title: '连接到下游节点', 'aria-label': outputs[definition.output] + '输出' })));
}
const nodeTypes = { operation: OperationNode };

export function MapFlow({ open, layers, busy, setBusy, close, glyph, onLayers, onHighlight, onApply, request, components, openComponents }) {
    const [catalog, setCatalog] = useState([]);
    const [nodes, setNodes] = useState([]);
    const [edges, setEdges] = useState([]);
    const [bindings, setBindings] = useState({});
    const [selected, select] = useState(null);
    const [diagram, setDiagram] = useState(null);
    const [search, setSearch] = useState('');
    const [templates, setTemplates] = useState([]);
    const [name, setName] = useState('');
    const [templateId, setTemplateId] = useState(null);
    const [saveOpen, setSaveOpen] = useState(false);
    const [templateOpen, setTemplateOpen] = useState(false);
    const [deleteTemplate, setDeleteTemplate] = useState(null);
    const [lastRequest, setLastRequest] = useState(null);
    const [pending, setPending] = useState(false);
    const [running, setRunning] = useState(false);
    const [stopping, setStopping] = useState(false);
    const [progress, setProgress] = useState('');
    const [results, setResults] = useState({});
    const [failed, setFailed] = useState(null);
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
    }
    function patchSettings(id, name, value) {
        clearResults([id]);
        setNodes(old => old.map(node => node.id === id ? { ...node, data: { ...node.data,
            settings: { ...node.data.settings, [name]: value } } } : node));
    }
    function bindLayer(id, ref) {
        clearResults([id]);
        setBindings(old => ({ ...old, [id]: ref }));
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
        forms.set(id, form);
        const layer = results[id]?.layers[0];
        if (layer) descriptors.set(id, layer);
        else if (operation === 'geometry.create_dataset' && form.valid) descriptors.set(id, {
            geometry: { point: 'Point', polyline: 'LineString', polygon: 'Polygon' }[form.parameters.geometry_type],
            crs: 'EPSG:' + form.parameters.epsg, fields: [], unavailable: true,
        });
        else if (inputs[0] && ['layer', 'selection'].includes(tool.output)) {
            const inherited = { ...inputs[0], unavailable: true };
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
        return { ...node, data: { ...node.data, result, failed: failed === id, lock, field: form?.parameters.field,
            name: label => setNodes(old => old.map(item => item.id === id ? { ...item,
                ariaLabel: label || item.data.definition.title, data: { ...item.data, label } } : item)),
            editor: isInput ? [h(TokenPicker, { label: '选择图层', items: choices, selected: bindings[id],
                onPick: value => bindLayer(id, value) })] : form?.controls || [],
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
        if (!tool) return;
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
    function validConnection(link) {
        if (lock || !link.source || !link.target || link.source === link.target ||
                edges.some(item => item.target === link.target && item.targetHandle === link.targetHandle)) return false;
        const source = nodes.find(item => item.id === link.source)?.data.definition.output;
        const target = nodes.find(item => item.id === link.target)?.data.definition.inputTypes?.[Number(link.targetHandle)];
        if (!target || (source === 'selection' ? 'layer' : source) !== target) return false;
        const node = nodes.find(item => item.id === link.target);
        if (target !== 'layer' && node.data.operation !== 'flow.calculate' &&
            edges.some(item => item.target === link.target && Number(item.targetHandle) >= node.data.definition.inputs.length)) return false;
        const seen = new Set([link.target]);
        for (const id of seen) edges.filter(item => item.source === id).forEach(item => seen.add(item.target));
        return !seen.has(link.source);
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
        else if (removed.includes(selected)) select(null);
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
    async function run(target) {
        if (lock) return;
        const ids = required(target);
        setError('');
        setMessage('');
        setFailed(null);
        if (missingRaster && nodes.some(node => ids.has(node.id) && node.data.operation.startsWith('raster.'))) {
            setError('请先安装栅格处理组件');
            return;
        }
        const invalid = nodes.find(node => ids.has(node.id) && node.data.operation !== 'input' && !forms.get(node.id)?.valid);
        if (invalid) {
            select(invalid.id);
            setFailed(invalid.id);
            setError('请检查「' + (invalid.data.label || invalid.data.definition.title) + '」的连线和参数。');
            return;
        }
        const model = cleanGraph(nodes, edges, forms);
        model.nodes = model.nodes.filter(node => ids.has(node.id));
        model.connections = model.connections.filter(link => ids.has(link.source) && ids.has(link.target));
        setBusy(true);
        setProgress('正在读取图层…');
        setRunning(true);
        try {
            const bound = Object.fromEntries([...ids].flatMap(id => layerFor(id) ? [[id, layerFor(id).ref]] : []));
            const result = await api.mapFlowRun({ model, bindings: bound, target });
            if (result.status === 'cancelled') setMessage('流程已停止');
            else if (result.status !== 'succeeded') {
                if (result.failedNode) { select(result.failedNode); setFailed(result.failedNode); }
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
        if (lock || item.model.nodes.some(node => !definition(node.operation))) return;
        setNodes(item.model.nodes.map(node => graphNode(node, definition(node.operation))));
        setEdges(item.model.connections.map(link => edge(link.source, link.target, link.port)));
        setTemplateId(item.id);
        setName(item.title);
        setBindings({});
        setResults({});
        select(null);
        clearResults([]);
        setTemplateOpen(false);
        requestAnimationFrame(() => diagram?.fitView());
    }
    async function save() {
        const title = name.trim();
        if (lock || !title) return;
        setPending(true);
        setError('');
        try {
            const item = await api.map({ action: 'flow-save', title, id: templateId,
                model: cleanGraph(nodes, edges, forms) });
            setTemplateId(item.id);
            setSaveOpen(false);
            setMessage('已保存「' + item.title + '」');
        } catch (problem) { reportError(problem); }
        finally { setPending(false); }
    }
    async function showTemplates(open) {
        setTemplateOpen(open);
        if (!open) return;
        try { setTemplates(await api.map({ action: 'flow-templates' })); }
        catch (problem) { setTemplateOpen(false); reportError(problem); }
    }
    async function removeTemplate(item) {
        await perform(async () => {
            await api.map({ action: 'flow-delete', id: item.id });
            setTemplates(list => list.filter(value => value.id !== item.id));
            if (templateId === item.id) setTemplateId(null);
            setDeleteTemplate(null);
            setMessage('已删除「' + item.title + '」');
        });
    }
    function reset() {
        setNodes([]);
        setEdges([]);
        setBindings({});
        setResults({});
        setTemplateId(null);
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
            addNode(item.operation, choices.find(choice => choice.value === item.layer)?.layer,
                diagram.screenToFlowPosition({ x: event.clientX, y: event.clientY }));
        },
    }, h(Background, { gap: 20, size: 1.4 }), h(Controls, { showInteractive: false }));
    return h(Modal, { title: '流程编辑器', open, width: 'min(1400px, calc(100vw - 32px))', centered: true,
        className: 'gis-dialog gis-flow-dialog', rootClassName: 'gis-modal-root', footer: null, maskClosable: false,
        onCancel: close },
    h('div', { className: 'gis-flow-toolbar' },
        h(Button, { type: 'primary', disabled: lock || !forms.size, onClick: () => run() }, '运行流程'),
        h(Button, { disabled: lock || !forms.has(selected), onClick: () => run(selected) }, '运行至此'),
        running && h(Button, { disabled: stopping, onClick: stop }, stopping ? '正在停止' : '停止'),
        h('span', { className: 'gis-flow-space' }),
        h(Button, { disabled: lock || !nodes.length, onClick: reset }, '新建'),
        h(Popover, { open: templateOpen, onOpenChange: showTemplates, trigger: 'click', placement: 'bottomRight',
            content: h('div', { className: 'gis-choice-list gis-flow-templates' },
                templates.length ? templates.map(item => h('div', { className: 'gis-flow-template-row', key: item.id },
                    h(Button, { type: 'text', onClick: () => load(item) }, item.title),
                    h(Button, { type: 'text', danger: true, icon: glyph('trash'), 'aria-label': '删除模板 ' + item.title,
                        title: '删除模板', onClick: () => { setTemplateOpen(false); setDeleteTemplate(item); } })))
                    : h('span', { className: 'muted' }, '暂无模板')) },
        h(Button, { disabled: lock || !catalog.length }, '模板')),
        h(Popover, { open: saveOpen, onOpenChange: setSaveOpen, trigger: 'click', placement: 'bottomRight',
            content: h('div', { className: 'gis-flow-save' }, h(Input, { value: name, 'aria-label': '模板名称',
                maxLength: 100, placeholder: '模板名称', onChange: event => setName(event.target.value), onPressEnter: save }),
            h(Button, { disabled: !name.trim() || pending, onClick: save }, '保存')) },
        h(Button, { disabled: lock || !nodes.length }, '保存模板')),
        missingRaster && h(Button, { disabled: lock, onClick: () => { close(); openComponents(); } }, '安装栅格组件')),
    h('div', { className: 'gis-flow-layout' },
        h('aside', { className: 'gis-flow-library', 'aria-label': '流程节点' },
            h(Input, { placeholder: '搜索图层和工具', 'aria-label': '搜索图层和工具', value: search, onChange: event => setSearch(event.target.value) }),
            h('h3', null, '图层'),
            choices.filter(item => match(item.label)).map(item => libraryButton(item.label, 'layers', { operation: 'input', layer: item.value })),
            !search && libraryButton('输入图层', 'add', { operation: 'input' }),
            h('h3', null, '工具'),
            catalog.filter(item => match(item.title)).map(item =>
                libraryButton(item.title, toolIcon(item.operation), { operation: item.operation }))),
        h('div', { className: 'gis-flow-canvas', 'aria-label': '流程画布' }, graph,
            !nodes.length && h('p', { className: 'gis-flow-empty' }, '从左侧添加图层和工具，再拖动端口连接处理步骤。'))),
    deleteTemplate && h(Modal, { open: true, centered: true, title: '删除模板', okText: '删除', cancelText: '取消',
        confirmLoading: pending, okButtonProps: { danger: true }, onOk: () => removeTemplate(deleteTemplate),
        onCancel: () => { if (!pending) setDeleteTemplate(null); } }, '删除「' + deleteTemplate.title + '」？'),
    h('div', { className: 'gis-flow-status', role: error ? 'alert' : 'status' },
        error ? h('span', { className: 'error' }, error) : running ? progress : message || ''));
}
