const h = window.React.createElement;
const { useEffect, useState } = window.React;
const { Button, Checkbox, Input, Modal, Select } = window.antd;

export function AttributeSort({ columns, order, apply, close }) {
    const [rows, setRows] = useState(order.length ? order : [{}]);
    function update(index, patch) {
        setRows(current => current.map((item, i) => i === index ? { ...item, ...patch } : item));
    }
    return h(Modal, { open: true, centered: true, title: '高级排序', okText: '应用', cancelText: '取消',
        rootClassName: 'gis-modal-root', onCancel: close,
        onOk: () => { apply(rows.filter(item => item.field).map(item => ({ ...item, direction: item.direction || 'ascend' }))); close(); } },
        h('div', { className: 'map-field-editor' }, ...rows.map((item, index) => h('div', { className: 'gis-sort-row', key: index },
            h(Select, { 'aria-label': '排序字段 ' + (index + 1), placeholder: '排序字段', value: item.field,
                options: columns.filter(column => column.value === item.field || !rows.some(row => row.field === column.value)),
                onChange: field => update(index, { field }) }),
            h(Select, { 'aria-label': '排序方向 ' + (index + 1), value: item.direction || 'ascend',
                options: [{ value: 'ascend', label: '升序' }, { value: 'descend', label: '降序' }],
                onChange: direction => update(index, { direction }) }),
            h(Button, { type: 'text', 'aria-label': '移除排序条件 ' + (index + 1),
                onClick: () => setRows(current => current.filter((_, i) => i !== index)) }, '移除'))),
        rows.length < Math.min(5, columns.length) && h(Button, { onClick: () => setRows(current => [...current, {}]) }, '添加排序条件')));
}

export function AttributeStatistics({ source, selected, columns, close }) {
    const [field, setField] = useState(columns[1]?.value || columns[0].value);
    const [only, setOnly] = useState(false);
    const [data, setData] = useState(null);
    const [error, setError] = useState('');
    useEffect(() => {
        let alive = true;
        setData(null);
        setError('');
        window.guizhi.map({ action: 'table-statistics', ref: source, field, selection: only ? selected : undefined })
            .then(value => { if (alive) setData(value); }, issue => { if (alive) setError(window.GuizhiErrorText(issue)); });
        return () => { alive = false; };
    }, [field, only]);
    return h(Modal, { open: true, centered: true, title: '字段统计', rootClassName: 'gis-modal-root',
        onCancel: close, footer: h(Button, { onClick: close }, '关闭') },
        h('div', { className: 'map-field-editor' },
            h(Select, { 'aria-label': '统计字段', value: field, options: columns, onChange: setField }),
            h(Checkbox, { checked: only, disabled: !selected.length, onChange: event => setOnly(event.target.checked) }, '仅统计已选要素'),
            error && h('p', { className: 'error', role: 'alert' }, error),
            !data && !error && h('p', { role: 'status' }, '正在统计…'),
            data && h('dl', { className: 'gis-result-metrics' },
                ...Object.entries({ count: '要素数', valid: '非空值', distinct: '不同值', min: '最小值', max: '最大值', sum: '总和', mean: '平均值' })
                    .filter(([key]) => key in data).map(([key, label]) => h('div', { key }, h('dt', null, label),
                        h('dd', null, data[key]?.toLocaleString('zh-CN', { maximumFractionDigits: 6 }) ?? '—'))))));
}

export function AttributeCell({ cell, busy, save, close }) {
    const [value, setValue] = useState(cell.value == null ? '' : String(cell.value));
    const [isNull, setNull] = useState(cell.value == null);
    return h(Modal, { open: true, centered: true, title: cell.field + ' · FID ' + cell.fid, rootClassName: 'gis-modal-root',
        okText: '保存', cancelText: '关闭', onCancel: () => { if (!busy) close(); },
        onOk: () => save(isNull ? null : value), confirmLoading: busy,
        okButtonProps: { disabled: cell.readonly },
        h('div', { className: 'map-field-editor' },
            h(Input, { 'aria-label': '属性值', value, autoFocus: true, readOnly: cell.readonly,
                onChange: event => { setValue(event.target.value); setNull(false); } }),
            h('div', { className: 'setting-row' },
                h(Checkbox, { checked: isNull, disabled: cell.readonly, onChange: event => setNull(event.target.checked) }, '空值'),
                h(Button, { onClick: () => window.guizhi.copyAttribute(isNull ? '' : value).catch(cell.fail) }, '复制值')))));
}
