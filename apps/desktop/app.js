(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const api = window.guizhi;
    const labels = { running: '正在办理', cancelling: '正在停止', succeeded: '已完成',
        failed: '未完成', cancelled: '已停止', timed_out: '已超时', interrupted: '已中断',
        missing_materials: '待补材料', invalid_materials: '材料有误', no_result: '待补信息', answered: '已答复',
        budget_exhausted: '已保存进度' };
    const active = task => task && ['running', 'cancelling'].includes(task.status);
    const emptyDraft = () => ({ prompt: '', project: null, control: null, skill: null, materials: {} });
    const number = value => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });

    function Settings({ current, speech, close, save, entries, setEntries, manage }) {
        const dialog = useRef(null);
        const [value, change] = useState({ ...current, key: '' });
        const [settingsError, setSettingsError] = useState('');
        const [modelStatus, setModelStatus] = useState('');
        const fail = error => setSettingsError(String(error.message || error)
            .replace(/^Error invoking remote method '[^']+': Error: /, ''));
        const update = (name, next) => {
            setModelStatus('');
            change(old => ({ ...old, [name]: next }));
        };
        useEffect(() => { dialog.current.showModal(); }, []);
        const local = value.provider === 'ollama';
        const check = async (kind, url, model) => {
            setModelStatus('正在检测');
            setSettingsError('');
            try {
                const { capabilities } = await api.modelCheck(kind, url, model);
                const names = { completion: '对话', tools: '工具调用', vision: '图像', thinking: '推理' };
                setModelStatus(capabilities.filter(item => names[item]).map(item => names[item]).join(' · '));
            } catch (error) { setModelStatus(''); fail(error); }
        };
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
                setModelStatus('');
                change(old => ({ ...old, provider, model: provider === 'ollama' ? 'qwen3.5:9b' : 'MiniMax-M3' }));
            } }, h('option', { value: 'minimax-cn' }, 'MiniMax 在线'), h('option', { value: 'ollama' }, 'Ollama 本地或内网'))),
            h('label', null, '模型', h('input', { value: value.model, required: true,
                onChange: event => update('model', event.target.value) })),
            local ? h('label', null, '服务地址', h('input', { value: value.modelUrl || '',
                placeholder: 'http://127.0.0.1:11434/v1', onChange: event => update('modelUrl', event.target.value) }))
                : h('label', null, 'API 密钥', h('input', { type: 'password', value: value.key,
                    placeholder: current.keyConfigured ? '已配置，可输入新密钥' : '输入 MiniMax API 密钥',
                    autoComplete: 'off', onChange: event => update('key', event.target.value) })),
            local && h('button', { type: 'button', onClick: () => check('chat', value.modelUrl, value.model) }, '检测对话模型'),
            h('details', null, h('summary', null, '视觉模型'),
                h('label', null, 'Ollama 模型', h('input', { value: value.visionModel || '',
                    onChange: event => update('visionModel', event.target.value) })),
                h('label', null, '服务地址', h('input', { value: value.visionUrl || '', placeholder: 'http://127.0.0.1:11434/v1',
                    onChange: event => update('visionUrl', event.target.value) })),
                h('button', { type: 'button', disabled: !value.visionModel?.trim(),
                    onClick: () => check('vision', value.visionUrl, value.visionModel) }, '检测视觉模型')),
            modelStatus && h('p', { role: 'status' }, modelStatus),
            h('label', { className: 'voice-toggle' },
                h('input', { type: 'checkbox', checked: value.showOperation !== false,
                    onChange: event => update('showOperation', event.target.checked) }), '显示办理过程'),
            value.showOperation !== false && h('label', { className: 'voice-toggle' },
                h('input', { type: 'checkbox', checked: value.screenHighlight !== false,
                    onChange: event => update('screenHighlight', event.target.checked) }), '高亮屏幕中的对应图层'),
            current.workspace && h(window.GuizhiCoordinates, { entries, setEntries }),
            speech && h(window.GuizhiSpeechSettings, { current: speech }),
            current.workspace && h('button', { type: 'button', onClick: manage }, '管理业务技能'),
            settingsError && h('p', { role: 'alert', className: 'error' }, settingsError),
            h('button', { className: 'primary', disabled: !value.workspace, type: 'submit' }, '保存设置')));
    }

    function Material({ label, item, change, fail, plans, kind = 'vector' }) {
        return h('div', { className: 'material' },
            h('button', { type: 'button', className: item ? 'attached' : 'attach', title: item?.name || label,
                onClick: () => api.material(kind).then(value => { if (value) change(value); }).catch(fail) },
                h('span', { 'aria-hidden': true }, item ? '◇' : '＋'), ' ', item?.name || label),
            item && item.layers.length > 1 && h('select', { 'aria-label': label + '图层', value: item.layer,
                onChange: event => change({ ...item, layer: event.target.value }) },
                h('option', { value: '' }, '选择图层'), ...item.layers.map(layer => h('option', { key: layer }, layer))),
            item?.layer && plans.length > 0 && h('select', { className: 'plan-select', 'aria-label': label + '坐标方案',
                value: item.plan || '', onChange: event => change({ ...item, plan: event.target.value }) },
                h('option', { value: '' }, '沿用坐标'),
                ...plans.map(plan => {
                    const id = plan.id + '@' + plan.revision;
                    return h('option', { key: id, value: id }, plan.title + ' · v' + plan.revision);
                })),
            item && h('button', { type: 'button', className: 'remove', 'aria-label': '移除' + label, onClick: () => change(null) }, '×'));
    }

    function Approval({ task, parcel, selectParcel, fail }) {
        const [shown, show] = useState(null);
        const caseFile = task.approval;
        const labels = { supported: '有依据', conflict: '材料冲突', insufficient: '证据不足', outside_period: '时点之外' };
        const parcels = new Map(caseFile.parcels.map(item => [item.id, item.code]));
        const activeClaims = parcel ? caseFile.claims.filter(item => item.subject === parcel ||
            item.subject === caseFile.project.id) : caseFile.claims;
        function locator(ref) {
            const loc = ref.locator;
            if (loc.page) return '第 ' + loc.page + ' 页';
            if (loc.sheet) return loc.sheet + ' · ' + loc.cell;
            if (loc.block) return '第 ' + loc.block + ' 个文档块' + (loc.row ? ' · ' + loc.row + ' 行 ' + loc.column + ' 列' : '');
            return '要素 ' + loc.fid;
        }
        return h('section', { className: 'approval-results', 'aria-label': '审批结论' },
            caseFile.parcel_changes?.length > 0 && h('p', null, caseFile.parcel_changes.map(item =>
                ({ added: '新增 ', removed: '移除 ', updated: '更新 ' })[item.status] + item.code).join('，')),
            parcel && h('button', { onClick: () => selectParcel(null) }, parcels.get(parcel) + ' ×'),
            ...activeClaims.map(item => h('div', { className: 'finding', key: item.id },
                h('button', { type: 'button', onClick: () => {
                    show(shown === item.id ? null : item.id);
                    if (parcels.has(item.subject)) selectParcel(item.subject);
                } }, (parcels.has(item.subject) ? parcels.get(item.subject) + ' · ' : '') + item.field,
                    h('span', null, item.value == null ? '—' : typeof item.value === 'number' ? number(item.value) : item.value,
                        item.unit && ' ' + item.unit)),
                h('small', null, labels[item.status] || item.status),
                shown === item.id && h('div', { className: 'finding-source' },
                    item.edition && h('p', null, item.as_of + ' · ' + item.edition),
                    item.quote && h('blockquote', null, item.quote),
                    ...item.evidence.map((ref, index) => ref.artifact.startsWith('document-')
                        ? h('button', { key: index, onClick: () => api.approvalSource(task.id, item.id, index).catch(fail) },
                            '打开原件 · ' + locator(ref))
                        : h('small', { key: index }, locator(ref)))))));
    }

    function Result({ task, retry, fail, editSkill, parcel, selectParcel }) {
        if (active(task)) return h('article', { className: 'bubble assistant working', role: 'status' },
            h('i', { className: 'pulse' }), task.status === 'cancelling' ? '正在结束任务' : task.step || '正在理解任务');
        if (task.status === 'succeeded' || task.status === 'answered') {
            const stats = task.statistics;
            return h('article', { className: 'bubble assistant' },
                h('div', { className: 'result-head' }, h('img', { src: '/mark.svg', alt: '', width: 29 }),
                    h('strong', null, task.status === 'answered' ? '规智' : '办理结果')),
                h('p', null, stats?.coverage_ratio !== undefined
                    ? `项目范围 ${number(stats.project_area_m2)} m²` : task.response),
                stats?.coverage_ratio !== undefined && h('dl', { className: 'metrics' },
                    h('div', null, h('dt', null, '重叠面积'), h('dd', null, number(stats.covered_area_m2), h('small', null, ' m²'))),
                    h('div', null, h('dt', null, '覆盖率'), h('dd', null, (stats.coverage_ratio * 100).toFixed(2), h('small', null, '%')))),
                task.approval && h(Approval, { task, parcel, selectParcel, fail }),
                h('div', { className: 'delivery-actions' },
                    task.candidate && h('button', { className: 'report', onClick: () => editSkill(task.candidate) }, '编辑技能草稿 ↗'),
                    task.hasReport && h('button', { className: 'report', onClick: () => api.report(task.id).catch(fail) }, '查看报告 ↗'),
                    ...(task.deliveries || []).map(kind => h('button', { key: kind, className: 'report',
                        onClick: () => api.delivery(task.id, kind).catch(fail) },
                        { export: '成果数据 ↗', table: '属性表 ↗', workflow: '办理留痕 ↗', package: '共享数据包 ↗' }[kind]))));
        }
        const detail = { missing_materials: '请按业务要求补齐材料。',
            invalid_materials: '请确认材料、图层和办理参数。', no_result: '请补充办理要求或从业务能力中选择。',
            timed_out: '办理超时。请检查模型服务或输入文件是否正被占用。' }[task.status];
        return h('article', { className: 'bubble assistant' }, h('strong', null, labels[task.status] || '未完成'),
            h('p', null, task.response || detail || '请检查材料、坐标和模型设置后继续。'),
            h('button', { className: 'report', onClick: () => retry(task) }, '继续处理'));
    }

    function App() {
        const [state, setState] = useState(null);
        const [draft, setDraft] = useState(emptyDraft);
        const [selected, select] = useState(null);
        const [parcel, selectParcel] = useState(null);
        const [settings, showSettings] = useState(false);
        const [error, setError] = useState('');
        const [sending, setSending] = useState(false);
        const [coordinates, setCoordinates] = useState([]);
        const [catalog, setCatalog] = useState([]);
        const [business, showBusiness] = useState('');
        const [arcmap, showArcmap] = useState(false);
        const [initialMarkdown, setInitialMarkdown] = useState('');
        const { Business, RoleMaterials, reference } = window.GuizhiBusiness;
        const fail = error => setError(String(error.message || error).replace(/^Error invoking remote method '[^']+': Error: /, ''));
        const update = (key, value) => setDraft(old => ({ ...old, [key]: value }));
        const refresh = () => api.skills().then(setCatalog);
        useEffect(() => {
            api.state().then(value => { setState(value); select(value.tasks[0]?.id || null); }).catch(fail);
            const off = api.onState(setState);
            const offSettings = api.onSettings(() => showSettings(true));
            return () => { off(); offSettings(); };
        }, []);
        useEffect(() => {
            if (state?.settings.workspace) {
                api.coordinates().then(setCoordinates).catch(fail);
                refresh().catch(fail);
            }
        }, [state?.settings.workspace]);
        const plans = coordinates.filter(entry => entry.kind === 'plan');
        const task = state?.tasks.find(item => item.id === selected);
        useEffect(() => { selectParcel(null); }, [selected]);
        const busy = state?.tasks.some(active) || sending;
        const skill = draft.skillDefinition;
        const materialsReady = !skill || skill.materials.every(role => {
            const items = draft.materials[role.id] || [];
            return items.length >= role.minimum && items.every(item => item.layer || ['raster', 'cad', 'document'].includes(item.kind));
        });
        async function submit(event) {
            event.preventDefault();
            if (window.GuizhiTaskCommand(draft.prompt) === 'reset') {
                startNew();
                return;
            }
            if (busy || !draft.prompt.trim() || !materialsReady) return;
            setError('');
            setSending(true);
            try {
                const next = await api.submit({ ...draft, previousTask: draft.previousTask || task?.id });
                setState(next);
                select(next.tasks[0].id);
                setDraft(emptyDraft());
            } catch (error) { fail(error); } finally { setSending(false); }
        }
        function retry(item) {
            const restored = catalog.find(entry => reference(entry) === item.skill);
            if (item.skill && !restored) {
                api.skillShow(item.skill).then(skillDefinition => setDraft({ ...emptyDraft(), ...item, skillDefinition })).catch(fail);
            } else setDraft({ ...emptyDraft(), ...item, skillDefinition: restored });
            select(null);
        }
        function choose(item) {
            setDraft({ ...emptyDraft(), skill: reference(item), skillDefinition: item, prompt: '请办理' + item.title });
            select(null);
            showBusiness('');
        }
        function startNew() {
            setError('');
            setDraft(emptyDraft());
            select(null);
            void api.submit({ prompt: '开始新任务' }).then(setState).catch(fail);
        }
        return h('div', { className: 'shell' },
            h('aside', { className: 'sidebar' },
                h('div', { className: 'brand' }, h('img', { src: '/mark.svg', alt: '', width: 39 }), h('span', null, '规智')),
                h('button', { className: 'new-task', onClick: startNew }, '＋ 新任务'),
                h('button', { disabled: !state?.settings.workspace, onClick: () => showBusiness('browse') }, '业务能力'),
                h('button', { disabled: !state?.settings.workspace || busy, onClick: () => showArcmap(true) }, 'ArcMap 交接'),
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
                            h(Result, { key: 'result', task, retry, fail, parcel, selectParcel, editSkill: text => {
                                setInitialMarkdown(text);
                                showBusiness('manage');
                            } }),
                        ] : h('div', { className: 'welcome' },
                            h('img', { src: '/mark.svg', alt: '', width: 70 }),
                            h('h2', null, '今天要办理什么？'),
                            h('p', null, '输入办理要求，或选择已有业务。'),
                            ...catalog.slice(0, 3).map(item => h('button', { key: reference(item), className: 'suggestion',
                                onClick: () => choose(item) }, item.title + ' ↗')))),
                        h('form', { className: 'composer', onSubmit: submit },
                            error && h('p', { role: 'alert', className: 'error' }, error),
                            skill ? h('div', { className: 'materials skill-materials' },
                                h('button', { type: 'button', className: 'attached', onClick: () => setDraft(emptyDraft()) },
                                    skill.title + ' ×'),
                                h(RoleMaterials, { roles: skill.materials, materials: draft.materials,
                                    change: value => update('materials', value), Material, plans, fail }))
                            : h('div', { className: 'materials' },
                                h(Material, { label: '项目范围', item: draft.project, change: value => update('project', value), fail, plans }),
                                h(Material, { label: '管控范围', item: draft.control, change: value => update('control', value), fail, plans }),
                                h(RoleMaterials, { roles: [{ id: 'inputs', label: '其他材料', kind: 'any', maximum: 30 }],
                                    materials: draft.materials, change: value => update('materials', value), Material, plans: [], fail })),
                            h('div', { className: 'compose-row' },
                                h('button', { type: 'button', title: '打开语音输入', onClick: () => api.portraitInput().catch(fail) }, '语音'),
                                h('textarea', { 'aria-label': '办理事项', placeholder: '输入办理事项…', rows: 2,
                                    value: draft.prompt, maxLength: 4000, onChange: event => update('prompt', event.target.value),
                                    onKeyDown: event => {
                                        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                                            event.preventDefault();
                                            event.currentTarget.form.requestSubmit();
                                        }
                                    } }),
                                busy ? h('button', { type: 'button', onClick: () => api.cancel().then(setState).catch(fail) }, '停止')
                                    : h('button', { type: 'submit', className: 'primary', disabled: !draft.prompt.trim() || !materialsReady }, '发送 ↑')))),
                    h(window.GuizhiMap, { key: task?.id || 'empty', map: task?.map, view: state?.mapView,
                        parcel, onParcel: selectParcel,
                        enabled: Boolean(state?.settings.workspace), onState: setState, fail }))),
            business && h(Business, { catalog, refresh, choose, Material, manage: business === 'manage',
                initialMarkdown, close: () => { setInitialMarkdown(''); showBusiness(''); } }),
            arcmap && h(window.GuizhiArcmap, { configured: state.settings.arcmapBridge, task,
                close: () => showArcmap(false), attach: project => {
                    setDraft({ ...emptyDraft(), project });
                    select(null);
                } }),
            state && (settings || !state.settings.workspace) && h(Settings, { current: state.settings,
                speech: state.speech,
                entries: coordinates, setEntries: setCoordinates,
                manage: () => { showSettings(false); showBusiness('manage'); },
                close: () => showSettings(false), save: async value => {
                    const next = await api.configure(value);
                    setState(next);
                    setDraft(emptyDraft());
                    select(next.tasks[0]?.id || null);
                    showSettings(false);
                } }));
    }
    ReactDOM.createRoot(document.getElementById('app')).render(h(App));
})();
