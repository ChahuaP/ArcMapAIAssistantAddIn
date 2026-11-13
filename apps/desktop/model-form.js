(() => {
    const h = React.createElement;
    const { useState } = React;
    const api = window.guizhi;
    const { button } = window.GuizhiShell;
    const localAddress = 'http://127.0.0.1:11434/v1';
    const routes = [['custom', '自定义服务'], ['minimax-cn', 'MiniMax'], ['deepseek', 'DeepSeek'],
        ['openai', 'OpenAI'], ['anthropic', 'Anthropic']];
    const protocols = [['openai-completions', 'OpenAI Chat Completions'], ['openai-responses', 'OpenAI Responses'],
        ['anthropic-messages', 'Anthropic Messages']];
    const help = {
        '运行模式': '在线服务由服务商运行。本地服务连接这台电脑上的程序，例如 Ollama。',
        '服务商': '选择密钥所属的平台，例如 MiniMax。单位提供兼容接口时请选择自定义服务。',
        '接口协议': '按服务商给出的接口类型选择。例如接口末尾为 /responses 时选 OpenAI Responses。',
        '服务地址': '填写服务商给的基础地址，例如 https://ai.example.gov.cn/v1。',
        '模型查询地址': '一般留空。服务商另有列表地址时填写，例如 https://ai.example.gov.cn/v1/models。',
        'API 密钥': '粘贴服务商后台生成的密钥，例如 sk-…。无需加引号或 Bearer。',
        '模型名称': '从查询结果选择模型，例如 MiniMax-M3.1-Flash。',
    };
    const hint = label => h(antd.Popover, { content: h('span', { className: 'model-help' }, help[label]),
        trigger: 'click', placement: 'topLeft' }, h(antd.Button, { size: 'small', type: 'text',
        'aria-label': label + '说明' }, '?'));
    const field = (label, control) => h('div', { className: 'model-field' },
        h('div', { className: 'model-field-label' }, label, hint(label)), React.cloneElement(control, { 'aria-label': label }));
    const errorText = window.GuizhiErrorText;
    const scope = config => JSON.stringify([config?.mode, config?.provider, ...(config?.provider === 'custom'
        ? [config.api, config.baseUrl?.trim().replace(/\/+$/, '')] : [])]);
    const initial = () => ({ mode: '', provider: '', api: 'openai-completions', baseUrl: '', modelsUrl: '', model: '' });

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
        const [manual, setManual] = useState(false);
        const local = config.mode === 'local';
        const custom = config.provider === 'custom';
        const hasSavedKey = current.keyConfigured && scope(current.modelConfig) === scope(config);
        const anonymous = local && /^https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\])(?=[:/]|$)/i.test(config.baseUrl);
        const canQuery = Boolean(config.provider && (!custom || config.baseUrl?.trim()) &&
            (anonymous || value.key?.trim() || hasSavedKey));
        function update(patch, key = value.key) {
            const next = { ...config, ...patch };
            const changedRoute = scope(config) !== scope(next);
            if (changedRoute || next.modelsUrl !== config.modelsUrl || key !== value.key) {
                next.model = '';
                setListed([]);
                setManual(false);
            }
            change({ ...value, modelConfig: next, key: changedRoute ? '' : key });
            setStatus('');
            setError('');
            verified(false);
        }
        async function run(kind) {
            setStatus(kind === 'test' ? '正在检查模型服务…' : '正在查询模型…');
            setError('');
            setLoading(kind);
            busy(true);
            if (kind === 'test') verified(false);
            try {
                if (kind === 'list') {
                    const { models, supported } = await api.modelList(value);
                    setListed(models);
                    setManual(!supported);
                    if (supported && config.model && !models.includes(config.model)) update({ model: '' });
                    setStatus(!supported ? '此服务不提供模型列表，请填写服务商给出的模型名称。'
                        : models.length ? `已查询到 ${models.length} 个模型，请选择`
                            : '没有查到可选模型，请检查账户权限或本机已安装模型。');
                } else {
                    const result = await api.modelTest(value);
                    setStatus(result.check === 'catalog' ? '连接正常，已找到所选模型' : '连接正常，模型已响应');
                    verified(true);
                }
            } catch (error) {
                if (kind === 'list') setListed([]);
                setStatus('');
                setError(errorText(error));
            }
            finally { setLoading(''); busy(false); }
        }
        const input = (name, props = {}) => h('input', { ...props, disabled: Boolean(loading),
            value: config[name] || '', onChange: event => update({ [name]: event.target.value }) });
        const select = (name, options, onChange) => h('select', { disabled: Boolean(loading), value: config[name],
            onChange: event => onChange(event.target.value) },
            ...options.map(([id, label]) => h('option', { key: id, value: id }, label)));
        const modelOptions = [...new Set([...listed, config.model].filter(Boolean))];
        const modeButton = (mode, icon, title, detail) => h('button', { type: 'button',
            className: 'mode-choice' + (config.mode === mode ? ' selected' : ''), 'aria-pressed': config.mode === mode,
            disabled: Boolean(loading), onClick: () => {
                if (config.mode === mode) return;
                update({ ...initial(), mode, provider: mode === 'local' ? 'custom' : '', baseUrl: mode === 'local' ? localAddress : '' });
            } }, h('span', { className: 'tile-icon' }, window.GuizhiShell.icon(icon)),
            h('span', null, h('b', null, title), h('small', null, detail)));
        return h(React.Fragment, null,
            h('section', { className: 'settings-card' },
                h('h2', { className: 'model-field-label' }, '运行模式', hint('运行模式')),
                h('div', { className: 'mode-choices' },
                    modeButton('online', 'globe', '在线服务', '连接服务商或单位模型'),
                    modeButton('local', 'server', '本地服务', '连接本机 Ollama 或兼容服务'))),
            config.mode && h('section', { className: 'settings-card' }, h('h2', null, '连接配置'),
                h('div', { className: 'setting-fields' },
                    !local && field('服务商', select('provider', [['', '请选择服务商'], ...routes], provider =>
                        update({ provider, api: protocols[0][0], baseUrl: '', modelsUrl: '', model: '' }))),
                    custom && field('接口协议', select('api', protocols, next => update({ api: next }))),
                    custom && field('服务地址', input('baseUrl', { placeholder: local ? localAddress : 'https://model.example.org/v1' })),
                    config.provider && field('API 密钥', h('input', { type: 'password', autoComplete: 'off', disabled: Boolean(loading),
                        value: value.key || '', placeholder: hasSavedKey ? '已配置' : anonymous ? '本机服务可留空' : '输入此服务的 API 密钥',
                        onChange: event => update({}, event.target.value) })),
                    custom && field('模型查询地址', input('modelsUrl', { placeholder: '选填，默认使用服务的列表接口' })))),
            config.provider && h('section', { className: 'settings-card' }, h('h2', null, '选择模型'),
                h('div', { className: 'setting-fields' },
                    field('模型名称', manual ? input('model', { placeholder: '例如 my-model' })
                        : select('model', [['', listed.length ? '请选择模型' : '请先读取模型'],
                            ...modelOptions.map(name => [name, name])], model => update({ model })))),
                h('div', { className: 'setting-row service-actions' },
                    h('span', { className: error ? 'error' : 'muted', role: error ? 'alert' : 'status' }, error || status),
                    h('div', { className: 'business-actions' },
                        button('layers', '读取模型',
                            { loading: loading === 'list', disabled: Boolean(loading) || !canQuery, onClick: () => run('list') }),
                        button('server', '测试连接', { loading: loading === 'test',
                            disabled: Boolean(loading) || !canQuery || !config.model?.trim(), onClick: () => run('test') })))));

    }

    window.GuizhiModel = { ModelForm, Workspace, initial, errorText };
})();
