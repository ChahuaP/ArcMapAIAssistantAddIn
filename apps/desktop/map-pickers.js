const h = window.React.createElement;
const { useEffect, useState } = window.React;
const { Button, Input, Popover } = window.antd;

export function TokenPicker({ label, selected, items, onPick, disabled, dragType }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const choices = items.filter(item => String(item.label).toLowerCase().includes(query.trim().toLowerCase()));
    const choose = value => { onPick(value); setOpen(false); };
    const change = next => { setOpen(next); setQuery(''); };
    return h(Popover, { open, onOpenChange: change, trigger: 'click', placement: 'bottomLeft',
        content: h('div', { className: 'gis-choice-panel' },
            items.length > 7 && h(Input, { size: 'small', autoFocus: true, placeholder: '搜索' + label, 'aria-label': '搜索' + label,
                value: query, onChange: event => setQuery(event.target.value) }),
            h('div', { className: 'gis-choice-list', role: 'group', 'aria-label': label },
                choices.length ? choices.map(item => h(Button, { key: item.value, type: 'text', size: 'small',
                    'aria-pressed': item.value === selected, draggable: Boolean(dragType),
                    onDragStart: dragType ? event => event.dataTransfer.setData(dragType, item.value) : undefined,
                    onClick: () => choose(item.value) }, item.label)) : h('span', { className: 'muted' }, '没有可选项'))) },
    h(Button, { size: 'small', className: 'gis-token', disabled, 'aria-label': label, title: label, 'aria-expanded': open,
        onDragOver: dragType ? event => event.preventDefault() : undefined,
        onDrop: dragType ? event => {
            event.preventDefault();
            event.stopPropagation();
            const value = event.dataTransfer.getData(dragType);
            if (items.some(item => item.value === value)) choose(value);
        } : undefined }, items.find(item => item.value === selected)?.label || label));
}

export function UniquePicker({ layer, field, onPick, display, label = '唯一值', current, manual = false }) {
    const [open, setOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [page, setPage] = useState(0);
    const [result, setResult] = useState(null);
    const [error, setError] = useState('');
    useEffect(() => {
        if (!open || !layer?.ref || !field) return;
        let alive = true;
        setResult(null);
        setError('');
        const timer = setTimeout(() => window.guizhi.map({ action: 'table-unique-values', ref: layer.ref, field, query: query.trim(), page })
            .then(value => { if (alive) setResult(value); })
            .catch(issue => { if (alive) setError(window.GuizhiErrorText(issue)); }), query ? 220 : 0);
        return () => { alive = false; clearTimeout(timer); };
    }, [open, layer?.ref, field, query, page]);
    return h(Popover, { open, trigger: 'click', placement: 'bottomLeft', onOpenChange: next => {
        setOpen(next); setQuery(''); setPage(0);
    }, content: h('div', { className: 'gis-choice-panel' },
        h(Input, { size: 'small', autoFocus: true, maxLength: 200, placeholder: manual ? '搜索或输入值' : '搜索图层中的值',
            'aria-label': manual ? '搜索或输入值' : '搜索图层值',
            value: query, onChange: event => { setQuery(event.target.value); setPage(0); } }),
        h('div', { className: 'gis-choice-list', role: 'group', 'aria-label': '字段唯一值' },
            result?.values.length ? result.values.map((value, index) => h(Button, { type: 'text', size: 'small', key: index,
                onClick: () => { onPick(value); setOpen(false); } }, display(value))) :
                h('span', { role: error ? 'alert' : 'status', className: error ? 'error' : 'muted' },
                    error || (result ? '没有匹配的值' : '正在读取…'))),
        (page > 0 || result?.more || manual) && h('div', { className: 'gis-choice-pages' },
            manual && h(Button, { size: 'small', onClick: () => { onPick(query); setOpen(false); } }, '使用输入值'),
            (page > 0 || result?.more) && h(Button, { size: 'small', disabled: !page, onClick: () => setPage(page - 1) }, '上一页'),
            (page > 0 || result?.more) && h(Button, { size: 'small', disabled: !result?.more, onClick: () => setPage(page + 1) }, '下一页'))) },
    h(Button, { size: 'small', disabled: !field || !layer, 'aria-label': label, className: 'gis-token', 'aria-expanded': open }, current ?? label));
}

export function AttributePreview({ layer, field }) {
    const [rows, setRows] = useState(null);
    const [error, setError] = useState('');
    useEffect(() => {
        let active = true;
        window.guizhi.map({ action: 'table', ref: layer.ref, page: 0 }).then(result => {
            if (active) setRows(result.rows.slice(0, 8));
        }).catch(issue => { if (active) setError(window.GuizhiErrorText(issue)); });
        return () => { active = false; };
    }, [layer.ref]);
    const columns = field ? [field] : layer.fields.slice(0, 4).map(item => item.name);
    if (error) return h('p', { role: 'alert', className: 'error' }, error);
    if (!rows) return h('p', { role: 'status', className: 'muted' }, '正在读取预览…');
    if (!rows.length) return h('p', { className: 'muted' }, '没有符合条件的要素');
    return h('div', { className: 'gis-preview-table' }, h('table', { 'aria-label': '属性预览' },
        h('thead', null, h('tr', null, h('th', null, '要素编号'), columns.map(name => h('th', { key: name }, name)))),
        h('tbody', null, rows.map(row => h('tr', { key: row.id }, h('td', null, row.id),
            columns.map(name => h('td', { key: name }, row.values[name] == null ? '—' : String(row.values[name]))))))));
}
