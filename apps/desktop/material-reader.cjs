const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { randomUUID } = require('node:crypto');

class MaterialReader {
    constructor(python, cli) {
        this.python = python;
        this.cli = cli;
        this.pending = new Map();
    }

    start() {
        if (this.process) return;
        const child = spawn(this.python, ['-I', '-B', this.cli, '_material-reader'],
            { windowsHide: true, stdio: ['pipe', 'pipe', 'ignore'] });
        this.process = child;
        const failed = error => {
            if (this.process === child) this.close('本地材料读取进程已退出，请重新添加材料', error);
        };
        child.once('error', failed);
        child.once('exit', (code, signal) => failed(new Error('材料读取进程退出（' + (signal || code) + '）')));
        child.stdin.on('error', failed);
        createInterface({ input: child.stdout }).on('line', line => {
            if (this.process !== child) return;
            let reply;
            try { reply = JSON.parse(line); }
            catch { this.close('本地材料读取返回了无效结果，请重试'); return; }
            const request = this.pending.get(reply.id);
            if (request) request.finish(reply.error ? new Error(reply.error) : null, reply.result);
        });
    }

    read(workspace, paths, kind) {
        return this.request(workspace, { paths, kind });
    }

    map(workspace, map) {
        return this.request(workspace, { map });
    }

    request(workspace, payload) {
        this.start();
        return new Promise((resolve, reject) => {
            const id = randomUUID();
            const timer = setTimeout(() => this.close('读取材料超时，请检查文件或解压后重试'), 240_000);
            this.pending.set(id, { finish: (error, result) => {
                this.pending.delete(id);
                clearTimeout(timer);
                if (error) reject(error);
                else resolve(result);
            } });
            this.process.stdin.write(JSON.stringify({ id, workspace, ...payload }) + '\n');
        });
    }

    close(message = '已停止读取材料', cause) {
        const child = this.process;
        this.process = undefined;
        child?.kill();
        const error = new Error(message);
        if (cause) {
            error.diagnosticMessage = cause.message;
            error.code = cause.code;
        }
        for (const request of this.pending.values()) request.finish(error);
    }
}

module.exports = { MaterialReader };
