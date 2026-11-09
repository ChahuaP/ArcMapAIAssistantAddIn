(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const api = window.guizhi;
    const labels = { running: '正在办理', cancelling: '正在停止', succeeded: '已完成',
        failed: '未完成', cancelled: '已停止', timed_out: '已超时', interrupted: '已中断',
        missing_materials: '待补材料', invalid_materials: '材料有误', no_result: '待补信息' };
    const active = task => task && ['running', 'cancelling'].includes(task.status);
    const emptyDraft = () => ({ prompt: '', project: null, control: null });
    const number = value => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });

    function Settings({ current, close, save, entries, setEntries }) {
        const dialog = useRef(null);
        const [value, change] = useState({ ...current, key: '' });
        const [settingsError, setSettingsError] = useState('');
        const fail = error => setSettingsError(String(error.message || error)
            .replace(/^Error invoking remote method '[^']+': Error: /, ''));
        const update = (name, next) => change(old => ({ ...old, [name]: next }));
        useEffect(() => { dialog.current.showModal(); }, []);
        const local = value.provider === 'ollama';
        return h('dialog', { ref: dialog, onCancel: event => {
            event.preventDefault();
            if (current.workspace) close();
        } },
        h('form', { onSubmit: event => {
            event.preventDefault();
            setSettingsError('');
            void save(value).catch(fail);
        } },
            h('div', { className: 'dialog-head' }, h('h2', null, '设置'),
                current.workspace && h('button', { type: 'button', className: 'quiet', onClick: close }, '关闭')),
            h('label', null, '成果保存位置', h('div', { className: 'field-row' },
                h('input', { readOnly: true, value: value.workspace, required: true, placeholder: '选择一个文件夹' }),
                h('button', { type: 'button', onClick: () => api.workspace().then(folder => {
                    if (folder) update('workspace', folder);
                }).catch(fail) }, '选择'))),
            h('label', null, '模型服务', h('select', { value: value.provider, onChange: event => {
                const provider = event.target.value;
                change(old => ({ ...old, provider, model: provider === 'ollama' ? 'qwen2.5:7b' : 'MiniMax-M3' }));
            } }, h('option', { value: 'minimax-cn' }, 'MiniMax 在线'), h('option', { value: 'ollama' }, 'Ollama 本地'))),
            h('label', null, '模型', h('input', { value: value.model, required: true,
                onChange: event => update('model', event.target.value) })),
            local ? h('label', null, '服务地址', h('input', { value: value.modelUrl || '',
                placeholder: 'http://127.0.0.1:11434/v1', onChange: event => update('modelUrl', event.target.value) }))
                : h('label', null, 'API 密钥', h('input', { type: 'password', value: value.key,
                    placeholder: current.keyConfigured ? '已配置，可输入新密钥' : '输入 MiniMax API 密钥',
                    autoComplete: 'off', onChange: event => update('key', event.target.value) })),
            current.workspace && h(window.GuizhiCoordinates, { entries, setEntries }),
            settingsError && h('p', { role: 'alert', className: 'error' }, settingsError),
            h('button', { className: 'primary', disabled: !value.workspace, type: 'submit' }, '保存设置')));
    }

    function Material({ label, item, change, fail, plans }) {
        return h('div', { className: 'material' },
            h('button', { type: 'button', className: item ? 'attached' : 'attach', title: item?.name || label,
                onClick: () => api.material().then(value => { if (value) change(value); }).catch(fail) },
                h('span', { 'aria-hidden': true }, item ? '◇' : '＋'), ' ', item?.name || label),
            item && item.layers.length > 1 && h('select', { 'aria-label': label + '图层', value: item.layer,
                onChange: event => change({ ...item, layer: event.target.value }) },
                h('option', { value: '' }, '选择图层'), ...item.layers.map(layer => h('option', { key: layer }, layer))),
            item && plans.length > 0 && h('select', { className: 'plan-select', 'aria-label': label + '坐标方案',
                value: item.plan || '', onChange: event => change({ ...item, plan: event.target.value }) },
                h('option', { value: '' }, '沿用坐标'),
                ...plans.map(plan => {
                    const id = plan.id + '@' + plan.revision;
                    return h('option', { key: id, value: id }, plan.title + ' · v' + plan.revision);
                })),
            item && h('button', { type: 'button', className: 'remove', 'aria-label': '移除' + label, onClick: () => change(null) }, '×'));
    }

    function Result({ task, retry, fail }) {
        if (active(task)) return h('article', { className: 'bubble assistant working', role: 'status' },
            h('i', { className: 'pulse' }), task.status === 'cancelling' ? '正在结束任务' : task.step || '正在理解任务');
        if (task.status === 'succeeded') {
            const stats = task.statistics;
            return h('article', { className: 'bubble assistant' },
                h('div', { className: 'result-head' }, h('img', { src: '/mark.svg', alt: '', width: 29 }), h('strong', null, '核查结果')),
                h('p', null, stats?.coverage_ratio !== undefined
                    ? `项目范围 ${number(stats.project_area_m2)} m²` : task.response),
                stats?.coverage_ratio !== undefined && h('dl', { className: 'metrics' },
                    h('div', null, h('dt', null, '重叠面积'), h('dd', null, number(stats.covered_area_m2), h('small', null, ' m²'))),
                    h('div', null, h('dt', null, '覆盖率'), h('dd', null, (stats.coverage_ratio * 100).toFixed(2), h('small', null, '%')))),
                task.hasReport && h('button', { className: 'report', onClick: () => api.report(task.id).catch(fail) }, '查看核查报告 ↗'));
        }
        const detail = { missing_materials: '请补齐项目范围和管控范围，或确认需要清点的图层。',
            invalid_materials: '请确认文件存在且格式正确。', no_result: '请说明要清点要素，还是核查两个范围。',
            timed_out: '办理超时。请检查模型服务或输入文件是否正被占用。' }[task.status];
        return h('article', { className: 'bubble assistant' }, h('strong', null, labels[task.status] || '未完成'),
            h('p', null, detail || '请检查材料、坐标和模型设置后继续。'),
            h('button', { className: 'report', onClick: () => retry(task) }, '继续处理'));
    }

    function App() {
        const [state, setState] = useState(null);
        const [draft, setDraft] = useState(emptyDraft);
        const [selected, select] = useState(null);
        const [settings, showSettings] = useState(false);
        const [error, setError] = useState('');
        const [sending, setSending] = useState(false);
        const [coordinates, setCoordinates] = useState([]);
        const fail = error => setError(String(error.message || error).replace(/^Error invoking remote method '[^']+': Error: /, ''));
        const update = (key, value) => setDraft(old => ({ ...old, [key]: value }));
        useEffect(() => {
            api.state().then(value => { setState(value); select(value.tasks[0]?.id || null); }).catch(fail);
            const off = api.onState(setState);
            const offSettings = api.onSettings(() => showSettings(true));
            return () => { off(); offSettings(); };
        }, []);
        useEffect(() => {
            if (state?.settings.workspace) api.coordinates().then(setCoordinates).catch(fail);
        }, [state?.settings.workspace]);
        const plans = coordinates.filter(entry => entry.kind === 'plan');
        const task = state?.tasks.find(item => item.id === selected);
        const busy = state?.tasks.some(active) || sending;
        async function submit(event) {
            event.preventDefault();
            const command = draft.prompt.trim().replace(/[。！!？?\s]+$/g, '');
            if (['结束本轮', '结束这一轮任务', '结束当前任务', '开始新任务', '开始新的任务'].includes(command)) {
                startNew();
                return;
            }
            if (busy || !draft.prompt.trim()) return;
            setError('');
            setSending(true);
            try {
                const next = await api.submit(draft);
                setState(next);
                select(next.tasks[0].id);
                setDraft(emptyDraft());
            } catch (error) { fail(error); } finally { setSending(false); }
        }
        function retry(item) {
            setDraft({ prompt: item.prompt, project: item.project, control: item.control });
            select(null);
        }
        function startNew() {
            setError('');
            setDraft(emptyDraft());
            select(null);
            if (busy) void api.cancel().then(setState).catch(fail);
        }
        return h('div', { className: 'shell' },
            h('aside', { className: 'sidebar' },
                h('div', { className: 'brand' }, h('img', { src: '/mark.svg', alt: '', width: 39 }), h('span', null, '规智')),
                h('button', { className: 'new-task', onClick: startNew }, '＋ 新任务'),
                h('div', { className: 'history-label' }, '办理记录'),
                h('nav', { 'aria-label': '办理记录' }, ...(state?.tasks || []).map(item =>
                    h('button', { key: item.id, className: item.id === selected ? 'history selected' : 'history',
                        onClick: () => { select(item.id); setError(''); } },
                        h('span', { className: 'history-title' }, item.title), h('small', null, labels[item.status] || '未完成')))),
                h('button', { className: 'settings-link', onClick: () => showSettings(true) }, '设置')),
            h('main', { className: 'content' },
                h('header', null, h('h1', null, task ? task.title : '规智工作台'),
                    state && h('span', { className: 'model-chip' }, h('i'), state.settings.provider === 'ollama' ? '本地模型' : '在线模型')),
                h('div', { className: 'workbench' },
                    h('section', { className: 'conversation', 'aria-label': '任务对话' },
                        h('div', { className: 'messages' }, task ? [
                            h('article', { key: 'user', className: 'bubble user' }, task.prompt),
                            h(Result, { key: 'result', task, retry, fail }),
                        ] : h('div', { className: 'welcome' },
                            h('img', { src: '/mark.svg', alt: '', width: 70 }),
                            h('h2', null, '今天要办理什么？'),
                            h('p', null, '添加项目范围和管控范围，告诉规智你要核查什么。'),
                            h('button', { className: 'suggestion', onClick: () =>
                                update('prompt', '请核查项目范围与管控范围的重叠面积、覆盖比例和空间关系。') }, '管控线核查 ↗'))),
                        h('form', { className: 'composer', onSubmit: submit },
                            error && h('p', { role: 'alert', className: 'error' }, error),
                            h('div', { className: 'materials' },
                                h(Material, { label: '项目范围', item: draft.project, change: value => update('project', value), fail, plans }),
                                h(Material, { label: '管控范围', item: draft.control, change: value => update('control', value), fail, plans })),
                            h('div', { className: 'compose-row' },
                                h('textarea', { 'aria-label': '办理事项', placeholder: '输入办理事项…', rows: 2,
                                    value: draft.prompt, maxLength: 4000, onChange: event => update('prompt', event.target.value),
                                    onKeyDown: event => {
                                        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                                            event.preventDefault();
                                            event.currentTarget.form.requestSubmit();
                                        }
                                    } }),
                                busy ? h('button', { type: 'button', onClick: () => api.cancel().then(setState).catch(fail) }, '停止')
                                    : h('button', { type: 'submit', className: 'primary', disabled: !draft.prompt.trim() }, '发送 ↑')))),
                    h(window.GuizhiMap, { key: task?.id || 'empty', map: task?.map }))),
            state && (settings || !state.settings.workspace) && h(Settings, { current: state.settings,
                entries: coordinates, setEntries: setCoordinates,
                close: () => showSettings(false), save: async value => {
                    const next = await api.configure(value);
                    setState(next);
                    select(next.tasks[0]?.id || null);
                    showSettings(false);
                } }));
    }
    ReactDOM.createRoot(document.getElementById('app')).render(h(App));
})();
