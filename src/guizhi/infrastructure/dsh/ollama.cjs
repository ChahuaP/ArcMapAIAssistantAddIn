const defaultUrl = 'http://127.0.0.1:11434/v1';

function ollamaUrl(value) {
    const url = new URL(value?.trim() || defaultUrl);
    const parts = url.hostname.split('.').map(Number);
    const [first, second] = parts;
    const privateIp = parts.length === 4 && parts.every(Number.isInteger) &&
        (first === 127 || first === 10 || (first === 192 && second === 168) ||
            (first === 172 && second >= 16 && second <= 31));
    if (!['http:', 'https:'].includes(url.protocol) || !(privateIp || url.hostname === '[::1]') ||
            url.pathname !== '/v1' || url.username || url.password || url.search || url.hash) {
        throw new Error('Ollama 地址须使用本机或内网 IP，并以 /v1 结尾');
    }
    return url;
}

async function request(url, endpoint, body, signal) {
    const target = new URL(endpoint, ollamaUrl(url));
    const timeout = AbortSignal.timeout(150_000);
    let response;
    try {
        response = await fetch(target, { method: 'POST', redirect: 'error',
            headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
            signal: signal ? AbortSignal.any([signal, timeout]) : timeout });
    } catch {
        if (signal?.aborted) throw signal.reason;
        throw new Error('无法连接 Ollama，请检查服务地址');
    }
    if (!response.ok) throw new Error(response.status === 404 ? 'Ollama 未安装所选模型'
        : 'Ollama 响应异常（HTTP ' + response.status + '）');
    return response.json();
}

async function modelCapabilities(url, model, signal) {
    if (typeof model !== 'string' || !model.trim() || model.length > 160) throw new Error('请输入 Ollama 模型名称');
    const reply = await request(url, '/api/show', { model: model.trim() }, signal);
    if (!Array.isArray(reply.capabilities)) throw new Error('Ollama 未返回模型能力，请检查服务版本');
    return { model: model.trim(), capabilities: reply.capabilities.filter(value =>
        ['completion', 'tools', 'vision', 'thinking', 'embedding'].includes(value)) };
}

async function describeImage(url, model, image, prompt, signal) {
    if (!Buffer.isBuffer(image) || !image.length || image.length > 8 << 20) throw new Error('图像大小无效');
    const info = await modelCapabilities(url, model, signal);
    if (!info.capabilities.includes('vision')) throw new Error('所选 Ollama 模型不支持图像');
    const reply = await request(url, '/api/chat', { model: info.model, stream: false, think: false,
        messages: [{ role: 'user', content: prompt, images: [image.toString('base64')] }],
        options: { temperature: 0, num_predict: 256 } }, signal);
    const text = reply.message?.content?.trim();
    if (!text) throw new Error('视觉模型未返回结果');
    return { text, inputTokens: reply.prompt_eval_count, outputTokens: reply.eval_count };
}

module.exports = { ollamaUrl, modelCapabilities, describeImage };
