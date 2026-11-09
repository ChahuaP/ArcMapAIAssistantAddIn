import { randomUUID } from 'node:crypto';
import { addAbortListener } from 'node:events';
import { appendFileSync, existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const source = realpathSync(fileURLToPath(new URL('../../../../', import.meta.url)));
const readJson = path => JSON.parse(readFileSync(path, 'utf8'));

function verifiedResponse(receipt) {
    const stats = receipt.statistics;
    const area = value => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const parts = [];
    if (receipt.mode) parts.push(`已完成 ${stats.step_count} 个步骤。`);
    for (const item of receipt.steps ?? [{ statistics: stats }]) {
        const fact = item.statistics;
        if (fact.coverage_ratio !== undefined) {
            parts.push(`项目范围 ${area(fact.project_area_m2)} m²，重叠 ${area(fact.covered_area_m2)} m²，覆盖率 ${(fact.coverage_ratio * 100).toFixed(2)}%。结果反映空间关系，审批依据请查阅适用规则。`);
        }
    }
    if (stats.feature_count !== undefined) parts.push(`共 ${stats.feature_count} 个要素。`);
    if (stats.geometry_types) parts.push(Object.entries(stats.geometry_types)
        .map(([name, count]) => `${name} ${count} 个`).join('，') + '。');
    if (stats.row_count !== undefined) parts.push(`共 ${stats.row_count} 行。`);
    return parts.join('') || '已完成办理，成果和证据已保存。';
}

function within(root, path) {
    const rel = relative(root, path);
    return rel === '' || (!isAbsolute(rel) && rel.split(/[\\/]/)[0] !== '..');
}

export function externalPath(path) {
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
    const mcpArgs = ['-I', '-B', fileURLToPath(new URL('./mcp.py', import.meta.url)),
        '--workspace', workspace, '--dispatch', directory];
    if (options.input) mcpArgs.push('--input', resolve(options.input));
    if (options.layer) mcpArgs.push('--layer', options.layer);
    if (options.control) mcpArgs.push('--control', resolve(options.control));
    if (options.controlLayer) mcpArgs.push('--control-layer', options.controlLayer);
    if (options.projectPlan) mcpArgs.push('--project-plan', options.projectPlan);
    if (options.controlPlan) mcpArgs.push('--control-plan', options.controlPlan);
    if (options.skill) mcpArgs.push('--skill', options.skill);
    if (options.previousRun) mcpArgs.push('--previous-run', options.previousRun);
    if (options.materials) mcpArgs.push('--materials', JSON.stringify(options.materials));
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
    let attempted = false;
    try {
        event({ kind: 'dispatch', id, provider: options.provider, model: options.model });
        const aborted = new Promise(resolveAbort => {
            stopListener = addAbortListener(stop, () => resolveAbort(null));
        });
        const roles = Object.fromEntries(Object.entries(options.materials ?? {}).map(([role, items]) => [role, items.length]));
        if (options.input && options.layer) roles.project = 1;
        if (options.control && options.controlLayer) roles.control = 1;
        const context = `已绑定材料数量 ${JSON.stringify(roles)}。已选技能 ${options.skill || '无'}。有已选历史成果：${Boolean(options.previousRun)}。\n`;
        const response = await Promise.race([harness.run(context + '用户请求：' + options.prompt, {
            sessionId: id,
            onNotification(note) {
                if (note.method === 'session.status') {
                    event({ kind: 'status', status: note.params.status });
                }
                if (note.method !== 'session.event') return;
                const fact = note.params.event;
                const item = { kind: 'session_event', type: fact.type, seq: fact.seq, time: fact.time };
                if (fact.type === 'tool/call') {
                    item.tool = fact.data.name;
                    if (item.tool === 'mcp__guizhi__execute_workflow') attempted = true;
                }
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
            const receiptPath = join(directory, 'operation.json');
            const issuePath = join(directory, 'input-issue.json');
            const receipt = existsSync(receiptPath) ? readJson(receiptPath) : null;
            const issue = existsSync(issuePath) ? readJson(issuePath) : null;
            const status = receipt?.status ?? (response.finishReason !== 'completed' ? 'failed' : issue?.status ??
                (attempted ? 'no_result' : existsSync(join(directory, 'discovery')) ? 'answered'
                    : Object.keys(roles).length ? 'no_result' : 'missing_materials'));
            const terminal = response.events.findLast(fact => fact.type === 'turn/end');
            const candidatePath = join(directory, 'candidate.json');
            result = { status, finish_reason: response.finishReason,
                response: status === 'succeeded' ? verifiedResponse(receipt)
                    : receipt?.error ?? issue?.error ?? (attempted ? '本轮没有交付成果。' : response.finalResponse),
                operation: receipt, ...(issue && !receipt ? { input_issue: issue } : {}),
                ...(status === 'answered' && existsSync(candidatePath) ? { candidate: readJson(candidatePath) } : {}),
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
        control: { type: 'string' }, 'control-layer': { type: 'string' },
        'project-plan': { type: 'string' }, 'control-plan': { type: 'string' },
        'timeout-ms': { type: 'string' }, skill: { type: 'string' }, materials: { type: 'string' }, 'previous-run': { type: 'string' },
    } });
    const controller = new AbortController();
    const cancel = () => controller.abort();
    process.once('SIGINT', cancel);
    process.once('SIGTERM', cancel);
    try {
        const result = await dispatch({ ...values, modelUrl: values['model-url'], controlLayer: values['control-layer'],
            materials: values.materials ? JSON.parse(values.materials) : undefined,
            previousRun: values['previous-run'],
            projectPlan: values['project-plan'], controlPlan: values['control-plan'],
            timeoutMs: values['timeout-ms'] === undefined ? undefined : Number(values['timeout-ms']) },
        { signal: controller.signal, onEvent: item => console.log(JSON.stringify(item)) });
        console.log(JSON.stringify({ kind: 'result', ...result }));
        process.exitCode = ['succeeded', 'answered'].includes(result.status) ? 0 : result.status === 'cancelled' ? 130 : 1;
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
