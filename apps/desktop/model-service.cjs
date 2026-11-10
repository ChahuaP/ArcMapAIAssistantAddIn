const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { startAgent } = require('./agent-process.cjs');

async function listModels(config, key) {
    if (config.api !== 'openai-completions') throw new Error('请手动填写此服务的模型名称');
    let response;
    try {
        response = await fetch(config.baseUrl + '/models', { redirect: 'error',
            headers: key ? { authorization: 'Bearer ' + key } : {},
            signal: AbortSignal.timeout(12_000) });
    } catch { throw new Error('无法连接模型服务，请检查地址和网络'); }
    if (response.status === 401 || response.status === 403) throw new Error('模型服务认证失败，请检查 API 密钥');
    if (!response.ok) throw new Error('模型列表读取失败，请手动填写模型名称');
    let result;
    try { result = await response.json(); }
    catch { throw new Error('模型列表格式不受支持，请手动填写模型名称'); }
    if (!Array.isArray(result.data)) throw new Error('模型列表格式不受支持，请手动填写模型名称');
    return [...new Set(result.data.map(entry => entry?.id)
        .filter(id => typeof id === 'string' && id && id.length <= 160))].slice(0, 200);
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

module.exports = { listModels, testModel };
