const fs = require('node:fs');
const path = require('node:path');
const { Readable, Transform } = require('node:stream');
const { pipeline } = require('node:stream/promises');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');
const { randomUUID } = require('node:crypto');

const exec = promisify(execFile);
const catalog = path.join(__dirname, '../../packaging/components.json');
const installer = path.join(__dirname, '../../packaging/component_archive.py');
const origin = 'https://123.56.108.57:8443/guizhi/releases/';

class Components {
    constructor(userData, version, python, changed) {
        this.root = path.join(userData, 'components', version);
        this.pythonRoot = path.resolve(path.dirname(python), '../../../../guizhi-python-components');
        this.entries = fs.existsSync(catalog) ? JSON.parse(fs.readFileSync(catalog, 'utf8')) : {};
        this.python = python;
        this.changed = changed;
        this.progress = new Map();
        this.active = new Map();
    }

    installed(kind) {
        const entry = this.entries[kind];
        if (!entry) return undefined;
        const root = this.target(kind, entry);
        if (!fs.existsSync(root) || fs.lstatSync(root).isSymbolicLink()) return undefined;
        const marker = entry.python ? 'component.json' : kind === 'speech' ? 'identity.json' : 'ArcMapBridge.exe';
        return fs.existsSync(path.join(root, marker)) ? root : undefined;
    }

    target(kind, entry) {
        return path.join(entry.python ? this.pythonRoot : this.root, kind + '-' + entry.sha256.slice(0, 16));
    }

    state() {
        return Object.fromEntries(Object.entries(this.entries).map(([kind, entry]) => [kind, {
            installed: Boolean(this.installed(kind)), size: entry.size, progress: this.progress.get(kind),
        }]));
    }

    async install(kind, archive) {
        if (!Object.hasOwn(this.entries, kind)) throw new Error('该组件不可用');
        if (this.installed(kind)) return;
        if (kind === 'imagery' && !this.installed('raster')) throw new Error('请先安装栅格处理组件');
        if (this.progress.has(kind)) throw new Error('该组件正在安装');
        const entry = this.entries[kind];
        const target = this.target(kind, entry);
        const root = path.dirname(target);
        fs.mkdirSync(root, { recursive: true });
        if (fs.realpathSync(root).toLowerCase() !== root.toLowerCase()) throw new Error('组件目录不能指向其他位置');
        const temp = path.join(root, randomUUID() + '.download');
        const stop = new AbortController();
        this.active.set(kind, stop);
        try {
            this.update(kind, archive ? 'installing' : 0);
            if (!archive) {
                const url = new URL(entry.file, origin);
                const signal = AbortSignal.any([stop.signal, AbortSignal.timeout(900_000)]);
                const response = await fetch(url, { signal, redirect: 'error' });
                if (!response.ok || Number(response.headers.get('content-length')) !== entry.size || !response.body) {
                    throw new Error('组件下载失败，请稍后重试或选择离线组件包');
                }
                let bytes = 0;
                let percent = 0;
                const count = new Transform({ transform: (chunk, _, done) => {
                    bytes += chunk.length;
                    if (bytes > entry.size) return done(new Error('组件下载大小异常'));
                    const next = Math.floor(bytes * 100 / entry.size);
                    if (next > percent) this.update(kind, percent = next);
                    done(null, chunk);
                } });
                await pipeline(Readable.fromWeb(response.body), count, fs.createWriteStream(temp, { flags: 'wx' }), { signal });
                signal.throwIfAborted();
                archive = temp;
                this.update(kind, 'installing');
            }
            await exec(this.python, ['-I', '-B', '-X', 'utf8', installer, archive, target, JSON.stringify(entry)],
                { windowsHide: true, timeout: 300_000 });
        } catch (error) {
            if (stop.signal.aborted) return;
            throw new Error(String(error.stderr || error.message).trim().match(/组件包校验失败[^\r\n]*/u)?.[0] ||
                '组件安装失败，请检查网络、磁盘空间和组件包后重试');
        } finally {
            try { fs.rmSync(temp, { force: true }); }
            finally {
                this.active.delete(kind);
                this.progress.delete(kind);
                this.changed();
            }
        }
    }

    cancel(kind) {
        if (typeof this.progress.get(kind) === 'number') this.active.get(kind)?.abort();
    }

    update(kind, progress) {
        this.progress.set(kind, progress);
        this.changed();
    }
}

module.exports = { Components };
