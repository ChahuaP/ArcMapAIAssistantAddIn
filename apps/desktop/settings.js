(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const api = window.guizhi;
    const { Titlebar, button, icon, productName } = window.GuizhiShell;
    const { ModelForm, Workspace, errorText } = window.GuizhiModel;
    const sections = [
        ['model', 'server', '模式与模型'], ['voice', 'mic', '语音与音色'],
        ['crs', 'globe', '坐标系与转换'], ['appearance', 'sun', '外观与交互'],
        ['storage', 'folder', '成果与存储'], ['skills', 'grid', '业务管理'],
        ['extensions', 'link', '第三方扩展'], ['about', 'info', '关于与诊断'],
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
        useEffect(() => { void refresh(); }, []);
        const statuses = { off: '关闭', verified: '可用', failed: '检查失败', ready: '组件就绪',
            configured: '已配置 · 待测试', missing: '未配置' };
        const row = (title, summary, detail, tone) => h('div', { key: title }, h('dt', null, title), h('dd', null,
            h(antd.Tag, { color: tone }, summary), detail && h('small', null, detail)));
        const voiceRow = (kind, title) => {
            const item = report.voice[kind];
            return row(title, statuses[item.state],
                [window.GuizhiSpeechNames[item.provider], item.at && new Date(item.at).toLocaleString()].filter(Boolean).join(' · '),
                item.state === 'verified' || item.state === 'ready' ? 'success' : item.state === 'failed' ? 'error' : 'default');
        };
        const host = state.arcgis.host;
        return [card('产品信息', h('div', { className: 'about-product' },
            h('h2', null, productName),
            h('p', { lang: 'en', className: 'muted' }, 'Smart Governance System for the Liuhe Branch of the Nanjing Municipal Bureau of Planning and Natural Resources'),
            h('p', null, '规智 · ' + state.version), h('p', null, '版权所有 © 于佳民 · 个人自研'))),
        card('诊断报告', button('server', '检查运行状态', { loading: busy, onClick: () => void refresh(true) }),
            report && h('dl', { className: 'diagnostic-list' },
                row('智能体模型', statuses[report.model.state], [
                    report.model.mode === 'local' ? '本地' : report.model.mode === 'online' ? '在线' : '',
                    report.model.name, report.model.detail,
                    report.model.at && new Date(report.model.at).toLocaleString()].filter(Boolean).join(' · '),
                    report.model.state === 'verified' ? 'success' : report.model.state === 'failed' ? 'error' : 'default'),
                voiceRow('asr', '语音识别'), voiceRow('tts', '语音播报'),
                row('ArcMap', host ? '已连接' : state.arcgis.connection ? '等待工程打开' : '未连接',
                    host ? [host.version, host.map?.split(/[\\/]/).at(-1), !host.publish_ready && '需从规智重新打开工程'].filter(Boolean).join(' · ')
                        : report.bridge ? '连接组件就绪' : '未配置连接组件', host ? 'success' : 'default'),
                row('全局快捷键', report.shortcut.registered ? '已注册' : '未注册', report.shortcut.value),
                row('成果位置', state.settings.workspace ? '已配置' : '未配置', state.settings.workspace)),
            error && h('p', { role: 'alert', className: 'error' }, error))];
    }

    window.GuizhiSettings = function Settings({ state, appearance, saveAppearance, save, close, entries, setEntries,
        initial, catalog, refresh, plans, initialMarkdown, task, attach }) {
        const [section, select] = useState(typeof initial === 'string' ? initial : state.settings.workspace ? 'model' : 'storage');
        const [value, change] = useState({ ...state.settings, key: '' });
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
                await save(value);
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
                    value.showOperation !== false && checkbox('screenHighlight', '高亮屏幕中的对应图层')),
            ],
            storage: () => card('成果保存位置', h(Workspace, { value: value.workspace,
                change: workspace => update('workspace', workspace), fail, disabled: saving })),
            skills: () => h(window.GuizhiBusiness.Business, { catalog, refresh, plans, initialMarkdown, manage: true }),
            extensions: () => h(window.GuizhiArcmap, { configured: state.settings.arcmapBridge, sync: state.arcgis, task, attach }),
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
