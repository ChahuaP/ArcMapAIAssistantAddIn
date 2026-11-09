(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const api = window.guizhi;
    const reference = item => item.id + '@' + item.revision;
    const domains = { data: '数据', vector: '空间分析', geometry: '几何', table: '属性表',
        crs: '坐标', presentation: '地图与版面', evidence: '留痕', system: '运行状态', semantics: '审批材料',
        knowledge: '业务知识', governance: '依据与解释', raster: '栅格', cad: 'CAD',
        annual: '年度地类', imagery: '影像识别', delivery: '共享交付', business: '业务' };
    const materialReady = item => Boolean(item.layer || (item.kind && item.kind !== 'vector'));
    const parametersReady = (fields, values) => Object.keys(fields).every(name => values[name]?.trim());
    function parameterValues(fields, values) {
        return Object.fromEntries(Object.keys(fields).map(name => {
            const text = values[name].trim();
            try { return [name, JSON.parse(text)]; } catch { return [name, text]; }
        }));
    }

    function ParameterFields({ fields, values, change, disabled }) {
        return Object.entries(fields).map(([name, label]) => h('label', { key: name }, label,
            h('input', { value: values[name] || '', disabled, onChange: event =>
                change({ ...values, [name]: event.target.value }) })));
    }

    function RoleMaterials({ roles, materials, change, Material, plans, fail }) {
        return roles.map(role => {
            const items = materials[role.id] || [];
            const slots = items.length < role.maximum ? [...items, null] : items;
            return h('div', { key: role.id, className: 'role-material' },
                h('span', null, role.label, role.maximum > 1 && ' · ' + items.length + '/' + role.maximum),
                ...slots.map((item, index) => h(Material, { key: index, label: item ? role.label : '添加' + role.label,
                    item, plans, fail, kind: role.kind, change: next => {
                        const group = items.slice();
                        if (next) group[index] = next;
                        else group.splice(index, 1);
                        change({ ...materials, [role.id]: group });
                    } })));
        });
    }

    function Business({ catalog, refresh, choose, close, manage, initialMarkdown, Material }) {
        const dialog = useRef(null);
        const [operations, setOperations] = useState({});
        const [schema, setSchema] = useState(null);
        const [editing, setEditing] = useState(manage);
        const [markdown, setMarkdown] = useState(initialMarkdown || '');
        const [preview, setPreview] = useState(null);
        const [samples, setSamples] = useState({});
        const [parameters, setParameters] = useState({});
        const [busy, setBusy] = useState(false);
        const [notice, setNotice] = useState('');
        const [error, setError] = useState('');
        const showError = issue => setError(String(issue.message || issue).replace(/^Error invoking remote method '[^']+': Error: /, ''));
        useEffect(() => {
            dialog.current.showModal();
            api.capabilities().then(setOperations).catch(showError);
        }, []);
        function edit(text) {
            setMarkdown(text);
            setPreview(null);
            setSamples({});
            setParameters({});
            setNotice('');
            setEditing(true);
        }
        async function perform(action) {
            setBusy(true);
            setError('');
            try { await action(); } catch (issue) { showError(issue); } finally { setBusy(false); }
        }
        const fields = preview?.parameters || {};
        const publishable = preview && parametersReady(fields, parameters) && preview.materials.every(role => {
            const items = samples[role.id] || [];
            return items.length >= role.minimum && items.every(materialReady);
        });
        return h('dialog', { className: 'business-dialog', ref: dialog, onCancel: event => {
            event.preventDefault();
            if (!busy) close();
        } },
        h('div', { className: 'dialog-head' }, h('h2', null, editing ? '管理业务技能' : '业务能力'),
            h('button', { className: 'quiet', disabled: busy, onClick: close }, '关闭')),
        h('div', { className: 'business-actions' },
            h('button', { disabled: busy, onClick: () => setEditing(!editing) }, editing ? '返回业务' : '管理技能'),
            editing && h('button', { disabled: busy, onClick: () => perform(() => api.skillTemplate()) }, '下载模板'),
            editing && h('button', { disabled: busy, onClick: () => perform(async () => {
                const text = await api.skillOpen();
                if (text !== null) edit(text);
            }) }, '导入 Markdown')),
        h('div', { className: 'skill-cards' }, ...catalog.map(item => h('div', { className: 'skill-card', key: reference(item) },
            h('strong', null, item.title), h('small', null, 'v' + item.revision),
            h('span', null, item.materials.map(role => role.label).join('、')),
            h('button', { disabled: busy, className: editing ? '' : 'primary',
                onClick: () => editing ? perform(async () => edit((await api.skillShow(reference(item))).markdown)) : choose(item) },
            editing ? '编辑' : '办理')))),
        editing && h('section', { className: 'skill-editor' },
            h('label', null, '技能定义', h('textarea', { rows: 10, spellCheck: false, value: markdown, disabled: busy,
                onChange: event => edit(event.target.value) })),
            h('button', { disabled: !markdown.trim() || busy, onClick: () => perform(async () => {
                setPreview(await api.skillPreview(markdown));
            }) }, '读取材料要求'),
            preview && h('section', { className: 'skill-samples' },
                h('h3', null, '验证样本'),
                h(RoleMaterials, { roles: preview.materials, materials: samples, change: setSamples, Material, plans: [], fail: showError }),
                h(ParameterFields, { fields, values: parameters, change: setParameters, disabled: busy }),
                h('button', { className: 'primary', disabled: busy || !publishable, onClick: () => perform(async () => {
                    const result = await api.skillPublish(markdown, samples,
                        parameterValues(fields, parameters));
                    await refresh();
                    setNotice(result.title + ' v' + result.revision + ' 已发布');
                }) }, busy ? '正在处理' : '验证并发布'))),
        h('details', null, h('summary', null, '查看全部操作'), ...Object.entries(domains).map(([id, title]) =>
            h('details', { key: id, className: 'capability-group' }, h('summary', null, title),
                h('ul', null, ...Object.entries(operations).filter(([name]) =>
                    name.startsWith(id + '.') || (id === 'business' && !name.includes('.')))
                    .map(([name, spec]) => h('li', { key: name }, spec.title, editing && h('button', {
                        onClick: () => perform(async () => setSchema({ name, ...(await api.capability(name)) })),
                        disabled: busy,
                    }, name)))))),
            editing && schema && h('pre', null, schema.name + '\n' + JSON.stringify(schema.parameters, null, 2))),
        notice && h('p', { role: 'status' }, notice),
        error && h('p', { className: 'error', role: 'alert' }, error));
    }

    window.GuizhiBusiness = { Business, RoleMaterials, ParameterFields, materialReady, parametersReady, parameterValues, reference };
})();
