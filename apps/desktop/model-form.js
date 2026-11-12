(() => {
    const h = React.createElement;
    const { useState } = React;
    const api = window.guizhi;
    const { button } = window.GuizhiShell;
    const localAddress = 'http://127.0.0.1:11434/v1';
    const routes = [['custom', '自定义服务'], ['minimax-cn', 'MiniMax'], ['deepseek', 'DeepSeek'],
        ['openai', 'OpenAI'], ['anthropic', 'Anthropic']];
    const protocols = [['openai-completions', 'OpenAI Chat Completions'], ['anthropic-messages', 'Anthropic Messages']];
    const field = (label, control) => h('label', null, label, control);
    const errorText = window.GuizhiErrorText;
    const scope = config => JSON.stringify([config?.mode, config?.provider, ...(config?.provider === 'custom'
        ? [config.api, config.baseUrl?.trim().replace(/\/+$/, '')] : [])]);
    const initial = () => ({ mode: 'online', provider: 'custom', api: 'openai-completions', baseUrl: '', model: '' });

    function Workspace({ value, change, fail, disabled }) {
        return h('div', { className: 'field-row' },
            h('input', { 'aria-label': '成果保存位置', value, disabled, placeholder: '选择或输入本地目录',
                onChange: event => change(event.target.value) }),
            button('folder', '选择目录', { disabled, onClick: () => api.workspace().then(folder => {
                if (folder) change(folder);
            }).catch(fail) }));
    }

    function ModelForm({ value, change, current, verified = () => {}, busy = () => {} }) {
        const config = value.modelConfig;
        const [status, setStatus] = useState('');
        const [error, setError] = useState('');
        const [loading, setLoading] = useState('');
        const [listed, setListed] = useState([]);
        const local = config.mode === 'local';
        const custom = config.provider === 'custom';
        function update(patch, key = value.key) {
            const next = { ...config, ...patch };
            const changedRoute = scope(config) !== scope(next);
            change({ ...value, modelConfig: next, key: changedRoute ? '' : key });
            if (changedRoute) setListed([]);
            setStatus('');
            setError('');
            verified(false);
        }
        async function run(kind) {
            setStatus(kind === 'test' ? '正在检查模型服务…' : '');
            setError('');
            setLoading(kind);
            busy(true);
            if (kind === 'test') verified(false);
            try {
                if (kind === 'list') {
                    const models = await api.modelList(value);
                    setListed(models);
                    setStatus(models.length ? '请选择模型，也可手动输入名称' : '服务未列出模型，请手动输入名称');
                } else {
                    const result = await api.modelTest(value);
                    setStatus(result.check === 'catalog' ? '连接正常，已找到所选模型' : '连接正常，模型已响应');
                    verified(true);
                }
            } catch (error) { setStatus(''); setError(errorText(error)); }
            finally { setLoading(''); busy(false); }
        }
        const input = (name, props = {}) => h('input', { ...props, disabled: Boolean(loading),
            value: config[name] || '', onChange: event => update({ [name]: event.target.value }) });
        const select = (name, options, onChange) => h('select', { disabled: Boolean(loading), value: config[name],
            onChange: event => onChange(event.target.value) },
            ...options.map(([id, label]) => h('option', { key: id, value: id }, label)));
        const modeButton = (mode, icon, title, detail) => h('button', { type: 'button',
            className: 'mode-choice' + (config.mode === mode ? ' selected' : ''), 'aria-pressed': config.mode === mode,
            disabled: Boolean(loading), onClick: () => {
                if (config.mode === mode) return;
                update({ ...initial(), mode, baseUrl: mode === 'local' ? localAddress : '' });
            } }, h('span', { className: 'tile-icon' }, window.GuizhiShell.icon(icon)),
            h('span', null, h('b', null, title), h('small', null, detail)));
        return h(React.Fragment, null,
            h('section', { className: 'settings-card' }, h('h2', null, '运行模式'),
                h('div', { className: 'mode-choices' },
                    modeButton('online', 'globe', '在线服务', '使用已授权的在线模型或单位服务'),
                    modeButton('local', 'server', '本地服务', '连接本机 Ollama 或兼容服务'))),
            h('section', { className: 'settings-card' }, h('h2', null, '服务配置'),
                h('div', { className: 'setting-fields' },
                    !local && field('服务商', select('provider', routes, provider =>
                        update({ provider, api: protocols[0][0], baseUrl: '',
                            model: provider === 'minimax-cn' ? 'MiniMax-M3' : '' }))),
                    custom && field('接口协议', select('api', protocols, next => update({ api: next }))),
                    custom && field('服务地址', input('baseUrl', { placeholder: local ? localAddress : 'https://model.example.org/v1' })),
                    field('模型名称', input('model', { placeholder: '填写服务返回的模型标识' })),
                    field('API 密钥', h('input', { type: 'password', autoComplete: 'off', disabled: Boolean(loading),
                        value: value.key || '', placeholder: current.keyConfigured && scope(current.modelConfig) === scope(config)
                            ? '已配置 · 可输入新密钥' : local ? '本机服务可留空' : '输入此服务的 API 密钥',
                        onChange: event => update({}, event.target.value) }))),
                listed.length > 0 && field('可用模型', h('select', { disabled: Boolean(loading), value: listed.includes(config.model) ? config.model : '',
                    onChange: event => update({ model: event.target.value }) }, h('option', { value: '' }, '选择模型'),
                    ...listed.map(name => h('option', { key: name, value: name }, name)))),
                h('div', { className: 'setting-row service-actions' },
                    h('span', { className: error ? 'error' : 'muted', role: error ? 'alert' : 'status' }, error || status),
                    h('div', { className: 'business-actions' },
                        custom && config.api === 'openai-completions' && button('layers', '读取模型列表',
                            { loading: loading === 'list', disabled: Boolean(loading), onClick: () => run('list') }),
                        button('server', '测试连接', { loading: loading === 'test', disabled: Boolean(loading), onClick: () => run('test') })))));
    }

    window.GuizhiModel = { ModelForm, Workspace, initial, errorText };
})();
