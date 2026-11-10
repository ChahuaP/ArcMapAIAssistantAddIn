const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');

const protocol = 1;
const identity = () => randomUUID().replaceAll('-', '');
const empty = () => ({ connection: null, auto: false, jobs: [] });
const resultLayer = task => ['export', 'boundaries'].find(kind =>
    ['.gpkg', '.tif', '.tiff'].includes(task?.deliveryFormats?.[kind]));
const canonical = value => path.normalize(value || '').toLowerCase();
const same = (left, right) => canonical(left) === canonical(right);
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const time = () => Date.now() / 1000;

function write(file, value) {
    const pending = file + '.' + identity() + '.pending';
    try {
        fs.writeFileSync(pending, JSON.stringify(value), { flag: 'wx' });
        fs.renameSync(pending, file);
    } finally { fs.rmSync(pending, { force: true }); }
}

class ArcGisSync {
    constructor(controller, root, changed) {
        this.controller = controller;
        this.root = root;
        this.changed = changed;
        this.file = '';
        this.record = empty();
        this.hosts = [];
        this.busy = false;
        this.last = '';
        this.mapStamp = '';
        this.timer = setInterval(() => void this.tick(), 900);
        this.timer.unref();
        this.refresh();
    }

    close() { clearInterval(this.timer); }

    async open(file) {
        const { executable } = await this.controller.python('arcgis-launcher');
        const logs = path.join(path.dirname(this.root), 'diagnostics');
        fs.mkdirSync(logs, { recursive: true });
        const env = { ...process.env, GUIZHI_ARCGIS_HOSTS: this.root,
            FORT0: path.join(logs, 'ArcGIS-' + identity() + '.log') };
        return new Promise((resolve, reject) => {
            const child = spawn(executable, [file], { detached: true, windowsHide: true, stdio: 'ignore', env });
            child.once('error', () => reject(new Error('无法打开 ArcGIS 工程')));
            child.once('spawn', () => { child.unref(); resolve(child.pid); });
        });
    }

    refresh() {
        const workspace = this.controller.settings.workspace;
        const file = workspace ? path.join(workspace, 'arcgis-sync.json') : '';
        if (file !== this.file && !this.busy) {
            this.file = file;
            this.record = file && fs.existsSync(file) ? read(file) : empty();
        }
        this.hosts = (fs.existsSync(this.root) ? fs.readdirSync(this.root, { withFileTypes: true }) : [])
            .filter(item => item.isDirectory() && /^\d+-[a-f0-9]{32}$/.test(item.name)).flatMap(item => {
                try {
                    const host = read(path.join(this.root, item.name, 'status.json'));
                    if (host.protocol !== protocol || host.session !== item.name || !['waiting', 'ready'].includes(host.state) ||
                            host.pid !== Number(item.name.split('-')[0]) || !Number.isSafeInteger(host.window) ||
                            !Array.isArray(host.frames)) return [];
                    process.kill(host.pid, 0);
                    const { seen, protocol: version, ...publicState } = host;
                    return [publicState];
                } catch { return []; }
            });
    }

    active(connection = this.record.connection) {
        if (!connection) return null;
        const candidates = this.hosts.filter(host => host.state === 'ready' && same(host.map, connection.map) &&
            host.frames[connection.frame]?.name === connection.frameName);
        return candidates.find(host => host.session === connection.session) || (candidates.length === 1 ? candidates[0] : null);
    }

    state() {
        const { connection, auto, jobs } = this.record;
        const host = this.active();
        return { connection, auto, hosts: this.hosts, host,
            jobs: jobs.slice(-20).reverse().map(({ id, task, name, status, message, data, artifact, target }) =>
                ({ id, task, name, status, message, data, artifact, map: target.map, frame: target.frame })),
            newEdits: Boolean(host && host.edit_revision > (connection?.capturedSession === host.session
                ? connection.capturedEdit : 0)) };
    }

    notify() {
        const next = JSON.stringify(this.state());
        if (next !== this.last) {
            this.last = next;
            this.changed();
        }
    }

    save() {
        if (!this.file) throw new Error('请先选择成果保存位置');
        if (fs.existsSync(this.file) && fs.lstatSync(this.file).isSymbolicLink()) throw new Error('同步记录不能指向其他位置');
        try { write(this.file, this.record); }
        catch (error) {
            this.record = fs.existsSync(this.file) ? read(this.file) : empty();
            throw error;
        }
        this.notify();
    }

    configure(input) {
        if (this.busy) throw new Error('ArcGIS 正在同步');
        this.refresh();
        if (input.disconnect) {
            this.record.connection = null;
        } else if (input.session) {
            const host = this.hosts.find(item => item.session === input.session);
            const frame = host?.frames[input.frame];
            if (!frame || frame.index !== input.frame) throw new Error('请选择打开的 ArcGIS 工程和数据框');
            this.record.connection = { session: host.session, map: host.map, frame: frame.index, frameName: frame.name,
                capturedSession: host.session, capturedEdit: 0 };
        }
        if (typeof input.auto === 'boolean') this.record.auto = input.auto;
        this.save();
        return this.state();
    }

    enqueue(task) {
        this.refresh();
        const connection = this.record.connection;
        if (!connection) throw new Error('请先连接 ArcGIS 工程');
        const artifact = resultLayer(task);
        if (task?.status !== 'succeeded' || !artifact) throw new Error('请选择已有可显示成果的任务');
        const atTarget = job => same(job.target.map, connection.map) && job.target.frame === connection.frame &&
            job.target.frameName === connection.frameName;
        if (this.record.jobs.some(job => job.task === task.id && atTarget(job))) return this.state();
        const previous = this.record.jobs.find(job => job.task === task.previousTask && job.status === 'published' && atTarget(job));
        this.record.jobs.push({ id: identity(), task: task.id, run: task.runId, artifact, target: { ...connection },
            name: '规智 · ' + task.title.slice(0, 26) + ' · ' + task.runId.slice(0, 6), status: 'pending',
            previous: previous?.id || null, message: '' });
        this.save();
        return this.state();
    }

    completed(task) {
        if (!this.record.auto || !this.record.connection || !resultLayer(task)) return;
        this.enqueue(task);
    }

    retry(id) {
        const job = this.record.jobs.find(item => item.id === id);
        if (!job || job.status !== 'failed') throw new Error('此成果无需重试');
        job.status = 'pending';
        job.message = '';
        this.save();
        return this.state();
    }

    async request(host, connection, action, payload) {
        const folder = path.join(this.root, host.session);
        if (fs.realpathSync(folder).toLowerCase() !== path.resolve(folder).toLowerCase()) throw new Error('宿主目录不能重定向');
        const name = identity() + '.json';
        const request = path.join(folder, 'requests', name);
        const reply = path.join(folder, 'replies', name);
        const expires = time() + 18;
        write(request, { ...payload, protocol, session: host.session, map: connection.map, frame: connection.frame,
            frame_name: connection.frameName, generation: host.generation, action, expires });
        try {
            await this.notifyHost(host);
            while (time() < expires) {
                if (fs.existsSync(reply)) return read(reply);
                await delay(170);
            }
            throw new Error('ArcGIS 未响应，请检查扩展状态后重试');
        } finally {
            fs.rmSync(request, { force: true });
            fs.rmSync(reply, { force: true });
        }
    }

    notifyHost(host) {
        return this.controller.python('arcgis-notify', String(host.pid), String(host.window));
    }

    async discover(session) {
        this.refresh();
        const host = this.hosts.find(item => item.session === session);
        if (!host || this.busy) throw new Error('请选择正在运行的 ArcGIS');
        const result = await this.request(host, {}, 'discover', {});
        if (result.status !== 'discovered') throw new Error(result.message || '无法读取当前工程');
        this.refresh();
        this.notify();
        return this.state();
    }

    async capture(layerIndex, selectedOnly) {
        this.refresh();
        const connection = this.record.connection;
        const host = this.active();
        const layer = host?.frames[connection?.frame]?.layers.find(item => item.index === layerIndex && item.kind === 'vector');
        if (!layer || host.editing || this.busy || this.controller.hostBusy) throw new Error('请结束 ArcGIS 编辑并选择已连接图层');
        this.busy = this.controller.hostBusy = true;
        try {
            const snapshot = await this.request(host, connection, 'snapshot', { layer: layerIndex,
                expected: layer.path, selected_only: Boolean(selectedOnly) });
            if (snapshot.status !== 'captured') throw new Error(snapshot.message || 'ArcGIS 图层已变化，请重试');
            const result = await this.controller.request('arcgis-capture', { ...snapshot, map: connection.map });
            const material = await this.controller.describe(result.file);
            connection.capturedSession = host.session;
            connection.capturedEdit = snapshot.edit_revision;
            this.save();
            return { ...material, name: result.name, revision: result.data_revision,
                arcgis: { map: connection.map, revision: result.revision,
                source: layer.path, manifest: result.manifest } };
        } finally { this.busy = this.controller.hostBusy = false; }
    }

    async tick() {
        if (this.busy) return;
        try {
            this.refresh();
            this.notify();
            const connection = this.record.connection;
            const host = this.active();
            if (!host || this.controller.hostBusy) return;
            if (fs.existsSync(connection.map)) {
                const { mtimeMs, size } = fs.statSync(connection.map);
                const stamp = [connection.map, mtimeMs, size].join(':');
                if (this.mapStamp !== stamp) {
                    this.mapStamp = stamp;
                    await this.notifyHost(host);
                }
            }
            if (!host.publish_ready || host.editing || this.busy || this.controller.hostBusy) return;
            const job = this.record.jobs.find(item => item.status === 'pending' && same(item.target.map, connection.map) &&
                item.target.frame === connection.frame && item.target.frameName === connection.frameName &&
                (!item.resume || item.resume <= time()));
            if (!job) return;
            this.busy = this.controller.hostBusy = true;
            try { await this.publish(job, connection); }
            catch (error) {
                job.message = error.message;
                if (error.message.includes('锁定')) job.resume = time() + 3;
                else job.status = 'failed';
                this.save();
            } finally { this.busy = this.controller.hostBusy = false; }
        } catch (error) {
            console.error('ArcGIS sync:', error.message);
        }
    }

    async publish(job, connection) {
        const bridge = this.controller.settings.arcmapBridge || this.controller.bundledArcmap;
        if (!bridge) throw new Error('请先配置 ArcGIS 连接组件');
        if (!job.staged) {
            job.staged = await this.controller.request('arcgis-stage',
                { run: job.run, artifact: job.artifact, name: job.name }, bridge);
            job.data = job.staged.data;
            this.save();
        }
        this.refresh();
        const host = this.active(connection);
        if (!host || host.editing) return;
        let previous = this.record.jobs.find(item => item.id === job.previous && item.status === 'published');
        const liveLayers = host.frames[connection.frame].layers;
        const already = liveLayers.some(layer => same(layer.path, job.staged.path));
        if (!already && previous) {
            const oldLayer = liveLayers.find(layer => same(layer.path, previous.staged.path));
            const current = oldLayer && await this.controller.python('arcgis-revision', previous.staged.data);
            if (!current || current.revision !== previous.staged.revision) {
                if (oldLayer) job.retained = true;
                job.previous = null;
                previous = null;
                this.save();
            }
        }
        const result = await this.request(host, connection, 'publish', {
            path: job.staged.path, layer_file: job.staged.layer_file, layer_sha256: job.staged.layer_sha256,
            previous: previous?.staged.path, name: job.name,
        });
        this.refresh();
        if (['deferred', 'stale'].includes(result.status)) return;
        job.status = result.status === 'published' ? 'published' : 'failed';
        if (job.status === 'published' && result.previous_hidden && previous) previous.status = 'history';
        delete job.resume;
        job.message = result.message || (job.retained ? '同时保留 ArcGIS 中的人工修订。' : '');
        this.save();
    }
}

module.exports = { ArcGisSync };
