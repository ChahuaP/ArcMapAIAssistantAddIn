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
    if (stats.reused_steps) parts.push(`其中复用 ${stats.reused_steps} 个步骤。`);
    if (stats.changed_parcels) parts.push(`更新涉及 ${stats.changed_parcels} 宗地。`);
    for (const item of receipt.steps ?? [{ statistics: stats }]) {
        const fact = item.statistics;
        if (fact.coverage_ratio !== undefined) {
            const counts = fact.relations || {};
            const findings = [['inside', '完全覆盖'], ['overlap', '部分重叠'], ['touch_line', '线接触'],
                ['touch_point', '点接触'], ['disjoint', '不相交']]
                .filter(([key]) => counts[key]).map(([key, label]) => `${label} ${counts[key]} 宗`);
            parts.push(`项目范围 ${area(fact.project_area_m2)} m²，重叠 ${area(fact.covered_area_m2)} m²，覆盖率 ${(fact.coverage_ratio * 100).toFixed(2)}%。`);
            if (findings.length) parts.push(findings.join('、') + '。');
        }
    }
    if (stats.feature_count !== undefined) parts.push(`共 ${stats.feature_count} 个要素。`);
    if (stats.geometry_types && !dialogue) parts.push(Object.entries(stats.geometry_types)
        .map(([name, count]) => `${name} ${count} 个`).join('，') + '。');
    if (stats.selected_count !== undefined) parts.push(`已选中 ${stats.selected_count} 个要素。`);
    if (stats.row_count !== undefined) parts.push(`共 ${stats.row_count} 行。`);
    if (stats.changed_area_m2 !== undefined && stats.evaluation) {
        if (stats.evaluation === 'simulation') parts.push('本轮使用模拟样本。');
        parts.push(`变化面积 ${area(stats.changed_area_m2)} m²，另有 ${area(stats.unknown_pixels)} 个未知像元和 ${area(stats.nodata_pixels)} 个缺测像元。`);
    }
    if (receipt.mode === 'free' && stats.feature_count !== undefined) {
        const operation = receipt.steps?.at(-1)?.operation;
        const title = { 'vector.buffer': '缓冲分析', 'vector.clip': '裁切', 'vector.intersect': '相交',
            'vector.dissolve': '融合', 'vector.spatial_join': '空间连接', 'data.export_selection': '导出选区' }[operation];
        if (title) parts.unshift(title + '已完成。');
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

export async function dispatch(options, { signal, onEvent = () => {}, getService } = {}) {
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
    const { versions } = await loadHarness(options.python, options.runtime);
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
    const usage = new Map();
    function event(item) {
        appendFileSync(join(directory, 'events.jsonl'), JSON.stringify(item) + '\n');
        onEvent(item);
    }
    let stopListener;
    let stopProgress;
    let owned;
    let boundService;
    let ran = false;
    let result;
    let attempted = false;
    try {
        event({ kind: 'dispatch_preparing', id });
        const cancelled = join(directory, 'cancelled');
        stopListener = addAbortListener(stop, () => writeFileSync(cancelled, ''));
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
            event({ kind: 'dispatch', id, provider, model });
            const service = getService ? await getService() :
                (owned = new (await import('./service.mjs')).DispatchService(options));
            await service.ready;
            stop.throwIfAborted();
            service.bind({ id, workspace, cancelled, materials: prepared.materials,
                skill: prepared.selected?.reference ?? null, parameters: options.skillParameters ?? null,
                plans: { project_plan: options.projectPlan, control_plan: options.controlPlan },
                previousRun: options.previousRun ?? null, provenance, planKey: prepared.key,
                flushRequest: join(directory, 'flush.request'), flushReceipt: join(directory, 'flush.receipt'),
                budget: { tokens, calls, checkpoint: budgetPath, operation: operationPath } });
            boundService = service;
            event({ kind: 'runtime_bound', runtime_id: service.id });
            const { harness } = service;
            const roles = Object.keys(prepared.materials);
            const bound = options.skillParameters !== undefined ? '办理参数已在本机绑定，执行技能时省略 skill_parameters。' : '';
            const selection = prepared.selected ? `已选技能和本轮材料摘要：${JSON.stringify(prepared.selected)}。`
                : `本轮材料图层清单（键为 material. 后的引用）：${JSON.stringify(prepared.layers)}。已选技能无。`;
            const context = `${selection}${bound}有已选历史成果：${Boolean(options.previousRun)}。\n`;
            const conversation = history.length ? '最近对话记录（每项为用户提问与规智答复）：' + JSON.stringify(history) + '\n' : '';
            const speech = options.dialogue ? '本轮使用语音输入。先实际办理，再用简洁口语回答，不用 Markdown。\n' : '';
            const run = prompt => harness.run(prompt, {
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
            });
            let response = await run(context + conversation + speech + '用户请求：' + options.prompt);
            ran = true;
            const needsSelectedWorkflow = options.skill && roles.length > 0 && /^请办理/u.test(options.prompt.trim());
            const needsMeasurement = roles.length > 0 &&
                /统计|计算|清点|多少|数量|覆盖率|重叠面积|核查|缓冲|裁切|相交|导出|筛选|叠加/u.test(options.prompt);
            const firstAnswer = assistantAnswer(response.finalResponse);
            const asks = /[？?]|请(?:提供|补充|确认|指定|选择|告诉)/u.test(firstAnswer);
            if ((needsSelectedWorkflow || needsMeasurement) && response?.finishReason === 'completed' &&
                    !attempted && (needsSelectedWorkflow || !asks) && !stop.aborted && !existsSync(operationPath) &&
                    !existsSync(join(directory, 'discovery')) && !existsSync(join(directory, 'input-issue.json'))) {
                event({ kind: 'workflow_retry', reason: needsSelectedWorkflow ? 'selected_workflow_without_tool' : 'spatial_request_without_tool' });
                response = await run('请调用 execute_workflow，按本轮图层引用和用户要求实际办理，取得作业回执后再回答。' +
                    context + '用户请求：' + options.prompt);
            }
            if (stop.aborted) {
                writeFileSync(join(directory, 'cancelled'), '');
                result = { status: deadline.aborted ? 'timed_out' : 'cancelled' };
            } else {
                if (!existsSync(budgetPath)) throw new Error('本轮模型预算未初始化');
                const issuePath = join(directory, 'input-issue.json');
                const receipt = existsSync(operationPath) ? readJson(operationPath) : null;
                const issue = existsSync(issuePath) ? readJson(issuePath) : null;
                const stopped = existsSync(budgetPath) && readJson(budgetPath).status === 'exhausted';
                const answer = assistantAnswer(response.finalResponse);
                const status = receipt?.status ?? (stopped ? 'budget_exhausted' : response.finishReason !== 'completed' ? 'failed' : issue?.status ??
                    (attempted ? 'no_result' : answer && (!needsSelectedWorkflow && (!needsMeasurement ||
                        /[？?]|请(?:提供|补充|确认|指定|选择|告诉)/u.test(answer))) ? 'answered'
                        : roles.length ? 'no_result' : 'missing_materials'));
                const terminal = response.events.findLast(fact => fact.type === 'turn/end');
                const failure = terminal?.data.reason.error;
                const candidatePath = join(directory, 'candidate.json');
                result = { status, finish_reason: response.finishReason,
                    response: status === 'succeeded' ? verifiedResponse(receipt, options.dialogue)
                        : receipt?.error ?? issue?.error ?? (stopped ? '模型预算已用完，已保存办理进度。'
                            : status === 'no_result' ? '本轮没有生成可核验的成果。可以继续让我重新办理。' : answer),
                    operation: receipt, ...(issue && !receipt ? { input_issue: issue } : {}),
                    ...(status === 'answered' && existsSync(candidatePath) ? { candidate: readJson(candidatePath) } : {}),
                    ...(failure ? { error: safeError(failure.message), error_code: failure.code } : {}) };
            }
        }
    } catch (error) {
        writeFileSync(join(directory, 'cancelled'), '');
        result = stop.aborted ? { status: deadline.aborted ? 'timed_out' : 'cancelled' }
            : { status: 'failed', error: safeError(error.message) };
    } finally {
        stopListener?.[Symbol.dispose]();
        try {
            if (boundService) {
                try { if (ran) await boundService.archive(id, directory); }
                finally { boundService.release(id); }
            }
            await owned?.close();
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
    const receipt = { ...result, id, directory, provider, model, workflow_attempted: attempted,
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
