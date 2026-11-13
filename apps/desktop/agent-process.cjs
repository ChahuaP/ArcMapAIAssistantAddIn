const { fork } = require('node:child_process');
const path = require('node:path');
const { addAbortListener } = require('node:events');

class AgentProcess {
    constructor(options) {
        this.child = fork(path.join(__dirname, 'agent.mjs'), { execPath: options.node, windowsHide: true,
            stdio: ['ignore', 'ignore', 'ignore', 'ipc'] });
        this.exited = new Promise(resolve => this.child.once('close', resolve));
        this.ready = new Promise((resolve, reject) => {
            this.child.on('message', message => {
                if (message.type === 'ready') {
                    resolve(this);
                } else if (message.type === 'error') reject(new Error(message.error));
                else if (message.type === 'event') this.current?.onEvent(message.event);
                else if (message.type === 'result') this.current?.finish(message.result);
            });
            const failed = () => {
                const error = new Error('模型调度进程已退出');
                reject(error);
                this.current?.finish({ status: 'interrupted', error: error.message });
            };
            this.child.on('error', failed);
            this.child.once('close', failed);
            this.child.send({ type: 'warm', options }, error => { if (error) failed(); });
        });
    }

    async run(options, { signal, onEvent = () => {} }) {
        await this.ready;
        signal?.throwIfAborted();
        if (!this.child.connected || this.closing || this.current) throw new Error('模型调度服务不可用');
        return new Promise(resolve => {
            const listener = signal && addAbortListener(signal, () => {
                if (this.child.connected) this.child.send({ type: 'cancel' }, () => {});
            });
            const finish = result => {
                if (this.current?.finish !== finish) return;
                this.current = undefined;
                listener?.[Symbol.dispose]();
                resolve(result);
            };
            this.current = { onEvent, finish };
            this.child.send({ type: 'run', options }, error => {
                if (error) finish({ status: 'interrupted', error: '无法提交任务到模型调度进程' });
            });
        });
    }

    close() {
        if (this.closing) return this.exited;
        this.closing = true;
        if (this.child.connected) this.child.send({ type: 'close' }, () => {});
        return this.exited;
    }
}

module.exports = { AgentProcess };
