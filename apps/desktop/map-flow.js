import { ReactFlow, Background, Controls, Handle, Position, applyEdgeChanges, applyNodeChanges } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { flowForm } from './map-processing.js';
import { AttributePreview, TokenPicker } from './map-pickers.js';

const h = window.React.createElement;
const { useEffect, useState } = window.React;
const { Button, Input, Modal, Popover } = window.antd;
const api = window.guizhi;
const layerKey = layer => layer.ref.run + ':' + layer.ref.artifact;
const dragType = 'application/x-guizhi-flow';
const outputs = { layer: '图层', selection: '筛选结果', statistics: '统计结果', table: '属性表' };
const headings = { area_m2: '合并面积（平方米）', feature_count: '要素数', selected_count: '选中要素数',
    updated: '计算要素数', row_count: '记录数' };
const inputTool = { title: '输入图层', inputs: [], output: 'layer' };
const graphNode = (node, definition, result, failed) => ({ id: node.id, type: 'operation', position: { x: node.x, y: node.y },
    data: { ...node, definition, result, failed }, ariaLabel: node.label || definition.title });
const edge = (source, target, port) => ({ id: [source, target, port].join(':'), source, target, targetHandle: String(port) });
const connection = link => ({ source: link.source, target: link.target, port: Number(link.targetHandle) });
const cleanGraph = (nodes, edges, forms) => ({ nodes: nodes.map(node => ({
    id: node.id, operation: node.data.operation, label: node.data.label || null, x: Math.round(node.position.x),
    y: Math.round(node.position.y), settings: node.data.settings, parameters: forms.get(node.id)?.parameters || {},
})), connections: edges.map(connection) });

function OperationNode({ data }) {
    const { definition, failed, result } = data;
    return h('div', { className: 'gis-flow-node', 'data-kind': definition.output,
        'data-state': failed ? 'error' : result ? 'done' : undefined },
        h('strong', null, data.label || definition.title),
        definition.inputs.map((name, index) => h('div', { key: index, className: 'gis-flow-port' },
            h(Handle, { id: String(index), type: 'target', position: Position.Left, style: { top: 50 + index * 25 },
                title: name, 'aria-label': name }), name)),
        h('div', { className: 'gis-flow-output' }, outputs[definition.output],
            ['layer', 'selection'].includes(definition.output) &&
                h(Handle, { type: 'source', position: Position.Right, title: '连接到下游节点', 'aria-label': '图层输出' })));
}
const nodeTypes = { operation: OperationNode };

export function MapFlow({ open, layers, busy, setBusy, close, glyph, onLayers }) {
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
        api.map({ action: 'flow-tools' }).then(items => { if (current) setCatalog(items); })
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
    const choices = layers.filter(layer => layer.geometry).map(layer => ({ value: layerKey(layer), label: layer.name, layer }));
    const layerFor = id => choices.find(item => item.value === bindings[id])?.layer;
    const active = nodes.find(node => node.id === selected);
    const lock = running || pending || busy;

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
        const inputs = tool.inputs.map((_, port) => describe(edges.find(link => link.target === id && Number(link.targetHandle) === port)?.source));
        const operation = node.data.operation;
        const form = flowForm(operation, inputs, node.data.settings, (name, value) => patchSettings(id, name, value));
        forms.set(id, form);
        const layer = results[id]?.layers[0];
        if (layer) descriptors.set(id, layer);
        else if (inputs[0] && ['layer', 'selection'].includes(tool.output)) {
            const inherited = { ...inputs[0], unavailable: true };
            const field = operation === 'table.calculate_geometry' && form.parameters.field;
            if (field && !inherited.fields.some(item => item.name === field)) {
                inherited.fields = [...inherited.fields, { name: field, type: 'float64' }];
            }
            if (operation === 'vector.buffer') inherited.geometry = 'MultiPolygon';
            descriptors.set(id, inherited);
        }
        return descriptors.get(id);
    }
    nodes.forEach(node => describe(node.id));
    const visibleNodes = nodes.map(node => ({ ...node, data: { ...node.data, result: Boolean(results[node.id]), failed: failed === node.id } }));
    const layerResults = results[selected]?.layers || [];
    const metrics = Object.entries(results[selected]?.statistics || {}).filter(([name, value]) =>
        headings[name] && typeof value === 'number');

    function addNode(operation, layer, position) {
        if (lock || nodes.length >= 16) return;
        const tool = definition(operation);
        if (!tool) return;
        const id = 'node-' + crypto.randomUUID().slice(0, 8);
        const location = position || { x: 36 + nodes.length % 3 * 215, y: 30 + Math.floor(nodes.length / 3) * 140 };
        const label = layer?.name.slice(0, 100) || undefined;
        setNodes(old => [...old.map(node => ({ ...node, selected: false })), {
            ...graphNode({ id, operation, label, settings: {} }, tool),
            position: location, selected: true,
        }]);
        if (layer) bindLayer(id, layerKey(layer));
        select(id);
    }
    function validConnection(link) {
        if (lock || !link.source || !link.target || link.source === link.target ||
                edges.some(item => item.target === link.target && item.targetHandle === link.targetHandle)) return false;
        if (nodes.find(item => item.id === link.target)?.data.operation === 'input') return false;
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
    const preview = layerResults[0];
    return h(Modal, { title: '流程编辑器', open, width: 'min(1400px, calc(100vw - 32px))', centered: true,
        className: 'gis-dialog gis-flow-dialog', rootClassName: 'gis-modal-root', footer: null, maskClosable: false,
        closable: !lock, keyboard: !lock, onCancel: close },
    h('div', { className: 'gis-flow-toolbar' },
        h(Button, { type: 'primary', disabled: lock || !forms.size, onClick: () => run() }, '运行流程'),
        h(Button, { disabled: lock || !forms.has(selected), onClick: () => run(selected) }, '运行至此'),
        running && h(Button, { disabled: stopping, onClick: stop }, stopping ? '正在停止' : '停止'),
        h('span', { className: 'gis-flow-space' }),
        h(Button, { disabled: lock || !nodes.length, onClick: reset }, '新建'),
        h(Popover, { open: templateOpen, onOpenChange: showTemplates, trigger: 'click', placement: 'bottomRight',
            content: h('div', { className: 'gis-choice-list gis-flow-templates' },
                templates.length ? templates.map(item => h(Button, { type: 'text', key: item.id, onClick: () => load(item) }, item.title))
                    : h('span', { className: 'muted' }, '暂无模板')) },
        h(Button, { disabled: lock || !catalog.length }, '模板')),
        h(Popover, { open: saveOpen, onOpenChange: setSaveOpen, trigger: 'click', placement: 'bottomRight',
            content: h('div', { className: 'gis-flow-save' }, h(Input, { value: name, 'aria-label': '模板名称',
                maxLength: 100, placeholder: '模板名称', onChange: event => setName(event.target.value), onPressEnter: save }),
            h(Button, { disabled: !name.trim() || pending, onClick: save }, '保存')) },
        h(Button, { disabled: lock || !nodes.length }, '保存模板'))),
    h('div', { className: 'gis-flow-layout' },
        h('aside', { className: 'gis-flow-library', 'aria-label': '流程节点' },
            h(Input, { placeholder: '搜索图层和工具', 'aria-label': '搜索图层和工具', value: search, onChange: event => setSearch(event.target.value) }),
            h('h3', null, '图层'),
            choices.filter(item => match(item.label)).map(item => libraryButton(item.label, 'layers', { operation: 'input', layer: item.value })),
            !search && libraryButton('输入图层', 'add', { operation: 'input' }),
            h('h3', null, '工具'),
            catalog.filter(item => match(item.title)).map(item =>
                libraryButton(item.title, { selection: 'select', layer: 'clip', statistics: 'analysis', table: 'table' }[item.output],
                    { operation: item.operation }))),
        h('div', { className: 'gis-flow-canvas', 'aria-label': '流程画布' }, graph,
            !nodes.length && h('p', { className: 'gis-flow-empty' }, '从左侧添加图层和工具，再拖动端口连接处理步骤。')),
        h('aside', { className: 'gis-flow-inspector', 'aria-label': '节点设置' },
            h('h3', null, active ? active.data.label || active.data.definition.title : '节点设置'),
            active ? h('fieldset', { disabled: lock, className: 'gis-flow-fields' },
                active.data.operation === 'input' ? h(TokenPicker, { label: '选择图层', items: choices, selected: bindings[selected],
                    onPick: value => bindLayer(selected, value) }) : forms.get(selected)?.controls.map((control, index) =>
                        h(window.React.Fragment, { key: index }, control)),
                h(Input, { value: active.data.label || '', maxLength: 100, 'aria-label': '节点名称', placeholder: '节点名称',
                    onChange: event => {
                        const label = event.target.value;
                        setNodes(old => old.map(node => node.id === selected ? { ...node, ariaLabel: label || node.data.definition.title,
                            data: { ...node.data, label } } : node));
                    } })) : h('p', { className: 'muted' }, '选择节点查看参数和结果。'),
            results[selected] && h('section', { className: 'gis-flow-preview', 'aria-label': '节点结果' },
                h('h3', null, '结果'),
                metrics.map(([name, value]) => h('p', { key: name }, headings[name], ' ', value.toLocaleString('zh-CN', { maximumFractionDigits: 3 }))),
                preview && h(AttributePreview, { key: layerKey(preview), layer: preview, field: forms.get(selected)?.parameters.field }),
                layerResults.length > 0 && h(Button, { disabled: lock, onClick: async () => {
                    setPending(true);
                    try { await onLayers(layerResults, active?.data.definition.output === 'selection'); close(); }
                    catch (problem) { reportError(problem); }
                    finally { setPending(false); }
                } }, active?.data.definition.output === 'selection' ? '在地图中高亮' : '添加到地图'),
                results[selected].table && h(Button, { disabled: lock, onClick: async () => {
                    setPending(true);
                    try { await api.mapFlowTable(results[selected].flow, results[selected].run); }
                    catch (problem) { reportError(problem); }
                    finally { setPending(false); }
                } }, '导出 CSV')))),
    h('div', { className: 'gis-flow-status', role: error ? 'alert' : 'status' },
        error ? h('span', { className: 'error' }, error) : running ? progress : message || ''));
}
