(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const api = window.guizhi;
    const { Theme, Workbench, brand, button, iconButton, icon } = window.GuizhiShell;
    const launcher = new URLSearchParams(location.search).get('surface') === 'launcher';
    const labels = { running: '正在办理', cancelling: '正在停止', succeeded: '已完成',
        failed: '未完成', cancelled: '已停止', timed_out: '已超时', interrupted: '已中断',
        missing_materials: '待补材料', invalid_materials: '材料有误', no_result: '待补信息', answered: '已答复',
        budget_exhausted: '达到限额' };
    const active = task => task && ['running', 'cancelling'].includes(task.status);
    const emptyDraft = () => ({ prompt: '', project: null, control: null, skill: null, materials: {}, parameterText: {},
        unassigned: [], images: [], mentions: {}, freeMode: false });
    const number = value => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const { Material, RoleMaterials, materialReady, roleReady, mergeMaterials, intake } = window.GuizhiMaterials;
    const { render, open } = window.GuizhiMarkdown;
    const errorText = window.GuizhiErrorText;
    const { ImageAttachments, MentionList, AddMenu } = window.GuizhiComposerMedia;
    const { BusinessLog, ReportPanel } = window.GuizhiBusinessLog;
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

    function ResultImage({ reference }) {
        const [url, setUrl] = useState('');
        const [error, setError] = useState('');
        useEffect(() => {
            let mounted = true;
            api.mapImage(reference).then(value => { if (mounted) setUrl(value); })
                .catch(issue => { if (mounted) setError(errorText(issue)); });
            return () => { mounted = false; };
        }, [reference.run, reference.artifact]);
        return url ? h('img', { className: 'reply-image', src: url, alt: '影像预览' })
            : error ? h('p', { role: 'alert' }, error) : null;
    }

    function Result({ task, retry, fail, editSkill, parcel, selectParcel, voiceReply, openComponents }) {
        if (active(task)) return h('article', { className: 'bubble assistant working', role: 'status' },
            task.status === 'cancelling' ? '正在结束任务'
                : task.progress?.length ? h(Progress, { task }) : '等待回复…');
        if (voiceReply?.task === task.id) return h('article', { className: 'bubble assistant' },
            voiceReply.text ? h('p', { className: 'voice-caption' }, voiceReply.text)
                : h('span', { role: 'status' }, h('i', { className: 'pulse' }), '正在准备语音'));
        if (task.status === 'succeeded' || task.status === 'answered') {
            const stats = task.statistics;
            return h('article', { className: 'bubble assistant' },
                h('div', { className: 'result-head' }, h('img', { src: '/mark.svg', alt: '', width: 29 }),
                    h('strong', null, task.status === 'answered' ? '智能体' : '办理结果')),
                markdown(task.response),
                ...(task.outputImages || []).map(reference => h(ResultImage, { key: reference.run, reference })),
                task.progress?.length > 0 && h('details', { className: 'result-progress' },
                    h('summary', null, task.business ? '办理过程' : '执行过程'), h(Progress, { task })),
                stats?.coverage_ratio !== undefined && h('dl', { className: 'metrics' },
                    h('div', null, h('dt', null, '重叠面积'), h('dd', null, number(stats.covered_area_m2), h('small', null, ' m²'))),
                    h('div', null, h('dt', null, '覆盖率'), h('dd', null, (stats.coverage_ratio * 100).toFixed(2), h('small', null, '%')))),
                task.approval && h(Approval, { task, parcel, selectParcel, fail }),
                task.candidate && h('button', { className: 'report', onClick: () => editSkill(task.candidate) }, '编辑技能草稿 ↗'),
                task.hasReport && task.business && h(ReportPanel, { id: task.id }));
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

    function ConversationFeedback({ close }) {
        const [preview, setPreview] = useState(null);
        const [sending, setSending] = useState(false);
        const [receipt, setReceipt] = useState('');
        const [error, setError] = useState('');
        useEffect(() => {
            let mounted = true;
            api.conversationPreview().then(value => { if (mounted) setPreview(value); })
                .catch(issue => { if (mounted) setError(errorText(issue)); });
            return () => { mounted = false; };
        }, []);
        async function send() {
            setError('');
            setSending(true);
            try { setReceipt(await api.conversationUpload(preview.id)); }
            catch (issue) { setError(errorText(issue)); }
            finally { setSending(false); }
        }
        return h(antd.Modal, { open: true, centered: true, width: 640, title: '上传本次会话',
            closable: !sending, keyboard: !sending, maskClosable: false,
            onCancel: close, onOk: receipt ? close : send, confirmLoading: sending,
            okText: receipt ? '完成' : '确认上传', okButtonProps: { disabled: !preview },
            cancelText: '取消', cancelButtonProps: { disabled: sending, ...(receipt && { style: { display: 'none' } }) } },
            h('div', { className: 'conversation-feedback' }, receipt
                ? h('p', { role: 'status' }, '会话已上传。反馈编号 ', h('code', null, receipt))
                : preview ? h(React.Fragment, null,
                    h('p', null, '共 ' + preview.turns + ' 轮，包含提问、回复、办理过程、附件名称和故障编号。'
                        + '内容将发送到项目服务器，保存 ' + preview.retentionDays + ' 天，用于改进软件。'),
                    h('p', { className: 'muted' }, '请确认下方内容适合分享。'),
                    h(antd.Input.TextArea, { rows: 10, readOnly: true, value: preview.text, 'aria-label': '待上传的会话内容' }))
                    : !error && h('p', { role: 'status' }, '正在准备会话预览…'),
            error && h('p', { className: 'error', role: 'alert' }, error)));
    }

    function App() {
        const [state, setState] = useState(null);
        const currentWorkspace = useRef(null);
        currentWorkspace.current = state?.settings.workspace;
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
        const [showLog, setShowLog] = useState(false);
        const [showFeedback, setShowFeedback] = useState(false);
        const [error, setError] = useState('');
        const [sending, setSending] = useState(false);
        const [coordinates, setCoordinates] = useState([]);
        const [catalog, setCatalog] = useState([]);
        const [business, showBusiness] = useState('');
        const [tab, setTab] = useState('成果');
        const [mapMode, setMapMode] = useState('chat');
        const [mapMounted, setMapMounted] = useState(false);
        const mapOpen = mapMode !== 'chat';
        function showMapWorkspace(show) {
            setShowLog(false);
            if (show) setMapMounted(true);
            setMapMode(show ? 'split' : 'chat');
        }
        const [mapEditing, setMapEditing] = useState(false);
        const [mapDisplay, setMapDisplay] = useState(null);
        const [mapBackground, setMapBackground] = useState(null);
        const [mapActions, setMapActions] = useState(null);
        const [layers, setLayers] = useState([]);
        const [hiddenLayers, hideLayers] = useState([]);
        const [activeLayer, setActiveLayer] = useState(null);
        const [mapSelection, setMapSelection] = useState(null);
        const [selectionReceipt, setSelectionReceipt] = useState(null);
        const [references, setReferences] = useState([]);
        const [imageReferences, setImageReferences] = useState([]);
        const [mention, setMention] = useState(null);
        const [mentionIndex, setMentionIndex] = useState(0);
        const composerInput = useRef(null);
        function updateMention(input) {
            const value = input.value.slice(0, input.selectionStart);
            const match = value.match(/(?:^|[\s，。；：])[@＠]([^@＠「」\n]*)$/u);
            setMentionIndex(0);
            setMention(match ? { from: value.length - match[1].length - 1, to: value.length, query: match[1] } : null);
        }
        useEffect(() => {
            if (launcher) return;
            let current = true;
            api.mapCatalog(layers, mapSelection, activeLayer, hiddenLayers).then(async () => {
                if (current && selectionReceipt) {
                    await api.finishMapDelivery(selectionReceipt);
                    if (current) setSelectionReceipt(null);
                }
            }).catch(fail);
            return () => { current = false; };
        }, [layers, mapSelection, activeLayer, hiddenLayers, selectionReceipt]);
        const mapKey = window.GuizhiMap.key;
        function addLayers(items) {
            if (!items.length) return;
            const identity = layer => layer.sourceRevision && layer.ref.artifact === 'import'
                ? JSON.stringify([layer.sourceName.toLowerCase(), layer.sourceLayer || layer.name, layer.sourceRevision]) : null;
            const known = new Map(layers.filter(layer => identity(layer)).map(layer => [identity(layer), layer]));
            const open = items.map(layer => known.get(identity(layer)) || layer);
            setLayers(old => {
                const next = new Map(old.map(layer => [mapKey(layer), layer]));
                const sources = new Set(old.map(identity).filter(Boolean));
                for (const item of open) {
                    const signature = identity(item);
                    if (next.has(mapKey(item)) || signature && sources.has(signature)) continue;
                    next.set(mapKey(item), { ...item, sourceLayer: item.name,
                        ...(item.kind !== 'raster' && !item.style && { style: window.GuizhiMap.initialStyle(next.size) }) });
                    if (signature) sources.add(signature);
                }
                return [...next.values()];
            });
            setActiveLayer(mapKey(open.at(-1)));
            setMapSelection(null);
        }
        useEffect(() => {
            setLayers([]);
            hideLayers([]);
            setActiveLayer(null);
            setMapSelection(null);
            setMapDisplay(null);
            setMapBackground(null);
            setMapActions(null);
            setMapMounted(Boolean(state?.settings.workspace && !state.settings.modelConfig));
            setMapMode(state?.settings.workspace && !state.settings.modelConfig ? 'map' : 'chat');
        }, [state?.settings.workspace]);
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
            api.mapReferences().then(setReferences).catch(fail);
            api.imageReferences().then(setImageReferences).catch(fail);
            const callbacks = [
                api.onMapReferences(setReferences), api.onImageReferences(setImageReferences), api.onState(setState), api.onVoiceCaption(setVoiceReply),
                api.onSettings(section => showSettings(section || true)), api.onAppearance(setAppearance), api.onDraft(receiveDraft),
                api.onLauncherFocus(() => document.querySelector('.launcher textarea')?.focus()),
            ];
            const dismiss = event => {
                if (launcher && event.key === 'Escape' && !event.isComposing && !document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]')) {
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
        const [mapReply, setMapReply] = useState(null);
        const observedTurn = useRef(null);
        useEffect(() => {
            if (!state) return;
            const previous = observedTurn.current;
            const workspace = state.settings.workspace;
            if (previous?.workspace !== workspace) setMapReply(null);
            else if (task?.id && task.id !== previous.id && mapMode === 'map') setMapReply(task.id);
            observedTurn.current = { workspace, id: task?.id };
        }, [state?.settings.workspace, task?.id, mapMode]);
        const taskRunning = ['running', 'cancelling'].includes(task?.status);
        const activity = taskRunning && task.visual && state?.settings.showOperation !== false ? task.visual : null;
        const shownMapRuns = useRef(new Set());
        const delivery = useRef(null);
        const [delivering, setDelivering] = useState(null);
        const [takingOver, setTakingOver] = useState(false);
        const [layoutReady, setLayoutReady] = useState(null);
        const [layoutOpen, setLayoutOpen] = useState(null);
        const hasMapResult = Boolean(task?.map || task?.mapLayers?.length || task?.layout || task?.mapActions || task?.mapSelection);
        const pendingDelivery = !launcher && task?.status === 'succeeded' && hasMapResult && !shownMapRuns.current.has(task.id);
        const locked = taskRunning || sending || Boolean(delivering || pendingDelivery || state?.delivery);
        const cancelling = takingOver || task?.status === 'cancelling' || sending && !taskRunning;
        const ownership = locked ? { cancelling, label: cancelling ? '正在停止小六' :
            delivering || pendingDelivery || state?.delivery ? '正在应用地图成果' : activity?.label || '小六正在处理' } : null;

        function settleDelivery(id, error) {
            if (delivery.current?.id !== id) return;
            delivery.current = null;
            setDelivering(null);
            setLayoutReady(null);
            setMapActions(null);
            if (task?.mapSelection && !error) setSelectionReceipt(id);
            else void api.finishMapDelivery(id, error).catch(fail);
        }
        async function takeOver() {
            if (cancelling || !task) return;
            setTakingOver(true);
            delivery.current?.stop.abort();
            try {
                setState(await api.cancel(task.id));
                shownMapRuns.current.add(task.id);
                settleDelivery(task.id);
            } catch (error) { fail(error); }
            finally { setTakingOver(false); }
        }
        useEffect(() => {
            shownMapRuns.current.clear();
            delivery.current?.stop.abort();
            delivery.current = null;
            setDelivering(null);
            setLayoutReady(null);
            setMapActions(null);
        }, [state?.settings.workspace]);
        useEffect(() => {
            if (launcher || !pendingDelivery || task?.status !== 'succeeded') return;
            shownMapRuns.current.add(task.id);
            const stop = new AbortController();
            delivery.current = { id: task.id, stop };
            setDelivering(task.id);
            void openMap(task, stop.signal).then(opened => {
                if (stop.signal.aborted) return;
                if (!opened) { settleDelivery(task.id, '地图未能应用本轮成果'); return; }
                const layout = task.layout && !task.layoutImage && !task.layoutError;
                if (layout) setLayoutReady({ id: task.id, signal: stop.signal });
                if (task.mapActions) setMapActions({ id: task.id, signal: stop.signal, actions: task.mapActions.map(action => {
                    const layerRef = task.mapTargets?.[action.layer] ?? task.mapLayers?.find(
                        layer => layer.stepReferences?.includes(action.layer))?.ref;
                    return layerRef ? { ...action, layerRef } : action;
                }) });
                else if (!layout) settleDelivery(task.id);
            }).catch(error => { if (!stop.signal.aborted) { fail(error); settleDelivery(task.id, window.GuizhiErrorText(error)); } });
        }, [task?.id, task?.status, pendingDelivery]);
        const resultTask = turns.find(item => item.id === viewed) ||
            turns.findLast(item => item.map || item.hasReport || item.mapLayers?.length) || task;
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
        const suggestion = !skill && !draft.freeMode && !draft.images.length && !references.some(item => draft.prompt.includes(item.name)) ? matchBusiness(draft.prompt, catalog) : { items: [] };
        const canSubmit = materialsReady && !missingComponents.length && parametersReady(fields, draft.parameterText) &&
            (suggestion.items.length < 2 || suggestion.exact);
        async function submit(event) {
            event.preventDefault();
            const command = action(draft.prompt);
            if (command === 'workbench') return api.workbench().catch(fail);
            if (command === 'reset') return resetConversation();
            if (busy || adding || !draft.prompt.trim()) return;
            if (!ready) return api.workbench().catch(fail);
            if (!state.settings.modelConfig) {
                if (launcher) return api.workbench('model').catch(fail);
                showSettings('model');
                return;
            }
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
            const { prompt, project, control, skill, materials, parameterText, mentions } = item;
            const images = (item.images || []).filter(image => imageReferences.some(ref => ref.id === image.id));
            const next = { ...emptyDraft(), prompt, project, control, skill, materials, images, mentions: mentions || {},
                parameterText: parameterText || {} };
            const restored = catalog.find(entry => reference(entry) === skill);
            if (skill && !restored) {
                const current = shared.current;
                api.skillShow(skill).then(skillDefinition => {
                    if (shared.current === current) setDraft({ ...next, skillDefinition });
                }).catch(fail);
            } else setDraft({ ...next, skillDefinition: restored });
        }
        async function resetConversation(next = {}) {
            setMention(null);
            setShowLog(false);
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
        const ready = Boolean(state?.settings.workspace);
        const picked = [draft.project, draft.control, ...draft.unassigned, ...Object.values(draft.materials).flat()].filter(Boolean);
        const materialKey = item => JSON.stringify([item.file, item.layer]);
        const priorMaterials = turns.flatMap(item => [item.project, item.control, ...Object.values(item.materials || {}).flat()])
            .filter(item => item && !item.ref && Array.isArray(item.layers));
        const referenceMaterial = mergeMaterials(priorMaterials, picked).filter(item => !item.ref && item.name);
        const mentionItems = mention ? [
            ...references.filter(layer => !skill || skill.materials.some(role => window.GuizhiMap.layerMatches(role, layer)))
                .map(item => ({ type: 'layer', key: item.id, name: item.name, value: item })),
            ...referenceMaterial.map(item => ({ type: 'material', key: materialKey(item), name: item.name, value: item })),
            ...imageReferences.map(item => ({ type: 'image', key: item.id, name: item.name, thumbnail: item.thumbnail, value: item })),
        ].filter(item => item.name.toLocaleLowerCase().includes(mention.query.trim().toLocaleLowerCase())) : [];

        function withMaterials(current, items) {
            if (current.skill) return { ...current, unassigned: mergeMaterials(current.unassigned, items) };
            const inputs = mergeMaterials(current.materials.inputs || [], items);
            if (inputs.length > 30) throw new Error('一次最多添加 30 份自由材料');
            return { ...current, materials: { ...current.materials, inputs } };
        }
        function withImages(current, items) {
            const images = [...new Map([...current.images, ...items].map(item => [item.id, item])).values()];
            if (images.length > 4) throw new Error('每轮最多添加 4 张图片');
            return { ...current, images };
        }
        function insertMention(item) {
            if (!mention || !item) return;
            const { from, to } = mention;
            const base = item.name.replaceAll('「', '').replaceAll('」', '').trim() || '引用内容';
            const same = ref => ref?.type === item.type && ref.key === item.key;
            let label = base;
            for (let number = 2; Object.hasOwn(draft.mentions, label) && !same(draft.mentions[label]); number++) {
                label = base + '（' + number + '）';
            }
            const token = '@「' + label + '」 ';
            if (draft.prompt.length - (to - from) + token.length > 4000) return fail('办理事项最多填写 4000 个字符');
            try {
                setDraft(current => {
                    let next = current;
                    if (item.type === 'image') next = withImages(current, [item.value]);
                    if (item.type === 'material' && !picked.some(material => materialKey(material) === item.key)) {
                        next = withMaterials(current, [item.value]);
                    }
                    if (item.type === 'layer' && skill && !picked.some(material => material.mapKey === item.key)) {
                        next = withMaterials(current, [window.GuizhiMap.mapLayerMaterial(item.value)]);
                    }
                    return { ...next, prompt: current.prompt.slice(0, from) + token + current.prompt.slice(to),
                        mentions: { ...current.mentions, [label]: { type: item.type, key: item.key } } };
                });
                setError('');
                setMention(null);
                requestAnimationFrame(() => {
                    composerInput.current?.focus();
                    composerInput.current?.setSelectionRange(from + token.length, from + token.length);
                });
            } catch (error) { fail(error); }
        }
        function removeImage(image) {
            setDraft(current => {
                const labels = Object.entries(current.mentions).filter(([, entry]) => entry.type === 'image' && entry.key === image.id);
                let prompt = current.prompt;
                const mentions = { ...current.mentions };
                for (const [label] of labels) {
                    prompt = prompt.replaceAll('@「' + label + '」', '');
                    delete mentions[label];
                }
                return { ...current, prompt, mentions, images: current.images.filter(item => item.id !== image.id) };
            });
        }
        async function addMaterials(source) {
            if (adding) return;
            if (!ready) return api.workbench().catch(fail);
            setAdding(true);
            setError('');
            try { await intake(source(), items => {
                if (shared.current === draft) setDraft(current => withMaterials(current, items));
            }, fail); }
            finally { setAdding(false); }
        }
        async function addImages(job) {
            try {
                const items = await job;
                if (!items.length) return;
                setDraft(current => withImages(current, items));
                setError('');
            } catch (error) { fail(error); }
        }
        async function addFromMenu(key) {
            if (key === 'image') return addImages(api.addImages());
            if (key === 'file') return addMaterials(() => api.material());
            if (key === 'folder') return addMaterials(() => api.material('any', true));
            const [, layer, scope] = key.split(':');
            setAdding(true);
            try {
                const item = await api.arcgisCapture(Number(layer), scope === 'selection');
                if (shared.current === draft) setDraft(current => withMaterials(current, [item]));
            } catch (error) { fail(error); }
            finally { setAdding(false); }
        }
        function paste(event) {
            const pictures = Array.from(event.clipboardData?.files || []).filter(file => file.type.startsWith('image/'));
            if (!pictures.length) return;
            event.preventDefault();
            if (busy) return;
            if (shared.current.images.length + pictures.length > 4) return fail('每轮最多添加 4 张图片');
            if (pictures.some(file => file.size > 12 * 1024 * 1024)) return fail('请粘贴不超过 12 MB 的图片');
            void addImages(Promise.all(pictures.map(file => file.arrayBuffer())).then(api.pasteImages));
        }
        function drop(event) {
            event.preventDefault();
            const files = [...event.dataTransfer.files];
            const pictures = files.filter(file => /\.(png|jpe?g|webp)$/i.test(file.name));
            if (pictures.length) void addImages(api.imageDrop(pictures));
            const materials = files.filter(file => !pictures.includes(file));
            if (!materials.length) return;
            if (!files.length) return;
            if (skill) return fail('请把材料拖到对应的业务角色');
            void addMaterials(() => api.intakeDropped(materials, 'any'));
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
            draft.images.length > 0 && h(ImageAttachments, { images: draft.images, remove: removeImage }),
            h(antd.Popover, { open: Boolean(mention), trigger: [], arrow: false, destroyOnHidden: true,
                placement: launcher ? 'bottomLeft' : 'topLeft', classNames: { root: 'composer-reference-popover' },
                content: h(MentionList, { items: mentionItems, activeIndex: mentionIndex,
                    setIndex: setMentionIndex, choose: insertMention }) },
                h('div', { className: 'composer-text' },
                    h('textarea', { ref: composerInput, 'aria-label': '办理事项', placeholder: launcher
                        ? '说说需要办理什么，也可以把材料拖到这里' : '继续提问或补充办理要求', rows: 2,
                        value: draft.prompt, maxLength: 4000, onPaste: paste,
                        'aria-autocomplete': 'list', 'aria-controls': mention ? 'composer-references' : undefined,
                        'aria-activedescendant': mentionItems.length ? 'composer-reference-' + Math.min(mentionIndex, mentionItems.length - 1) : undefined,
                        onBlur: () => setMention(null), onClick: event => updateMention(event.currentTarget),
                        onChange: event => { update('prompt', event.target.value); updateMention(event.target); },
                        onKeyDown: event => {
                            if (event.nativeEvent.isComposing || event.keyCode === 229) return;
                            if (event.key === 'Escape' && mention) {
                                event.preventDefault(); event.stopPropagation(); setMention(null); return;
                            }
                            if (mention && ['ArrowUp', 'ArrowDown'].includes(event.key) && mentionItems.length) {
                                event.preventDefault();
                                const delta = event.key === 'ArrowDown' ? 1 : -1;
                                setMentionIndex(index => (Math.min(index, mentionItems.length - 1) + mentionItems.length + delta) % mentionItems.length);
                                return;
                            }
                            if (mention && (event.key === 'Enter' || event.key === 'Tab') && !event.shiftKey) {
                                if (event.key === 'Enter' || mentionItems.length) event.preventDefault();
                                if (mentionItems.length) insertMention(mentionItems[Math.min(mentionIndex, mentionItems.length - 1)]);
                                else setMention(null);
                                return;
                            }
                            if (event.key === 'Enter' && !event.shiftKey) {
                                event.preventDefault();
                                event.currentTarget.form.requestSubmit();
                            }
                        } }))),
            suggestion.items.length > 0 && h('div', { className: 'business-suggestion' },
                h('span', null, suggestion.items.length > 1 ? '请选择要办理的业务' : '匹配业务'),
                ...suggestion.items.map(item => button('layers', item.title,
                    { key: reference(item), onClick: () => choose(item, true) })),
                h(antd.Button, { type: 'text', onClick: () => update('freeMode', true) }, '按自由任务')),
            skill && h('div', { className: 'materials skill-materials' },
                h('div', { className: 'section-head' }, h('strong', null, skill.title),
                    h(antd.Button, { type: 'text', onClick: () => setDraft(old => ({ ...emptyDraft(), prompt: old.prompt,
                        freeMode: true, images: old.images, mentions: old.mentions, materials: { inputs: [...old.unassigned, ...Object.values(old.materials).flat()] } })) }, '改为自由任务')),
                freeInputs.length > 0 && h('div', { className: 'unassigned' }, h('small', null, '请为已添加的材料分配角色'),
                    ...freeInputs.map(freeMaterial)),
                missingComponents.length > 0 && h('div', { className: 'section-head' },
                    h('span', null, '需安装：' + missingComponents.map(kind => window.GuizhiComponentNames[kind]).join('、')),
                    button('settings', '打开按需组件', { onClick: openComponents })),
                h(RoleMaterials, { roles: skill.materials, materials: draft.materials,
                    change: value => {
                        if (shared.current.materials === draft.materials) update('materials', value);
                    }, plans, fail, disabled: busy || Boolean(missingComponents.length),
                    picker: window.GuizhiMap.roleLayerPicker(launcher ? references : layers, busy) }),
                Object.keys(fields).length > 0 && h('div', { className: 'setting-fields' }, h(ParameterFields, {
                    fields, values: draft.parameterText, disabled: busy, change: value => update('parameterText', value) }))),
            !skill && picked.length > 0 && h('div', { className: 'materials' },
                ...['project', 'control'].filter(key => draft[key]).map(key => h(Material, {
                    key, item: draft[key], change: value => update(key, value), plans,
                })),
                ...freeInputs.map(freeMaterial)),
            h('div', { className: 'compose-actions' },
                h(AddMenu, { skill, host: state?.arcgis.host, busy: busy || adding, add: addFromMenu }),
                h('span', { className: 'spacer' }),
                iconButton('mic', '语音对话', { onClick: () => {
                    if (!launcher && state?.speech.asr === 'off') showSettings('voice');
                    else void api.portraitVoice().catch(fail);
                } }),
                busy ? h(antd.Button, { onClick: () => api.cancel().then(setState).catch(fail) }, '停止')
                    : iconButton('send', '发送', { type: 'primary', htmlType: 'submit',
                        disabled: adding || !draft.prompt.trim() || (!suggestion.exact && !canSubmit) })));
        const tiles = h('div', { className: 'business-tiles' }, ...catalog.slice(0, 3).map(item =>
            h('button', { key: reference(item), className: 'business-tile', title: item.title, onClick: () => choose(item) },
                h('span', { className: 'tile-icon' }, icon(window.GuizhiShell.businessIcon(item.id))),
                h('b', null, item.title))));
        const quick = h('main', { className: 'launcher' },
            h('header', { className: 'launcher-head' }, brand(), h('div', { className: 'title-group' },
                appearance.shortcutRegistered && h('kbd', null, appearance.shortcut.replaceAll('+', ' + ')),
                button('app', '打开工作台', { type: 'text', onClick: () => api.workbench().catch(fail) }),
                iconButton('close', '收起快捷面板', { onClick: () => api.hideLauncher().catch(fail) }))),
            h('div', { className: 'launcher-body' }, h('div', { className: 'launcher-content', ref: launcherContent }, composer,
                !skill && ready && h('section', { className: 'quick-section' }, h('div', { className: 'section-head' },
                    h('h2', null, '常用业务'), h(antd.Button, { type: 'link', onClick: () => showBusiness('browse') }, '全部业务')), tiles))),
            h('footer', { className: 'launcher-footer' },
                h('span', { className: 'muted' }, ready ? (state.settings.modelConfig?.mode === 'local' ? '本地模型' : state.settings.modelConfig ? '在线模型' : '未配置模型')
                    : '请在工作台完成首次设置'),
                h('span', null, h('kbd', null, 'Enter'), ' 发送　', h('kbd', null, 'Esc'), ' 收起')));
        const sidebar = expanded => {
            const nav = (glyph, label, props) => expanded
                ? button(glyph, label, { type: 'text', title: label, ...props })
                : iconButton(glyph, label, props);
            return h('aside', { className: 'sidebar' + (expanded ? ' expanded' : '') },
            nav('plus', '新的对话', { type: 'primary', disabled: sending || adding || mapEditing, onClick: () => resetConversation() }),
            nav('app', '工作台', { className: mapOpen || showLog ? '' : 'rail-current', 'aria-current': mapOpen || showLog ? undefined : 'page',
                disabled: mapEditing, onClick: () => showMapWorkspace(false) }),
            nav('map', '地图工作区', { disabled: !ready || (mapOpen && !showLog), 'aria-current': mapOpen && !showLog ? 'page' : undefined, onClick: () => openMap() }),
            nav('briefcase', '业务中心', { disabled: !ready || mapEditing, onClick: () => showBusiness('browse') }),
            nav('book', '业务办理日志', { disabled: mapEditing, className: showLog ? 'rail-current' : '',
                'aria-current': showLog ? 'page' : undefined, onClick: () => setShowLog(true) }),
            nav('settings', '设置', { disabled: mapEditing, onClick: () => showSettings(true) }));
        };
        function reply(item) {
            return h(React.Fragment, { key: item.id + '-reply' },
                h(Result, { task: item, retry, fail, voiceReply, openComponents,
                    parcel: resultTask?.id === item.id ? parcel : null, selectParcel: id => { viewResult(item.id); selectParcel(id); },
                    editSkill: text => { setInitialMarkdown(text); showSettings('skills'); } }),
                item.layout && h('div', { key: item.id + '-layout', className: 'map-layout-result' },
                    item.layoutImage ? button('map', '打开图件', { onClick: () => api.openMapLayout(item.id).catch(fail) })
                        : item.layoutReady ? button('layout', '编辑版面', { onClick: () => { setLayoutOpen({ id: item.id }); } })
                            : h('span', { role: 'status' }, item.layoutError || '正在生成版面…')));
        }
        const dockReply = task && h('section', { className: 'map-reply' },
            h('button', { type: 'button', className: 'map-reply-toggle', 'aria-controls': 'map-reply-content',
                'aria-expanded': mapReply === task.id, onClick: () => setMapReply(mapReply === task.id ? null : task.id) },
                icon('chat'), h('span', null, task.prompt), icon('chevron')),
            mapReply === task.id && h('div', { id: 'map-reply-content', className: 'map-reply-content' }, reply(task)));
        const conversation = h('section', { className: 'conversation' + (task ? '' : ' conversation-empty'), 'aria-label': '办理对话' },
            task && h('div', { className: 'task-head' }, h('h1', null, turns[0].title),
                h('div', { className: 'task-head-actions' },
                    h('span', { className: 'status-tag' }, labels[task.status] || '未完成'),
                    button('upload', '上传会话', { type: 'text', size: 'small', disabled: state.busy || sending,
                        onClick: () => setShowFeedback(true) }))),
            h('div', { className: 'messages', ref: messages }, task ? turns.flatMap(item => [
                h('article', { key: item.id + '-question', className: 'bubble user' },
                    item.images?.length > 0 && h(ImageAttachments, { images: item.images }), item.prompt),
                reply(item),
            ]) : h('div', { className: 'welcome' }, h('img', { src: '/mark.svg', alt: '', width: 50 }),
                h('h2', null, '今天要办理什么？'), h('p', null, '输入办理要求，或选择已有业务。'), tiles)),
            h('div', { className: 'compose-wrap' }, composer));
        const materials = task ? [task.project, task.control, ...Object.values(task.materials || {}).flat()].filter(Boolean) : picked;
        const inspector = h('aside', { className: 'inspector', 'aria-label': '材料与成果' },
            h('div', { className: 'inspector-tabs' }, ...['材料', '成果'].map(name =>
                h('button', { key: name, className: 'inspector-tab' + (tab === name ? ' selected' : ''),
                    'aria-pressed': tab === name, onClick: () => setTab(name) }, name,
                    h('small', null, name === '材料' ? materials.length : Number(Boolean(resultTask?.hasReport)) + Number(Boolean(resultTask?.map || resultTask?.mapLayers?.length)))))),
            h('div', { className: 'inspector-body' }, tab === '材料'
                ? materials.length ? materials.map((item, index) => h('div', { key: index, className: 'artifact-row' },
                    icon('file'), h('span', null, item.name || item.file?.split(/[\\/]/).at(-1)),
                    item.layer && h('small', null, item.layer))) : h('p', { className: 'muted' }, '暂无材料')
                : h(React.Fragment, null,
                    h(window.GuizhiMap.Preview, { key: resultTask?.id || 'empty', map: resultTask?.map, selected: parcel,
                        hasLayers: Boolean(resultTask?.mapLayers?.length), open: () => openMap(resultTask), choose: item => selectParcel(item.id) }),
                    resultTask?.hasReport && resultTask.business && h(ReportPanel, { id: resultTask.id }))));
        async function openMap(item, signal) {
            if (signal?.aborted) return false;
            setShowLog(false);
            if (item?.map || item?.mapLayers?.length) {
                try {
                    const groups = { ...item.materials, ...(item.project && { project: [item.project] }),
                        ...(item.control && { control: [item.control] }) };
                    if (!item.skill && groups.inputs) {
                        groups.inputs.forEach((material, index) => { groups['layer-' + (index + 1)] = [material]; });
                    }
                    const replacements = [], additions = [];
                    for (const result of item.mapLayers || []) {
                        const target = groups[result.replacesRole]?.[0]?.ref || item.mapTargets?.[result.replacesRole];
                        if (!target) { additions.push(result); continue; }
                        if (layers.some(layer => mapKey(layer) === mapKey(result))) continue;
                        const before = layers.find(layer => mapKey(layer) === mapKey({ ref: target }));
                        if (!before) throw new Error('原图层已变更或移除，请重新选择要编辑的图层');
                        replacements.push([before, result]);
                    }
                    const inputs = item.map ? await api.mapTaskInputs(item.id) : [];
                    if (signal?.aborted) return false;
                    const current = new Set(layers.map(mapKey));
                    addLayers([...inputs.filter(input => !current.has(mapKey(input))), ...additions]);
                    if (replacements.length) replaceLayers(replacements);
                } catch (error) {
                    if (signal?.aborted) return false;
                    fail(error);
                    if (item.layout) await api.mapLayoutError(item.id, window.GuizhiErrorText(error));
                    return false;
                }
            }
            if (item?.mapSelection) {
                const target = mapKey(item.mapSelection);
                if (!layers.some(layer => mapKey(layer) === target)) throw new Error('目标图层已移除，无法应用选区');
                setActiveLayer(target);
                setMapSelection({ key: target, ids: item.mapSelection.fids });
            }
            if (item?.layout && !item.mapLayers?.length && !layers.length) {
                await api.mapLayoutError(item.id, '地图中没有可绘制的图层，请先添加图层。');
                return false;
            }
            setMapMounted(true);
            setMapMode(mode => mode === 'chat' ? 'split' : mode);
            return true;
        }
        function replaceLayers(pairs) {
            const replacements = new Map(pairs.map(([before, after]) => [mapKey(before),
                { ...after, name: before.name, style: before.style, group: before.group }]));
            setLayers(old => old.map(layer => replacements.get(mapKey(layer)) || layer));
            hideLayers(old => old.map(id => replacements.has(id) ? mapKey(replacements.get(id)) : id));
            setActiveLayer(mapKey(pairs.at(-1)[1]));
            setMapSelection(null);
        }
        const replaceLayer = (before, after) => replaceLayers([[before, after]]);
        const mapWorkspace = mapMounted && h(window.GuizhiMap.WorkspaceMap, { layers, add: addLayers, active: activeLayer,
            moveLayer: (source, target, before) => setLayers(old => {
                const from = old.findIndex(item => mapKey(item) === source);
                const to = old.findIndex(item => mapKey(item) === target);
                if (from < 0 || to < 0 || from === to) return old;
                const insert = to + (before ? 0 : 1) - (from < to ? 1 : 0);
                if (insert === from) return old;
                const copy = old.map((item, i) => item.style || item.kind === 'raster' ? item
                    : { ...item, style: window.GuizhiMap.initialStyle(i) });
                const [item] = copy.splice(from, 1);
                copy.splice(insert, 0, item);
                return copy;
            }),
            remove: id => {
                setLayers(old => old.filter(item => mapKey(item) !== id));
                if (activeLayer === id) { setActiveLayer(null); setMapSelection(null); }
            },
            renameLayer: (id, name) => setLayers(old => old.map(item => mapKey(item) === id ? { ...item, name } : item)),
            hidden: hiddenLayers, toggle: id => hideLayers(old => old.includes(id) ? old.filter(value => value !== id) : [...old, id]),
            selectLayer: id => { setActiveLayer(id); setMapSelection(null); },
            entries: coordinates, catalog, components: state.components, openComponents,
            display: mapDisplay, setDisplay: setMapDisplay, background: mapBackground, setBackground: setMapBackground,
            styleLayer: (id, style) => setLayers(old => old.map(item => mapKey(item) === id ? { ...item, style } : item)),
            importFrame: ({ layers: items, frame }) => {
                setLayers(items);
                setActiveLayer(mapKey(items[0]));
                setMapSelection(null);
                hideLayers(items.filter(item => !item.visible).map(mapKey));
                setMapDisplay(frame.crs ? { wkt: frame.crs, units: frame.units, metresPerUnit: frame.metresPerUnit,
                    label: frame.crsName || frame.name, bounds: frame.bounds } : null);
                setMapBackground(frame.background);
            },
            fail, activity: state.settings.screenHighlight !== false ? activity : null,
            requestedLayout: layoutReady && task?.id === layoutReady.id && !task.layoutImage && !task.layoutError && task.layout
                ? { taskId: task.id, layout: task.layout, signal: layoutReady.signal } : null,
            openLayout: layoutOpen, onLayout: api.mapLayoutError, onLayoutSettled: settleDelivery, mapActions,
            onMapActions: (id, error) => {
                setMapActions(current => current?.id === id ? null : current);
                if (error || layoutReady?.id !== id) settleDelivery(id, error);
            },
            selection: mapSelection, setSelection: setMapSelection,
            replace: replaceLayer, replaceLayers, editing: setMapEditing });
        async function configure(value) {
            const next = await api.configure(value);
            setState(next);
            showSettings(false);
        }
        const layout = launcher ? quick : !state ? null : !ready ? h(window.GuizhiOnboarding, { state, save: configure })
            : settings && !ownership ? h(window.GuizhiSettings, {
            state, appearance, saveAppearance, entries: coordinates, setEntries: setCoordinates,
            initial: settings, catalog, refresh, plans, initialMarkdown,
            close: () => { setInitialMarkdown(''); showSettings(false); },
            save: configure,
        }) : h(Workbench, { appearance, saveAppearance, sidebar, conversation, inspector: showLog ? null : inspector, map: mapWorkspace, composer, dockReply,
            page: showLog ? h(BusinessLog, { revision: state }) : null,
            mode: mapMode, setMode: mode => { if (mode !== 'chat') setMapMounted(true); setMapMode(mode); },
            activity, ownership, onCancel: takeOver, fail,
            arcgis: () => { if (!mapEditing) showSettings('extensions'); },
            connected: Boolean(state?.arcgis?.host) });
        return h(Theme, { appearance }, layout,
            showFeedback && h(ConversationFeedback, { close: () => setShowFeedback(false) }),
            business && h(Business, { catalog, choose, close: () => showBusiness('') }));
    }
    ReactDOM.createRoot(document.getElementById('app')).render(h(App));
})();
