const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { randomUUID } = require('node:crypto');

class LocalPython {
    constructor(python, cli, command, label, timeoutMs) {
        this.python = python;
        this.cli = cli;
        this.command = command;
        this.label = label;
        this.timeoutMs = timeoutMs;
        this.pending = new Map();
    }

    start() {
        if (this.process || this.stopping) return;
        const child = spawn(this.python, ['-I', '-B', this.cli, this.command],
            { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
        this.process = child;
        let stderr = '';
        child.stderr.setEncoding('utf8').on('data', data => { stderr = (stderr + data).slice(-4000); });
        const fail = (message, cause) => {
            if (this.process !== child) return;
            this.process = undefined;
            child.kill();
            const error = new Error(message);
            error.diagnosticMessage = stderr.trim() || cause?.message;
            error.code = cause?.code;
            for (const request of this.pending.values()) request.finish(error);
        };
        child.once('error', error => fail(this.label + '进程无法启动，请重试', error));
        child.stdin.on('error', error => {
            if (!this.stopping) fail(this.label + '进程已断开，请重试', error);
        });
        this.exited = new Promise(resolve => child.once('close', (code, signal) => {
            fail(this.stopping ? this.label + '已停止' : this.label + '进程已退出，请重试',
                new Error('Python process exited: ' + (signal || code)));
            resolve();
        }));
        createInterface({ input: child.stdout }).on('line', line => {
            if (this.process !== child) return;
            let reply;
            try { reply = JSON.parse(line); }
            catch { fail(this.label + '返回了无效结果，请重试'); return; }
            const request = this.pending.get(reply.id);
            const error = reply.error ? Object.assign(new Error(reply.error),
                { code: reply.errorCode, diagnosticMessage: reply.error }) : null;
            if (request) request.finish(error, reply.result);
        });
        this.timeout = () => fail(this.label + '超时，请重试');
    }

    read(workspace, paths, kind) {
        return this.request(workspace, { paths, kind });
    }

    map(workspace, map) {
        return this.request(workspace, { map });
    }

    request(workspace, payload) {
        if (this.stopping) return Promise.reject(new Error(this.label + '已停止'));
        this.start();
        return new Promise((resolve, reject) => {
            const id = randomUUID();
            const timer = setTimeout(this.timeout, this.timeoutMs);
            this.pending.set(id, { finish: (error, result) => {
                this.pending.delete(id);
                clearTimeout(timer);
                if (error) reject(error);
                else resolve(result);
            } });
            this.process.stdin.write(JSON.stringify({ id, workspace, ...payload }) + '\n');
        });
    }

    stop() {
        if (this.stopping) return this.exited;
        this.stopping = true;
        const child = this.process;
        if (!child) return this.exited;
        child.stdin.end();
        const timer = setTimeout(() => child.kill(), 5000);
        return this.exited.finally(() => clearTimeout(timer));
    }
}

module.exports = { LocalPython };
