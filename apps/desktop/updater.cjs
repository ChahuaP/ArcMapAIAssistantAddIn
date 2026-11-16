const fs = require('node:fs');
const fsp = fs.promises;
const path = require('node:path');
const os = require('node:os');
const { createHash, randomUUID, verify } = require('node:crypto');
const { spawn } = require('node:child_process');
const { Readable, Transform } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const trust = require('../../packaging/update-trust.json');

const versionPattern = /^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/;
const buildPattern = /^\d+\.\d+\.\d+-[0-9a-f]{16}$/;
const hashPattern = /^[0-9a-f]{64}$/;
const parts = value => value.split('.').map(Number);
const newer = (next, current) => {
    const right = parts(current);
    return parts(next).some((value, index, all) => all.slice(0, index).every((part, i) => part === right[i]) && value > right[index]);
};
const json = file => JSON.parse(fs.readFileSync(file, 'utf8'));
const ns = file => path.toNamespacedPath(file);
const releaseFile = name => /^(core|runtime|renderer)\/[^\\:]+$/.test(name) &&
    name.split('/').every(part => part && part !== '.' && part !== '..' && !/[ .]$/.test(part));
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

async function fileHash(file) {
    const hash = createHash('sha256');
    for await (const chunk of fs.createReadStream(ns(file))) hash.update(chunk);
    return hash.digest('hex');
}

async function response(relative, signal) {
    const input = new URL(relative, trust.url);
    const result = await fetch(input, { signal, redirect: 'error', cache: 'no-store' });
    if (!result.ok || !result.body) throw new Error('更新服务返回 HTTP ' + result.status);
    return result;
}

async function metadata(relative, maximum) {
    const result = await response(relative, AbortSignal.timeout(25_000));
    const chunks = [];
    let size = 0;
    for await (const chunk of Readable.fromWeb(result.body)) {
        size += chunk.length;
        if (size > maximum) throw new Error('更新清单大小超出限制');
        chunks.push(chunk);
    }
    return Buffer.concat(chunks);
}

function verifyPointer(raw) {
    const { payload, signature } = JSON.parse(raw);
    const bytes = Buffer.from(payload, 'base64');
    if (!verify(null, bytes, trust.publicKey, Buffer.from(signature, 'base64'))) throw new Error('更新签名无效');
    const item = JSON.parse(bytes);
    if (item.schema !== 1 || item.platform !== 'win32-x64' || !versionPattern.test(item.version) ||
        !buildPattern.test(item.build) || !item.build.startsWith(item.version + '-') ||
        !hashPattern.test(item.manifest_sha256)) throw new Error('更新清单与 Windows x64 版本不匹配');
    return item;
}

function validateManifest(raw, pointer, bootstrap) {
    if (digest(raw) !== pointer.manifest_sha256) throw new Error('更新文件清单校验失败');
    const item = JSON.parse(raw);
    const { install, sizes } = item;
    if (item.schema !== 1 || install?.schema !== 1 || install.build !== pointer.build ||
        install.version !== pointer.version || !sizes || install.components?.length ||
        !install.files || !bootstrap) throw new Error('更新文件清单格式无效');
    if (Object.entries(install.bootstrap).some(([name, hash]) => bootstrap[name] !== hash) ||
        Object.keys(install.bootstrap).length !== Object.keys(bootstrap).length) {
        throw new Error('此版本更改了启动器，需要使用正式安装包');
    }
    const names = Object.keys(install.files);
    if (!names.length || names.length > 60_000 || new Set(names.map(name => name.toLowerCase())).size !== names.length ||
        Object.keys(sizes).length !== names.length ||
        names.some(name => !releaseFile(name) || !hashPattern.test(install.files[name]) ||
            !Number.isSafeInteger(sizes[name]) || sizes[name] < 0 || sizes[name] > (1 << 30))) {
        throw new Error('更新文件路径或大小无效');
    }
    return item;
}

async function download(hash, size, file, signal, progress) {
    const result = await response('objects/' + hash, AbortSignal.any([signal, AbortSignal.timeout(180_000)]));
    const checksum = createHash('sha256');
    let total = 0;
    const check = new Transform({
        transform(chunk, _, done) {
            total += chunk.length;
            if (total > size) return done(new Error('更新文件大小异常'));
            checksum.update(chunk);
            progress(chunk.length);
            done(null, chunk);
        },
    });
    await pipeline(Readable.fromWeb(result.body), check, fs.createWriteStream(ns(file), { flags: 'wx' }), { signal });
    if (total !== size || checksum.digest('hex') !== hash) throw new Error('更新文件校验失败，请重试');
}

class Updater {
    constructor({ root, userData, version, changed }) {
        this.root = root;
        this.userData = userData;
        this.version = version;
        this.changed = changed;
        this.running = path.resolve(__dirname, '../../..');
        this.status = { phase: 'idle' };
        this.resultFile = path.join(userData, 'update-result.json');
        if (fs.existsSync(this.resultFile)) {
            try {
                const value = json(this.resultFile);
                if (value.root === this.root && ['failed', 'complete'].includes(value.phase)) this.status = { phase: value.phase, message: value.message };
            } catch (error) { console.error('软件更新状态：', error.message); }
        }
    }

    installedRelease() {
        if (!this.root) return null;
        const stateFile = path.join(this.root, 'active.json');
        if (!fs.existsSync(stateFile) || !fs.existsSync(path.join(this.root, 'bootstrap', 'apply_update.py'))) return null;
        const active = json(stateFile).current;
        if (active?.components?.length || !/^[a-z0-9][a-z0-9.-]{0,90}$/.test(active?.release || '')) return null;
        const directory = path.join(this.root, 'releases', active.release);
        if (directory.toLowerCase() !== this.running.toLowerCase()) return null;
        return { active, directory };
    }

    state() {
        return { version: this.version, available: Boolean(this.installedRelease()), ...this.status };
    }

    set(phase, values = {}) {
        this.status = { phase, ...values };
        this.changed(this.state());
    }

    assertIdle() {
        if (this.operation || this.handoff) throw new Error('软件更新正在处理');
        const current = this.installedRelease();
        if (!current) throw new Error('请在正式安装版中检查更新');
        return { ...current, manifest: json(path.join(current.directory, 'installation.json')) };
    }

    async check() {
        const current = this.assertIdle();
        if (this.prepared) return this.state();
        this.operation = new AbortController();
        this.set('checking');
        try {
            const pointer = verifyPointer(await metadata('latest.json', 24 * 1024));
            if (!newer(pointer.version, this.version)) {
                this.set('current');
                return this.state();
            }
            const manifest = validateManifest(await metadata('manifests/' + pointer.build + '.json', 8 * 1024 * 1024),
                pointer, current.manifest.bootstrap);
            const oldHashes = new Set(Object.values(current.manifest.files));
            const changed = new Map();
            for (const [name, hash] of Object.entries(manifest.install.files)) {
                if (!oldHashes.has(hash)) changed.set(hash, manifest.sizes[name]);
            }
            this.candidate = { pointer, manifest, source: current.active.release };
            this.set('offered', { target: pointer.version, count: changed.size,
                bytes: [...changed.values()].reduce((sum, size) => sum + size, 0) });
        } catch (error) {
            this.candidate = null;
            this.failure(error);
        } finally { this.operation = null; }
        return this.state();
    }

    failure(error) {
        console.error('软件更新：', error.message);
        this.set('failed', { message: /\p{Script=Han}/u.test(error.message) ? error.message
            : '无法完成更新。请检查网络连接和安装目录权限后重试。' });
    }

    async prepare() {
        const current = this.assertIdle();
        if (!this.candidate || this.candidate.source !== current.active.release) throw new Error('请先检查更新');
        if (this.prepared) return this.state();
        const { pointer, manifest } = this.candidate;
        const targetName = pointer.build + '-' + randomUUID().slice(0, 8);
        const directory = path.join(this.root, 'releases', targetName);
        const pending = directory + '.pending';
        const downloads = path.join(pending, '.downloads');
        const stop = this.operation = new AbortController();
        const before = this.status;
        this.set('preparing', { target: pointer.version, bytes: before.bytes, received: 0 });
        let received = 0;
        let last = 0;
        const progress = amount => {
            received += amount;
            if (Date.now() - last > 180) {
                last = Date.now();
                this.set('preparing', { target: pointer.version, bytes: Math.max(before.bytes || 0, received), received });
            }
        };
        try {
            await fsp.mkdir(ns(pending));
            await fsp.mkdir(ns(downloads));
            const old = new Map(Object.entries(current.manifest.files).map(([name, hash]) => [hash, name]));
            const tasks = Object.entries(manifest.install.files);
            const downloaded = new Map();
            let index = 0;
            const take = async () => {
                while (index < tasks.length) {
                    stop.signal.throwIfAborted();
                    const [name, hash] = tasks[index++];
                    const output = path.join(pending, name);
                    await fsp.mkdir(ns(path.dirname(output)), { recursive: true });
                    const input = path.join(current.directory, current.manifest.files[name] === hash ? name : old.get(hash) || '');
                    if (input !== current.directory && fs.existsSync(ns(input)) &&
                        (await fsp.lstat(ns(input))).isFile() && await fileHash(input) === hash) {
                        await fsp.copyFile(ns(input), ns(output), fs.constants.COPYFILE_EXCL);
                    } else {
                        if (!downloaded.has(hash)) {
                            const file = path.join(downloads, hash);
                            downloaded.set(hash, download(hash, manifest.sizes[name], file, stop.signal, progress).then(() => file));
                        }
                        await fsp.copyFile(ns(await downloaded.get(hash)), ns(output), fs.constants.COPYFILE_EXCL);
                    }
                }
            };
            const workers = Array.from({ length: 8 }, () => take().catch(error => { stop.abort(error); throw error; }));
            const results = await Promise.allSettled(workers);
            const failed = results.find(result => result.status === 'rejected');
            if (failed) throw stop.signal.reason || failed.reason;
            await fsp.rm(ns(downloads), { recursive: true });
            await fsp.writeFile(ns(path.join(pending, 'installation.json')), JSON.stringify(manifest.install), { flag: 'wx' });
            await fsp.rename(ns(pending), ns(directory));
            this.prepared = { release: targetName, components: [] };
            this.set('ready', { target: pointer.version, received, count: downloaded.size });
        } catch (error) {
            await fsp.rm(ns(pending), { recursive: true, force: true });
            if (stop.signal.reason?.name === 'AbortError') this.set('offered', before);
            else this.failure(error);
        } finally { this.operation = null; }
        return this.state();
    }

    cancel() {
        if (this.status.phase === 'preparing') this.operation?.abort();
        return this.state();
    }

    async apply() {
        const current = this.assertIdle();
        if (!this.prepared || this.candidate.source !== current.active.release) throw new Error('请先下载更新');
        const bootstrap = path.join(this.root, 'bootstrap');
        const args = ['-I', '-B', path.join(bootstrap, 'apply_update.py'), this.root,
            this.userData, String(process.pid), current.active.release, this.prepared.release];
        const child = spawn(path.join(bootstrap, 'pythonw.exe'), args, {
            cwd: os.tmpdir(), detached: true, windowsHide: true, stdio: 'ignore',
        });
        this.handoff = true;
        try { await new Promise((resolve, reject) => { child.once('spawn', resolve); child.once('error', reject); }); }
        catch (error) { this.handoff = false; throw error; }
        child.unref();
        this.set('restarting', { target: this.candidate.pointer.version });
    }
}

module.exports = { Updater };
