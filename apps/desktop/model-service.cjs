const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { presets, route, validModel, modelFailure } = require('../../src/guizhi/infrastructure/dsh/model-config.cjs');

const { builtinVision, supportsVision, probe, observed } = require('./model-vision.cjs');

const connectionTimeout = 12_000;
const timeoutError = () => new Error('12 秒内未收到模型服务响应，请检查服务和网络后重试');

async function readModels(config, key, signal, allowUnsupported = false) {
    const { api, baseUrl } = presets[config.provider] || config;
    const anthropic = api === 'anthropic-messages';
    const address = new URL(config.modelsUrl || baseUrl + (anthropic ? '/v1/models' : '/models'));
    if (config.provider === 'anthropic') address.searchParams.set('limit', '1000');
    const sameOrigin = address.origin === new URL(baseUrl).origin;
    const headers = anthropic ? { 'anthropic-version': '2023-06-01' } : {};
    if (key && sameOrigin) {
        if (anthropic) headers['x-api-key'] = key;
        else headers.authorization = 'Bearer ' + key;
    }
    let response;
    try {
        response = await fetch(address, { redirect: 'error', headers, signal });
    } catch { throw signal.aborted ? timeoutError() : new Error('无法连接模型服务，请检查地址和网络'); }
    if ([401, 403, 429].includes(response.status)) throw new Error(modelFailure(String(response.status)));
    if (allowUnsupported && [404, 405, 501].includes(response.status)) return null;
    if (!response.ok) throw new Error(`模型列表读取失败（HTTP ${response.status}），请检查查询地址或稍后重试。`);
    let result;
    try { result = await response.json(); }
    catch { throw signal.aborted ? timeoutError() : new Error('模型查询地址没有返回有效的 JSON 列表'); }
    const entries = result?.data ?? result?.models ?? result;
    if (!Array.isArray(entries)) throw new Error('模型列表格式不受支持');
    const unique = new Map();
    for (const entry of entries) {
        const id = typeof entry === 'string' ? entry : entry?.id ?? entry?.model ?? entry?.name;
        if (validModel(id)) unique.set(id, supportsVision(entry));
    }
    return unique;
}

async function listModels(runtime, config, key) {
    const signal = AbortSignal.timeout(connectionTimeout);
    const entries = await readModels(config, key, signal, config.provider === 'custom');
    if (entries === null) return { supported: false, models: [], unverified: [] };
    const { api } = presets[config.provider] || config;
    const known = builtinVision(runtime, config.provider, api);
    if (config.mode === 'local' && config.provider === 'custom' && !config.modelsUrl) {
        const local = new URL(config.baseUrl);
        if (/^(127\.0\.0\.1|localhost|\[::1\])$/.test(local.hostname)) {
            await Promise.all([...entries].slice(0, 32).map(async ([name]) => {
                try {
                    const response = await fetch(new URL('/api/show', local), { method: 'POST', redirect: 'error',
                        headers: { 'content-type': 'application/json' }, body: JSON.stringify({ model: name }), signal });
                    if (response.ok) {
                        const detail = await response.json();
                        if (supportsVision(detail)) entries.set(name, true);
                        else if (Array.isArray(detail.capabilities)) entries.delete(name);
                    }
                } catch { /* Generic local providers may only expose the compatible API. */ }
            }));
        }
    }
    const models = [...entries].filter(([id, vision]) => vision || known.has(id)).map(([id]) => id);
    return { supported: true, models, unverified: config.provider === 'custom'
        ? [...entries.keys()].filter(id => !models.includes(id)) : [] };
}

function responseError(reason = '') {
    return new Error(modelFailure(reason) || '模型服务未能响应，请检查地址、模型名称和网络');
}

async function checkModelConnection(runtime, config, key) {
    const deadline = AbortSignal.timeout(config.mode === 'local' ? 60_000 : 20_000);
    // Use the same bundled provider catalog and protocol adapters as the harness.
    const pi = path.join(runtime.runtime, 'node_modules', '@earendil-works', 'pi-ai', 'dist');
    const load = file => import(pathToFileURL(path.join(pi, file)).href);
    const { id, providers } = route(config);
    const settings = providers[id];
    const model = { id: config.model, name: config.model, provider: id, api: settings.api,
        baseUrl: settings.baseURL, input: ['text', 'image'], reasoning: false, compat: settings.compat,
        contextWindow: settings.defaultContextWindow ?? 1_000_000, maxTokens: settings.defaultMaxTokens ?? 128_000,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
    const stop = new AbortController();
    const signal = AbortSignal.any([deadline, stop.signal]);
    const challenge = probe();
    const context = { messages: [{ role: 'user', content: challenge.content, timestamp: Date.now() }] };
    const options = { apiKey: key || 'local', maxTokens: 128, maxRetries: 0, signal,
        fetch: (url, init) => fetch(url, { ...init, redirect: 'error' }) };
    try {
        let stream;
        if (config.provider === 'custom') {
            const { openAICompletionsApi, openAIResponsesApi, anthropicMessagesApi } = await load(`api/${config.api}.lazy.js`);
            const client = (openAICompletionsApi || openAIResponsesApi || anthropicMessagesApi)();
            signal.throwIfAborted();
            stream = client.streamSimple(model, context, options);
        } else {
            const { builtinModels } = await load('providers/all.js');
            const models = builtinModels();
            const known = models.getModel(id, config.model);
            signal.throwIfAborted();
            stream = models.streamSimple(known || model, context, options);
        }
        let answer = '';
        for await (const event of stream) {
            if (event.type === 'error') {
                const reason = event.error.errorMessage;
                if (/image|vision|multimodal|media.type/i.test(reason)) throw new Error('此模型或接口未能接收图片');
                throw responseError(reason);
            }
            if (event.type === 'text_delta') answer += event.delta;
        }
        if (observed(answer) !== challenge.expected) throw new Error('模型未能正确识别测试图片，请选择支持读图的模型');
        return { model: config.model, check: 'vision' };
    } catch (error) {
        if (deadline.aborted) throw new Error('读图测试超时，请检查模型服务后重试');
        throw error;
    } finally {
        stop.abort();
    }
}

async function testModel(controller, modelConfig, apiKey, signal) {
    const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'guizhi-model-'));
    let usedTool = false;
    try {
        const receipt = await controller.dispatch({ workspace, modelConfig, apiKey,
            prompt: '这是连接测试。请调用 list_skills 工具，随后回答已读取业务目录。不要执行任何办理工作流。',
            timeoutMs: 90000, maxModelCalls: 4, maxModelTokens: 32000 },
        { signal, onEvent: event => { if (event.tool === 'mcp__guizhi__list_skills') usedTool = true; } });
        if (usedTool && receipt.status === 'answered') return { model: modelConfig.model, tools: true };
        const reason = receipt.error || receipt.response || '';
        if (/\b(401|403)\b|unauthorized|invalid api key/i.test(reason)) {
            throw new Error('模型服务认证失败，请检查 API 密钥');
        }
        if (/\b404\b|unknown model|model.+not found/i.test(reason)) throw new Error('请检查服务地址和模型名称');
        if (/connection|ECONN|fetch|network/i.test(reason) || receipt.status === 'timed_out') {
            throw new Error('无法连接模型服务或响应超时，请检查地址和网络');
        }
        throw new Error('模型未完成工具调用，请检查所选模型是否支持工具');
    } finally {
        fs.rmSync(workspace, { recursive: true, force: true });
    }
}

module.exports = { listModels, checkModelConnection, testModel };
