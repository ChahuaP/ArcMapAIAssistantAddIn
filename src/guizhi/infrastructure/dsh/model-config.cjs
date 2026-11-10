const presets = Object.freeze({
    'minimax-cn': 'MiniMax',
    deepseek: 'DeepSeek',
    openai: 'OpenAI',
    anthropic: 'Anthropic',
});
const protocols = ['openai-completions', 'anthropic-messages'];
const loopback = hostname => ['127.0.0.1', 'localhost', '[::1]'].includes(hostname);

function endpoint(value) {
    let url;
    try { url = new URL(value?.trim()); }
    catch { throw new Error('请输入完整的服务地址'); }
    if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback(url.hostname))) ||
            url.username || url.password || url.search || url.hash) {
        throw new Error('服务地址须使用 HTTPS 或本机 HTTP，且不含账号、查询参数或片段');
    }
    return url.href.replace(/\/+$/, '');
}

function modelConfig(input, requireModel = true) {
    const { mode, provider } = input || {};
    const model = typeof input?.model === 'string' ? input.model.trim() : '';
    if ((requireModel && !model) || model.length > 160 || /[\x00-\x20]/.test(model)) {
        throw new Error('请输入有效的模型名称');
    }
    if (mode === 'online' && Object.hasOwn(presets, provider)) return { mode, provider, model };
    if ((mode !== 'online' && mode !== 'local') || provider !== 'custom' || !protocols.includes(input.api)) {
        throw new Error('请选择模型模式、服务商和接口协议');
    }
    const baseUrl = endpoint(input.baseUrl);
    return { mode, provider, api: input.api, baseUrl, model };
}

function credentialScope(config) {
    return JSON.stringify([config.mode, config.provider, config.api, config.baseUrl]);
}

function normalizeKey(value) {
    if (value == null || value === '') return '';
    if (typeof value !== 'string' || value.length > 2048 || /[\r\n\0]/.test(value)) {
        throw new Error('API 密钥格式无效');
    }
    return value.trim();
}

function modelKey(config, value) {
    const key = normalizeKey(value);
    if (!key && !(config.mode === 'local' && loopback(new URL(config.baseUrl).hostname))) {
        throw new Error('请配置此模型服务的 API 密钥');
    }
    return key;
}

function route(config) {
    const id = config.provider !== 'custom' ? config.provider : 'guizhi-' + config.mode;
    const settings = { apiKeyEnv: 'GUIZHI_MODEL_KEY' };
    if (config.provider === 'custom') {
        Object.assign(settings, { api: config.api, baseURL: config.baseUrl, models: [{ id: config.model }],
            defaultContextWindow: config.mode === 'local' ? 8192 : 32768, defaultMaxTokens: 2048 });
        if (config.api === 'openai-completions') settings.compat = { supportsDeveloperRole: false };
    }
    return { id, providers: { [id]: settings } };
}

module.exports = { presets, endpoint, modelConfig, modelKey, credentialScope, normalizeKey, route };
