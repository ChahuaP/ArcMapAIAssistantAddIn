import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { meter } from './budget.mjs';

export const inject = ['llm', 'tools', 'agents', 'sessions', 'tokenMeter', 'subagents'];

export function apply(ctx, { binding }) {
    let current;
    let gisCalls = 0;
    let team;
    const childLifetime = 120000;
    const readTools = ['read_approval_materials', 'inspect_current_map', 'inspect_layer_rows', 'inspect_layer_crs',
        'list_coordinate_systems', 'describe_bound_layers', 'list_rule_catalogs', 'list_run_results', 'list_capabilities',
        'lookup_layer_field', 'summarize_layer', 'sum_layer_area', 'list_skills'].map(name => 'mcp__guizhi__' + name);

    function bindingValue() {
        try { return JSON.parse(readFileSync(binding, 'utf8')); }
        catch (error) { if (error.code !== 'ENOENT') throw error; return null; }
    }
    function job(id) {
        const value = bindingValue();
        if (!value) throw new Error('No active Guizhi dispatch');
        if (value.id !== id && (team?.id !== value.id || !team.children.has(id))) throw new Error('Guizhi dispatch does not own this session');
        if (value.id !== id && performance.now() >= team.children.get(id).deadline)
            throw new Error('子任务已超时');
        return value;
    }

    ctx.on('agent/created', ({ agent }) => {
        const active = bindingValue();
        if (!active) return;
        const child = agent.session.header.origin === 'subagent' && agent.session.header.parentSession === active.id;
        if (active.id !== agent.session.id && !child) return;
        if (team?.id !== active.id) team = { id: active.id, children: new Map() };
        if (child) {
            const member = team.children.get(agent.session.id) || { deadline: performance.now() + childLifetime };
            team.children.set(agent.session.id, { ...member, agent });
            const web = JSON.parse(process.env.GUIZHI_WEB_TOOLS);
            agent.ctx.tools.restrict({ allow: [...readTools, 'team_task_list', 'team_task_get', 'team_task_update',
                'send_message', 'list_agents', ...(web.search ? ['web_search'] : []), ...(web.fetch ? ['web_fetch'] : [])] });
        }
        agent.ctx.systemPrompt.context({ name: 'guizhi-workspace', order: 10, required: true, interpolate: false,
            text: JSON.stringify(active.modelContext) });
        if (!child && active.fixedInvocation) {
            agent.ctx.tools.restrict({ allow: ['mcp__guizhi__execute_workflow'] });
        }
    });

    ctx.on('llm/stream', async function* (options, next) {
        const active = job(options.sessionId);
        if (existsSync(active.cancelled)) throw new Error('Guizhi dispatch cancelled');
        if (current?.id !== active.id) current = { id: active.id, stream: meter(active.budget, request => ctx.tokenMeter.measure(ctx.agents.get(request.sessionId).session).totalTokens) };
        yield* current.stream(options, next);
    });

    ctx.on('tools/execute', async (execution, next) => {
        const id = execution.agent?.session.id;
        const active = job(id);
        if (existsSync(active.cancelled)) throw new Error('Guizhi dispatch cancelled');
        if (!execution.name.startsWith('mcp__guizhi__')) return next();
        if (execution.name === 'mcp__guizhi__deliver_result' && team?.id === active.id &&
                [...team.children.values()].some(member => member.agent.status !== 'idle'))
            throw new Error('仍有子任务在执行，请先等候回执或中断子任务，再交付结果');
        if (id !== active.id && !readTools.includes(execution.name)) throw new Error('子智能体只能核查与读取证据');
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
        const cancelled = existsSync(active.cancelled);
        const members = team?.id === active.id ? [...team.children.values()] : [];
        for (const member of members) {
            if (member.agent.status !== 'idle' && (cancelled || performance.now() >= member.deadline))
                member.agent.cancel({ kind: 'user' });
        }
        if (gisCalls === 0 && cancelled) agent?.cancel({ kind: 'user' });
        if (!agent || !existsSync(active.flushRequest) || existsSync(active.flushReceipt)) return;
        draining = true;
        try {
            await ctx.subagents.drainDescendants([agent]);
            await agent.whenIdle();
            await ctx.sessions.flush(agent.session);
            writeFileSync(active.flushReceipt, JSON.stringify(team?.id === active.id ? [...team.children.keys()] : []));
        } catch (error) {
            writeFileSync(active.flushReceipt, JSON.stringify({ error: error.message }));
        } finally { draining = false; }
    }, 50);
    timer.unref();
    ctx.effect(() => () => clearInterval(timer));
}
