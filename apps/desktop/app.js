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
    const { render, open } = window.GuizhiMarkdown;
    const errorText = window.GuizhiErrorText;
    const { deliverables } = window.GuizhiSkillEditor;
    const markdown = text => h('div', { className: 'answer-markdown', onClick: open,
        dangerouslySetInnerHTML: { __html: render(text) } });

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

    function Progress({ task }) {
        if (!task.progress?.length) return null;
        return h(antd.Steps, { className: 'task-progress', size: 'small', direction: 'vertical',
            current: task.progress.length - Number(active(task) && task.status !== 'cancelling'),
            status: !active(task) && !['answered', 'succeeded'].includes(task.status) ? 'error' : 'process',
            items: task.progress.map(item => ({ title: item.label, description: item.target || undefined })) });
    }

    function Result({ task, retry, fail, editSkill, parcel, selectParcel, showMap, voiceReply, openComponents }) {
        if (active(task)) return h('article', { className: 'bubble assistant working', role: 'status' },
            task.status === 'cancelling' ? '正在结束任务'
                : task.progress?.length ? h(Progress, { task }) : '正在准备本轮任务');
        if (voiceReply?.task === task.id) return h('article', { className: 'bubble assistant' },
            voiceReply.text ? h('p', { className: 'voice-caption' }, voiceReply.text)
                : h('span', { role: 'status' }, h('i', { className: 'pulse' }), '规智正在准备语音'));
        if (task.status === 'succeeded' || task.status === 'answered') {
            const stats = task.statistics;
            return h('article', { className: 'bubble assistant' },
                h('div', { className: 'result-head' }, h('img', { src: '/mark.svg', alt: '', width: 29 }),
                    h('strong', null, task.status === 'answered' ? '规智' : '办理结果')),
                markdown(task.response),
                task.progress?.length > 0 && h('details', { className: 'result-progress' },
                    h('summary', null, '办理过程'), h(Progress, { task })),
                stats?.coverage_ratio !== undefined && h('dl', { className: 'metrics' },
                    h('div', null, h('dt', null, '重叠面积'), h('dd', null, number(stats.covered_area_m2), h('small', null, ' m²'))),
                    h('div', null, h('dt', null, '覆盖率'), h('dd', null, (stats.coverage_ratio * 100).toFixed(2), h('small', null, '%')))),
                task.approval && h(Approval, { task, parcel, selectParcel, fail }),
                h('div', { className: 'delivery-actions' },
                    showMap && h('button', { className: 'report', onClick: showMap }, '查看成果地图'),
                    task.candidate && h('button', { className: 'report', onClick: () => editSkill(task.candidate) }, '编辑技能草稿 ↗'),
                    task.hasReport && h('button', { className: 'report', onClick: () => api.report(task.id).catch(fail) }, '查看报告 ↗'),
                    ...(task.deliveries || []).map(kind => h('button', { key: kind, className: 'report',
                        onClick: () => api.delivery(task.id, kind).catch(fail) },
                        (deliverables[kind] || '业务成果') + ' ↗'))));
        }
        const detail = { missing_materials: '请按业务要求补齐材料。',
            invalid_materials: '请确认材料、图层和办理参数。', no_result: '请补充办理要求或从业务能力中选择。',
            timed_out: '办理超时。请检查模型服务或输入文件是否正被占用。' }[task.status];
        return h('article', { className: 'bubble assistant' }, h('strong', null, labels[task.status] || '未完成'),
            markdown(task.response ? errorText(task.response) : detail || '请检查材料、坐标和模型设置后继续。'),
            h(window.GuizhiComponentLink, { message: task.response, open: openComponents }),
            task.diagnosticId && h('p', { className: 'muted' }, '故障编号 ', h('code', null, task.diagnosticId)),
            h('button', { className: 'report', onClick: () => retry(task) }, '继续处理'));
    }

    function App() {
        const [state, setState] = useState(null);
        const [voiceReply, setVoiceReply] = useState(null);
        const [draft, renderDraft] = useState(emptyDraft);
        const shared = useRef(draft);
        const [appearance, setAppearance] = useState({
            theme: 'system', dark: matchMedia('(prefers-color-scheme: dark)').matches, panels: [56, 362],
            shortcut: 'Ctrl+G',
        });
        const [viewed, viewResult] = useState(null);
        const [parcel, selectParcel] = useState(null);
        const [settings, showSettings] = useState(false);
        const [error, setError] = useState('');
        const [sending, setSending] = useState(false);
        const [coordinates, setCoordinates] = useState([]);
        const [catalog, setCatalog] = useState([]);
        const [business, showBusiness] = useState('');
        const [tab, setTab] = useState('成果');
        const [initialMarkdown, setInitialMarkdown] = useState('');
        const [adding, setAdding] = useState(false);
        const { Business } = window.GuizhiBusiness;
        const { action, reference, matchBusiness } = window.GuizhiTaskCommands;
        const { ParameterFields, parametersReady, parameterValues } = window.GuizhiSkillEditor;
        const fail = error => setError(errorText(error));
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
                setVoiceReply(value.voiceReply);
                setAppearance(value.appearance);
            }).catch(fail);
            api.readDraft().then(receiveDraft).catch(fail);
            const callbacks = [
                api.onState(setState), api.onVoiceCaption(setVoiceReply),
                api.onSettings(section => showSettings(section || true)), api.onAppearance(setAppearance), api.onDraft(receiveDraft),
                api.onLauncherFocus(() => document.querySelector('.launcher textarea')?.focus()),
            ];
            const dismiss = event => {
                if (launcher && event.key === 'Escape' && !event.isComposing && !document.querySelector('dialog[open]')) {
                    void api.hideLauncher().catch(fail);
                }
            };
            addEventListener('keydown', dismiss);
            return () => { callbacks.forEach(unsubscribe => unsubscribe()); removeEventListener('keydown', dismiss); };
        }, []);
        const messages = useRef(null);
        const launcherContent = useRef(null);
        useEffect(() => {
            if (state?.settings.workspace) {
                api.coordinates().then(setCoordinates).catch(fail);
                refresh().catch(fail);
            }
        }, [state?.settings.workspace]);
        const plans = coordinates.filter(entry => entry.kind === 'plan');
        const turns = (state?.tasks || []).slice().reverse();
        const task = turns.at(-1);
        const resultTask = turns.find(item => item.id === viewed) ||
            turns.findLast(item => item.map || item.hasReport || item.deliveries?.length) || task;
        useEffect(() => { viewResult(null); selectParcel(null); }, [task?.id]);
        useEffect(() => {
            if (messages.current) messages.current.scrollTop = messages.current.scrollHeight;
        }, [task?.id, task?.status]);
        useEffect(() => {
            const panel = messages.current;
            if (voiceReply && panel && panel.scrollHeight - panel.scrollTop - panel.clientHeight < 64) {
                panel.scrollTop = panel.scrollHeight;
            }
        }, [voiceReply]);
        const busy = state?.busy || sending;
        const skill = draft.skillDefinition;
        useEffect(() => {
            if (!launcher) return;
            if (!skill) { void api.fitLauncher(0).catch(fail); return; }
            const node = launcherContent.current;
            node.parentElement.scrollTop = 0;
            const resize = new ResizeObserver(() => {
                const chrome = document.querySelector('.launcher-head').offsetHeight +
                    document.querySelector('.launcher-footer').offsetHeight;
                void api.fitLauncher(Math.ceil(node.getBoundingClientRect().height) + chrome).catch(fail);
            });
            resize.observe(node);
            return () => resize.disconnect();
        }, [skill?.id]);
        const freeInputs = skill ? draft.unassigned : draft.materials.inputs || [];
        const materialsReady = skill ? draft.unassigned.length === 0 && skill.materials.every(role => roleReady(role, draft.materials[role.id]))
            : [draft.project, draft.control, ...freeInputs].filter(Boolean).every(materialReady);
        const fields = skill?.parameters || {};
        const missingComponents = (skill?.components || []).filter(kind => state?.components?.[kind]?.installed === false);
        const openComponents = () => launcher ? api.workbench('components').catch(fail) : showSettings('components');
        const suggestion = !skill && !draft.freeMode ? matchBusiness(draft.prompt, catalog) : { items: [] };
        const canSubmit = materialsReady && !missingComponents.length && parametersReady(fields, draft.parameterText) &&
            (suggestion.items.length < 2 || suggestion.exact);
        async function submit(event) {
            event.preventDefault();
            const command = action(draft.prompt);
            if (command === 'workbench') return api.workbench().catch(fail);
            if (command === 'reset') return resetConversation();
            if (busy || adding || !draft.prompt.trim()) return;
            if (!ready) return api.workbench().catch(fail);
            if (suggestion.exact && !picked.length) return choose(suggestion.items[0], true);
            if (!canSubmit) return;
            setError('');
            setSending(true);
            try {
                const next = await api.submit({ ...draft,
                    skillParameters: skill ? parameterValues(fields, draft.parameterText) : undefined });
                setState(next);
                if (launcher) await api.workbench();
            } catch (error) { fail(error); } finally { setSending(false); }
        }
        function retry(item) {
            const { prompt, project, control, skill, materials, parameterText } = item;
            const next = { ...emptyDraft(), prompt, project, control, skill, materials, parameterText: parameterText || {} };
            const restored = catalog.find(entry => reference(entry) === skill);
            if (skill && !restored) {
                const current = shared.current;
                api.skillShow(skill).then(skillDefinition => {
                    if (shared.current === current) setDraft({ ...next, skillDefinition });
                }).catch(fail);
            } else setDraft({ ...next, skillDefinition: restored });
        }
        async function resetConversation(next = {}) {
            setError('');
            setSending(true);
            try {
                setState(await api.resetConversation(next));
                showBusiness('');
            } catch (error) { fail(error); }
            finally { setSending(false); }
        }
        function choose(item, preservePrompt = false) {
            const next = { ...emptyDraft(), skill: reference(item), skillDefinition: item,
                prompt: preservePrompt ? draft.prompt : '请办理' + item.title };
            if (preservePrompt) next.unassigned = [...draft.unassigned, ...Object.values(draft.materials).flat(),
                ...[draft.project, draft.control].filter(Boolean)];
            return resetConversation(next);
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
        function attachArcgis(item) {
            if (shared.current !== draft) return;
            if (draft.skill) setDraft(old => ({ ...old, unassigned: mergeMaterials(old.unassigned, [item]) }));
            else appendMaterials([item]);
        }
        async function addMaterials(source) {
            if (adding) return;
            if (!ready) return api.workbench().catch(fail);
            setAdding(true);
            setError('');
            try { await intake(source(), items => {
                if (shared.current === draft) appendMaterials(items);
            }, fail); }
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
        const composer = h('form', { className: 'composer' + (skill ? ' composer-skill' : ''), onSubmit: submit,
            onDragOver: event => event.preventDefault(), onDrop: drop },
            error && h('p', { role: 'alert', className: 'error' }, error),
            h(window.GuizhiComponentLink, { message: error, open: openComponents }),
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
                missingComponents.length > 0 && h('div', { className: 'section-head' },
                    h('span', null, '需安装：' + missingComponents.map(kind => window.GuizhiComponentNames[kind]).join('、')),
                    button('settings', '打开按需组件', { onClick: openComponents })),
                h(RoleMaterials, { roles: skill.materials, materials: draft.materials,
                    change: value => {
                        if (shared.current.materials === draft.materials) update('materials', value);
                    }, plans, fail, disabled: busy || Boolean(missingComponents.length) }),
                Object.keys(fields).length > 0 && h('div', { className: 'setting-fields' }, h(ParameterFields, {
                    fields, values: draft.parameterText, disabled: busy, change: value => update('parameterText', value) }))),
            !skill && picked.length > 0 && h('div', { className: 'materials' },
                ...['project', 'control'].filter(key => draft[key]).map(key => h(Material, {
                    key, item: draft[key], change: value => update(key, value), plans,
                })),
                ...freeInputs.map(freeMaterial)),
            h('div', { className: 'compose-actions' },
                !skill && button('paperclip', adding ? '正在读取本地材料…' : '添加材料', { type: 'text', loading: adding, onClick: () => addMaterials(() => api.material()) }),
                !skill && iconButton('folder', '添加材料目录', { disabled: adding, onClick: () => addMaterials(() => api.material('any', true)) }),
                h(window.GuizhiArcgisMaterials, { host: state?.arcgis.host, disabled: adding || busy, attach: attachArcgis, fail }),
                h('span', { className: 'spacer' }),
                iconButton('mic', '语音对话', { type: 'default', onClick: () => {
                    if (!launcher && state?.speech.asr === 'off') showSettings('voice');
                    else void api.portraitVoice().catch(fail);
                } }),
                busy ? h(antd.Button, { onClick: () => api.cancel().then(setState).catch(fail) }, '停止')
                    : button('send', '发送', { type: 'primary', htmlType: 'submit',
                        disabled: adding || !draft.prompt.trim() || (!suggestion.exact && !canSubmit) })));
        const tiles = h('div', { className: 'business-tiles' }, ...catalog.slice(0, 3).map(item =>
            h('button', { key: reference(item), className: 'business-tile', title: item.title, onClick: () => choose(item) },
                h('span', { className: 'tile-icon' }, icon(['layers', 'globe', 'folder'][catalog.indexOf(item)])),
                h('b', null, item.title))));
        const quick = h('main', { className: 'launcher' },
            h('header', { className: 'launcher-head' }, brand(true), h('div', { className: 'title-group' },
                appearance.shortcutRegistered && h('kbd', null, appearance.shortcut.replaceAll('+', ' + ')),
                button('app', '打开工作台', { type: 'text', onClick: () => api.workbench().catch(fail) }),
                iconButton('close', '收起快捷面板', { onClick: () => api.hideLauncher().catch(fail) }))),
            h('div', { className: 'launcher-body' }, h('div', { className: 'launcher-content', ref: launcherContent }, composer,
                !skill && ready && h('section', { className: 'quick-section' }, h('div', { className: 'section-head' },
                    h('h2', null, '常用业务'), h(antd.Button, { type: 'link', onClick: () => showBusiness('browse') }, '全部业务')), tiles))),
            h('footer', { className: 'launcher-footer' },
                h('span', { className: 'muted' }, ready ? (state.settings.modelConfig.mode === 'local' ? '本地服务' : '在线服务')
                    : '请在工作台完成首次设置'),
                h('span', null, h('kbd', null, 'Enter'), ' 发送　', h('kbd', null, 'Esc'), ' 收起')));
        const sidebar = h('aside', { className: 'sidebar' },
            iconButton('plus', '新的对话', { type: 'primary', disabled: sending || adding, onClick: () => resetConversation() }),
            h('span', { className: 'rail-current', title: '工作台', 'aria-label': '工作台', 'aria-current': 'page' }, icon('app')),
            iconButton('grid', '业务中心', { disabled: !ready, onClick: () => showBusiness('browse') }),
            iconButton('settings', '设置', { onClick: () => showSettings(true) }));
        const conversation = h('section', { className: 'conversation' + (task ? '' : ' conversation-empty'), 'aria-label': '办理对话' },
            task && h('div', { className: 'task-head' }, h('h1', null, turns[0].title),
                h('span', { className: 'status-tag' }, labels[task.status] || '未完成')),
            h('div', { className: 'messages', ref: messages }, task ? turns.flatMap(item => [
                h('article', { key: item.id + '-question', className: 'bubble user' }, item.prompt),
                h(Result, { key: item.id + '-answer', task: item, retry, fail, voiceReply, openComponents,
                    parcel: resultTask?.id === item.id ? parcel : null, selectParcel: id => { viewResult(item.id); selectParcel(id); },
                    showMap: item.map && item.id !== resultTask?.id ? () => { viewResult(item.id); selectParcel(null); setTab('成果'); } : undefined,
                    editSkill: text => { setInitialMarkdown(text); showSettings('skills'); } }),
            ]) : h('div', { className: 'welcome' }, h('img', { src: '/mark.svg', alt: '', width: 50 }),
                h('h2', null, '今天要办理什么？'), h('p', null, '输入办理要求，或选择已有业务。'), tiles)),
            h('div', { className: 'compose-wrap' }, composer));
        const materials = task ? [task.project, task.control, ...Object.values(task.materials || {}).flat()].filter(Boolean) : picked;
        const inspector = h('aside', { className: 'inspector', 'aria-label': '材料与成果' },
            h('div', { className: 'inspector-tabs' }, ...['材料', '成果'].map(name =>
                h('button', { key: name, className: 'inspector-tab' + (tab === name ? ' selected' : ''),
                    'aria-pressed': tab === name, onClick: () => setTab(name) }, name,
                    h('small', null, name === '材料' ? materials.length : (resultTask?.deliveries?.length || 0) + Number(Boolean(resultTask?.hasReport)))))),
            h('div', { className: 'inspector-body' }, tab === '材料'
                ? materials.length ? materials.map((item, index) => h('div', { key: index, className: 'artifact-row' },
                    icon('file'), h('span', null, item.name || item.file?.split(/[\\/]/).at(-1)),
                    item.layer && h('small', null, item.layer))) : h('p', { className: 'muted' }, '暂无材料')
                : h(React.Fragment, null,
                    h(window.GuizhiMap, { key: resultTask?.id || 'empty', map: resultTask?.map, selected: parcel,
                        choose: item => selectParcel(item.id) }),
                    (resultTask?.hasReport || resultTask?.deliveries?.length > 0) && h('section', { className: 'artifact-list' },
                        h('h2', null, '成果文件'),
                        resultTask.hasReport && h('div', { className: 'delivery-row' },
                            button('file', '办理报告', { type: 'text', onClick: () => api.report(resultTask.id).catch(fail) }),
                            button('file', '导出 PDF', { onClick: () => api.exportDelivery(resultTask.id, 'report', 'pdf').catch(fail) })),
                        ...(resultTask.deliveries || []).map(kind => {
                            const extension = resultTask.deliveryFormats?.[kind];
                            const name = extension === '.tif' ? 'GeoTIFF' : extension === '.xlsx' ? 'Excel' : null;
                            const title = deliverables[kind] || '业务成果';
                            return h('div', { key: kind, className: 'delivery-row' },
                                button('file', title, { type: 'text', onClick: () => api.delivery(resultTask.id, kind).catch(fail) }),
                                extension && button('folder', name ? '导出 ' + name : '另存为', {
                                    'aria-label': name ? '导出 ' + name + ' · ' + title : '另存为 · ' + title,
                                    onClick: () => api.exportDelivery(resultTask.id, kind, 'original').catch(fail) }),
                                extension === '.gpkg' && button('file', '导出 SHP', {
                                    onClick: () => api.exportDelivery(resultTask.id, kind, 'shp').catch(fail) }));
                        })))));
        async function configure(value) {
            const next = await api.configure(value);
            setState(next);
            showSettings(false);
        }
        const layout = launcher ? quick : !state ? null : !ready ? h(window.GuizhiOnboarding, { state, save: configure })
            : settings ? h(window.GuizhiSettings, {
            state, appearance, saveAppearance, entries: coordinates, setEntries: setCoordinates,
            initial: settings, catalog, refresh, plans, initialMarkdown,
            close: () => { setInitialMarkdown(''); showSettings(false); },
            save: configure,
        }) : h(Workbench, { appearance, saveAppearance, sidebar, conversation, inspector, fail,
            arcgis: () => showSettings('extensions'),
            connected: Boolean(state?.arcgis?.host) });
        return h(Theme, { appearance }, layout,
            business && h(Business, { catalog, choose, close: () => showBusiness('') }));
    }
    ReactDOM.createRoot(document.getElementById('app')).render(h(App));
})();
