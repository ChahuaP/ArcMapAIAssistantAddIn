(() => {
    const h = React.createElement;
    const { useEffect, useState } = React;
    const api = window.guizhi;
    const { button } = window.GuizhiShell;
    const { RoleMaterials, roleReady, kinds } = window.GuizhiMaterials;
    const deliverables = { export: '成果数据', report: '办理报告', map: '成果地图', table: '统计表',
        workflow: '办理留痕', package: '交付包', boundaries: '边界图层', classification: '地类识别结果',
        balance: '占补平衡核算结果', permit_history: '用地手续与历年地类核查结果' };
    const errorText = window.GuizhiErrorText;
    const idFor = prefix => prefix + '-' + crypto.randomUUID().slice(0, 8);
    const blank = () => ({ id: idFor('business'), title: '', aliases: [], materials: [], parameters: {}, steps: [],
        rules: [], evidence: [] });
    const options = values => Object.entries(values).map(([key, title]) => h('option', { key, value: key }, title));
    const field = (label, input) => h('label', null, label, input);
    const text = (value, change, props = {}) => h('input', { ...props, value: value ?? '',
        onChange: event => change(event.target.value) });
    const select = (value, values, change) => h('select', { value, onChange: event => change(event.target.value) }, ...options(values));
    const array = value => value.split(/[,，]/).map(part => part.trim()).filter(Boolean);
    const { parametersReady, parameterValues } = window.GuizhiTaskCommands;
    function ParameterFields({ fields, values, change, disabled }) {
        return Object.entries(fields).map(([name, label]) => h('label', { key: name }, label,
            text(values[name], value => change({ ...values, [name]: value }), { disabled,
                ...(name.endsWith('_date') ? { type: 'date' } : {}) })));
    }

    function SkillEditor({ initialMarkdown, onPublished, plans }) {
        const [document, setDocument] = useState(blank);
        const [description, setDescription] = useState('');
        const [advanced, setAdvanced] = useState(false);
        const [markdown, setMarkdown] = useState('');
        const [operations, setOperations] = useState({});
        const [parameters, setParameters] = useState({});
        const [samples, setSamples] = useState({});
        const [prepared, setPrepared] = useState(null);
        const [trial, setTrial] = useState(null);
        const [busy, setBusy] = useState(false);
        const [error, setError] = useState('');
        const [notice, setNotice] = useState('');
        const fail = issue => setError(errorText(issue));
        const fromText = async raw => {
            setDocument(await api.skillPreview(raw));
            setDescription(raw.split(/^---[ \t]*\r?$/m).slice(2).join('\n---\n').trim());
            setMarkdown(raw);
            setAdvanced(false);
            reset();
        };
        useEffect(() => {
            api.capabilities().then(setOperations).catch(fail);
            if (initialMarkdown) fromText(initialMarkdown).catch(fail);
        }, []);
        function reset() {
            setPrepared(null);
            setTrial(null);
            setNotice('');
        }
        const update = (name, value) => { setDocument(old => ({ ...old, [name]: value })); reset(); };
        const serialize = () => '---\n' + JSON.stringify(document, null, 2) + '\n---\n' + description.trim() + '\n';
        const rowChange = (group, index, row) => update(group, document[group].flatMap((item, i) =>
            i === index ? row ? [row] : [] : [item]));
        async function perform(action) {
            setError('');
            setBusy(true);
            try { await action(); } catch (issue) { fail(issue); }
            finally { setBusy(false); }
        }
        const collection = (key, title, addLabel, create, render, limit) => h('section', { className: 'skill-section' },
            h('h3', null, title),
            ...document[key].map((row, index) => h('div', { key: index, className: 'skill-row' },
                h('div', { className: 'setting-fields' }, ...render(row, next => rowChange(key, index, { ...row, ...next }))),
                button('close', '移除', { type: 'text', 'aria-label': '移除' + title + ' ' + (index + 1),
                    onClick: () => rowChange(key, index, null) }))),
            button('plus', addLabel, { disabled: document[key].length >= limit,
                onClick: () => update(key, [...document[key], create()]) }));
        const addRole = () => ({ id: idFor('material'), label: '', purpose: '', kind: 'vector', geometry: 'any', coordinate: 'defined',
            minimum: 1, maximum: 1 });
        const operationsByTitle = Object.fromEntries(Object.entries(operations).map(([id, spec]) => [id, spec.title]));
        const stepOptions = Object.fromEntries(document.steps.filter(step => step.id).map(step => [step.id, step.id]));
        const bindInputs = Object.fromEntries([
            ...document.materials.filter(role => role.id).map(role => ['material.' + role.id, role.label || role.id]),
            ...document.steps.filter(step => step.id).map(step => ['step.' + step.id, '步骤 ' + step.id]),
        ]);
        const ready = prepared && parametersReady(prepared.definition.parameters, parameters) &&
            prepared.definition.materials.every(role => roleReady(role, samples[role.id]));
        const parameterRows = Object.entries(document.parameters);
        const changeParameter = (index, entry) => {
            const rows = [...parameterRows];
            if (entry) rows[index] = entry;
            else rows.splice(index, 1);
            if (new Set(rows.map(([key]) => key)).size !== rows.length) return fail('参数标识不能重复');
            update('parameters', Object.fromEntries(rows));
        };
        return h('div', { className: 'skill-editor' },
            h('div', { className: 'business-actions' },
                button('file', '下载模板', { disabled: busy, onClick: () => perform(() => api.skillTemplate()) }),
                button('folder', '导入模板', { disabled: busy, onClick: () => perform(async () => {
                    const raw = await api.skillOpen();
                    if (raw !== null) await fromText(raw);
                }) }),
                button('layers', advanced ? '返回表单' : 'Markdown 定义', { disabled: busy, onClick: () => {
                    if (advanced) void perform(() => fromText(markdown));
                    else { setMarkdown(serialize()); setAdvanced(true); }
                } })),
            advanced ? field('Markdown 定义', h('textarea', { value: markdown, rows: 12, spellCheck: false, disabled: busy,
                onChange: event => { setMarkdown(event.target.value); reset(); } })) : h('fieldset', { disabled: busy },
                h('div', { className: 'setting-fields' },
                    field('业务名称', text(document.title, value => update('title', value))),
                    field('业务标识', text(document.id, value => update('id', value), { placeholder: '例如 parcel-check' })),
                    field('常用说法', h('input', { key: document.id + description, defaultValue: document.aliases.join('、'),
                        onBlur: event => update('aliases', array(event.target.value.replaceAll('、', '，'))) }))),
                field('业务用途', h('textarea', { rows: 2, value: description,
                    onChange: event => { setDescription(event.target.value); reset(); } })),
                collection('materials', '材料角色', '添加角色', addRole, (role, change) => [
                    field('角色名称', text(role.label, label => change({ label }))),
                    field('角色标识', text(role.id, id => change({ id }), { readOnly: Boolean(role.resource) })),
                    field('用途', text(role.purpose, purpose => change({ purpose }))),
                    field('材料格式', role.resource ? h('span', null, '由系统提供') : select(role.kind, kinds,
                        kind => change({ kind, geometry: 'any', coordinate: 'defined' }))),
                    role.kind === 'vector' && field('图形', select(role.geometry, { any: '不限', point: '点', line: '线', polygon: '面' },
                        geometry => change({ geometry }))),
                    ['vector', 'raster'].includes(role.kind) && field('坐标要求', select(role.coordinate,
                        { any: '不限', defined: '已有坐标定义', projected_meters: '米制投影' }, coordinate => change({ coordinate }))),
                    ...[['minimum', '最少份数', 0], ['maximum', '最多份数', 1]].map(([key, title, min]) =>
                        field(title, text(role[key], value => change({ [key]: Number(value) }), { type: 'number', min, max: 32 }))),
                ], 12),
                h('details', { className: 'skill-section' }, h('summary', null, '办理参数'),
                    ...parameterRows.map(([key, label], index) => h('div', { key: index, className: 'skill-row' },
                        h('div', { className: 'setting-fields' },
                            field('参数标识', h('input', { defaultValue: key, onBlur: event => changeParameter(index, [event.target.value, label]) })),
                            field('参数名称', text(label, value => changeParameter(index, [key, value])))),
                        button('close', '移除', { type: 'text', onClick: () => changeParameter(index) }))),
                    button('plus', '添加办理参数', { disabled: parameterRows.length >= 20,
                        onClick: () => changeParameter(parameterRows.length, ['param-' + (parameterRows.length + 1), '']) })),
                collection('steps', '办理流程', '添加步骤', () => ({ id: idFor('step'), operation: '', inputs: [], parameters: {} }), (step, change) => [
                    field('步骤标识', text(step.id, id => change({ id }))),
                    field('办理操作', select(step.operation, { '': '请选择操作', ...operationsByTitle }, operation => change({ operation }))),
                    field('输入材料或前序成果', h(antd.Select, { mode: 'multiple', value: step.inputs, disabled: busy,
                        getPopupContainer: trigger => trigger.closest('.skill-row'),
                        options: Object.entries(bindInputs).map(([value, label]) => ({ value, label })),
                        onChange: inputs => change({ inputs }), 'aria-label': '输入材料或前序成果' })),
                    h('details', { key: 'parameters' }, h('summary', null, '操作参数'),
                        field('参数对象', h('textarea', { key: step.operation, rows: 2,
                            defaultValue: JSON.stringify(step.parameters), onBlur: event => {
                                try { change({ parameters: JSON.parse(event.target.value) }); }
                                catch { fail('操作参数需填写有效的 JSON 对象'); event.target.value = JSON.stringify(step.parameters); }
                            } }))),
                ], 12),
                collection('rules', '结果规则', '添加规则', () => ({ step: '', statistic: '', compare: 'eq', value: 0 }), (rule, change) => [
                    field('检查步骤', select(rule.step, { '': '选择步骤', ...stepOptions }, step => change({ step }))),
                    field('指标标识', text(rule.statistic, statistic => change({ statistic }))),
                    field('比较', select(rule.compare, { eq: '等于', ge: '至少', le: '至多' }, compare => change({ compare }))),
                    field('指标值', h('input', { defaultValue: String(rule.value), onBlur: event => change({ value: scalar(event.target.value) }) })),
                ], 16),
                collection('evidence', '交付要求', '添加交付要求', () => ({ step: '', kind: 'report' }), (item, change) => [
                    field('成果步骤', select(item.step, { '': '选择步骤', ...stepOptions }, step => change({ step }))),
                    field('成果类型', select(item.kind, { ...deliverables,
                        ...(deliverables[item.kind] ? {} : { [item.kind]: item.kind }) }, kind => change({ kind }))),
                ], 16)),
            h('div', { className: 'business-actions' },
                button('layers', '准备试办', { disabled: busy, onClick: () => perform(async () => {
                    const raw = advanced ? markdown : serialize();
                    setPrepared({ raw, definition: await api.skillPreview(raw) });
                    setTrial(null);
                    setNotice('');
                }) })),
            prepared && h('section', { className: 'skill-section' }, h('h3', null, '试办材料'),
                h(RoleMaterials, { roles: prepared.definition.materials, materials: samples, plans, fail, disabled: busy,
                    change: value => { setSamples(value); setTrial(null); } }),
                h('div', { className: 'setting-fields' }, h(ParameterFields, {
                    fields: prepared.definition.parameters, values: parameters, disabled: busy,
                    change: value => { setParameters(value); setTrial(null); } })),
                h('div', { className: 'business-actions' },
                    button('clock', '试办一次', { loading: busy, disabled: !ready || busy, onClick: () => perform(async () => {
                        const result = await api.skillTrial(prepared.raw, samples,
                            parameterValues(prepared.definition.parameters, parameters));
                        setTrial(result);
                        if (result.error) fail(result.error);
                    }) }),
                    button('plus', '发布业务', { type: 'primary', disabled: busy || trial?.status !== 'succeeded',
                        onClick: () => perform(async () => {
                            const result = await api.skillPublish(prepared.raw, trial.run_id);
                            await onPublished();
                            setTrial(null);
                            setNotice(result.title + ' v' + result.release_version + ' 已发布');
                        }) })),
                trial?.status === 'succeeded' && h('p', { role: 'status' },
                    '试办通过 · ' + trial.deliveries.filter(kind => deliverables[kind]).map(kind => deliverables[kind]).join('、'))),
            notice && h('p', { role: 'status' }, notice),
            error && h('p', { role: 'alert', className: 'error' }, error));
    }
    window.GuizhiSkillEditor = { SkillEditor, ParameterFields, parametersReady, parameterValues, deliverables };
})();
