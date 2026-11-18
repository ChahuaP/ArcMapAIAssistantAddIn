const presets = Object.freeze({
    'minimax-cn': { api: 'anthropic-messages', baseUrl: 'https://api.minimaxi.com/anthropic' },
    deepseek: { api: 'openai-completions', baseUrl: 'https://api.deepseek.com' },
    openai: { api: 'openai-responses', baseUrl: 'https://api.openai.com/v1' },
    anthropic: { api: 'anthropic-messages', baseUrl: 'https://api.anthropic.com' },
});
const protocols = ['openai-completions', 'openai-responses', 'anthropic-messages'];
const loopback = hostname => ['127.0.0.1', 'localhost', '[::1]'].includes(hostname);

function endpoint(value, catalog = false) {
    let url;
    try { url = new URL(value?.trim()); }
    catch { throw new Error(catalog ? '请输入完整的模型查询地址' : '请输入完整的服务地址'); }
    if ((url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback(url.hostname))) ||
            url.username || url.password || (!catalog && url.search) || url.hash) {
        throw new Error(catalog ? '模型查询地址须使用 HTTPS 或本机 HTTP，且不含账号或片段'
            : '服务地址须使用 HTTPS 或本机 HTTP，且不含账号、查询参数或片段');
    }
    return catalog ? url.href : url.href.replace(/\/+$/, '');
}

const validModel = value => typeof value === 'string' && value.length > 0 && value.length <= 160 && !/[\x00-\x20]/.test(value);

function modelConfig(input, requireModel = true) {
    const { mode, provider } = input || {};
    const model = typeof input?.model === 'string' ? input.model.trim() : '';
    if ((requireModel || model) && !validModel(model)) {
        throw new Error('请输入有效的模型名称');
    }
    if (mode === 'online' && Object.hasOwn(presets, provider)) return { mode, provider, model };
    if ((mode !== 'online' && mode !== 'local') || provider !== 'custom' || !protocols.includes(input.api)) {
        throw new Error('请选择模型模式、服务商和接口协议');
    }
    const baseUrl = endpoint(input.baseUrl);
    const modelsUrl = typeof input.modelsUrl === 'string' && input.modelsUrl.trim() ? endpoint(input.modelsUrl, true) : undefined;
    return { mode, provider, api: input.api, baseUrl, model, ...(modelsUrl ? { modelsUrl } : {}) };
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
    // The adapter prefers the registered model's capacity. Unknown models need a conservative pressure threshold.
    const settings = { apiKeyEnv: 'GUIZHI_MODEL_KEY', defaultContextWindow: config.mode === 'local' ? 8192 : 32768,
        models: [{ id: config.model, input: ['text', 'image'] }] };
    if (config.provider === 'custom') {
        Object.assign(settings, { api: config.api, baseURL: config.baseUrl,
            defaultMaxTokens: 2048 });
        if (config.api === 'openai-completions') settings.compat = { supportsDeveloperRole: false };
    } else {
        const preset = presets[id];
        Object.assign(settings, { api: preset.api, baseURL: preset.baseUrl });
    }
    return { id, providers: { [id]: settings } };
}

function modelFailureCode(reason = '') {
    if (/initialize timed out.*sdk-minimal/i.test(reason)) return 'agent_startup_timeout';
    if (/\b(401|403)\b|unauthorized|authentication_error|invalid api.?key/i.test(reason)) {
        return 'model_auth';
    }
    if (/\b429\b|rate.?limit|quota|insufficient balance/i.test(reason)) {
        return 'model_limit';
    }
    if (/\b404\b|unknown model|model.+(?:not found|not available|not supported)/i.test(reason)) {
        return 'model_missing';
    }
    if (/connection|ECONN|ENOTFOUND|ETIMEDOUT|fetch|network|timeout/i.test(reason)) {
        return 'model_network';
    }
    return '';
}

function modelFailure(reason) {
    return {
        agent_startup_timeout: '智能体本地服务启动超时，请稍后重试。若仍未启动，请重启工作台。',
        model_auth: '模型服务认证失败，请检查 API 密钥和模型权限。',
        model_limit: '模型服务限流或额度不足，请稍后重试或检查服务额度。',
        model_missing: '模型服务未找到所选模型，请在设置中重新查询并选择。',
        model_network: '无法连接模型服务或响应超时，请检查服务地址和网络。',
    }[modelFailureCode(reason)] || '';
}

module.exports = { presets, endpoint, validModel, modelConfig, modelKey, credentialScope, normalizeKey, route, modelFailure, modelFailureCode };
