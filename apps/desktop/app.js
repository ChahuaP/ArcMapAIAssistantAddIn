(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const api = window.guizhi;
    const { Theme, Workbench, brand, button, iconButton, icon } = window.GuizhiShell;
    const launcher = new URLSearchParams(location.search).get('surface') === 'launcher';
    const labels = { running: '正在办理', cancelling: '正在停止', succeeded: '已完成',
        failed: '未完成', cancelled: '已停止', timed_out: '已超时', interrupted: '已中断',
        missing_materials: '待补材料', invalid_materials: '材料有误', no_result: '待补信息', answered: '已答复',
        budget_exhausted: '已保存进度' };
    const active = task => task && ['running', 'cancelling'].includes(task.status);
    const emptyDraft = () => ({ prompt: '', project: null, control: null, skill: null, materials: {}, parameterText: {},
        unassigned: [], freeMode: false });
    const number = value => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const { Material, RoleMaterials, materialReady, roleReady, mergeMaterials, intake } = window.GuizhiMaterials;

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
            if (loc.line) return '第 ' + loc.line + ' 行';
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
                        { export: '成果数据 ↗', table: '属性表 ↗', workflow: '办理留痕 ↗',
                            package: '共享数据包 ↗', boundaries: '边界图层 ↗' }[kind]))));
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
        const [draft, renderDraft] = useState(emptyDraft);
        const shared = useRef(draft);
        const [appearance, setAppearance] = useState({
            theme: 'system', dark: matchMedia('(prefers-color-scheme: dark)').matches, panels: [222, 362],
            shortcut: 'Ctrl+G',
        });
        const [selected, select] = useState(null);
        const [parcel, selectParcel] = useState(null);
        const [settings, showSettings] = useState(false);
        const [error, setError] = useState('');
        const [sending, setSending] = useState(false);
        const [coordinates, setCoordinates] = useState([]);
        const [catalog, setCatalog] = useState([]);
        const [business, showBusiness] = useState('');
        const [arcmap, showArcmap] = useState(false);
        const [tab, setTab] = useState('成果');
        const [initialMarkdown, setInitialMarkdown] = useState('');
        const [adding, setAdding] = useState(false);
        const { Business, reference, matchBusiness } = window.GuizhiBusiness;
        const { ParameterFields, parametersReady, parameterValues } = window.GuizhiSkillEditor;
        const fail = error => setError(String(error.message || error).replace(/^Error invoking remote method '[^']+': Error: /, ''));
        function receiveDraft(value) {
            shared.current = { ...emptyDraft(), ...value };
            renderDraft(shared.current);
        }
        function setDraft(next) {
            receiveDraft(typeof next === 'function' ? next(shared.current) : next);
            void api.saveDraft(shared.current).catch(fail);
        }
        const saveAppearance = input => api.appearance(input).then(setAppearance);
        const update = (key, value) => setDraft(old => ({ ...old, [key]: value }));
        const refresh = () => api.skills().then(setCatalog);
        useEffect(() => {
            api.state().then(value => {
                setState(value);
                setAppearance(value.appearance);
                select(value.focusTask);
            }).catch(fail);
            api.readDraft().then(receiveDraft).catch(fail);
            const callbacks = [
                api.onState(value => {
                    setState(value);
                    if (value.focusTask && value.focusTask !== focusTask.current) select(value.focusTask);
                    focusTask.current = value.focusTask;
                }),
                api.onSettings(() => showSettings(true)), api.onAppearance(setAppearance), api.onDraft(receiveDraft),
                api.onLauncherFocus(() => document.querySelector('.launcher textarea')?.focus()),
                api.onLauncherSubmit(() => document.querySelector('.launcher form')?.requestSubmit()),
                api.onOpenTask(id => { showSettings(false); select(id); }),
            ];
            const dismiss = event => {
                if (launcher && event.key === 'Escape' && !event.isComposing && !document.querySelector('dialog[open]')) {
                    void api.hideLauncher().catch(fail);
                }
            };
            addEventListener('keydown', dismiss);
            return () => { callbacks.forEach(unsubscribe => unsubscribe()); removeEventListener('keydown', dismiss); };
        }, []);
        const focusTask = useRef(null);
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
        const freeInputs = skill ? draft.unassigned : draft.materials.inputs || [];
        const materialsReady = skill ? draft.unassigned.length === 0 && skill.materials.every(role => roleReady(role, draft.materials[role.id]))
            : [draft.project, draft.control, ...freeInputs].filter(Boolean).every(materialReady);
        const fields = skill?.parameters || {};
        const suggestion = !skill && !draft.freeMode ? matchBusiness(draft.prompt, catalog) : { items: [] };
        const canSubmit = materialsReady && parametersReady(fields, draft.parameterText) &&
            (suggestion.items.length < 2 || suggestion.exact);
        async function submit(event) {
            event.preventDefault();
            const action = window.GuizhiTaskCommand(draft.prompt);
            if (action === 'workbench') return api.workbench().catch(fail);
            if (action === 'reset') {
                startNew();
                return;
            }
            if (busy || adding || !draft.prompt.trim()) return;
            if (!ready) return api.workbench().catch(fail);
            if (suggestion.exact) return choose(suggestion.items[0], true);
            if (!canSubmit) return;
            setError('');
            setSending(true);
            try {
                const next = await api.submit({ ...draft, previousTask: draft.previousTask || (launcher ? state.focusTask : task?.id),
                    skillParameters: skill ? parameterValues(fields, draft.parameterText) : undefined });
                setState(next);
                select(next.tasks[0].id);
                if (launcher) await api.workbench(next.focusTask);
            } catch (error) { fail(error); } finally { setSending(false); }
        }
        function retry(item) {
            const restored = catalog.find(entry => reference(entry) === item.skill);
            if (item.skill && !restored) {
                api.skillShow(item.skill).then(skillDefinition => setDraft({ ...emptyDraft(), ...item, skillDefinition })).catch(fail);
            } else setDraft({ ...emptyDraft(), ...item, skillDefinition: restored });
            select(null);
        }
        function choose(item, preservePrompt = false) {
            setDraft(old => ({ ...emptyDraft(), skill: reference(item), skillDefinition: item,
                prompt: preservePrompt ? old.prompt : '请办理' + item.title,
                unassigned: [...old.unassigned, ...Object.values(old.materials).flat(), ...[old.project, old.control].filter(Boolean)] }));
            setError('');
            select(null);
            showBusiness('');
        }
        function startNew() {
            setError('');
            setDraft(emptyDraft());
            select(null);
            void api.submit({ prompt: '开始新任务' }).then(setState).catch(fail);
        }
        const ready = Boolean(state?.settings.workspace && state?.settings.modelConfig);
        const picked = [draft.project, draft.control, ...draft.unassigned, ...Object.values(draft.materials).flat()].filter(Boolean);
        function appendMaterials(items) {
            setDraft(current => {
                const unique = mergeMaterials(current.materials.inputs || [], items);
                if (unique.length > 30) throw new Error('一次最多添加 30 份自由材料');
                return { ...current, materials: { ...current.materials, inputs: unique } };
            });
        }
        async function addMaterials(source) {
            if (adding) return;
            if (!ready) return api.workbench().catch(fail);
            setAdding(true);
            setError('');
            try { await intake(source(), appendMaterials, fail); }
            finally { setAdding(false); }
        }
        function drop(event) {
            event.preventDefault();
            const files = [...event.dataTransfer.files];
            if (!files.length) return;
            if (skill) return fail('请把材料拖到对应的业务角色');
            void addMaterials(() => api.intakeDropped(files, 'any'));
        }
        function freeMaterial(item, index) {
            const roles = skill?.materials.filter(role => !role.resource && role.kind === item.kind &&
                (draft.materials[role.id]?.length || 0) < role.maximum) || [];
            const change = (next, role = '') => {
                const inputs = freeInputs.slice();
                if (next && !role) inputs[index] = next;
                else inputs.splice(index, 1);
                if (skill) {
                    setDraft({ ...draft, unassigned: inputs, materials: { ...draft.materials,
                        ...(role ? { [role]: [...(draft.materials[role] || []), next] } : {}) } });
                } else update('materials', { ...draft.materials, inputs });
            };
            return h('div', { key: item.file + '#' + index, className: 'unassigned-material' }, h(Material, { item, change }),
                roles.length > 0 && h('select', { 'aria-label': '分配 ' + item.name, value: '',
                    onChange: event => change(item, event.target.value) }, h('option', { value: '' }, '分配到材料角色'),
                    ...roles.map(role => h('option', { key: role.id, value: role.id }, role.label))));
        }
        const composer = h('form', { className: 'composer', onSubmit: submit,
            onDragOver: event => event.preventDefault(), onDrop: drop },
            error && h('p', { role: 'alert', className: 'error' }, error),
            h('textarea', { 'aria-label': '办理事项', placeholder: launcher
                ? '说说需要办理什么，也可以把材料拖到这里' : '继续提问或补充办理要求', rows: 2,
                value: draft.prompt, maxLength: 4000, onChange: event => update('prompt', event.target.value),
                onKeyDown: event => {
                    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
                        event.preventDefault();
                        event.currentTarget.form.requestSubmit();
                    }
                } }),
            suggestion.items.length > 0 && h('div', { className: 'business-suggestion' },
                h('span', null, suggestion.items.length > 1 ? '请选择要办理的业务' : '匹配业务'),
                ...suggestion.items.map(item => button('layers', item.title,
                    { key: reference(item), onClick: () => choose(item, true) })),
                h(antd.Button, { type: 'text', onClick: () => update('freeMode', true) }, '按自由任务')),
            skill && h('div', { className: 'materials skill-materials' },
                h('div', { className: 'section-head' }, h('strong', null, skill.title),
                    h(antd.Button, { type: 'text', onClick: () => setDraft(old => ({ ...emptyDraft(), prompt: old.prompt,
                        freeMode: true, materials: { inputs: [...old.unassigned, ...Object.values(old.materials).flat()] } })) }, '改为自由任务')),
                freeInputs.length > 0 && h('div', { className: 'unassigned' }, h('small', null, '请为已添加的材料分配角色'),
                    ...freeInputs.map(freeMaterial)),
                h(RoleMaterials, { roles: skill.materials, materials: draft.materials,
                    change: value => update('materials', value), plans, fail, disabled: busy }),
                Object.keys(fields).length > 0 && h('div', { className: 'setting-fields' }, h(ParameterFields, {
                    fields, values: draft.parameterText, disabled: busy, change: value => update('parameterText', value) }))),
            !skill && picked.length > 0 && h('div', { className: 'materials' },
                ...['project', 'control'].filter(key => draft[key]).map(key => h(Material, {
                    key, item: draft[key], change: value => update(key, value), plans,
                })),
                ...freeInputs.map(freeMaterial)),
            h('div', { className: 'compose-actions' },
                !skill && button('paperclip', '添加材料', { type: 'text', loading: adding, onClick: () => addMaterials(() => api.material()) }),
                !skill && iconButton('folder', '添加材料目录', { disabled: adding, onClick: () => addMaterials(() => api.material('any', true)) }),
                h('span', { className: 'spacer' }),
                iconButton('mic', '语音输入', { type: 'default', onClick: () => api.portraitVoice().catch(fail) }),
                busy ? h(antd.Button, { onClick: () => api.cancel().then(setState).catch(fail) }, '停止')
                    : button('send', '发送', { type: 'primary', htmlType: 'submit',
                        disabled: adding || !draft.prompt.trim() || (!suggestion.exact && !canSubmit) })));
        const recent = state?.tasks || [];
        const taskButton = item => h('button', { key: item.id, className: 'history' + (item.id === selected ? ' selected' : ''),
            onClick: () => {
                if (launcher) return api.workbench(item.id).catch(fail);
                select(item.id);
                setError('');
            } }, h('span', { className: 'history-title' }, item.title),
            h('small', null, labels[item.status] || '未完成'));
        const tiles = h('div', { className: 'business-tiles' }, ...catalog.slice(0, 3).map(item =>
            h('button', { key: reference(item), className: 'business-tile', onClick: () => choose(item) },
                h('span', { className: 'tile-icon' }, icon('layers')),
                h('span', null, h('b', null, item.title),
                    h('small', null, item.materials.slice(0, 2).map(role => role.label).join(' · '))))));
        const quick = h('main', { className: 'launcher' },
            h('header', { className: 'launcher-head' }, brand(), h('div', { className: 'title-group' },
                appearance.shortcutRegistered && h('kbd', null, appearance.shortcut.replaceAll('+', ' + ')),
                button('app', '打开工作台', { type: 'text', onClick: () => api.workbench().catch(fail) }),
                iconButton('close', '收起快捷面板', { onClick: () => api.hideLauncher().catch(fail) }))),
            h('div', { className: 'launcher-body' }, composer,
                recent.length > 0 && h('section', { className: 'quick-section' }, h('h2', null, '最近使用'),
                    h('div', { className: 'recent-tasks' }, ...recent.slice(0, 2).map(taskButton))),
                ready && h('section', { className: 'quick-section' }, h('div', { className: 'section-head' },
                    h('h2', null, '常用业务'), h(antd.Button, { type: 'link', onClick: () => showBusiness('browse') }, '全部业务')), tiles)),
            h('footer', { className: 'launcher-footer' },
                h('span', { className: 'muted' }, ready ? (state.settings.modelConfig.mode === 'local' ? '本地服务' : '在线服务')
                    : '请在工作台完成首次设置'),
                h('span', null, h('kbd', null, 'Enter'), ' 发送　', h('kbd', null, 'Esc'), ' 收起')));
        const sidebar = h('aside', { className: 'sidebar' },
            button('plus', '新建任务', { type: 'primary', className: 'new-task', onClick: startNew }),
            h('div', { className: 'nav-item selected' }, icon('app'), '工作台'),
            h('button', { className: 'nav-item', disabled: !ready, onClick: () => showBusiness('browse') }, icon('grid'), '业务中心'),
            h('div', { className: 'history-label' }, '最近任务'),
            h('nav', { 'aria-label': '最近任务' }, ...recent.map(taskButton)),
            h('button', { className: 'nav-item', onClick: () => showSettings(true) }, icon('settings'), '设置'));
        const conversation = h('section', { className: 'conversation', 'aria-label': '任务对话' },
            h('div', { className: 'task-head' }, h('h1', null, task ? task.title : '规智工作台'),
                task && h('span', { className: 'status-tag' }, labels[task.status] || '未完成')),
            h('div', { className: 'messages' }, task ? [
                h('article', { key: 'user', className: 'bubble user' }, task.prompt),
                h(Result, { key: 'result', task, retry, fail, parcel, selectParcel, editSkill: text => {
                    setInitialMarkdown(text);
                    showBusiness('manage');
                } }),
            ] : h('div', { className: 'welcome' }, h('img', { src: '/mark.svg', alt: '', width: 50 }),
                h('h2', null, '今天要办理什么？'), h('p', null, '输入办理要求，或选择已有业务。'), tiles)),
            h('div', { className: 'compose-wrap' }, composer));
        const materials = task ? [task.project, task.control, ...Object.values(task.materials || {}).flat()].filter(Boolean) : picked;
        const deliveries = { export: '成果数据', table: '属性表', workflow: '办理留痕', package: '共享数据包', boundaries: '边界图层' };
        const inspector = h('aside', { className: 'inspector', 'aria-label': '材料与成果' },
            h('div', { className: 'inspector-tabs' }, ...['材料', '成果'].map(name =>
                h('button', { key: name, className: 'inspector-tab' + (tab === name ? ' selected' : ''),
                    'aria-pressed': tab === name, onClick: () => setTab(name) }, name,
                    h('small', null, name === '材料' ? materials.length : (task?.deliveries?.length || 0) + Number(Boolean(task?.hasReport)))))),
            h('div', { className: 'inspector-body' }, tab === '材料'
                ? materials.length ? materials.map((item, index) => h('div', { key: index, className: 'artifact-row' },
                    icon('file'), h('span', null, item.name || item.file?.split(/[\\/]/).at(-1)),
                    item.layer && h('small', null, item.layer))) : h('p', { className: 'muted' }, '暂无材料')
                : h(React.Fragment, null,
                    h(window.GuizhiMap, { key: task?.id || 'empty', map: task?.map, view: state?.mapView, parcel, onParcel: selectParcel,
                        enabled: ready, onState: setState, fail }),
                    (task?.hasReport || task?.deliveries?.length > 0) && h('section', { className: 'artifact-list' },
                        h('h2', null, '成果文件'),
                        task.hasReport && h('div', { className: 'delivery-row' },
                            button('file', '办理报告', { onClick: () => api.report(task.id).catch(fail) }),
                            button('file', '导出 PDF', { onClick: () => api.exportDelivery(task.id, 'report', 'pdf').catch(fail) })),
                        ...(task.deliveries || []).map(kind => {
                            const extension = task.deliveryFormats?.[kind];
                            const name = extension === '.tif' ? 'GeoTIFF' : extension === '.xlsx' ? 'Excel' : null;
                            return h('div', { key: kind, className: 'delivery-row' },
                                button('file', deliveries[kind] || kind, { onClick: () => api.delivery(task.id, kind).catch(fail) }),
                                extension && button('folder', name ? '导出 ' + name : '另存为', {
                                    onClick: () => api.exportDelivery(task.id, kind, 'original').catch(fail) }),
                                extension === '.gpkg' && button('file', '导出 SHP', {
                                    onClick: () => api.exportDelivery(task.id, kind, 'shp').catch(fail) }));
                        })))));
        async function configure(value) {
            const next = await api.configure(value);
            setState(next);
            if (value.workspace !== state.settings.workspace || !ready) select(next.focusTask);
            showSettings(false);
        }
        const layout = launcher ? quick : !state ? null : !ready ? h(window.GuizhiOnboarding, { state, save: configure })
            : settings ? h(window.GuizhiSettings, {
            state, appearance, saveAppearance, entries: coordinates, setEntries: setCoordinates,
            close: () => showSettings(false),
            manage: () => showBusiness('manage'), openArcgis: () => showArcmap(true),
            save: configure,
        }) : h(Workbench, { appearance, saveAppearance, sidebar, conversation, inspector, fail,
            arcgis: () => { if (ready) showArcmap(true); else showSettings(true); },
            connected: Boolean(state?.arcgis?.host) });
        return h(Theme, { appearance }, layout,
            business && h(Business, { catalog, refresh, choose, plans, manage: business === 'manage',
                initialMarkdown, close: () => { setInitialMarkdown(''); showBusiness(''); } }),
            arcmap && state && h(window.GuizhiArcmap, { configured: state.settings.arcmapBridge, sync: state.arcgis, task,
                close: () => showArcmap(false), attach: project => {
                    setDraft(old => old.skill ? { ...old, unassigned: mergeMaterials(old.unassigned, [project]) }
                        : { ...old, materials: { ...old.materials, inputs: mergeMaterials(old.materials.inputs || [], [project]) } });
                    select(null);
                    showSettings(false);
                } }));
    }
    ReactDOM.createRoot(document.getElementById('app')).render(h(App));
})();
