import { randomUUID } from 'node:crypto';
import { addAbortListener } from 'node:events';
import { appendFileSync, existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const source = realpathSync(fileURLToPath(new URL('../../../../', import.meta.url)));
const readJson = path => JSON.parse(readFileSync(path, 'utf8'));

function within(root, path) {
    const rel = relative(root, path);
    return rel === '' || (!isAbsolute(rel) && rel.split(/[\\/]/)[0] !== '..');
}

function externalPath(path) {
    if (!isAbsolute(path)) throw new Error('Workspace must be an absolute path');
    const full = resolve(path);
    let parent = full;
    while (!existsSync(parent)) parent = dirname(parent);
    const physical = resolve(realpathSync(parent), relative(parent, full));
    if (within(source, physical) || within(physical, source)) {
        throw new Error('Workspace must be outside the source tree and its ancestors');
    }
    return physical;
}

function runtimeEnv(options, mcpArgs) {
    const env = {};
    const allowed = /^(path|systemroot|windir|temp|tmp|home|userprofile|appdata|localappdata|comspec|https_proxy|http_proxy|no_proxy)$/i;
    for (const [name, value] of Object.entries(process.env)) {
        if (allowed.test(name)) env[name] = value;
    }
    let providers;
    if (options.provider === 'ollama') {
        const url = new URL(options.modelUrl ?? 'http://127.0.0.1:11434/v1');
        if (url.protocol !== 'http:' || !['127.0.0.1', '[::1]'].includes(url.hostname)
                || url.username || url.password || url.pathname !== '/v1' || url.search || url.hash) {
            throw new Error('Ollama URL must be a loopback HTTP /v1 endpoint');
        }
        env.GUIZHI_LOOPBACK_KEY = 'local';
        providers = { ollama: {
            api: 'openai-completions', baseURL: url.href, apiKeyEnv: 'GUIZHI_LOOPBACK_KEY',
            defaultContextWindow: 8192, defaultMaxTokens: 1024, models: [{ id: options.model }],
        } };
    } else if (options.provider === 'minimax-cn') {
        if (options.modelUrl) throw new Error('Custom URL is only supported for Ollama');
        if (!process.env.MINIMAX_API_KEY?.trim()) throw new Error('MINIMAX_API_KEY is required');
        env.MINIMAX_API_KEY = process.env.MINIMAX_API_KEY;
        providers = { 'minimax-cn': { apiKeyEnv: 'MINIMAX_API_KEY' } };
    } else {
        throw new Error('Provider must be ollama or minimax-cn');
    }
    return { ...env, GUIZHI_MODEL_PROVIDERS: JSON.stringify(providers),
        GUIZHI_PYTHON: options.python, GUIZHI_MCP_ARGS: JSON.stringify(mcpArgs) };
}

export async function dispatch(options, { signal, onEvent = () => {} } = {}) {
    if (!options.prompt?.trim() || !options.model?.trim()) throw new Error('Prompt and model are required');
    const timeoutMs = options.timeoutMs ?? 180000;
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 900000) {
        throw new Error('Timeout must be between 1 and 900000 ms');
    }
    if (!options.python || !options.runtime || !isAbsolute(options.python) || !isAbsolute(options.runtime)) {
        throw new Error('Python and DSH runtime paths must be absolute');
    }
    const runtime = realpathSync(options.runtime);
    const versions = readJson(join(source, 'packaging', 'dsh-runtime', 'package.json')).dependencies;
    for (const name of ['@deepseek-ai/dsh', '@deepseek-ai/dsh-sdk-client']) {
        const installed = readJson(join(runtime, 'node_modules', name, 'package.json'));
        if (installed.version !== versions[name]) throw new Error('DSH runtime does not match the locked versions');
    }
    const requireRuntime = createRequire(join(runtime, 'package.json'));
    const { DeepSeekHarness } = await import(pathToFileURL(requireRuntime.resolve('@deepseek-ai/dsh-sdk-client')).href);
    const workspace = externalPath(options.workspace ?? '');
    const parent = join(workspace, 'dispatch');
    if (existsSync(parent) && relative(parent, realpathSync(parent)) !== '') {
        throw new Error('Dispatch directory must not redirect');
    }
    const id = randomUUID();
    const directory = join(parent, id);
    const mcpArgs = ['-I', '-B', fileURLToPath(new URL('./mcp_inventory.py', import.meta.url)),
        '--workspace', workspace, '--dispatch', directory];
    if (options.input) mcpArgs.push('--input', resolve(options.input));
    if (options.layer) mcpArgs.push('--layer', options.layer);
    const env = runtimeEnv(options, mcpArgs);
    const deadline = AbortSignal.timeout(timeoutMs);
    const stop = signal ? AbortSignal.any([signal, deadline]) : deadline;
    stop.throwIfAborted();
    mkdirSync(parent, { recursive: true });
    mkdirSync(directory);
    const harness = new DeepSeekHarness({
        profile: 'sdk-minimal',
        patches: [fileURLToPath(new URL('./profile.yml', import.meta.url))],
        dshHome: join(directory, 'dsh-home'), cwd: directory, processCwd: directory,
        provider: options.provider, model: options.model, maxTokens: 1024,
        initializeTimeoutMs: 45000, env,
    });
    const usage = new Map();
    function event(item) {
        appendFileSync(join(directory, 'events.jsonl'), JSON.stringify(item) + '\n');
        onEvent(item);
    }
    let stopListener;
    let result;
    try {
        event({ kind: 'dispatch', id, provider: options.provider, model: options.model });
        const aborted = new Promise(resolveAbort => {
            stopListener = addAbortListener(stop, () => resolveAbort(null));
        });
        const response = await Promise.race([harness.run(options.prompt, {
            sessionId: id,
            onNotification(note) {
                if (note.method === 'session.status') {
                    event({ kind: 'status', status: note.params.status });
                }
                if (note.method !== 'session.event') return;
                const fact = note.params.event;
                const item = { kind: 'session_event', type: fact.type, seq: fact.seq, time: fact.time };
                if (fact.type === 'tool/call') item.tool = fact.data.name;
                if (fact.type === 'assistant/message' && fact.data.usage) {
                    item.usage = fact.data.usage;
                    usage.set(fact.seq, fact.data.usage);
                }
                event(item);
            },
        }), aborted]);
        if (response === null) {
            writeFileSync(join(directory, 'cancelled'), '');
            result = { status: deadline.aborted ? 'timed_out' : 'cancelled' };
        } else {
            const receiptPath = join(directory, 'inventory.json');
            const receipt = existsSync(receiptPath) ? readJson(receiptPath) : null;
            const status = response.finishReason !== 'completed' ? 'failed'
                : receipt?.status ?? (options.input && options.layer ? 'no_result' : 'missing_materials');
            const terminal = response.events.findLast(fact => fact.type === 'turn/end');
            result = { status, finish_reason: response.finishReason,
                response: response.finalResponse, operation: receipt,
                ...(terminal?.data.reason.error ? { error: terminal.data.reason.error } : {}) };
        }
    } catch (error) {
        writeFileSync(join(directory, 'cancelled'), '');
        result = { status: 'failed', error: error.message };
    } finally {
        stopListener?.[Symbol.dispose]();
        try {
            await harness.close();
        } catch (error) {
            result = { ...result, status: 'failed', shutdown_error: error.message };
        }
    }
    const accounting = { input_tokens: 0, output_tokens: 0, cache_read_tokens: 0,
        cache_write_tokens: 0, total_tokens: 0, reported_steps: usage.size };
    for (const tokens of usage.values()) {
        accounting.input_tokens += tokens.inputTokens;
        accounting.output_tokens += tokens.outputTokens;
        accounting.cache_read_tokens += tokens.cacheReadTokens ?? 0;
        accounting.cache_write_tokens += tokens.cacheWriteTokens ?? 0;
        accounting.total_tokens = tokens.totalTokens === undefined || accounting.total_tokens === null
            ? null : accounting.total_tokens + tokens.totalTokens;
    }
    const receipt = { ...result, id, directory, provider: options.provider, model: options.model,
        sdk_version: versions['@deepseek-ai/dsh-sdk-client'], usage: usage.size ? accounting : null };
    writeFileSync(join(directory, 'result.json'), JSON.stringify(receipt, null, 2) + '\n');
    return receipt;
}

async function main() {
    const { values } = parseArgs({ options: {
        workspace: { type: 'string' }, runtime: { type: 'string' }, python: { type: 'string' },
        provider: { type: 'string' }, model: { type: 'string' }, 'model-url': { type: 'string' },
        prompt: { type: 'string' }, input: { type: 'string' }, layer: { type: 'string' },
        'timeout-ms': { type: 'string' },
    } });
    const controller = new AbortController();
    const cancel = () => controller.abort();
    process.once('SIGINT', cancel);
    process.once('SIGTERM', cancel);
    try {
        const result = await dispatch({ ...values, modelUrl: values['model-url'],
            timeoutMs: values['timeout-ms'] === undefined ? undefined : Number(values['timeout-ms']) },
        { signal: controller.signal, onEvent: item => console.log(JSON.stringify(item)) });
        console.log(JSON.stringify({ kind: 'result', ...result }));
        process.exitCode = result.status === 'succeeded' ? 0 : result.status === 'cancelled' ? 130 : 1;
    } finally {
        process.off('SIGINT', cancel);
        process.off('SIGTERM', cancel);
    }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    main().catch(error => {
        console.error(JSON.stringify({ status: 'failed', error: error.message }));
        process.exitCode = 1;
    });
}
