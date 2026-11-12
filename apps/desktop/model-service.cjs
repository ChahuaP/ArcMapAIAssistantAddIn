const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { startAgent } = require('./agent-process.cjs');
const { presets, route, validModel, modelFailure } = require('../../src/guizhi/infrastructure/dsh/model-config.cjs');

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
    return [...new Set(entries.map(entry => typeof entry === 'string' ? entry : entry?.id ?? entry?.model ?? entry?.name)
        .filter(validModel))];
}

async function listModels(config, key) {
    const ids = await readModels(config, key, AbortSignal.timeout(connectionTimeout));
    // OpenAI's list also includes voice, embedding, image and moderation models.
    return config.provider === 'openai' ? ids.filter(id => /^(?:gpt-|chatgpt-|o\d|ft:)/i.test(id) &&
        !/(?:audio|realtime|transcrib|tts|image|moderation|embedding|search)/i.test(id)) : ids;
}

function responseError(reason = '') {
    return new Error(modelFailure(reason) || '模型服务未能响应，请检查地址、模型名称和网络');
}

async function checkModelConnection(runtime, config, key) {
    const deadline = AbortSignal.timeout(connectionTimeout);
    if (config.mode === 'local' && config.api === 'openai-completions') {
        const ids = await readModels({ ...config, modelsUrl: undefined }, key, deadline, true);
        if (ids) {
            if (!ids.includes(config.model)) throw new Error('模型服务已连接，但未找到所选模型');
            return { model: config.model, check: 'catalog' };
        }
    }

    // Use the same bundled provider catalog and protocol adapters as the harness.
    const pi = path.join(runtime.runtime, 'node_modules', '@earendil-works', 'pi-ai', 'dist');
    const load = file => import(pathToFileURL(path.join(pi, file)).href);
    const { id, providers } = route(config);
    const settings = providers[id];
    const model = { id: config.model, name: config.model, provider: id, api: settings.api,
        baseUrl: settings.baseURL, input: ['text'], reasoning: false, compat: settings.compat,
        contextWindow: settings.defaultContextWindow ?? 1_000_000, maxTokens: settings.defaultMaxTokens ?? 128_000,
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
    const stop = new AbortController();
    const signal = AbortSignal.any([deadline, stop.signal]);
    const context = { messages: [{ role: 'user', content: 'Reply with OK.', timestamp: Date.now() }] };
    const options = { apiKey: key || 'local', maxTokens: 24, maxRetries: 0, signal,
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
        for await (const event of stream) {
            if (event.type === 'error') throw responseError(event.error.errorMessage);
            if ((event.type === 'text_delta' || event.type === 'thinking_delta') && event.delta.trim()) {
                return { model: config.model, check: 'response' };
            }
        }
        throw responseError();
    } catch (error) {
        if (deadline.aborted) throw timeoutError();
        throw error;
    } finally {
        stop.abort();
    }
}

async function testModel(runtime, modelConfig, apiKey) {
    const workspace = fs.mkdtempSync(path.join(os.tmpdir(), 'guizhi-model-'));
    let usedTool = false;
    try {
        const receipt = await new Promise((resolve, reject) => {
            let result;
            const child = startAgent(runtime, workspace);
            child.on('message', message => {
                if (message.event?.tool === 'mcp__guizhi__list_skills') usedTool = true;
                if (message.type === 'result') result = message.result;
            });
            child.once('error', () => reject(new Error('无法启动模型调度进程')));
            child.once('close', () => result ? resolve(result) : reject(new Error('模型调度进程已退出')));
            child.send({ type: 'start', options: { ...runtime, workspace, modelConfig, apiKey,
                prompt: '这是连接测试。请调用 list_skills 工具，随后回答已读取业务目录。不要执行任何办理工作流。',
                timeoutMs: 90000, maxModelCalls: 4, maxModelTokens: 32000 } });
        });
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
