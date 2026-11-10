import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { addAbortListener } from 'node:events';
import { appendFileSync, existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, isAbsolute, join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { promisify } from 'node:util';
import { watchRun } from './progress.mjs';
import models from './model-config.cjs';

const source = realpathSync(fileURLToPath(new URL('../../../../', import.meta.url)));
const readJson = path => JSON.parse(readFileSync(path, 'utf8'));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const exec = promisify(execFile);

function assistantAnswer(text) {
    if (typeof text !== 'string') return '';
    return text.replace(/^[\s\S]*<\/think\s*>/i, '').replace(/<think\b[^>]*>[\s\S]*$/i, '').trim();
}

function verifiedResponse(receipt, dialogue) {
    const stats = receipt.statistics;
    const area = value => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const parts = [];
    if (receipt.mode && !dialogue) parts.push(`已完成 ${stats.step_count} 个步骤。`);
    if (stats.reused_steps) parts.push(`其中复用 ${stats.reused_steps} 个步骤。`);
    if (stats.changed_parcels) parts.push(`更新涉及 ${stats.changed_parcels} 宗地。`);
    for (const item of receipt.steps ?? [{ statistics: stats }]) {
        const fact = item.statistics;
        if (fact.coverage_ratio !== undefined) {
            parts.push(`项目范围 ${area(fact.project_area_m2)} m²，重叠 ${area(fact.covered_area_m2)} m²，覆盖率 ${(fact.coverage_ratio * 100).toFixed(2)}%。结果反映空间关系，审批依据请查阅适用规则。`);
        }
    }
    if (stats.feature_count !== undefined) parts.push(`共 ${stats.feature_count} 个要素。`);
    if (stats.geometry_types && !dialogue) parts.push(Object.entries(stats.geometry_types)
        .map(([name, count]) => `${name} ${count} 个`).join('，') + '。');
    if (stats.row_count !== undefined) parts.push(`共 ${stats.row_count} 行。`);
    if (stats.changed_area_m2 !== undefined && stats.evaluation) {
        if (stats.evaluation === 'simulation') parts.push('本轮使用模拟样本。');
        parts.push(`变化面积 ${area(stats.changed_area_m2)} m²，另有 ${area(stats.unknown_pixels)} 个未知像元和 ${area(stats.nodata_pixels)} 个缺测像元。`);
    }
    const answer = parts.join('') || '已完成办理，成果和证据已保存。';
    return dialogue ? answer.replaceAll(' m²', '平方米') : answer;
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

export function runtimeEnv(options, mcpArgs = []) {
    const env = {};
    const allowed = /^(path|systemroot|windir|temp|tmp|home|userprofile|appdata|localappdata|comspec|https_proxy|http_proxy|no_proxy|node_use_env_proxy)$/i;
    for (const [name, value] of Object.entries(process.env)) {
        if (allowed.test(name)) env[name] = value;
    }
    const key = models.modelKey(options.modelConfig, options.apiKey);
    // pi-ai requires a credential value even when a loopback service permits anonymous requests.
    env.GUIZHI_MODEL_KEY = key || 'local';
    return { ...env, GUIZHI_MODEL_PROVIDERS: JSON.stringify(models.route(options.modelConfig).providers),
        GUIZHI_PYTHON: options.python, GUIZHI_MCP_ARGS: JSON.stringify(mcpArgs) };
}

export async function loadHarness(python, runtime) {
    if (!python || !runtime || !isAbsolute(python) || !isAbsolute(runtime)) {
        throw new Error('Python and DSH runtime paths must be absolute');
    }
    const root = realpathSync(runtime);
    const versions = readJson(join(source, 'packaging', 'dsh-runtime', 'package.json')).dependencies;
    for (const name of ['@deepseek-ai/dsh', '@deepseek-ai/dsh-sdk-client']) {
        const installed = readJson(join(root, 'node_modules', name, 'package.json'));
        if (installed.version !== versions[name]) throw new Error('DSH runtime does not match the locked versions');
    }
    const requireRuntime = createRequire(join(root, 'package.json'));
    const { DeepSeekHarness } = await import(pathToFileURL(requireRuntime.resolve('@deepseek-ai/dsh-sdk-client')).href);
    return { DeepSeekHarness, versions };
}

export async function dispatch(options, { signal, onEvent = () => {} } = {}) {
    const modelConfig = models.modelConfig(options.modelConfig);
    const apiKey = models.modelKey(modelConfig, options.apiKey);
    const { id: provider } = models.route(modelConfig);
    const { model } = modelConfig;
    options = { ...options, modelConfig, apiKey };
    if (!options.prompt?.trim()) throw new Error('Prompt is required');
    if (options.skillParameters !== undefined && (!options.skill || !options.skillParameters ||
            typeof options.skillParameters !== 'object' || Array.isArray(options.skillParameters))) {
        throw new Error('请选择办理参数对应的技能');
    }
    const started = performance.now();
    const safeError = value => redact(value, apiKey);
    const timeoutMs = options.timeoutMs ?? 180000;
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 900000) {
        throw new Error('Timeout must be between 1 and 900000 ms');
    }
    const tokens = options.maxModelTokens ?? 180000;
    const calls = options.maxModelCalls ?? 12;
    if (!Number.isSafeInteger(tokens) || tokens < 1 || tokens > 1000000 ||
            !Number.isSafeInteger(calls) || calls < 1 || calls > 40) {
        throw new Error('Invalid model budget');
    }
    const { DeepSeekHarness, versions } = await loadHarness(options.python, options.runtime);
    const workspace = externalPath(options.workspace ?? '');
    const parent = join(workspace, 'dispatch');
    if (existsSync(parent) && relative(parent, realpathSync(parent)) !== '') {
        throw new Error('Dispatch directory must not redirect');
    }
    const id = randomUUID();
    const directory = join(parent, id);
    const profile = fileURLToPath(new URL('./profile.yml', import.meta.url));
    const schema = ['capabilities', 'skills', 'semantics', 'knowledge']
        .map(name => readFileSync(join(source, 'src', 'guizhi', 'domain', name + '.py')));
    const history = options.history || [];
    const request = history.length || options.dialogue
        ? JSON.stringify({ prompt: options.prompt, history, dialogue: Boolean(options.dialogue) }) : options.prompt;
    const provenance = { provider, model, route_sha256: hash(JSON.stringify(modelConfig)),
        profile_sha256: hash(readFileSync(profile)), request_sha256: hash(request),
        schema_sha256: hash(Buffer.concat(schema)), retrieval_version: '1.0.0' };
    const mcpArgs = ['-I', '-B', fileURLToPath(new URL('./mcp.py', import.meta.url)),
        '--workspace', workspace, '--dispatch', directory, '--model-provenance', JSON.stringify(provenance)];
    if (options.projectPlan) mcpArgs.push('--project-plan', options.projectPlan);
    if (options.controlPlan) mcpArgs.push('--control-plan', options.controlPlan);
    if (options.skill) mcpArgs.push('--skill', options.skill);
    if (options.previousRun) mcpArgs.push('--previous-run', options.previousRun);
    const deadline = AbortSignal.timeout(timeoutMs);
    const stop = signal ? AbortSignal.any([signal, deadline]) : deadline;
    stop.throwIfAborted();
    mkdirSync(parent, { recursive: true });
    mkdirSync(directory);
    const materials = { ...options.materials };
    if (options.input && options.layer) materials.project = [{ file: resolve(options.input), layer: options.layer }];
    if (options.control && options.controlLayer) materials.control = [{ file: resolve(options.control), layer: options.controlLayer }];
    const requestFile = join(directory, 'replay-input.json');
    const operationPath = join(directory, 'operation.json');
    const budgetPath = join(directory, 'budget.json');
    writeFileSync(requestFile, JSON.stringify({ materials, provenance,
        skill: options.skill ?? null, previous_run: options.previousRun ?? null, project_plan: options.projectPlan ?? null,
        control_plan: options.controlPlan ?? null, skill_parameters: options.skillParameters ?? null,
        dsh: versions['@deepseek-ai/dsh'] }));
    if (options.skillParameters !== undefined) mcpArgs.push('--use-bound-parameters');
    const usage = new Map();
    function event(item) {
        appendFileSync(join(directory, 'events.jsonl'), JSON.stringify(item) + '\n');
        onEvent(item);
    }
    let stopListener;
    let stopProgress;
    let harness;
    let result;
    let attempted = false;
    try {
        event({ kind: 'dispatch', id, provider, model });
        const aborted = new Promise(resolveAbort => {
            stopListener = addAbortListener(stop, () => {
                writeFileSync(join(directory, 'cancelled'), '');
                resolveAbort(null);
            });
        });
        const { stdout } = await exec(options.python, ['-I', '-B', join(source, 'src/guizhi/__main__.py'),
            '--workspace', workspace, '_dispatch-replay', requestFile], { windowsHide: true, maxBuffer: 4 << 20 });
        const prepared = JSON.parse(stdout);
        if (prepared.operation) {
            event({ kind: 'plan_reused', run_id: prepared.operation.run_id });
            writeFileSync(operationPath, JSON.stringify(prepared.operation));
            result = { status: prepared.operation.status, finish_reason: 'recorded_plan', plan_reused: true,
                operation: prepared.operation, response: prepared.operation.status === 'succeeded'
                    ? verifiedResponse(prepared.operation, options.dialogue) : prepared.operation.error };
        } else if (stop.aborted) {
            result = { status: deadline.aborted ? 'timed_out' : 'cancelled' };
        } else {
            stopProgress = watchRun(workspace, id, event);
            mcpArgs.push('--materials', JSON.stringify(prepared.materials));
            if (prepared.key) mcpArgs.push('--plan-key', prepared.key);
            const env = { ...runtimeEnv(options, mcpArgs),
                GUIZHI_BUDGET_CONFIG: JSON.stringify({ tokens, calls, checkpoint: budgetPath, operation: operationPath }) };
            harness = new DeepSeekHarness({
                profile: 'sdk-minimal', patches: [profile], dshHome: join(directory, 'dsh-home'),
                cwd: directory, processCwd: directory, provider, model, maxTokens: 1024,
                initializeTimeoutMs: 45000, env,
            });
            await harness.start();
            if (!existsSync(budgetPath)) throw new Error('Model budget guard did not initialize');
            const roles = Object.fromEntries(Object.entries(materials).map(([role, items]) => [role, items.length]));
            const bound = options.skillParameters !== undefined ? '办理参数已在本机绑定，执行技能时省略 skill_parameters。' : '';
            const context = `已绑定材料数量 ${JSON.stringify(roles)}。已选技能 ${options.skill || '无'}。${bound}有已选历史成果：${Boolean(options.previousRun)}。\n`;
            const conversation = history.length ? '最近对话记录（每项为用户提问与规智答复）：' + JSON.stringify(history) + '\n' : '';
            const speech = options.dialogue ? '本轮通过语音对话。用自然口语简洁回答，不用 Markdown。\n' : '';
            const response = await Promise.race([harness.run(context + conversation + speech + '用户请求：' + options.prompt, {
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
                        if (['mcp__guizhi__execute_workflow', 'mcp__guizhi__review_approval',
                            'mcp__guizhi__update_approval'].includes(item.tool)) attempted = true;
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
                const issuePath = join(directory, 'input-issue.json');
                const receipt = existsSync(operationPath) ? readJson(operationPath) : null;
                const issue = existsSync(issuePath) ? readJson(issuePath) : null;
                const stopped = existsSync(budgetPath) && readJson(budgetPath).status === 'exhausted';
                const answer = assistantAnswer(response.finalResponse);
                const status = receipt?.status ?? (stopped ? 'budget_exhausted' : response.finishReason !== 'completed' ? 'failed' : issue?.status ??
                    (attempted ? 'no_result' : existsSync(join(directory, 'discovery')) ||
                        (!Object.keys(roles).length && !options.skill && answer) ? 'answered'
                        : Object.keys(roles).length ? 'no_result' : 'missing_materials'));
                const terminal = response.events.findLast(fact => fact.type === 'turn/end');
                const candidatePath = join(directory, 'candidate.json');
                result = { status, finish_reason: response.finishReason,
                    response: status === 'succeeded' ? verifiedResponse(receipt, options.dialogue)
                        : receipt?.error ?? issue?.error ?? (stopped ? '模型预算已用完，已保存办理进度。'
                            : attempted ? '本轮没有交付成果。' : answer),
                    operation: receipt, ...(issue && !receipt ? { input_issue: issue } : {}),
                    ...(status === 'answered' && existsSync(candidatePath) ? { candidate: readJson(candidatePath) } : {}),
                    ...(terminal?.data.reason.error ? { error: safeError(terminal.data.reason.error) } : {}) };
            }
        }
    } catch (error) {
        writeFileSync(join(directory, 'cancelled'), '');
        result = { status: 'failed', error: safeError(error.message) };
    } finally {
        stopListener?.[Symbol.dispose]();
        try {
            await harness?.close();
        } catch (error) {
            result = { ...result, status: 'failed', shutdown_error: safeError(error.message) };
        }
        stopProgress?.();
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
    if (typeof result.response === 'string') result.response = safeError(result.response);
    const receipt = { ...result, id, directory, provider, model,
        elapsed_ms: Math.round(performance.now() - started), execution: { gis: 'local', model: modelConfig.mode },
        budget: existsSync(budgetPath) ? readJson(budgetPath) : null,
        sdk_version: versions['@deepseek-ai/dsh-sdk-client'], usage: usage.size ? accounting : null };
    writeFileSync(join(directory, 'result.json'), JSON.stringify(receipt, null, 2) + '\n');
    return receipt;
}

export function redact(value, secret) {
    return secret ? String(value).replaceAll(secret, '[密钥]') : String(value);
}

export const modelArguments = {
    mode: { type: 'string', default: 'online' }, provider: { type: 'string' },
    model: { type: 'string' }, api: { type: 'string' }, 'base-url': { type: 'string' },
};

export function modelFromArguments(values) {
    return { modelConfig: models.modelConfig({ ...values, baseUrl: values['base-url'] }),
        apiKey: process.env.GUIZHI_MODEL_KEY };
}

async function main() {
    const { values } = parseArgs({ options: {
        workspace: { type: 'string' }, runtime: { type: 'string' }, python: { type: 'string' },
        ...modelArguments,
        prompt: { type: 'string' }, input: { type: 'string' }, layer: { type: 'string' },
        control: { type: 'string' }, 'control-layer': { type: 'string' },
        'project-plan': { type: 'string' }, 'control-plan': { type: 'string' },
        'timeout-ms': { type: 'string' }, skill: { type: 'string' }, materials: { type: 'string' }, 'previous-run': { type: 'string' },
        'max-model-tokens': { type: 'string' }, 'max-model-calls': { type: 'string' },
    } });
    const controller = new AbortController();
    const cancel = () => controller.abort();
    process.once('SIGINT', cancel);
    process.once('SIGTERM', cancel);
    try {
        const result = await dispatch({ ...values, ...modelFromArguments(values), controlLayer: values['control-layer'],
            materials: values.materials ? JSON.parse(values.materials) : undefined,
            previousRun: values['previous-run'],
            projectPlan: values['project-plan'], controlPlan: values['control-plan'],
            maxModelTokens: values['max-model-tokens'] === undefined ? undefined : Number(values['max-model-tokens']),
            maxModelCalls: values['max-model-calls'] === undefined ? undefined : Number(values['max-model-calls']),
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
        console.error(JSON.stringify({ status: 'failed', error: redact(error.message, process.env.GUIZHI_MODEL_KEY) }));
        process.exitCode = 1;
    });
}
