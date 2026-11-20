(() => {
    const { createElement: h, useEffect, useState } = window.React;
    const { Button, Input, Modal, Spin } = window.antd;
    const api = window.guizhi;
    const fields = [['region', '适用区域', 'text'], ['version', '版本或文号', 'text'],
        ['start', '生效日期', 'date'], ['end', '失效日期', 'date']];

    window.GuizhiKnowledge = function Knowledge() {
        const [catalog, setCatalog] = useState({ documents: [], model: '' });
        const [name, setName] = useState('');
        const [draft, setDraft] = useState({});
        const [pending, setPending] = useState(false);
        const [busy, setBusy] = useState('list');
        const [issue, setIssue] = useState('');
        const [query, setQuery] = useState('');
        const [searchRegion, setSearchRegion] = useState('');
        const [searchDate, setSearchDate] = useState('');
        const [matches, setMatches] = useState(null);

        async function run(action, value) {
            setBusy(action);
            setIssue('');
            try {
                const result = await api.knowledge(action, value);
                if (!result) return;
                if (action === 'test') setMatches(result);
                else if (action !== 'export') {
                    setCatalog(result);
                    setName(result.model);
                    setMatches(null);
                    setPending(false);
                }
            } catch (error) { setIssue(window.GuizhiErrorText(error)); }
            finally { setBusy(''); }
        }

        useEffect(() => { void run('list'); }, []);
        return h('div', null,
            h('section', { className: 'settings-card' },
                h('h2', null, '本机索引'),
                h('p', { className: 'muted' }, '使用本机 Docling 解析文档、Qdrant 检索、Ollama 生成向量。请先启动这三个服务。'),
                h('div', { className: 'setting-row' },
                    h(Input, { value: name, placeholder: '例如 bge-m3', 'aria-label': '本机向量模型',
                        disabled: Boolean(busy || catalog.documents.length), onChange: event => setName(event.target.value) }),
                    h(Button, { disabled: !name.trim() || Boolean(busy || catalog.documents.length),
                        onClick: () => void run('model', name.trim()) }, '保存模型'))),
            h('section', { className: 'settings-card' },
                h('div', { className: 'section-head' }, h('h2', null, '资料'),
                    h(Button, { type: 'primary', disabled: !catalog.model || Boolean(busy),
                        onClick: () => { setDraft({}); setPending(true); } }, '导入资料')),
                !catalog.documents.length && !busy && h('p', { className: 'muted' }, '暂无资料'),
                ...catalog.documents.map(doc => h('article', { key: doc.id, className: 'diagnostic-row' },
                    h('div', null, h('strong', null, doc.name),
                        h('p', { className: 'muted' }, [doc.region, doc.version, doc.start, doc.end].filter(Boolean).join(' · '))),
                    h('div', { className: 'setting-row' },
                    h(Button, { disabled: Boolean(busy), onClick: () => { setDraft(doc); setPending(true); } }, '编辑'),
                    h(Button, { disabled: Boolean(busy), onClick: () => void run('export', doc.id) }, '保存原件'),
                    h(Button, { danger: true, disabled: Boolean(busy), onClick: () => Modal.confirm({
                        title: '删除资料', content: doc.name, okText: '删除', cancelText: '取消', okType: 'danger',
                        onOk: () => run('delete', doc.id),
                    }) }, '删除'))))),
            h('section', { className: 'settings-card' },
                h('h2', null, '检索验证'),
                h('div', { className: 'setting-row' },
                    h(Input, { value: query, placeholder: '输入术语、文号或问题', 'aria-label': '检索资料',
                        maxLength: 500, onChange: event => setQuery(event.target.value) }),
                    h(Button, { disabled: !query.trim() || !catalog.documents.length || Boolean(busy),
                        onClick: () => void run('test', { query, region: searchRegion, on_date: searchDate }) }, '检索')),
                h('div', { className: 'setting-row' },
                    h('label', { className: 'field' }, '适用区域', h(Input, { value: searchRegion, maxLength: 120,
                        onChange: event => setSearchRegion(event.target.value) })),
                    h('label', { className: 'field' }, '适用日期', h(Input, { type: 'date', value: searchDate,
                        onChange: event => setSearchDate(event.target.value) }))),
                matches?.length === 0 && h('p', { className: 'muted' }, '未找到相关资料'),
                matches?.map(item => h('details', { key: item.id }, h('summary', null, item.name + ' · ' + item.location),
                    h('p', { style: { whiteSpace: 'pre-wrap' } }, item.text)))),
            busy && h('p', { role: 'status' }, h(Spin, { size: 'small' }), busy === 'import' ? ' 正在解析并建立索引' : ' 正在处理'),
            issue && h('p', { role: 'alert', className: 'error' }, issue),
            h(Modal, { open: pending, title: draft.id ? '编辑资料' : '导入资料',
                okText: draft.id ? '保存' : '选择文件', cancelText: '取消',
                confirmLoading: busy === 'import' || busy === 'edit', onCancel: () => { if (!busy) setPending(false); },
                onOk: () => run(draft.id ? 'edit' : 'import', draft) },
                ...fields.map(([key, label, type]) => h('label', { key, className: 'field' }, label,
                    h(Input, { type, value: draft[key] || '', maxLength: 120,
                        onChange: event => setDraft(old => ({ ...old, [key]: event.target.value })) }))),
                issue && h('p', { role: 'alert', className: 'error' }, issue)));
    };
})();
