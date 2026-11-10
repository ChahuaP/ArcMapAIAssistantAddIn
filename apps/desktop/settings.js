(() => {
    const h = React.createElement;
    const { useState } = React;
    const api = window.guizhi;
    const { Titlebar, button, icon } = window.GuizhiShell;
    const sections = [
        ['model', 'server', '模式与模型'], ['voice', 'mic', '语音与音色'],
        ['crs', 'globe', '坐标系与转换'], ['appearance', 'sun', '外观与交互'],
        ['storage', 'folder', '成果与存储'], ['skills', 'grid', '业务与技能'],
        ['arcgis', 'map', 'ArcGIS 扩展'], ['about', 'info', '关于与诊断'],
    ];
    const themes = [{ value: 'system', label: '跟随系统' }, { value: 'light', label: '明亮' },
        { value: 'dark', label: '深绿暗色' }];
    const card = (title, ...children) => h('section', { className: 'settings-card' }, h('h2', null, title), ...children);
    const field = (label, input) => h('label', null, label, input);

    window.GuizhiSettings = function Settings({ state, appearance, saveAppearance, save, close, entries, setEntries,
        manage, openArcgis }) {
        const [section, select] = useState(state.settings.workspace ? 'model' : 'storage');
        const [value, change] = useState({ ...state.settings, key: '' });
        const [error, setError] = useState('');
        const [saving, setSaving] = useState(false);
        const [shortcut, setShortcut] = useState(appearance.shortcut);
        const fail = issue => setError(String(issue.message || issue).replace(/^Error invoking remote method '[^']+': Error: /, ''));
        const update = (name, next) => change(old => ({ ...old, [name]: next }));
        const input = (key, props = {}) => h('input', { ...props, value: value[key] || '',
            onChange: event => update(key, event.target.value) });
        const checkbox = (key, text) => h('label', { className: 'voice-toggle' },
            h('input', { type: 'checkbox', checked: value[key] !== false,
                onChange: event => update(key, event.target.checked) }), text);
        async function submit(event) {
            event.preventDefault();
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
            model: () => card('在线服务', h('div', { className: 'setting-fields' },
                field('模型名称', input('model', { required: true })),
                field('API 密钥', input('key', { type: 'password', autoComplete: 'off',
                    placeholder: state.settings.keyConfigured ? '已配置 · 可输入新密钥' : '输入 API 密钥' }))),
                h('p', { className: 'muted' }, '服务商 MiniMax')),
            voice: () => card('语音设备与服务', h(window.GuizhiSpeechSettings, { current: state.speech })),
            crs: () => card('坐标系与转换', state.settings.workspace
                ? h(window.GuizhiCoordinates, { entries, setEntries }) : h('p', null, '请先选择成果保存位置。')),
            appearance: () => [
                card('主题', h(antd.Segmented, { options: themes, value: appearance.theme,
                    'aria-label': '界面主题', onChange: theme => saveAppearance({ theme }).catch(fail) })),
                card('快捷面板', h('div', { className: 'field-row' },
                    h('label', { htmlFor: 'launcher-shortcut' }, '全局快捷键'),
                    h('input', { id: 'launcher-shortcut', value: shortcut, maxLength: 24,
                        onChange: event => setShortcut(event.target.value) }),
                    h(antd.Button, { onClick: () => {
                        setError('');
                        saveAppearance({ shortcut }).catch(fail);
                    } }, '更改快捷键')),
                    !appearance.shortcutRegistered && h('p', { className: 'muted' },
                        appearance.shortcut + ' 已被占用。可更改组合，或从托盘和数字形象打开。')),
                card('办理过程', checkbox('showOperation', '显示办理过程'),
                    value.showOperation !== false && checkbox('screenHighlight', '高亮屏幕中的对应图层')),
            ],
            storage: () => card('成果保存位置', h('div', { className: 'field-row' },
                input('workspace', { readOnly: true, 'aria-label': '成果保存位置', placeholder: '选择保存目录' }),
                button('folder', '选择目录', { onClick: () => api.workspace().then(folder => {
                    if (folder) update('workspace', folder);
                }).catch(fail) }))),
            skills: () => card('业务管理', button('grid', '管理业务技能',
                { onClick: manage, disabled: !state.settings.workspace })),
            arcgis: () => card('ArcGIS 扩展', h('p', { className: 'muted' },
                state.settings.arcmapBridge ? '已配置 ArcMap Bridge' : '配置 ArcMap Bridge 后可使用工程与图层。'),
                button('map', '打开 ArcGIS 扩展', { onClick: openArcgis, disabled: !state.settings.workspace })),
            about: () => card('规智', h('p', { className: 'muted' }, state.version)),
        };
        const current = sections.find(([id]) => id === section);
        return h('div', { className: 'desktop-frame' }, h(Titlebar, { settings: true, back: close }),
            h('div', { className: 'settings-layout' },
                h('nav', { className: 'settings-nav', 'aria-label': '设置分类' },
                    ...sections.map(([id, glyph, label]) => h('button', { key: id, type: 'button',
                        className: 'nav-item' + (section === id ? ' selected' : ''),
                        'aria-current': section === id ? 'page' : undefined,
                        onClick: () => { select(id); setError(''); } }, icon(glyph), label))),
                h('form', { className: 'settings-page', onSubmit: submit },
                    h('div', { className: 'settings-scroll', key: section },
                        h('div', { className: 'settings-content' }, h('h1', null, current[2]), ...[pages[section]()].flat())),
                    h('footer', { className: 'settings-footer' }, h('div', { className: 'settings-content setting-row' },
                        error ? h('p', { role: 'alert', className: 'error' }, error) : h('span', { className: 'muted' }, '配置适用于新发起的任务'),
                        h(antd.Button, { type: 'primary', htmlType: 'submit', loading: saving,
                            disabled: !value.workspace }, '保存设置'))))));
    };
})();
