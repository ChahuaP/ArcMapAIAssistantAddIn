(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const api = window.guizhi;
    const { Titlebar, button, icon, productName } = window.GuizhiShell;
    const { ModelForm, Workspace, errorText } = window.GuizhiModel;
    const sections = [
        ['model', 'server', '模式与模型'], ['voice', 'mic', '语音与音色'],
        ['crs', 'globe', '坐标系与转换'], ['appearance', 'sun', '外观与交互'],
        ['storage', 'folder', '成果与存储'], ['skills', 'briefcase', '业务管理'],
        ['components', 'layers', '按需组件'], ['extensions', 'link', '第三方扩展'], ['about', 'info', '关于与诊断'],
    ];
    const themes = [{ value: 'system', label: '跟随系统' }, { value: 'light', label: '明亮' },
        { value: 'dark', label: '暗色' }];
    const card = (title, ...children) => h('section', { className: 'settings-card' }, h('h2', null, title), ...children);

    function ShortcutCapture({ appearance, save, fail }) {
        const [recording, setRecording] = useState(false);
        const active = useRef(false);
        useEffect(() => {
            const release = () => {
                if (!active.current) return;
                active.current = false;
                setRecording(false);
                void api.shortcutCapture(false).catch(fail);
            };
            addEventListener('blur', release);
            return () => { removeEventListener('blur', release); release(); };
        }, []);
        async function finish(value) {
            if (!active.current) return;
            active.current = false;
            setRecording(false);
            try { if (value) await save({ shortcut: value }); }
            catch (issue) { fail(issue); }
            finally { await api.shortcutCapture(false).catch(fail); }
        }
        async function start() {
            if (active.current) return;
            active.current = true;
            try {
                await api.shortcutCapture(true);
                if (active.current) setRecording(true);
            } catch (issue) { active.current = false; fail(issue); }
        }
        function record(event) {
            if (!active.current || event.key === 'Tab') return;
            event.preventDefault();
            if (event.key === 'Escape') return void finish();
            if (event.repeat || event.nativeEvent.isComposing) return;
            const code = /^(?:Key|Digit)([A-Z0-9])$/.exec(event.code)?.[1] || event.key.toUpperCase();
            if (event.metaKey || (!event.ctrlKey && !event.altKey) || !/^(?:[A-Z0-9]|F(?:[1-9]|1[0-2]))$/.test(code)) return;
            void finish([event.ctrlKey && 'Ctrl', event.altKey && 'Alt', event.shiftKey && 'Shift', code].filter(Boolean).join('+'));
        }
        return h(React.Fragment, null, h('div', { className: 'field-row' },
            h('span', null, '全局快捷键'), h('kbd', null, appearance.shortcut.replaceAll('+', ' + ')),
            h(antd.Button, { className: 'shortcut-capture', 'aria-pressed': recording,
                onClick: start, onBlur: () => void finish(), onKeyDown: record },
                recording ? '请按下组合键' : '更改快捷键')),
            recording && h('small', { role: 'status' }, '按 Ctrl 或 Alt 加字母、数字或功能键。Esc 取消。'),
            !recording && !appearance.shortcutRegistered && h('small', null, '快捷键尚未注册，可选择其他组合。'));
    }

    function SoftwareUpdate({ state, busy }) {
        const [error, setError] = useState('');
        const { phase, target, count, bytes, received, message } = state;
        const size = value => value < 1024 * 1024 ? (value / 1024).toFixed(0) + ' KB' : (value / (1024 * 1024)).toFixed(1) + ' MB';
        const run = action => {
            setError('');
            void action().catch(issue => setError(errorText(issue)));
        };
        const labels = { checking: '正在检查更新…', current: '已是最新版本', complete: '更新已完成',
            offered: '可更新到 ' + target + ' · ' + count + ' 个文件 · 约 ' + size(bytes || 0),
            preparing: '正在准备 ' + target + ' · 已下载 ' + size(received || 0),
            ready: target + ' 已就绪 · 已下载 ' + size(received || 0),
            restarting: '正在重启并验证新版…', failed: message };
        return card('软件更新', h('div', { className: 'setting-row' },
            h('span', { role: 'status' }, error || labels[phase] || '当前版本 ' + state.version),
            h('div', { className: 'business-actions' },
                ['idle', 'current', 'failed', 'checking', 'complete'].includes(phase) &&
                    button('refresh', '检查更新', { loading: phase === 'checking', onClick: () => run(api.updateCheck) }),
                phase === 'offered' && button('download', '下载更新', { type: 'primary', onClick: () => run(api.updatePrepare) }),
                phase === 'preparing' && h(antd.Button, { onClick: () => run(api.updateCancel) }, '取消下载'),
                phase === 'ready' && h(antd.Button, { type: 'primary', disabled: busy, onClick: () => run(api.updateApply) }, '重启更新'))));
    }

    function About({ state }) {
        const [report, setReport] = useState(null);
        const [busy, setBusy] = useState(false);
        const [error, setError] = useState('');
        async function refresh(check = false) {
            setBusy(true);
            setError('');
            try { setReport(await api.diagnostics(check)); }
            catch (issue) { setError(errorText(issue)); }
            finally { setBusy(false); }
        }
        async function toggleUpload(enabled) {
            setBusy(true);
            setError('');
            try {
                const upload = await api.diagnosticsUpload(enabled);
                setReport(current => ({ ...current, upload }));
            } catch (issue) { setError(errorText(issue)); }
            finally { setBusy(false); }
        }
        useEffect(() => { void refresh(); }, []);
        const statuses = { off: '关闭', disabled: '未启用', verified: '可用', failed: '检查失败', ready: '组件就绪',
            configured: '待检查', missing: '未配置', connected: '已连接', waiting: '等待连接',
            unavailable: '扩展未就绪', registered: '已注册', unregistered: '未注册' };
        const tones = { verified: 'success', ready: 'success', connected: 'success', registered: 'success',
            failed: 'error', missing: 'error', unavailable: 'error', unregistered: 'error' };
        const details = (...items) => items.filter(Boolean).join(' · ');
        const row = (title, status, detail) => h('div', { key: title }, h('dt', null, title), h('dd', null,
            h(antd.Tag, { color: tones[status] }, statuses[status]), detail && h('small', null, detail)));
        const voiceRow = (kind, title) => {
            const item = report.voice[kind];
            return row(title, item.state, item.state !== 'off' &&
                details(window.GuizhiSpeechNames[item.provider], item.at && new Date(item.at).toLocaleString()));
        };
        const host = state.arcgis.host;
        const arcgis = state.settings.carrier === 'none' ? 'disabled' : host ? 'connected' :
            report?.bridge && state.arcgis.installed ? 'waiting' : 'unavailable';
        return [card('产品信息', h('div', { className: 'about-product' },
            h('p', { className: 'about-name' }, productName),
            h('p', { lang: 'en', className: 'muted' }, 'Xiaoliu - Smart Business Governance System for the Liuhe Branch of the Nanjing Municipal Bureau of Planning and Natural Resources'),
            h('p', null, '数字智能体 · 小六　　版本 ' + state.version), h('p', null, '版权所有 © 于佳民 · 个人自研'))),
        state.update?.available && h(SoftwareUpdate, { state: state.update, busy: state.busy }),
        card('诊断报告', button('server', '检查运行状态', { loading: busy, onClick: () => void refresh(true) }),
            report && h('dl', { className: 'diagnostic-list' },
                row('智能体模型', report.model.state, details(
                    report.model.mode === 'local' ? '本地' : report.model.mode === 'online' ? '在线' : '',
                    report.model.name, report.model.detail && errorText(report.model.detail),
                    report.model.at && new Date(report.model.at).toLocaleString())),
                voiceRow('asr', '语音识别'), voiceRow('tts', '语音回答'),
                row('ArcGIS', arcgis, host ? 'ArcGIS Desktop ' + host.version : ''),
                row('全局快捷键', report.shortcut.registered ? 'registered' : 'unregistered', report.shortcut.value),
                row('成果位置', report.workspace.state, details(report.workspace.path, report.workspace.detail))),
            error && h('p', { role: 'alert', className: 'error' }, error)),
        report?.upload && card('故障上报', h('div', { className: 'setting-row' },
            h('span', null, '自动上报故障摘要'), h(antd.Switch, { checked: report.upload.enabled, disabled: busy,
                'aria-label': '自动上报故障摘要', onChange: enabled => void toggleUpload(enabled) })),
        h('small', { className: 'muted' }, '摘要包含版本、故障类型和编号。材料、对话和密钥保留在本机。'),
        report.upload.latest && h('p', { className: 'muted' },
            { pending: '待上传', sent: '已上传', local_error: '无法保存上报记录' }[report.upload.latest.state],
            ' · ', report.upload.latest.id))];
    }

    window.GuizhiSettings = function Settings({ state, appearance, saveAppearance, save, close, entries, setEntries,
        initial, catalog, refresh, plans, initialMarkdown }) {
        const [section, select] = useState(typeof initial === 'string' ? initial : state.settings.workspace ? 'model' : 'storage');
        useEffect(() => { if (typeof initial === 'string') select(initial); }, [initial]);
        const [value, change] = useState({ ...state.settings, modelConfig: state.settings.modelConfig || window.GuizhiModel.initial(), key: '' });
        const [error, setError] = useState('');
        const [saving, setSaving] = useState(false);
        const [testingModel, setTestingModel] = useState(false);
        const fail = issue => setError(errorText(issue));
        const update = (name, next) => change(old => ({ ...old, [name]: next }));
        const checkbox = (key, text) => h('label', { className: 'voice-toggle' },
            h('input', { type: 'checkbox', checked: value[key] !== false,
                onChange: event => update(key, event.target.checked) }), text);
        async function submit(event) {
            event.preventDefault();
            if (testingModel || saving) return;
            setError('');
            setSaving(true);
            try {
                await save({ ...value, modelConfig: value.modelConfig.mode ? value.modelConfig : undefined });
                update('key', '');
                close();
            } catch (issue) { fail(issue); }
            finally { setSaving(false); }
        }

        const pages = {
            model: () => h(ModelForm, { value, change, current: state.settings, busy: setTestingModel }),
            voice: () => h(window.GuizhiSpeechSettings, { current: state.speech }),
            crs: () => state.settings.workspace
                ? h(window.GuizhiCoordinates, { entries, setEntries }) : h('p', null, '请先选择成果保存位置。'),
            appearance: () => [
                card('主题', h(antd.Segmented, { options: themes, value: appearance.theme,
                    'aria-label': '界面主题', onChange: theme => saveAppearance({ theme }).catch(fail) })),
                card('快捷面板', h(ShortcutCapture, { appearance, save: saveAppearance, fail })),
                card('办理过程', checkbox('showOperation', '显示办理过程'),
                    value.showOperation !== false && checkbox('screenHighlight', '高亮工作区中的工具和图层')),
            ],
            storage: () => card('成果保存位置', h(Workspace, { value: value.workspace,
                change: workspace => update('workspace', workspace), fail, disabled: saving }),
                h('label', { className: 'setting-field business-operator' }, h('span', null, '姓名'),
                    h(antd.Input, { value: value.operator || '', maxLength: 60, 'aria-label': '姓名',
                        onChange: event => update('operator', event.target.value) }))),
            skills: () => h(window.GuizhiBusiness.Business, { catalog, refresh, plans, initialMarkdown, manage: true }),
            components: () => {
                const available = state.components || {};
                return Object.entries(window.GuizhiComponentNames).filter(([id]) => available[id]).map(([id, label]) =>
                    h('section', { className: 'settings-card', key: id },
                        h('div', { className: 'section-head' }, h('h2', null, label),
                            h(antd.Tag, { color: available[id].installed ? 'success' : undefined },
                                available[id].installed ? '已安装' : '未安装')),
                        id === 'imagery' && !available.raster?.installed
                            ? h('p', { className: 'muted' }, '请先安装栅格处理组件。')
                            : h(window.GuizhiComponentActions, { kind: id, component: available[id] })));
            },
            extensions: () => h(window.GuizhiArcgisExtension, { sync: state.arcgis }),
            about: () => h(About, { state }),
        };
        const current = sections.find(([id]) => id === section);
        return h('div', { className: 'desktop-frame' }, h(Titlebar, { settings: true, back: close }),
            h('div', { className: 'settings-layout' },
                h('nav', { className: 'settings-nav', 'aria-label': '设置分类' },
                    ...sections.map(([id, glyph, label]) => h('button', { key: id, type: 'button',
                        disabled: testingModel || saving,
                        className: 'nav-item' + (section === id ? ' selected' : ''),
                        'aria-current': section === id ? 'page' : undefined,
                        onClick: () => { select(id); setError(''); } }, icon(glyph), label))),
                h('form', { className: 'settings-page', onSubmit: submit },
                    h('div', { className: 'settings-scroll', key: section },
                        h('div', { className: 'settings-content' }, h('h1', null, current[2]), ...[pages[section]()].flat())),
                    (section === 'model' || section === 'storage' || section === 'appearance' || error) &&
                    h('footer', { className: 'settings-footer' }, h('div', { className: 'settings-content setting-row' },
                        error ? h('p', { role: 'alert', className: 'error' }, error) : h('span'),
                        h(antd.Button, { type: 'primary', htmlType: 'submit', loading: saving,
                            disabled: !value.workspace.trim() || testingModel }, '保存设置'))))));
    };
})();
