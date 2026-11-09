const { execFile, fork } = require('node:child_process');
const { createHash, randomUUID } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { promisify } = require('node:util');

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
    constructor({ userData, runtime, dsh, safeStorage, externalPath, onChange }) {
        this.file = path.join(userData, 'settings.json');
        fs.mkdirSync(userData, { recursive: true });
        this.settings = load(this.file, { workspace: '', provider: 'minimax-cn', model: 'MiniMax-M3' });
        this.runtime = { python: path.join(runtime, 'python', 'python.exe'),
            node: path.join(runtime, 'node', 'node.exe'), runtime: dsh || path.join(runtime, 'dsh') };
        this.storage = safeStorage;
        this.externalPath = externalPath;
        this.onChange = onChange;
        this.tasks = [];
        this.active = null;
        this.mapView = null;
        this.mapBusy = false;
        this.restore();
    }

    historyPath() {
        return path.join(this.settings.workspace, 'desktop-tasks.json');
    }

    restore() {
        if (!this.settings.workspace) return;
        this.settings.workspace = this.externalPath(this.settings.workspace);
        this.tasks = load(this.historyPath(), []);
        this.mapView = load(path.join(this.settings.workspace, 'desktop-view.json'), null);
        let changed = false;
        for (const task of this.tasks) {
            if (['running', 'cancelling'].includes(task.status)) {
                task.status = 'interrupted';
                changed = true;
            }
        }
        if (changed) this.persist();
    }

    persist() {
        save(this.historyPath(), this.tasks);
    }

    state() {
        const { encryptedKey, ...settings } = this.settings;
        return { settings: { ...settings, keyConfigured: Boolean(encryptedKey || process.env.MINIMAX_API_KEY) },
            tasks: this.tasks, mapView: this.mapView };
    }

    configure(input) {
        if (this.active || this.mapBusy || this.skillBusy) throw new Error('请先结束当前任务');
        const workspace = this.externalPath(input.workspace);
        if (!['minimax-cn', 'ollama'].includes(input.provider) || !input.model?.trim()) {
            throw new Error('请选择模型服务和模型');
        }
        let encryptedKey = this.settings.encryptedKey;
        if (input.key?.trim()) {
            if (!this.storage.isEncryptionAvailable()) throw new Error('系统凭据加密不可用');
            encryptedKey = this.storage.encryptString(input.key.trim()).toString('base64');
        }
        fs.mkdirSync(workspace, { recursive: true });
        this.settings = { workspace, provider: input.provider, model: input.model.trim(),
            modelUrl: input.provider === 'ollama' ? input.modelUrl?.trim() || undefined : undefined, encryptedKey };
        save(this.file, this.settings);
        this.tasks = [];
        this.restore();
        return this.state();
    }

    async python(command, ...args) {
        try {
            const result = await exec(this.runtime.python, ['-I', '-B', cli, '--workspace',
                this.settings.workspace, command, ...args], {
                windowsHide: true, ...(command !== 'skill-publish' && { timeout: 30000 }), maxBuffer: 8 << 20 });
            return JSON.parse(result.stdout);
        } catch (error) {
            let message;
            try { message = JSON.parse(error.stderr).error; } catch { /* Process failed before producing a record. */ }
            throw new Error(message?.split('\n')[0].replace(/^ValueError: /, '') || '无法读取材料或成果');
        }
    }

    async describe(file) {
        if (['.pdf', '.docx', '.xlsx'].includes(path.extname(file).toLowerCase())) {
            if (!fs.statSync(file).isFile()) throw new Error('请选择材料文件');
            return { file, name: path.basename(file), kind: 'document', layer: null, layers: [] };
        }
        const layers = await this.python('layers', file);
        if (!layers.length) throw new Error('此文件没有可用的矢量图层');
        return { file, name: path.basename(file), layers, layer: layers.length === 1 ? layers[0] : '' };
    }

    submit({ prompt, project, control, skill, materials = {}, previousTask }) {
        if (!this.settings.workspace) throw new Error('请先选择成果保存位置');
        if (this.active) throw new Error('已有任务在运行');
        if (typeof prompt !== 'string' || !prompt.trim() || prompt.length > 4000) throw new Error('请输入办理事项');
        const bindings = materialBindings(materials);
        const previousRun = this.tasks.find(item => item.id === previousTask && item.status === 'succeeded')?.runId;
        materialBindings({ project: project ? [project] : [], control: control ? [control] : [] });
        if (skill && !/^[a-z][a-z0-9_-]{0,39}@[1-9]\d*$/.test(skill)) throw new Error('请选择已发布的技能');
        const env = { ...process.env };
        if (this.settings.provider === 'minimax-cn' && this.settings.encryptedKey) {
            env.MINIMAX_API_KEY = this.storage.decryptString(Buffer.from(this.settings.encryptedKey, 'base64'));
        }
        const task = { id: randomUUID(), title: [...prompt.trim()].slice(0, 32).join(''), prompt: prompt.trim(),
            project, control, skill, materials, previousTask, status: 'running', createdAt: new Date().toISOString() };
        this.tasks.unshift(task);
        this.persist();
        const child = fork(path.join(__dirname, 'agent.mjs'), {
            execPath: this.runtime.node, cwd: this.settings.workspace, env,
            windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
        });
        this.active = { child, task };
        child.on('message', message => {
            if (message.type === 'event') {
                if (message.event.type === 'tool/call') task.step = '正在办理';
                this.onChange(this.state());
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
            provider: this.settings.provider, model: this.settings.model, modelUrl: this.settings.modelUrl,
            prompt: task.prompt, input: project?.file, layer: project?.layer,
            control: control?.file, controlLayer: control?.layer, skill, materials: bindings, previousRun,
            projectPlan: project?.plan || materials.project?.[0]?.plan,
            controlPlan: control?.plan || materials.control?.[0]?.plan } });
        return this.state();
    }

    async coordinateCatalog() {
        return this.settings.workspace ? this.python('crs-list') : [];
    }

    async skillRequest(command, markdown, materials) {
        if (!this.settings.workspace || this.skillBusy) throw new Error('请先结束当前操作');
        if (typeof markdown !== 'string' || Buffer.byteLength(markdown) > 65536) throw new Error('技能文件最多 64 KB');
        const file = path.join(this.settings.workspace, 'skill-' + randomUUID());
        this.skillBusy = true;
        try {
            if (command === 'skill-preview') fs.writeFileSync(file, markdown, { flag: 'wx' });
            else save(file, { text: markdown, materials: materialBindings(materials) });
            return await this.python(command, file);
        } finally {
            fs.rmSync(file, { force: true });
            this.skillBusy = false;
        }
    }

    async mapAction(action, parameters = {}, material = null) {
        const actions = ['add_layer', 'set_visibility', 'remove_layer', 'clear_layers', 'reorder_layers',
            'zoom_to_dataset', 'zoom_to_selection', 'set_layout_text', 'set_view_mode'];
        if (!actions.includes(action) || !this.settings.workspace) throw new Error('无法执行地图操作');
        if (this.mapBusy) throw new Error('地图正在更新');
        const request = path.join(this.settings.workspace, 'map-' + randomUUID() + '.json');
        this.mapBusy = true;
        try {
            save(request, { operation: 'presentation.' + action,
                inputs: material ? [{ file: material.file, layer: material.layer }] : [],
                parameters: { ...parameters, ...(this.mapView ? { view_run: this.mapView.id } : {}) } });
            const run = await this.python('execute', request);
            const artifact = checkedArtifact(run, 'map');
            this.mapView = { id: run.id, data: artifact ? JSON.parse(artifact.bytes) : null,
                ...run.result.presentation };
            save(path.join(this.settings.workspace, 'desktop-view.json'), this.mapView);
            return this.state();
        } finally {
            fs.rmSync(request, { force: true });
            this.mapBusy = false;
        }
    }

    async layoutReport() {
        if (this.mapView?.mode !== 'layout') throw new Error('请先切换至版面');
        return checkedArtifact(await this.python('inspect', this.mapView.id), 'report').file;
    }

    async importCoordinate(kind, file) {
        if (this.active) throw new Error('请先结束当前任务');
        if (!['plan', 'definition'].includes(kind)) throw new Error('请选择坐标资料类型');
        return this.python(kind === 'plan' ? 'crs-import' : 'crs-define', file);
    }

    async complete(task, receipt) {
        if (this.active?.task !== task || this.active.settling) return;
        this.active.settling = true;
        try {
            task.status = receipt.status;
            task.runId = receipt.operation?.run_id;
            task.response = ['succeeded', 'answered'].includes(task.status) ? receipt.response : receipt.operation?.error || receipt.response;
            task.statistics = receipt.operation?.statistics;
            task.candidate = receipt.candidate?.markdown;
            if (task.status === 'succeeded') {
                const record = await this.python('inspect', task.runId);
                if (record.status !== 'succeeded') throw new Error('成果未发布');
                const map = checkedArtifact(record, 'map');
                if (map) task.map = JSON.parse(map.bytes);
                task.hasReport = Boolean(checkedArtifact(record, 'report'));
                const findings = checkedArtifact(record, 'case');
                if (findings) task.approval = JSON.parse(findings.bytes);
                task.deliveries = record.result.evidence.filter(item => ['export', 'table', 'workflow'].includes(item.step))
                    .map(item => item.step);
            }
        } catch {
            task.status = 'failed';
        } finally {
            this.active = null;
            this.persist();
            this.onChange(this.state());
        }
    }

    cancel() {
        if (this.active?.child.connected) {
            this.active.task.status = 'cancelling';
            this.persist();
            this.active.child.send({ type: 'cancel' });
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

function materialBindings(groups) {
    return Object.fromEntries(Object.entries(groups).filter(([, items]) => items.length).map(([role, items]) => [role, items.map(item => {
        if (!item?.file || !path.isAbsolute(item.file) ||
                (item.kind === 'document' ? !['.pdf', '.docx', '.xlsx'].includes(path.extname(item.file).toLowerCase())
                    : !item.layers?.includes(item.layer))) {
            throw new Error('请选择材料图层');
        }
        return { file: item.file, layer: item.layer };
    })]));
}

module.exports = { DesktopController };
