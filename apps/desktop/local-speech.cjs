const { spawn } = require('node:child_process');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const spec = require('../../packaging/local-speech/models.lock.json');

class LocalSpeechRuntime {
    constructor(python, externalPath) {
        this.python = python;
        this.externalPath = externalPath;
        this.verified = new Map();
    }

    verify(location, kind) {
        if (!location) throw new Error('本机语音组件未安装');
        const root = this.externalPath(location);
        const identity = path.join(root, 'identity.json');
        if (!fs.existsSync(identity) || !fs.existsSync(path.join(root, 'engine', 'sherpa_onnx'))) {
            throw new Error('本地语音引擎未安装');
        }
        const installed = JSON.parse(fs.readFileSync(identity, 'utf8'));
        if (installed.engine !== spec.engine || !installed.modes.includes(kind)) {
            throw new Error(kind === 'asr' ? '本地识别模型未安装' : '本地语音回答模型未安装');
        }
        for (const asset of spec.models[kind].files) {
            const file = path.join(root, 'models', kind, asset.file);
            if (!fs.existsSync(file) || path.relative(file, fs.realpathSync(file)) !== '') {
                throw new Error('本地语音资源缺失或路径已变更');
            }
            const info = fs.statSync(file);
            const stamp = [info.size, info.mtimeMs, info.ctimeMs].join(':');
            if (!info.isFile() || info.size !== asset.bytes) throw new Error('本地语音资源完整性检查失败');
            if (this.verified.get(file) !== stamp) {
                const hash = createHash('sha256').update(fs.readFileSync(file)).digest('hex');
                if (hash !== asset.sha256) throw new Error('本地语音资源完整性检查失败');
                this.verified.set(file, stamp);
            }
        }
        return root;
    }

    run(location, kind, data, signal) {
        const root = this.verify(location, kind);
        const worker = path.join(__dirname, 'local-speech.py');
        return new Promise((resolve, reject) => {
            const child = spawn(this.python, ['-I', '-B', worker, root, kind], {
                windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
                signal: AbortSignal.any([signal, AbortSignal.timeout(55_000)]),
            });
            const chunks = [];
            let size = 0;
            let error = '';
            child.stdout.on('data', chunk => {
                size += chunk.length;
                if (size > 4 << 20) {
                    child.kill();
                    reject(new Error('本地语音输出过长'));
                } else chunks.push(chunk);
            });
            child.stderr.on('data', chunk => { if (error.length < 3000) error += chunk; });
            child.stdin.on('error', failure => { if (failure.code !== 'EPIPE') reject(failure); });
            child.on('error', reject);
            child.on('close', code => {
                if (code === 0) resolve(Buffer.concat(chunks));
                else reject(new Error('本地语音执行失败' + (error.includes('ModuleNotFoundError') ? '，请检查运行组件' : '')));
            });
            child.stdin.end(data);
        });
    }

    provider(settings) {
        return {
            recognize: async (wav, signal, partial) => {
                const text = (await this.run(settings.localRoot, 'asr', wav, signal)).toString('utf8');
                if (text) partial(text);
                return text;
            },
            synthesize: (text, _voice, signal) => this.run(settings.localRoot, 'tts', Buffer.from(text), signal),
        };
    }
}

module.exports = { LocalSpeechRuntime };
