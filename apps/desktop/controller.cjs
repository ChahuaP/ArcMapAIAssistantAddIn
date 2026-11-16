const { BusinessLog, describeMaterials, operator, statusLabel } = require('./business-log.cjs');
const { publishReport } = require('./report-export.cjs');
const { execFile } = require('node:child_process');
const { createHash, randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { promisify } = require('node:util');
const { AgentProcess } = require('./agent-process.cjs');
const { materialBindings } = require('./materials.cjs');
const { MaterialReader } = require('./material-reader.cjs');
const { visualRecipe } = require('./visual-recipes.cjs');
const { modelConfig, modelKey, credentialScope, normalizeKey, route, modelFailure, modelFailureCode } = require('../../src/guizhi/infrastructure/dsh/model-config.cjs');

const exec = promisify(execFile);
const cli = path.join(__dirname, '../../src/guizhi/__main__.py');

function load(file, initial) {
    return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : initial;
}

function save(file, value) {
    if (fs.existsSync(file) && fs.lstatSync(file).isSymbolicLink()) throw new Error('记录路径不能指向其他位置');
    const pending = file + '.' + randomUUID() + '.pending';
    fs.writeFileSync(pending, JSON.stringify(value, null, 2), { flag: 'wx' });
    fs.renameSync(pending, file);
}

function checkedArtifact(record, step, parse = false) {
    const item = record.result?.evidence.find(ref => ref.step === step);
    if (!item) return null;
    const root = fs.realpathSync(record.directory);
    const file = fs.realpathSync(path.resolve(root, item.path));
    const relative = path.relative(root, file);
    if (path.isAbsolute(relative) || relative.split(path.sep)[0] === '..') throw new Error('成果路径无效');
    const bytes = fs.readFileSync(file);
    if (createHash('sha256').update(bytes).digest('hex') !== item.sha256) throw new Error('成果内容已变更');
    return parse ? JSON.parse(bytes) : { file, bytes };
}

class DesktopController {
    constructor({ userData, runtime, dsh, safeStorage, externalPath, onChange, bundledArcmap, installationRoot, diagnostics }) {
        this.file = path.join(userData, 'settings.json');
        this.historyFile = path.join(userData, 'desktop-conversation.json');
        fs.mkdirSync(userData, { recursive: true });
        this.settings = load(this.file, { workspace: '' });
        this.runtime = { python: path.join(runtime, 'python', 'python.exe'),
            node: path.join(runtime, 'node', 'node.exe'), runtime: dsh || path.join(runtime, 'dsh') };
        this.reader = new MaterialReader(this.runtime.python, cli);
        this.diagnostics = diagnostics;
        this.storage = safeStorage;
        this.bundledArcmap = bundledArcmap;
        this.installationRoot = installationRoot;
        this.externalPath = externalPath;
        this.onChange = onChange;
        this.tasks = [];
        this.active = null;
        this.restore();
    }

    removeLegacyHistory() {
        fs.rmSync(path.join(this.settings.workspace, 'desktop-tasks.json'), { force: true });
    }

    restore() {
        if (!this.settings.workspace) return;
        this.settings.workspace = this.workspacePath(this.settings.workspace);
        this.tasks = load(this.historyFile, []);
        this.removeLegacyHistory();
        let changed = false;
        for (const task of this.tasks) {
            if (['running', 'cancelling'].includes(task.status)) {
                task.status = 'interrupted';
                delete task.visual;
                changed = true;
            }
        }
        if (changed) this.persist();
    }

    persist(tasks = this.tasks) {
        save(this.historyFile, tasks);
    }

    workspacePath(file) {
        const folder = this.externalPath(file);
        if (this.installationRoot) {
            const inside = (root, child) => {
                const relative = path.relative(root, child);
                return !path.isAbsolute(relative) && relative.split(path.sep)[0] !== '..';
            };
            if (inside(this.installationRoot, folder) || inside(folder, this.installationRoot)) {
                throw new Error('请选择安装目录之外的成果保存位置');
            }
        }
        return folder;
    }

    state() {
        const { workspace, modelConfig, showOperation, screenHighlight, carrier, encryptedKey, modelVision } = this.settings;
        return { settings: { workspace, modelConfig, showOperation, screenHighlight,
            operator: this.settings.operator || '', carrier: carrier || 'none', modelVision: Boolean(modelVision), keyConfigured: Boolean(modelConfig && encryptedKey) },
            tasks: this.tasks, busy: Boolean(this.active) };
    }

    workspaceReady(value) {
        const workspace = this.workspacePath(value);
        fs.mkdirSync(workspace, { recursive: true });
        const probe = path.join(workspace, '.guizhi-write-' + randomUUID());
        fs.writeFileSync(probe, '', { flag: 'wx' });
        fs.unlinkSync(probe);
        return workspace;
    }

    selectModel(input, requireModel = true) {
        const config = modelConfig(input.modelConfig, requireModel);
        const same = this.settings.modelConfig && credentialScope(config) === credentialScope(this.settings.modelConfig);
        const encrypted = same ? this.settings.encryptedKey : undefined;
        const key = normalizeKey(input.key) || (encrypted
            ? this.storage.decryptString(Buffer.from(encrypted, 'base64')) : '');
        return { config, key: requireModel || config.provider !== 'custom' ? modelKey(config, key) : key, encrypted };
    }

    modelService(config, key) {
        const identity = JSON.stringify(route(config));
        if (this.service?.identity === identity && this.service.key === key) return this.service.ready;
        const prior = this.service;
        const entry = { identity, key };
        entry.ready = (async () => {
            await prior?.ready.then(service => service.close(), () => {});
            const service = new AgentProcess({ ...this.runtime, modelConfig: config, apiKey: key });
            void service.exited.then(() => { if (this.service === entry) this.service = undefined; });
            try { await service.ready; }
            catch (error) { await service.close(); throw error; }
            return service;
        })();
        this.service = entry;
        void entry.ready.catch(() => { if (this.service === entry) this.service = undefined; });
        return entry.ready;
    }

    warmModel() {
        if (!this.settings.modelConfig) return;
        try {
            const { config, key } = this.selectModel({ modelConfig: this.settings.modelConfig });
            void this.modelService(config, key).catch(error => console.error('DSH startup:', modelFailure(error.message) || '模型调度服务启动失败'));
        } catch (error) { console.error('DSH configuration:', modelFailure(error.message) || '模型服务配置不可用'); }
    }

    configure(input) {
        if (this.active || this.modelTesting || this.skillBusy || this.hostBusy) throw new Error('请先结束当前任务');
        const name = typeof input.operator === 'string' ? input.operator.trim() : '';
        if (!this.settings.workspace && !name) throw new Error('请输入姓名');
        if (name.length > 60 || /[\r\n]/.test(name)) throw new Error('姓名最多填写 60 个字');
        const { config, key, encrypted } = input.modelConfig ? this.selectModel(input) : {};
        const workspace = this.workspaceReady(input.workspace);
        let encryptedKey = encrypted;
        if (config && normalizeKey(input.key)) {
            if (!this.storage.isEncryptionAvailable()) throw new Error('系统凭据加密不可用');
            encryptedKey = this.storage.encryptString(key).toString('base64');
        }
        const settings = { workspace, operator: name || this.settings.operator, modelConfig: config, showOperation: input.showOperation !== false,
            screenHighlight: input.screenHighlight !== false, carrier: this.settings.carrier, modelVision: input.modelVision === true, encryptedKey };
        save(this.file, settings);
        const changedWorkspace = workspace !== this.settings.workspace;
        this.settings = settings;
        this.warmModel();
        if (changedWorkspace) {
            this.resetConversation();
            this.removeLegacyHistory();
        }
        return this.state();
    }

    async python(command, ...args) {
        try {
            const result = await exec(this.runtime.python, ['-I', '-B', cli, '--workspace',
                this.settings.workspace, command, ...args], {
                windowsHide: true, timeout: command === 'map' ? 600_000 : ['skill-trial', 'skill-publish', 'material-intake', 'crs-solve', 'crs-define',
                    'arcgis-capture', 'arcgis-revision'].includes(command) ? 240000 : command === 'arcgis-stage' ? 95000 : 30000,
                maxBuffer: 8 << 20 });
            return JSON.parse(result.stdout);
        } catch (error) {
            let message;
            try {
                message = JSON.parse(error.stderr || error.stdout).error;
                if (message?.startsWith('{')) message = JSON.parse(message).error;
            } catch { /* Process failed before producing a record. */ }
            throw new Error(message?.replace(/^(ValueError|ValidationError|Fault): /, '').trim() || '无法读取材料或成果');
        }
    }

    async describe(file) {
        return this.python('material-describe', file);
    }

    async request(command, payload, ...prefix) {
        if (!this.settings.workspace) throw new Error('请先选择成果保存位置');
        const file = path.join(this.settings.workspace, 'request-' + randomUUID() + '.json');
        try {
            if (typeof payload === 'string') fs.writeFileSync(file, payload, { flag: 'wx' });
            else save(file, payload);
            return await this.python(command, ...prefix, file);
        } finally { fs.rmSync(file, { force: true }); }
    }

    async intake(paths, kind) {
        if (!this.settings.workspace) throw new Error('请先选择成果保存位置');
        if (!Array.isArray(paths) || !paths.length || paths.length > 32) throw new Error('一次请选择 1 至 32 份材料');
        const files = paths.map(this.externalPath);
        const at = performance.now();
        try { return await this.reader.read(this.settings.workspace, files, kind); }
        catch (error) {
            if (error.message !== '已停止读取材料') this.diagnostics?.record({ stage: 'material_read', status: 'failed',
                code: error.message.includes('超时') ? 'timeout' : 'reader_failure', files, elapsedMs: performance.now() - at });
            throw error;
        }
    }

    selectCarrier(carrier) {
        const settings = { ...this.settings, carrier };
        save(this.file, settings);
        this.settings = settings;
    }


    submit({ prompt, project, control, skill, materials = {}, skillParameters, parameterText, mentions, images = [], imageBlocks = [], dialogue = false }) {
        if (!this.settings.workspace) throw new Error('请先选择成果保存位置');
        if (this.active || this.modelTesting) throw new Error('已有任务在运行');
        if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 4000) throw new Error('请输入办理事项');
        let bindings = materialBindings({ ...materials, ...(project ? { project: [project] } : {}),
            ...(control ? { control: [control] } : {}) });
        const suppliedMaterials = Object.keys(bindings).length > 0;
        if (!suppliedMaterials && !skill) {
            const previous = this.tasks[0];
            if (previous) {
                ({ project, control, materials } = previous);
                bindings = materialBindings({ ...materials, ...(project ? { project: [project] } : {}),
                    ...(control ? { control: [control] } : {}) });
            }
        }
        let layerLabels;
        if (!skill && bindings.inputs) {
            const { inputs, ...named } = bindings;
            layerLabels = Object.fromEntries((materials.inputs || []).map((item, index) => ['layer-' + (index + 1), {
                name: item.name || item.layer, layer: item.layer, ...(item.mapSelection ? { selected: item.mapSelection.fids.length } : {}),
            }]));
            bindings = { ...named, ...Object.fromEntries(inputs.map((item, index) => ['layer-' + (index + 1), [item]])) };
        }
        const history = [];
        let previousRun;
        for (const prior of this.tasks.slice(0, 4)) {
            if (prior.status === 'succeeded') previousRun ??= prior.runId;
            if (prior.response && ['answered', 'succeeded', 'missing_materials', 'no_result'].includes(prior.status)) {
                history.unshift([prior.prompt.slice(0, 600), prior.response.slice(0, 600)]);
            }
        }
        if (skill && !/^[a-z][a-z0-9_-]{0,39}@[1-9]\d*$/.test(skill)) throw new Error('请选择已发布的技能');
        if (skillParameters && Buffer.byteLength(JSON.stringify(skillParameters)) > 65536) throw new Error('办理参数最多 64 KB');
        const { config, key } = this.selectModel({ modelConfig: this.settings.modelConfig });
        const task = { id: randomUUID(), title: [...prompt.trim()].slice(0, 32).join(''), prompt: prompt.trim(),
            project, control, skill, materials, parameterText, mentions, images, dialogue, operator: operator(this.settings), status: 'running', createdAt: new Date().toISOString() };
        const next = [task, ...this.tasks];
        this.persist(next);
        this.tasks = next;
        const stop = new AbortController();
        const active = { stop, task, config, files: Object.values(bindings).flat().map(item => item.file) };
        this.active = active;
        const options = { workspace: this.settings.workspace, modelConfig: config, apiKey: key,
            prompt: task.prompt, skill, skillParameters, materials: bindings, layerLabels, previousRun, history, dialogue, images: imageBlocks,
            projectPlan: project?.plan || materials.project?.[0]?.plan,
            controlPlan: control?.plan || materials.control?.[0]?.plan };
        active.done = this.dispatch(options, { signal: stop.signal, onEvent: event => {
            if (task.status !== 'running') return;
            const details = event.details;
            const label = details?.role && layerLabels?.[details.role]?.name;
            const visual = visualRecipe(label ? { ...event, details: { ...details, layer: label } } : event);
            if (visual) {
                task.visual = { ...visual, operation: details?.operation, childRun: details?.child_run_id,
                    phase: event.phase, targets: (details?.layers || []).map(name =>
                        Object.values(layerLabels || {}).find(label => label.layer === name)?.name || name) };
                const progress = task.progress || [];
                if (progress.at(-1)?.label !== visual.label || progress.at(-1)?.target !== visual.target ||
                        progress.at(-1)?.childRun !== task.visual.childRun) {
                    task.progress = [...progress.slice(-63), task.visual];
                }
                this.onChange(this.state());
            }
        } }).then(receipt => this.complete(task, receipt), error => this.complete(task, {
            status: stop.signal.aborted ? 'cancelled' : 'failed', error: error.message,
        }));
        try { if (suppliedMaterials) this.onStarted?.(task); }
        catch (error) { console.error('ArcGIS display:', error.message); }
        return this.state();
    }

    async dispatch(options, callbacks) {
        const { modelConfig, apiKey, ...request } = options;
        const service = await this.modelService(modelConfig, apiKey);
        return service.run(request, callbacks);
    }

    resetConversation() {
        this.persist([]);
        this.tasks = [];
        this.cancel();
        return this.state();
    }

    async coordinateCatalog() {
        return this.settings.workspace ? this.python('crs-list') : [];
    }

    async skillRequest(command, markdown, payload) {
        if (!this.settings.workspace || this.skillBusy || this.active) throw new Error('请先结束当前操作');
        if (typeof markdown !== 'string' || Buffer.byteLength(markdown) > 65536) throw new Error('技能文件最多 64 KB');
        this.skillBusy = true;
        try {
            return await this.request(command, command === 'skill-preview' ? markdown : { text: markdown, ...payload });
        } finally { this.skillBusy = false; }
    }

    async importCoordinate(file, layer) {
        if (this.active) throw new Error('请先结束当前任务');
        return this.python('crs-define', this.externalPath(file), ...(layer ? ['--layer', layer] : []));
    }

    async transformMaterial(input, plan) {
        const file = this.externalPath(input.file);
        const [material] = materialBindings({ input: [{ ...input, file }] }).input;
        const run = await this.request('execute', { operation: 'crs.transform', inputs: [material], parameters: { plan } });
        return checkedArtifact(run, 'export').file;
    }

    async complete(task, receipt) {
        if (this.active?.task !== task || this.active.settling) return;
        const active = this.active;
        active.settling = true;
        let status = receipt.status;
        let stage = 'dispatch';
        let record;
        try {
            task.runId = receipt.operation?.run_id;
            task.response = ['succeeded', 'answered'].includes(status) ? receipt.response
                : receipt.operation?.error || (status === 'failed' && modelFailure(receipt.error || receipt.response)) ||
                    receipt.error || receipt.response;
            task.statistics = receipt.operation?.statistics;
            task.candidate = receipt.candidate?.markdown;
            if (status === 'succeeded' && this.tasks.includes(task)) {
                record = await this.python('inspect', task.runId);
                if (record.status !== 'succeeded') throw new Error('成果未发布');
                const map = checkedArtifact(record, 'map');
                if (map) task.map = JSON.parse(map.bytes);
                const layout = checkedArtifact(record, 'layout');
                if (layout) task.layout = JSON.parse(layout.bytes);
                const actions = checkedArtifact(record, 'map_actions');
                if (actions) task.mapActions = JSON.parse(actions.bytes);
                task.coordinateRuns = (record.result.workflow?.steps || []).filter(item =>
                    ['crs.assign', 'crs.assign_from'].includes(item.operation)).map(item => item.run_id);
                const lastOperation = record.result.workflow?.steps?.at(-1)?.operation;
                task.mapLayers = record.result.evidence
                    .filter(item => ['export', 'boundaries'].includes(item.step) && path.extname(item.path).toLowerCase() === '.gpkg' &&
                        !(item.step === 'export' && ['crs.assign', 'crs.assign_from'].includes(lastOperation)))
                    .map(item => ({ run: task.runId, artifact: item.step }));
                task.outputImages = record.result.evidence.filter(item => item.step === 'raster_preview')
                    .map(item => ({ run: task.runId, artifact: item.step }));
                const findings = checkedArtifact(record, 'case');
                if (findings) task.approval = JSON.parse(findings.bytes);
                const definition = record.result.workflow?.skill;
                const report = checkedArtifact(record, 'report', true);
                if (definition || task.skill || report) {
                    const groups = { ...task.materials, ...(task.project && { project: [task.project] }),
                        ...(task.control && { control: [task.control] }) };
                    task.business = await this.recordBusiness({ id: task.id, startedAt: task.createdAt, operator: task.operator,
                        materials: describeMaterials(groups, definition), record, report, response: task.response });
                    task.hasReport = Boolean(task.business.reportDigest);
                }
            }
        } catch {
            status = 'failed';
            stage = 'publish';
            task.response = '办理成果已生成，但工作台读取失败。请到成果保存位置查看本轮作业记录。';
        } finally {
            const code = stage === 'publish' ? 'result_read_failed' : {
                failed: receipt.operation ? 'operation_failed' : modelFailureCode(receipt.error || receipt.response) || 'agent_failure',
                interrupted: 'interrupted', timed_out: 'timeout', budget_exhausted: 'budget_exhausted',
                no_result: receipt.workflow_attempted === false ? 'tool_not_called' : 'no_result',
            }[status];
            if (code && task.status !== 'cancelling') task.diagnosticId = this.diagnostics?.record({ stage, status, code,
                taskId: task.id, dispatchId: receipt.id, runId: task.runId, elapsedMs: receipt.elapsed_ms,
                config: active.config, skill: task.skill, files: active.files });
            task.status = status;
            delete task.visual;
            const current = this.tasks.includes(task);
            if (current && !['succeeded', 'answered'].includes(status) && task.skill) {
                try {
                    const definition = await this.python('skill-show', task.skill);
                    await this.recordBusiness({ id: task.id, startedAt: task.createdAt, operator: task.operator,
                        materials: describeMaterials(task.materials, definition), type: definition.title, status,
                        response: task.response });
                } catch (error) { console.error('业务日志写入失败：', error.message); }
            }
            this.active = null;
            if (this.tasks.includes(task)) this.persist();
            this.onChange(this.state());
            if (current && status === 'succeeded') {
                try { this.onCompleted?.(task, this.tasks[1]?.id); }
                catch (error) { console.error('ArcGIS publication:', error.message); }
            }
        }
    }

    cancel() {
        const active = this.active;
        if (active && !active.settling && !active.stop.signal.aborted) {
            active.task.status = 'cancelling';
            if (this.tasks.includes(active.task)) this.persist();
            active.stop.abort();
        }
        return this.state();
    }

    businessLog() {
        if (!this.settings.workspace) throw new Error('请先选择成果保存位置');
        return new BusinessLog(this.settings.workspace, load, save);
    }

    async recordBusiness({ id, startedAt, operator: name, materials, record, report, type, status = 'succeeded', response }) {
        const log = this.businessLog();
        const item = { id, startedAt, finishedAt: new Date().toISOString(), operator: name, materials,
            type: type || record?.result.workflow?.skill?.title || report?.title || '空间分析',
            status, statusLabel: statusLabel(status), response, runId: record?.id,
            statistics: record?.result.statistics };
        if (report) item.reportDigest = await publishReport(record.directory, item, report);
        log.add(item);
        return item;
    }

    async mapBusiness({ id, startedAt, materials, output, type }) {
        const record = await this.python('inspect', output.run);
        const definition = record.result.workflow?.skill;
        const report = checkedArtifact(record, 'report', true);
        return this.recordBusiness({ id, startedAt, operator: operator(this.settings),
            materials: describeMaterials(materials, definition), type, record, report });
    }

    reportPage(id, page) {
        const item = this.tasks.find(task => task.id === id)?.business || this.businessLog().get(id);
        if (!item?.reportDigest || !Number.isInteger(page)) throw new Error('报告尚未生成');
        return this.python('_report-page', item.runId, item.reportDigest, String(page));
    }

    async approvalSource(id, claimId, evidenceIndex) {
        const task = this.tasks.find(item => item.id === id && item.status === 'succeeded');
        const record = task && await this.python('inspect', task.runId);
        const artifact = record && checkedArtifact(record, 'case');
        if (!artifact) throw new Error('本任务没有审批依据');
        const claim = JSON.parse(artifact.bytes).claims.find(item => item.id === claimId);
        const ref = claim?.evidence[evidenceIndex];
        if (!ref || !/^document-\d+$/.test(ref.artifact)) throw new Error('此依据请在地图或成果中查看');
        const source = checkedArtifact(await this.python('inspect', ref.run), ref.artifact);
        if (!source || createHash('sha256').update(source.bytes).digest('hex') !== ref.sha256) {
            throw new Error('依据文件内容已变更');
        }
        return source.file;
    }

    async close() {
        this.reader.close();
        const active = this.active;
        this.cancel();
        this.modelTesting?.stop.abort();
        try { await Promise.allSettled([active?.done, this.modelTesting?.done]); }
        finally { await this.service?.ready.then(service => service.close(), () => {}); }
    }
}

module.exports = { DesktopController };
