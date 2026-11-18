import { CoordinateChoice } from './map-processing.js';

const h = window.React.createElement;
const { useState } = window.React;
const { Modal, Select, Checkbox } = window.antd;

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
