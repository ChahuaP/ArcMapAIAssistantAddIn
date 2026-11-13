const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { spawn } = require('node:child_process');
const { setTimeout: delay } = require('node:timers/promises');

const protocol = 1;
const identity = () => randomUUID().replaceAll('-', '');
const read = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const time = () => Date.now() / 1000;
const spatial = /\.(shp|gpkg|geojson|tiff?|img)$/i;
const same = (left, right) => path.normalize(left || '').toLowerCase() === path.normalize(right || '').toLowerCase();

function write(file, value) {
    const pending = file + '.' + identity() + '.pending';
    try {
        fs.writeFileSync(pending, JSON.stringify(value), { flag: 'wx' });
        fs.renameSync(pending, file);
    } finally { fs.rmSync(pending, { force: true }); }
}

class ArcGisSync {
    constructor(controller, root, documents, changed) {
        this.controller = controller;
        this.root = root;
        this.addIns = path.join(documents, 'ArcGIS', 'AddIns', 'Desktop10.2');
        this.changed = changed;
        this.file = '';
        this.jobs = [];
        this.host = null;
        this.busy = false;
        this.starting = false;
        this.closed = false;
        this.last = '';
        this.timer = setInterval(() => void this.tick(), 900);
        this.timer.unref();
        this.refresh();
    }

    close() { this.closed = true; clearInterval(this.timer); }

    get carrier() { return this.bridge ? this.controller.settings.carrier || 'none' : 'none'; }
    get bridge() { return this.controller.bundledArcmap; }
    get view() { return this.file ? path.join(path.dirname(this.file), 'host-exchanges', '规智数据视图.mxd') : ''; }

    installed() {
        if (!fs.existsSync(this.addIns)) return false;
        return fs.readdirSync(this.addIns, { withFileTypes: true }).some(item => item.isDirectory() &&
            /^\{[a-f0-9-]{36}\}$/i.test(item.name) && fs.existsSync(path.join(this.addIns, item.name, 'Guizhi.esriaddin')));
    }

    refresh() {
        const workspace = this.controller.settings.workspace;
        const file = workspace ? path.join(workspace, 'arcgis-display.json') : '';
        if (file !== this.file && !this.busy) {
            this.file = file;
            this.jobs = file && fs.existsSync(file) ? read(file) : [];
        }
        this.hosts = (this.view && fs.existsSync(this.root) ? fs.readdirSync(this.root, { withFileTypes: true }) : [])
            .filter(item => item.isDirectory() && /^\d+-[a-f0-9]{32}$/.test(item.name)).flatMap(item => {
                try {
                    const host = read(path.join(this.root, item.name, 'status.json'));
                    if (host.protocol !== protocol || host.session !== item.name || !['waiting', 'ready'].includes(host.state) ||
                        host.pid !== Number(item.name.split('-')[0]) || !Number.isSafeInteger(host.window) ||
                        !Array.isArray(host.frames)) return [];
                    process.kill(host.pid, 0);
                    return [host];
                } catch { return []; }
            });
        this.host = this.carrier === 'arcgis' ? this.hosts.find(host => host.state === 'ready' && host.publish_ready &&
            same(host.map, this.view) && host.frames[0]) || null : null;
    }

    state() {
        const { host, carrier, starting } = this;
        return { carrier, starting, available: Boolean(this.bridge), installed: this.installed(), host,
            jobs: this.jobs.slice(-12).reverse().map(({ id, name, status, message }) => ({ id, name, status, message })) };
    }

    notify() {
        if (this.closed) return;
        const next = JSON.stringify(this.state());
        if (next === this.last) return;
        this.last = next;
        this.changed();
    }

    save() {
        if (!this.file || (fs.existsSync(this.file) && fs.lstatSync(this.file).isSymbolicLink())) {
            throw new Error('无法保存 ArcGIS 展示记录');
        }
        try { write(this.file, this.jobs); }
        catch (error) {
            this.jobs = fs.existsSync(this.file) ? read(this.file) : [];
            throw error;
        }
        this.notify();
    }

    configure(carrier) {
        if (this.busy) throw new Error('请等待当前图层同步完成');
        if (!['none', 'arcgis'].includes(carrier) || (carrier === 'arcgis' && !this.bridge)) {
            throw new Error('所选扩展不可用');
        }
        this.controller.selectCarrier(carrier);
        this.refresh();
        this.notify();
        return this.state();
    }

    started(task) {
        if (this.carrier !== 'arcgis') return;
        this.refresh();
        const seen = new Set();
        for (const item of [task.project, task.control, ...Object.values(task.materials).flat()]) {
            if (!item || !spatial.test(item.file)) continue;
            const key = JSON.stringify([item.file.toLowerCase(), item.layer || '']);
            if (seen.has(key)) continue;
            seen.add(key);
            const previous = this.jobs.findLast(job => job.key === key && job.status === 'published');
            this.jobs.push({ id: identity(), task: task.id, key, material: { file: item.file, layer: item.layer, revision: item.revision },
                name: '材料 · ' + (item.name || path.basename(item.file)), previous: previous?.id, status: 'pending' });
        }
        if (seen.size) { this.save(); void this.tick(); }
    }

    completed(task, previousTask) {
        if (this.carrier !== 'arcgis') return;
        const artifact = ['export', 'boundaries'].find(kind => ['.gpkg', '.tif', '.tiff'].includes(task.deliveryFormats?.[kind]));
        if (!artifact) return;
        this.refresh();
        if (this.jobs.some(job => job.task === task.id && job.artifact === artifact)) return;
        const previous = this.jobs.findLast(job => job.task === previousTask && job.artifact && job.status === 'published');
        this.jobs.push({ id: identity(), task: task.id, run: task.runId, artifact, previous: previous?.id,
            name: '成果 · ' + task.title.slice(0, 26) + ' · ' + task.runId.slice(0, 6), status: 'pending' });
        this.save();
        void this.tick();
    }

    retry(id) {
        if (this.busy || this.carrier !== 'arcgis') throw new Error('ArcGIS 展示当前不可用');
        const job = this.jobs.find(item => item.id === id);
        if (!job || job.status !== 'failed') throw new Error('此图层无需重试');
        job.status = 'pending';
        job.message = '';
        this.save();
        void this.tick();
        return this.state();
    }

    async open() {
        if (!this.installed()) throw new Error('请在第三方扩展安装 ArcGIS 扩展');
        const { executable, view } = await this.controller.python('arcgis-view', this.bridge);
        if (!same(view, this.view)) throw new Error('ArcGIS 数据视图位置不匹配');
        const logs = path.join(path.dirname(this.root), 'diagnostics');
        fs.mkdirSync(logs, { recursive: true });
        const env = { ...process.env, GUIZHI_ARCGIS_HOSTS: this.root,
            FORT0: path.join(logs, 'ArcGIS-' + identity() + '.log') };
        const child = spawn(executable, [view], { detached: true, windowsHide: true, stdio: 'ignore', env });
        await new Promise((resolve, reject) => {
            child.once('spawn', resolve);
            child.once('error', () => reject(new Error('无法启动 ArcGIS 数据视图')));
        });
        child.unref();
        const deadline = time() + 40;
        let probed;
        while (!this.closed && time() < deadline) {
            this.refresh();
            if (this.host) return this.host;
            const pending = this.hosts.find(host => host.pid === child.pid && host.state === 'waiting');
            if (pending && probed !== pending.session) {
                probed = pending.session;
                await this.request(pending, 'discover');
            }
            if (child.exitCode !== null) break;
            await delay(300);
        }
        throw new Error('ArcGIS 数据视图未就绪，请检查 ArcGIS 许可及扩展是否启用');
    }

    async request(host, action, payload = {}) {
        const folder = path.join(this.root, host.session);
        if (!same(fs.realpathSync(folder), path.resolve(folder))) throw new Error('宿主目录不能重定向');
        const name = identity() + '.json';
        const request = path.join(folder, 'requests', name);
        const reply = path.join(folder, 'replies', name);
        const expires = time() + 18;
        write(request, { ...payload, protocol, session: host.session, map: host.map, frame: 0,
            frame_name: host.frames[0]?.name, generation: host.generation, action, expires });
        try {
            await this.controller.python('arcgis-notify', String(host.pid), String(host.window));
            while (!this.closed && time() < expires) {
                if (fs.existsSync(reply)) return read(reply);
                await delay(170);
            }
            throw new Error('ArcGIS 未响应');
        } finally {
            fs.rmSync(request, { force: true });
            fs.rmSync(reply, { force: true });
        }
    }

    async capture(index, selectedOnly) {
        this.refresh();
        const { host } = this;
        const layer = host?.frames[0].layers.find(item => item.index === index && item.kind === 'vector');
        if (!layer || host.editing || this.busy || this.controller.hostBusy) {
            throw new Error('请结束 ArcGIS 编辑并选择图层');
        }
        this.busy = this.controller.hostBusy = true;
        try {
            const snapshot = await this.request(host, 'snapshot', { layer: index, expected: layer.path, selected_only: selectedOnly });
            if (snapshot.status !== 'captured') throw new Error(snapshot.message || 'ArcGIS 图层已变化，请重试');
            const result = await this.controller.request('arcgis-capture', { ...snapshot, map: host.map });
            return { ...await this.controller.describe(result.file), name: result.name, revision: result.data_revision,
                arcgis: { revision: result.revision, source: layer.path, manifest: result.manifest } };
        } finally { this.busy = this.controller.hostBusy = false; }
    }

    async tick() {
        if (this.busy || this.closed) return;
        try {
            this.refresh();
            this.notify();
            if (this.carrier !== 'arcgis' || this.controller.hostBusy || this.host?.editing) return;
            const job = this.jobs.find(item => item.status === 'pending' && (!item.resume || item.resume <= time()));
            if (!job) return;
            this.busy = this.controller.hostBusy = true;
            try {
                if (!this.host) {
                    this.starting = true;
                    this.notify();
                    await this.open();
                    this.starting = false;
                    this.notify();
                }
                await this.publish(job);
            } catch (error) {
                job.message = error.message;
                if (error.message.includes('锁定')) job.resume = time() + 3;
                else job.status = 'failed';
                this.save();
            } finally {
                this.busy = this.controller.hostBusy = this.starting = false;
                this.notify();
            }
        } catch (error) { console.error('ArcGIS display:', error.message); }
    }

    async publish(job) {
        if (!job.staged) {
            job.staged = await this.controller.request('arcgis-stage',
                { run: job.run, artifact: job.artifact, material: job.material, name: job.name }, this.bridge);
            this.save();
        }
        this.refresh();
        const { host } = this;
        if (!host || host.editing) return;
        let previous = this.jobs.find(item => item.id === job.previous && item.status === 'published');
        const layers = host.frames[0].layers;
        if (previous && !layers.some(layer => same(layer.path, job.staged.path))) {
            const old = layers.find(layer => same(layer.path, previous.staged.path));
            const current = old && await this.controller.python('arcgis-revision', previous.staged.data);
            if (!current || current.revision !== previous.staged.revision) previous = null;
        }
        const result = await this.request(host, 'publish', {
            path: job.staged.path, layer_file: job.staged.layer_file, layer_sha256: job.staged.layer_sha256,
            previous: previous?.staged.path, name: job.name, persist: true,
        });
        this.refresh();
        if (['deferred', 'stale'].includes(result.status)) return;
        job.status = result.status === 'published' ? 'published' : 'failed';
        if (job.status === 'published' && result.previous_hidden && previous) previous.status = 'history';
        delete job.resume;
        job.message = result.message || '';
        this.save();
    }
}

module.exports = { ArcGisSync };
