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

const coordinateQuestion = prompt => /坐标|4490|四四九零|投影|epsg/iu.test(prompt);
const askingForProjection = prompt => /转换|转成|转为|投影到|投到|重投影/u.test(prompt);
const coordinateLabel = value => [value.name, value.code, value.kind, value.unit && '单位为' + value.unit].filter(Boolean).join('，');

function coordinateReply(layer, coordinate, projection) {
    const name = layer ? '「' + layer + '」' : '图层';
    const current = coordinate.kind === '未定义' ? name + '当前未定义坐标系。'
        : name + '当前采用' + coordinateLabel(coordinate) + '。';
    if (!projection) return current + '地图显示坐标系和图层坐标系可分别设置。';
    if (coordinate.kind === '未定义') return current + '请先确认并定义源图层的实际坐标系。';
    return current + '请告诉我目标投影坐标系的 EPSG 编号、中央经线或参照图层。';
}

function coordinateRead(receipt, prompt) {
    return coordinateQuestion(prompt) && receipt.steps?.length && receipt.steps.every(step =>
        ['data.describe', 'data.list_fields'].includes(step.operation));
}

function areaRequest(prompt) {
    return /(?:面积.{0,5}(?:之和|总和|合计|累计)|(?:总|合计|合并|汇总|累计)(?:面积)|面积(?:一共|共计))/u.test(prompt);
}

function requestedOutcomes(prompt) {
    const outcomes = [];
    const add = (label, operations) => outcomes.push({ label, operations });
    if (areaRequest(prompt) || /(?:多大面积|面积有多大|面积多少|(?:这块|这片|选中).{0,12}有多大)/u.test(prompt)) {
        add('面积', ['vector.area', 'control_check', 'annual.summarize']);
    }
    if (/(?:多少|几个|几条|几宗|几块|数量|个数).{0,4}(?:要素|图斑|地块|宗地)|(?:要素|图斑|地块|宗地).{0,6}(?:多少|数量|个数)/u.test(prompt) &&
            !/(?:重叠|相交|覆盖|范围内)/u.test(prompt)) {
        add('要素数量', ['vector_inventory', 'data.describe', 'data.select_by_attribute', 'data.select_by_location', 'data.selection_count']);
    }
    if (/(?:帮我|请|把|将|为.+)(?:.{0,16})(?:投影转换|转换.{0,16}(?:坐标系|EPSG)|投影.{0,8}EPSG)/iu.test(prompt)) {
        add('投影转换', ['crs.reproject', 'crs.transform', 'raster.reproject']);
    }
    if (/(?:帮我|请|把|将).{0,20}(?:裁剪|裁切).{0,24}(?:图层|范围)|(?:图层|范围).{0,20}(?:裁剪|裁切)/u.test(prompt)) {
        add('裁剪', ['vector.clip', 'raster.clip', 'delivery.clip_vector']);
    }
    if (/(?:帮我|请|把|将).{0,20}(?:缓冲|缓冲区)/u.test(prompt)) {
        add('缓冲区', ['vector.buffer']);
    }
    if (/(?:在地图上.{0,7}选出来|选出.{0,15}(?:图斑|要素|地块)|按属性选|按位置选)/u.test(prompt)) {
        add('地图选区', ['data.select_by_attribute', 'data.select_by_location']);
    }
    if (/(?:导出|另存为).{0,24}(?:CSV|属性表)/iu.test(prompt)) add('导出属性表', ['presentation.export_table']);
    return outcomes;
}

function filterLabel(filter) {
    if (!filter) return '';
    if (filter.conditions) return '(' + filter.conditions.map(filterLabel).join(filter.op === 'or' ? ' 或 ' : ' 且 ') + ')';
    if (filter.op === 'not') return '不满足 ' + filterLabel(filter.condition);
    if (filter.op === 'in') return filter.field + ' 属于 ' + filter.values.join('、');
    if (filter.op === 'between') return filter.field + ' 介于 ' + filter.values.join(' 至 ');
    const action = { eq: '=', ne: '≠', gt: '>', gte: '≥', lt: '<', lte: '≤', contains: '包含', like: '匹配',
        is_null: '为空', is_not_null: '不为空' }[filter.op];
    return [filter.field, action, filter.value_field ?? filter.value].filter(value => value != null).join(' ');
}

function verifiedResponse(receipt, dialogue, prompt) {
    const stats = receipt.statistics;
    const area = value => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 });
    const parts = [];
    const steps = receipt.steps || [];
    const projected = steps.findLast(step => step.statistics.projection)?.statistics.projection;
    const coordinate = steps.findLast(step => step.statistics.coordinate_system)?.statistics;
    if (projected) {
        return '已完成投影转换，生成一个结果图层，源图层保留。\n\n源坐标系为' + coordinateLabel(projected.source) +
            '。\n\n结果坐标系为' + coordinateLabel(projected.target) + '。';
    }
    if (coordinate && coordinateRead(receipt, prompt)) {
        return coordinateReply(coordinate.layer, coordinate.coordinate_system, askingForProjection(prompt));
    }
    if (stats.changed_parcels) parts.push(`更新涉及 ${stats.changed_parcels} 宗地。`);
    for (const item of receipt.steps ?? [{ statistics: stats }]) {
        const fact = item.statistics;
        if (fact.area_summary) {
            const result = fact.area_summary;
            const summary = result.count === 0 ? '没有找到符合条件的要素。' :
                result.area_m2 == null ? '匹配了 ' + result.count + ' 个要素，所选面积字段均为空。' :
                    `匹配 ${result.count} 个要素，面积合计 ${area(result.area_m2)} m²。` +
                    (result.count === result.valid ? '' : `其中 ${result.count - result.valid} 个要素没有面积值。`);
            const basis = result.field ? '面积字段 ' + result.field : '按几何计算';
            return summary + '\n\n来源：' + [result.layer, filterLabel(result.where), basis].filter(Boolean).join(' · ') + '。';
        }
        if (fact.lookup) {
            const { fields, rows, count, layer, filter } = fact.lookup;
            const label = field => /(?:^|_)M2$/i.test(field) ? field + '（平方米）' : field;
            if (!count) parts.push('没有找到匹配的要素。');
            else if (count === 1) parts.push(fields.map(field => label(field) + '为' + (rows[0][field] ?? '空值')).join('，') + '。');
            else {
                const labels = rows.map((row, index) => (index + 1) + '. ' +
                    fields.map(field => field + '为' + (row[field] ?? '空值')).join('，'));
                parts.push('共匹配 ' + count + ' 个要素' + (count > rows.length ? '，以下为前 ' + rows.length + ' 个' : '') + '。\n\n' +
                    labels.join('\n') + '\n\n');
            }
            if (layer) parts.push('\n\n来源：' + layer + (filter?.op === 'eq' ? ' · ' + filter.field + ' = ' + String(filter.value) : '') + '。');
        }
        if (fact.coverage_ratio !== undefined) {
            const counts = fact.relations || {};
            const findings = [['inside', '完全覆盖'], ['overlap', '部分重叠'], ['touch_line', '线接触'],
                ['touch_point', '点接触'], ['disjoint', '不相交']]
                .filter(([key]) => counts[key]).map(([key, label]) => `${label} ${counts[key]} 宗`);
            if (/(?:在|位于|超出).{0,15}(?:范围|红线|里面|以内)/u.test(prompt)) {
                parts.push(fact.coverage_ratio >= 1 - 1e-9 ? '待核查区全部位于参照范围内。' :
                    fact.covered_area_m2 > 0 ? '待核查区只有部分位于参照范围内。' :
                        counts.touch_line || counts.touch_point ? '待核查区与参照范围边界接触，面积上没有重叠。' :
                            '待核查区位于参照范围外。');
            }
            parts.push(`项目范围 ${area(fact.project_area_m2)} m²，重叠 ${area(fact.covered_area_m2)} m²，覆盖率 ${(fact.coverage_ratio * 100).toFixed(2)}%。`);
            if (findings.length) parts.push(findings.join('、') + '。');
        }
    }
    if (receipt.steps?.some(step => step.operation === 'map_raster.preview')) parts.push('影像预览已生成。');
    if (receipt.steps?.some(step => step.operation === 'crs.assign_from' || step.operation === 'crs.assign')) {
        parts.push('已为目标图层定义坐标系，原始坐标值保持不变。');
        if (coordinate) parts.push('图层当前采用' + coordinateLabel(coordinate.coordinate_system) + '。');
    }
    if (stats.area_m2 !== undefined) parts.push(`合并范围面积 ${area(stats.area_m2)} m²。`);
    if (steps.some(step => step.operation === 'geometry.create_dataset')) {
        parts.push(steps.some(step => step.operation.startsWith('geometry.append_'))
            ? '已在新建图层中添加要素。' : '已创建新图层。');
    } else if (steps.some(step => step.operation.startsWith('geometry.append_'))) {
        parts.push('已向指定图层追加要素。');
    } else if (steps.some(step => step.operation.startsWith('geometry.create_'))) {
        parts.push('已绘制要素并生成图层。');
    }
    if (stats.feature_count !== undefined) parts.push(`共 ${stats.feature_count} 个${prompt.includes('图斑') ? '图斑' : '要素'}。`);
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
    if (receipt.steps?.some(step => step.operation === 'presentation.map_layout')) parts.push('版面设置已提交。');
    if (receipt.steps?.some(step => step.operation === 'presentation.map_view')) parts.push('地图操作已提交。');
    const answer = parts.join('') || '已完成处理。';
    return dialogue ? answer.replaceAll(' m²', '平方米') : answer;
}

function deliveredResponse(receipt, options, answer, coordinateAnswer) {
    const verified = verifiedResponse(receipt, options.dialogue, options.prompt);
    const goals = requestedOutcomes(options.prompt);
    const straightforward = goals.length === 1 && ['要素数量', '地图选区'].includes(goals[0].label);
    if (coordinateAnswer || receipt.mode !== 'free' || areaRequest(options.prompt) || straightforward ||
            receipt.steps?.some(step => step.statistics?.area_summary || step.statistics?.lookup || step.statistics?.projection ||
                step.statistics?.coverage_ratio !== undefined) || !answer?.trim()) return verified;
    return answer.trim().replace(/\s*[（(]`?map-\d+`?[）)]/g, '');
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
    const images = options.images || [];
    const outcomes = options.skill ? [] : requestedOutcomes(options.prompt);
    const request = history.length || options.dialogue
        ? JSON.stringify({ prompt: options.prompt, history, dialogue: Boolean(options.dialogue) }) : options.prompt;
    const provenance = { provider, model, route_sha256: hash(JSON.stringify(modelConfig)),
        profile_sha256: hash(readFileSync(profile)), request_sha256: hash(images.length ? JSON.stringify([request, images.map(image => hash(image.data))]) : request),
        schema_sha256: hash(Buffer.concat(schema)), retrieval_version: '1.0.0' };
    const deadline = AbortSignal.timeout(timeoutMs);
    const stop = signal ? AbortSignal.any([signal, deadline]) : deadline;
    stop.throwIfAborted();
    mkdirSync(parent, { recursive: true });
    mkdirSync(directory);
    const materials = { ...options.materials };
    if (options.input && options.layer) materials.project = [{ file: resolve(options.input), layer: options.layer }];
    if (options.control && options.controlLayer) materials.control = [{ file: resolve(options.control), layer: options.controlLayer }];
    const imageRecords = images.map(({ name, mimeType, data }, index) => {
        const bytes = Buffer.from(data, 'base64');
        const artifact = 'input-image-' + (index + 1) + (mimeType === 'image/png' ? '.png' : '.jpg');
        writeFileSync(join(directory, artifact), bytes, { flag: 'wx' });
        return { name, mimeType, artifact, sha256: hash(bytes) };
    });
    const requestFile = join(directory, 'replay-input.json');
    const operationPath = join(directory, 'operation.json');
    const budgetPath = join(directory, 'budget.json');
    writeFileSync(requestFile, JSON.stringify({ materials, provenance,
        skill: options.skill ?? null, previous_run: options.previousRun ?? null, project_plan: options.projectPlan ?? null,
        control_plan: options.controlPlan ?? null, skill_parameters: options.skillParameters ?? null,
        image_inputs: imageRecords,
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
    let failureStep = 'prepare_workflow';
    try {
        const cancelled = join(directory, 'cancelled');
        stopListener = addAbortListener(stop, () => writeFileSync(cancelled, ''));
        let prepared = { key: null, materials };
        if (options.skill || Object.values(materials).some(group => group.length)) {
            const { stdout } = await exec(options.python, ['-I', '-B', join(source, 'src/guizhi/__main__.py'),
                '--workspace', workspace, '_dispatch-replay', requestFile], { windowsHide: true, maxBuffer: 4 << 20 });
            prepared = JSON.parse(stdout);
        }
        if (prepared.operation) {
            event({ kind: 'plan_reused', run_id: prepared.operation.run_id });
            writeFileSync(operationPath, JSON.stringify(prepared.operation));
            const status = prepared.operation.status === 'succeeded' && coordinateRead(prepared.operation, options.prompt)
                ? 'answered' : prepared.operation.status;
            result = { status, finish_reason: 'recorded_plan', plan_reused: true,
                operation: prepared.operation, response: prepared.operation.status === 'succeeded'
                    ? verifiedResponse(prepared.operation, options.dialogue, options.prompt) : prepared.operation.error };
        } else if (stop.aborted) {
            result = { status: deadline.aborted ? 'timed_out' : 'cancelled' };
        } else {
            failureStep = 'bind_model_runtime';
            stopProgress = watchRun(workspace, id, event);
            event({ kind: 'dispatch', id, provider, model });
            const service = getService ? await getService() :
                (owned = new (await import('./service.mjs')).DispatchService(options));
            await service.ready;
            stop.throwIfAborted();
            service.bind({ id, workspace, cancelled, materials: prepared.materials,
                skill: prepared.selected?.reference ?? null, parameters: options.skillParameters ?? null,
                plans: { project_plan: options.projectPlan, control_plan: options.controlPlan },
                previousRun: options.previousRun ?? null, provenance, planKey: prepared.key, mapContext: options.mapContext,
                outcomes,
                selectionRoles: Object.entries(options.layerLabels || {}).filter(([, layer]) => layer.selected).map(([role]) => role),
                flushRequest: join(directory, 'flush.request'), flushReceipt: join(directory, 'flush.receipt'),
                budget: { tokens, calls, checkpoint: budgetPath, operation: operationPath,
                    synthesize: !prepared.selected && !(outcomes.length === 1 && ['要素数量', '地图选区'].includes(outcomes[0].label)) } });
            boundService = service;
            event({ kind: 'runtime_bound', runtime_id: service.id });
            const { harness } = service;
            const roles = Object.keys(prepared.materials);
            const bound = options.skillParameters !== undefined ? '办理参数已在本机绑定，执行技能时省略 skill_parameters。' : '';
            const layers = Object.fromEntries(Object.entries(prepared.layers || {}).map(([role, items]) => [
                role, items.map(item => ({ ...item, ...options.layerLabels?.[role] })),
            ]));
            const selection = prepared.selected ? `已选技能和本轮材料摘要：${JSON.stringify(prepared.selected)}。`
                : `本轮材料图层清单（键为 material. 后的引用）：${JSON.stringify(layers)}。已选技能无。`;
            const visualContext = images.length ? '本轮用户消息有图片附件。图片内容问题直接看图回答。\n' : '';
            const maps = (options.mapContext?.layers || []).slice(0, 24).map(layer => ({
                id: layer.id, name: layer.name, geometry: layer.geometry, count: layer.count, crs: layer.crs,
                active: layer.active, visible: layer.visible, selected: layer.fids?.length || undefined,
                fields: layer.fields?.slice(0, 45),
            }));
            const context = `${visualContext}${selection}${bound}有已选历史成果：${Boolean(options.previousRun)}。当前地图摘要：${JSON.stringify(maps)}。\n` +
                (outcomes.length ? '本轮要提供的结果：' + outcomes.map(item => item.label).join('、') + '。\n' : '');
            const conversation = history.length ? '最近对话记录（每项为用户提问与智能体答复）：' + JSON.stringify(history) + '\n' : '';
            const speech = options.dialogue ? '本轮使用语音输入，用简洁口语回答。\n' : '';
            const run = (prompt, includeImages = false) => harness.run(includeImages && images.length
                ? [{ type: 'text', text: prompt }, ...images] : prompt, {
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
                            'mcp__guizhi__update_approval', 'mcp__guizhi__sum_layer_area'].includes(item.tool)) attempted = true;
                    }
                    if (fact.type === 'assistant/message' && fact.data.usage) {
                        item.usage = fact.data.usage;
                        usage.set(fact.seq, fact.data.usage);
                    }
                    event(item);
                },
            });
            failureStep = 'run_model';
            let response = await run(context + conversation + speech + '用户请求：' + options.prompt, true);
            ran = true;
            const needsSelectedWorkflow = options.skill && roles.length > 0 && /^请办理/u.test(options.prompt.trim());
            const needsMeasurement = outcomes.length > 0 || ((roles.length > 0 || maps.length > 0) &&
                /统计|计算|清点|多少|数量|覆盖率|重叠面积|核查|缓冲|裁切|相交|导出|筛选|叠加/u.test(options.prompt));
            const firstAnswer = assistantAnswer(response.finalResponse);
            const asks = /[？?]|请(?:提供|补充|确认|指定|选择|告诉)/u.test(firstAnswer);
            if ((needsSelectedWorkflow || needsMeasurement) && response?.finishReason === 'completed' &&
                    (!attempted || outcomes.length > 0) && (needsSelectedWorkflow || !asks) && !stop.aborted &&
                    !existsSync(operationPath) && !existsSync(join(directory, 'operation.claim')) &&
                    (!existsSync(join(directory, 'discovery')) || outcomes.length > 0) &&
                    !existsSync(join(directory, 'input-issue.json'))) {
                event({ kind: 'workflow_retry', reason: needsSelectedWorkflow ? 'selected_workflow_without_tool' : 'spatial_request_without_tool' });
                response = await run('请调用 execute_workflow，按本轮图层引用和用户要求实际办理，取得作业回执后再回答。' +
                    context + '用户请求：' + options.prompt);
            }
            if (stop.aborted) {
                writeFileSync(join(directory, 'cancelled'), '');
                result = { status: deadline.aborted ? 'timed_out' : 'cancelled' };
            } else {
                failureStep = 'read_dispatch_result';
                if (!existsSync(budgetPath)) throw new Error('本轮模型预算未初始化');
                const issuePath = join(directory, 'input-issue.json');
                const receipt = existsSync(operationPath) ? readJson(operationPath) : null;
                const issue = existsSync(issuePath) ? readJson(issuePath) : null;
                const stopped = existsSync(budgetPath) && readJson(budgetPath).status === 'exhausted';
                let answer = assistantAnswer(response.finalResponse);
                const coordinatePath = join(directory, 'coordinate-lookup.json');
                if (!receipt && existsSync(coordinatePath) && coordinateQuestion(options.prompt)) {
                    const rows = Object.values(readJson(coordinatePath));
                    const missing = rows.find(row => row.status === 'target_required');
                    const requested = rows.length === 1 ? rows[0] : missing;
                    if (requested && (!askingForProjection(options.prompt) || missing || /EPSG\s*[:：]?\s*\d{4,5}/iu.test(options.prompt) === false)) {
                        answer = coordinateReply(requested.layer, requested.coordinate_system, askingForProjection(options.prompt));
                    }
                }
                const coordinateAnswer = receipt?.status === 'succeeded' && coordinateRead(receipt, options.prompt);
                const status = (coordinateAnswer ? 'answered' : receipt?.status) ?? (stopped ? 'budget_exhausted' : response.finishReason !== 'completed' ? 'failed' : issue?.status ??
                    (attempted ? 'no_result' : answer && (!needsSelectedWorkflow && (!needsMeasurement ||
                        /[？?]|请(?:提供|补充|确认|指定|选择|告诉)/u.test(answer))) ? 'answered'
                        : roles.length ? 'no_result' : 'missing_materials'));
                const terminal = response.events.findLast(fact => fact.type === 'turn/end');
                const failure = terminal?.data.reason.error;
                const candidatePath = join(directory, 'candidate.json');
                result = { status, finish_reason: response.finishReason,
                    response: status === 'succeeded' || coordinateAnswer
                        ? deliveredResponse(receipt, options, answer, coordinateAnswer)
                        : receipt?.error ?? issue?.error ?? (stopped ? '模型预算已用完，已保存办理进度。'
                            : status === 'no_result' ? '本轮没有生成可核验的成果。可以继续让我重新办理。' : answer),
                    operation: receipt, ...(issue && !receipt ? { input_issue: issue } : {}),
                    ...(status === 'answered' && existsSync(candidatePath) ? { candidate: readJson(candidatePath) } : {}),
                    ...(failure ? { error: safeError(failure.message), error_code: failure.code, failureStep: 'run_model' } : {}) };
            }
        }
    } catch (error) {
        writeFileSync(join(directory, 'cancelled'), '');
        result = stop.aborted ? { status: deadline.aborted ? 'timed_out' : 'cancelled' }
            : { status: 'failed', error: safeError(error.message), error_code: error.code };
    } finally {
        stopListener?.[Symbol.dispose]();
        try {
            if (boundService) {
                try { if (ran) await boundService.archive(id, directory); }
                finally { boundService.release(id); }
            }
            await owned?.close();
        } catch (error) {
            if (!result.error && !result.operation?.error) failureStep = 'archive_dispatch';
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
    const receipt = { failureStep, ...result, id, directory, provider, model, workflow_attempted: attempted,
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
