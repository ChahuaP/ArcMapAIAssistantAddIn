const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { startAgent } = require('./agent-process.cjs');
const { route } = require('../../src/guizhi/infrastructure/dsh/model-config.cjs');

const connectionTimeout = 12_000;
const timeoutError = () => new Error('12 秒内未收到模型服务响应，请检查服务和网络后重试');

async function readModels(config, key, signal, allowUnsupported = false) {
    if (config.api !== 'openai-completions') throw new Error('请手动填写此服务的模型名称');
    let response;
    try {
        response = await fetch(config.baseUrl + '/models', { redirect: 'error',
            headers: key ? { authorization: 'Bearer ' + key } : {},
            signal });
    } catch { throw signal.aborted ? timeoutError() : new Error('无法连接模型服务，请检查地址和网络'); }
    if (response.status === 401 || response.status === 403) throw new Error('模型服务认证失败，请检查 API 密钥');
    if (allowUnsupported && [404, 405, 501].includes(response.status)) return null;
    if (!response.ok) throw new Error('模型列表读取失败，请手动填写模型名称');
    let result;
    try { result = await response.json(); }
    catch { throw signal.aborted ? timeoutError() : new Error('模型列表格式不受支持，请手动填写模型名称'); }
    if (!Array.isArray(result.data)) throw new Error('模型列表格式不受支持，请手动填写模型名称');
    return [...new Set(result.data.map(entry => entry?.id)
        .filter(id => typeof id === 'string' && id && id.length <= 160))];
}

async function listModels(config, key) {
    return (await readModels(config, key, AbortSignal.timeout(connectionTimeout))).slice(0, 200);
}

function responseError(reason = '') {
    if (/\b(401|403)\b|unauthorized|invalid api key/i.test(reason)) {
        return new Error('模型服务认证失败，请检查 API 密钥');
    }
    if (/\b404\b|unknown model|model.+not found/i.test(reason)) return new Error('请检查服务地址和模型名称');
    if (/\b429\b|rate.limit|quota/i.test(reason)) return new Error('模型服务限流或额度不足，请稍后重试或检查服务额度');
    return new Error('模型服务未能响应，请检查地址、模型名称和网络');
}

async function checkModelConnection(runtime, config, key) {
    const deadline = AbortSignal.timeout(connectionTimeout);
    if (config.mode === 'local' && config.api === 'openai-completions') {
        const ids = await readModels(config, key, deadline, true);
        if (ids) {
            if (!ids.includes(config.model)) throw new Error('模型服务已连接，但未找到所选模型');
            return { model: config.model, check: 'catalog' };
        }
    }

    // Use the same bundled provider catalog and protocol adapters as the harness.
    const pi = path.join(runtime.runtime, 'node_modules', '@earendil-works', 'pi-ai', 'dist');
    const load = file => import(pathToFileURL(path.join(pi, file)).href);
    const { id, providers } = route(config);
    const stop = new AbortController();
    const signal = AbortSignal.any([deadline, stop.signal]);
    const context = { messages: [{ role: 'user', content: 'Reply with OK.', timestamp: Date.now() }] };
    const options = { apiKey: key || 'local', maxTokens: 24, maxRetries: 0, signal,
        fetch: (url, init) => fetch(url, { ...init, redirect: 'error' }) };
    try {
        let stream;
        if (config.provider === 'custom') {
            const settings = providers[id];
            const { openAICompletionsApi, anthropicMessagesApi } = await load(`api/${config.api}.lazy.js`);
            const client = openAICompletionsApi ? openAICompletionsApi() : anthropicMessagesApi();
            signal.throwIfAborted();
            stream = client.streamSimple({ id: config.model, name: config.model, provider: id, api: config.api,
                baseUrl: settings.baseURL, input: ['text'], reasoning: false, compat: settings.compat,
                contextWindow: settings.defaultContextWindow, maxTokens: settings.defaultMaxTokens,
                cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } }, context, options);
        } else {
            const { builtinModels } = await load('providers/all.js');
            const models = builtinModels();
            const model = models.getModel(id, config.model);
            if (!model) throw new Error('请检查所选服务商和模型名称');
            signal.throwIfAborted();
            stream = models.streamSimple(model, context, options);
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
