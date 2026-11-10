const { execFile } = require('node:child_process');
const { createHash, randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { promisify } = require('node:util');
const { startAgent } = require('./agent-process.cjs');
const { materialBindings } = require('./materials.cjs');
const { visualRecipe } = require('./visual-recipes.cjs');
const { modelConfig, modelKey, credentialScope, normalizeKey } = require('../../src/guizhi/infrastructure/dsh/model-config.cjs');

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

function checkedArtifact(record, step) {
    const item = record.result?.evidence.find(ref => ref.step === step);
    if (!item) return null;
    const root = fs.realpathSync(record.directory);
    const file = fs.realpathSync(path.resolve(root, item.path));
    const relative = path.relative(root, file);
    if (path.isAbsolute(relative) || relative.split(path.sep)[0] === '..') throw new Error('成果路径无效');
    const bytes = fs.readFileSync(file);
    if (createHash('sha256').update(bytes).digest('hex') !== item.sha256) throw new Error('成果内容已变更');
    return { file, bytes };
}

class DesktopController {
    constructor({ userData, runtime, dsh, safeStorage, externalPath, onChange, bundledArcmap, installationRoot }) {
        this.file = path.join(userData, 'settings.json');
        this.historyFile = path.join(userData, 'desktop-conversation.json');
        fs.mkdirSync(userData, { recursive: true });
        this.settings = load(this.file, { workspace: '' });
        this.runtime = { python: path.join(runtime, 'python', 'python.exe'),
            node: path.join(runtime, 'node', 'node.exe'), runtime: dsh || path.join(runtime, 'dsh') };
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
        const { workspace, modelConfig, showOperation, screenHighlight, carrier, encryptedKey } = this.settings;
        return { settings: { workspace, modelConfig, showOperation, screenHighlight,
            carrier: carrier || 'none', keyConfigured: Boolean(modelConfig && encryptedKey) },
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
        return { config, key: modelKey(config, key), encrypted };
    }

    configure(input) {
        if (this.active || this.skillBusy || this.hostBusy) throw new Error('请先结束当前任务');
        const { config, key, encrypted } = this.selectModel(input);
        const workspace = this.workspaceReady(input.workspace);
        let encryptedKey = encrypted;
        if (normalizeKey(input.key)) {
            if (!this.storage.isEncryptionAvailable()) throw new Error('系统凭据加密不可用');
            encryptedKey = this.storage.encryptString(key).toString('base64');
        }
        const settings = { workspace, modelConfig: config, showOperation: input.showOperation !== false,
            screenHighlight: input.screenHighlight !== false, carrier: this.settings.carrier, encryptedKey };
        save(this.file, settings);
        const changedWorkspace = workspace !== this.settings.workspace;
        this.settings = settings;
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
                windowsHide: true, timeout: ['skill-trial', 'skill-publish', 'material-intake', 'crs-solve', 'crs-define',
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

    intake(paths, kind) {
        if (!Array.isArray(paths) || !paths.length || paths.length > 32) throw new Error('一次请选择 1 至 32 份材料');
        return this.request('material-intake', { paths: paths.map(this.externalPath), kind });
    }

    selectCarrier(carrier) {
        const settings = { ...this.settings, carrier };
        save(this.file, settings);
        this.settings = settings;
    }


    submit({ prompt, project, control, skill, materials = {}, skillParameters, parameterText, dialogue = false }) {
        if (!this.settings.workspace) throw new Error('请先选择成果保存位置');
        if (this.active) throw new Error('已有任务在运行');
        if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 4000) throw new Error('请输入办理事项');
        const bindings = materialBindings({ ...materials, ...(project ? { project: [project] } : {}),
            ...(control ? { control: [control] } : {}) });
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
            project, control, skill, materials, parameterText, status: 'running', createdAt: new Date().toISOString() };
        const next = [task, ...this.tasks];
        this.persist(next);
        this.tasks = next;
        const child = startAgent(this.runtime, this.settings.workspace);
        this.active = { child, task };
        child.on('message', message => {
            if (message.type === 'event') {
                if (task.status !== 'running') return;
                const visual = visualRecipe(message.event);
                if (visual) {
                    task.visual = visual;
                    task.step = visual.label;
                    this.onChange(this.state());
                }
            } else if (message.type === 'result') {
                void this.complete(task, message.result);
            }
        });
        child.once('error', () => void this.complete(task, { status: 'failed' }));
        child.once('exit', () => {
            if (!this.active?.settling && this.active?.task === task) {
                void this.complete(task, { status: 'interrupted' });
            }
        });
        child.send({ type: 'start', options: { ...this.runtime, workspace: this.settings.workspace,
            modelConfig: config, apiKey: key,
            prompt: task.prompt, skill, skillParameters, materials: bindings, previousRun, history, dialogue,
            projectPlan: project?.plan || materials.project?.[0]?.plan,
            controlPlan: control?.plan || materials.control?.[0]?.plan } });
        try { this.onStarted?.(task); }
        catch (error) { console.error('ArcGIS display:', error.message); }
        return this.state();
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
        this.active.settling = true;
        let status = receipt.status;
        try {
            task.runId = receipt.operation?.run_id;
            task.response = ['succeeded', 'answered'].includes(status) ? receipt.response
                : receipt.operation?.error || receipt.error || receipt.response;
            task.statistics = receipt.operation?.statistics;
            task.candidate = receipt.candidate?.markdown;
            if (status === 'succeeded' && this.tasks.includes(task)) {
                const record = await this.python('inspect', task.runId);
                if (record.status !== 'succeeded') throw new Error('成果未发布');
                const map = checkedArtifact(record, 'map');
                if (map) task.map = JSON.parse(map.bytes);
                task.hasReport = Boolean(checkedArtifact(record, 'report'));
                const findings = checkedArtifact(record, 'case');
                if (findings) task.approval = JSON.parse(findings.bytes);
                const deliveries = record.result.evidence.filter(item => ['export', 'table', 'workflow', 'package', 'boundaries'].includes(item.step));
                task.deliveries = deliveries.map(item => item.step);
                task.deliveryFormats = Object.fromEntries(deliveries.map(item => [item.step, path.extname(item.path).toLowerCase()]));
            }
        } catch {
            status = 'failed';
        } finally {
            task.status = status;
            delete task.visual;
            this.active = null;
            const current = this.tasks.includes(task);
            if (current) this.persist();
            this.onChange(this.state());
            if (current && status === 'succeeded') {
                try { this.onCompleted?.(task, this.tasks[1]?.id); }
                catch (error) { console.error('ArcGIS publication:', error.message); }
            }
        }
    }

    cancel() {
        const active = this.active;
        if (active?.child.connected && !active.settling && active.task.status !== 'cancelling') {
            active.task.status = 'cancelling';
            if (this.tasks.includes(active.task)) this.persist();
            active.child.send({ type: 'cancel' }, error => { if (error) console.error('Agent cancellation:', error.message); });
        }
        return this.state();
    }

    async report(id) {
        const task = this.tasks.find(item => item.id === id);
        if (!task?.hasReport || task.status !== 'succeeded') throw new Error('报告尚未生成');
        const record = await this.python('inspect', task.runId);
        return checkedArtifact(record, 'report').file;
    }

    async delivery(id, kind) {
        const task = this.tasks.find(item => item.id === id);
        if (!task?.deliveries?.includes(kind) || task.status !== 'succeeded') throw new Error('成果尚未发布');
        return checkedArtifact(await this.python('inspect', task.runId), kind).file;
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
        const child = this.active?.child;
        if (!child) return;
        this.cancel();
        if (child.exitCode !== null || child.signalCode !== null) return;
        await new Promise(resolve => {
            const timer = setTimeout(() => { child.kill(); }, 15000);
            child.once('exit', () => { clearTimeout(timer); resolve(); });
        });
    }
}

module.exports = { DesktopController };
