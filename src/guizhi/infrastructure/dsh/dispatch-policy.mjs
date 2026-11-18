import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { meter } from './budget.mjs';

export const inject = ['llm', 'tools', 'agents', 'sessions', 'tokenMeter'];

export function apply(ctx, { binding }) {
    let current;
    let gisCalls = 0;
    function bindingValue() {
        try { return JSON.parse(readFileSync(binding, 'utf8')); }
        catch (error) { if (error.code !== 'ENOENT') throw error; return null; }
    }
    function job(id) {
        const value = bindingValue();
        if (!value) throw new Error('No active Guizhi dispatch');
        if (value.id !== id) throw new Error('Guizhi dispatch does not own this session');
        return value;
    }

    ctx.on('agent/created', ({ agent }) => {
        const active = bindingValue();
        if (active?.id !== agent.session.id) return;
        agent.ctx.systemPrompt.context({ name: 'guizhi-workspace', order: 10, required: true, interpolate: false,
            text: JSON.stringify(active.modelContext) });
        if (active.fixedInvocation) {
            agent.ctx.tools.restrict({ allow: ['mcp__guizhi__execute_workflow'] });
        }
    });

    ctx.on('llm/stream', async function* (options, next) {
        const active = job(options.sessionId);
        if (existsSync(active.cancelled)) throw new Error('Guizhi dispatch cancelled');
        if (current?.id !== active.id) current = { id: active.id, stream: meter(active.budget, () => ctx.tokenMeter.measure(ctx.agents.get(active.id).session).totalTokens) };
        yield* current.stream(options, next);
    });

    ctx.on('tools/execute', async (execution, next) => {
        if (!execution.name.startsWith('mcp__guizhi__')) return next();
        const active = job(execution.agent?.session.id);
        if (existsSync(active.cancelled)) throw new Error('Guizhi dispatch cancelled');
        gisCalls++;
        try { return await next(); }
        finally { gisCalls--; }
    });

    let draining = false;
    const timer = setInterval(async () => {
        if (draining) return;
        const active = bindingValue();
        if (!active) return;
        const agent = ctx.agents.get(active.id);
        // GIS reads this marker itself. Let it return its receipt before interrupting DSH.
        if (gisCalls === 0 && existsSync(active.cancelled)) agent?.cancel({ kind: 'user' });
        if (!agent || !existsSync(active.flushRequest) || existsSync(active.flushReceipt)) return;
        draining = true;
        try {
            await agent.whenIdle();
            await ctx.sessions.flush(agent.session);
            writeFileSync(active.flushReceipt, 'ok');
        } catch {
            writeFileSync(active.flushReceipt, 'error');
        } finally { draining = false; }
    }, 50);
    timer.unref();
    ctx.effect(() => () => clearInterval(timer));
}
