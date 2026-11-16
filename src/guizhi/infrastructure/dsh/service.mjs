import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { loadHarness, runtimeEnv } from './sdk.mjs';
import models from './model-config.cjs';

const profile = fileURLToPath(new URL('./profile.yml', import.meta.url));
const mcp = fileURLToPath(new URL('./mcp.py', import.meta.url));

export class DispatchService {
    constructor(options) {
        this.options = options;
        this.id = randomUUID();
        this.root = mkdtempSync(join(tmpdir(), 'guizhi-dsh-'));
        this.binding = join(this.root, 'binding.json');
        this.ready = this.start();
        // Startup is also awaited by the first dispatch.
        void this.ready.catch(() => {});
    }

    async start() {
        const { options } = this;
        const { DeepSeekHarness } = await loadHarness(options.python, options.runtime);
        const config = models.modelConfig(options.modelConfig);
        const { id: provider } = models.route(config);
        this.harness = new DeepSeekHarness({ profile: 'sdk-minimal', patches: [profile],
            dshHome: join(this.root, 'dsh-home'), cwd: this.root, processCwd: this.root,
            provider, model: config.model, maxTokens: 2048, initializeTimeoutMs: 45000,
            env: { ...runtimeEnv({ ...options, modelConfig: config }, ['-I', '-B', mcp, '--binding', this.binding]),
                GUIZHI_DISPATCH_CONFIG: JSON.stringify({ binding: this.binding }) } });
        await this.harness.start();
    }

    bind(job) {
        if (this.active) throw new Error('已有任务占用模型调度服务');
        if (this.closing) throw new Error('模型调度服务正在退出');
        const pending = this.binding + '.pending';
        try {
            writeFileSync(pending, JSON.stringify(job), { flag: 'wx' });
            renameSync(pending, this.binding);
            this.active = job;
        } finally { rmSync(pending, { force: true }); }
    }

    release(id) {
        if (this.active?.id !== id) return;
        rmSync(this.binding, { force: true });
        this.active = null;
    }

    async archive(id, directory) {
        const { flushRequest, flushReceipt } = this.active;
        writeFileSync(flushRequest, '');
        const deadline = performance.now() + 3000;
        while (!existsSync(flushReceipt) && performance.now() < deadline) await delay(25);
        if (!existsSync(flushReceipt) || readFileSync(flushReceipt, 'utf8') !== 'ok') throw new Error('本轮会话留痕未写入');
        const sessions = join(this.root, 'dsh-home', 'sessions');
        for (const project of readdirSync(sessions, { withFileTypes: true })) {
            if (!project.isDirectory()) continue;
            const log = join(sessions, project.name, id, 'session.v4.jsonl');
            if (!existsSync(log)) continue;
            const target = join(directory, 'dsh-home', 'sessions', project.name, id);
            mkdirSync(target, { recursive: true });
            copyFileSync(log, join(target, 'session.v4.jsonl'));
            rmSync(flushRequest);
            rmSync(flushReceipt);
            return;
        }
        throw new Error('本轮会话留痕文件缺失');
    }

    close() {
        return this.closing ??= this.ready.catch(() => {}).then(async () => {
            await this.harness?.close();
            rmSync(this.root, { recursive: true, force: true });
        });
    }
}
