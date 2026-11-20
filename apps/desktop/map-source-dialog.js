import { CoordinateChoice } from './map-processing.js';

const h = window.React.createElement;
const { useState } = window.React;
const { Modal, Select, Checkbox, Input, Button } = window.antd;

export function BasemapDialog({ close, apply }) {
    const [kind, setKind] = useState('xyz');
    const [address, setAddress] = useState('');
    const [layers, setLayers] = useState('');
    const [name, setName] = useState('');
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const field = (label, child) => h('label', { className: 'gis-tool-field' }, h('span', null, label), child);
    async function submit() {
        setBusy(true);
        setError('');
        try {
            apply(await window.guizhi.mapBasemap({ kind, address: address.trim(), layers, name }));
            close();
        } catch (problem) { setError(window.GuizhiErrorText(problem)); }
        finally { setBusy(false); }
    }
    return h(Modal, { open: true, centered: true, title: '添加底图', className: 'gis-dialog', rootClassName: 'gis-modal-root',
        okText: '添加', cancelText: '取消', onCancel: busy ? undefined : close, onOk: submit, confirmLoading: busy,
        okButtonProps: { disabled: !address.trim() || (kind === 'wms' && !layers.trim()) } },
        h('div', { className: 'map-field-editor' },
            field('服务类型', h(Select, { 'aria-label': '服务类型', value: kind, options: ['xyz', 'wms'].map(value => ({ value, label: value.toUpperCase() })),
                onChange: value => { setKind(value); setAddress(''); setError(''); } })),
            field('服务地址', h(Input, { 'aria-label': '服务地址', value: address, maxLength: 2048,
                placeholder: kind === 'xyz' ? 'https://example.com/{z}/{x}/{y}.png' : 'https://example.com/wms',
                onChange: event => setAddress(event.target.value) })),
            kind === 'wms' && field('图层名称', h(Input, { 'aria-label': 'WMS 图层名称', value: layers, maxLength: 255,
                placeholder: '多个名称用逗号分隔', onChange: event => setLayers(event.target.value) })),
            field('底图名称', h(Input, { 'aria-label': '底图名称', value: name, maxLength: 100,
                placeholder: '选填', onChange: event => setName(event.target.value) })),
            error && h('p', { className: 'error', role: 'alert' }, error)));
}

export function MapSourceDialog({ source, close, add }) {
    const database = source.kind === 'gdb';
    const [selected, setSelected] = useState({});
    const [sheetIndex, setSheetIndex] = useState(() => Math.max(0, source.tables?.findIndex(item => !item.issue) ?? 0));
    const table = source.tables?.[sheetIndex];
    const [x, setX] = useState(null);
    const [y, setY] = useState(null);
    const [coordinate, setCoordinate] = useState(null);
    const [running, setRunning] = useState(false);
    const [error, setError] = useState('');
    const field = (name, child) => h('label', { className: 'gis-tool-field' }, h('span', null, name), child);
    const choose = (label, value, set, exclude) => field(label, h(Select, { value, placeholder: '选择字段',
        'aria-label': label, showSearch: true, optionFilterProp: 'label', onChange: set,
        options: (table?.fields || []).map(name => ({ value: name, label: name, disabled: name === exclude })) }));
    const valid = database ? Object.values(selected).some(list => list.length) : !table?.issue && x && y && x !== y && coordinate;
    async function submit() {
        if (running || !valid) return;
        setRunning(true);
        setError('');
        try {
            const params = database ? selected : { x_field: x, y_field: y, source_crs: coordinate.value, sheet: table.sheet };
            add(await window.guizhi.mapSourceApply(source.id, params));
            close();
        } catch (problem) {
            setError(String(problem?.message || problem).replace(/^Error invoking remote method '[^']+': (?:Error: )?/, ''));
        } finally { setRunning(false); }
    }
    return h(Modal, { open: true, centered: true, title: database ? '选择数据库图层' : '添加 XY 数据 · ' + source.name,
        className: 'gis-dialog', rootClassName: 'gis-modal-root', okText: '添加', cancelText: '取消',
        okButtonProps: { disabled: !valid }, confirmLoading: running, closable: !running,
        onCancel: () => { if (!running) close(); }, onOk: submit },
        h('div', { className: 'map-field-editor', style: { maxHeight: '55vh', overflowY: 'auto' } },
            database ? source.databases.map((db, index) => h('fieldset', { key: index },
                h('legend', null, db.name), h(Checkbox.Group, { value: selected[index] || [],
                    options: db.layers.map(name => ({ label: name, value: name })),
                    style: { display: 'grid', gap: 8 }, 'aria-label': db.name,
                    onChange: value => setSelected(previous => ({ ...previous, [index]: value })) }))) :
                h(window.React.Fragment, null,
                    table?.sheet && field('工作表', h(Select, { value: sheetIndex, 'aria-label': '工作表',
                        options: source.tables.map((item, i) => ({ value: i, label: item.sheet })),
                        onChange: index => { setSheetIndex(index); setX(null); setY(null); setError(''); } })),
                    choose('X 字段（横坐标）', x, setX, y), choose('Y 字段（纵坐标）', y, setY, x),
                    field('坐标表的坐标系', h(CoordinateChoice, { value: coordinate, onPick: setCoordinate, placeholder: '选择坐标系' }))),
            (error || table?.issue) && h('p', { className: 'error', role: 'alert' }, error || table.issue)));
}
