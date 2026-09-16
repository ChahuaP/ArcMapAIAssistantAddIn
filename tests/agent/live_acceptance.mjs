/** Live acceptance driver mounted alongside the actual web profile.
 * Uses the same preset/model-selection setup as the web session controller.
 * The supplied tasks must be read-only. This never registers substitute tools.
 */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

export const name = 'arcmap-live-acceptance';
export const inject = ['agents', 'agentPresets', 'sessions', 'agentDefaultModel'];

export function apply(ctx) {
  run(ctx).then(() => ctx.get('appExit')(0), error => {
    console.error(error);
    ctx.get('appExit')(1);
  });
}

async function run(ctx) {
  await ctx.get('loader').await();
  const runtime = process.env.ARCMAP_RUNTIME_MODULES;
  const load = name => import(pathToFileURL(path.join(runtime, '@deepseek-ai', name, 'lib/index.js')).href);
  const { createUserMessage } = await load('dsh-llm');
  const { installModelSelection } = await load('dsh-agent');
  const selection = ctx.agentDefaultModel.currentSelection();
  const tasks = ['你好', '你能做什么？请简短回答。', '请只读查看当前地图有哪些图层，列出名称，不要修改地图。'];
  const reports = [];
  for (const task of tasks) {
    const { agent } = await ctx.agents.create({
      sessionId: `session-${randomUUID()}`,
      meta: { cwd: process.cwd(), agentPreset: 'arcmap' },
      agentOptions: selection,
      setup: async agentCtx => {
        installModelSelection(agentCtx, { current: selection, assembled: undefined });
        await ctx.agentPresets.mount(agentCtx, 'arcmap');
      },
    });
    agent.followup(createUserMessage({ content: [{ type: 'text', text: task }], source: { kind: 'user' } }));
    await agent.whenIdle();
    await ctx.sessions.flush(agent.session);
    const events = agent.session.snapshotEvents();
    const header = events.findLast(e => e.type === 'request/header').data.header;
    const tools = header.tools.map(t => t.function?.name ?? t.name);
    assert.equal(tools.length, 60);
    assert.ok(tools.every(t => t.startsWith('mcp__arcmap__') || t === 'ask_user_question'));
    assert.ok(header.system.startsWith('你是 ArcMap Harness'));
    assert.ok(!header.system.includes('coding agent'));
    const messages = events.filter(e => e.type === 'assistant/message');
    const calls = events.filter(e => e.type === 'tool/call').map(e => e.data.name);
    const text = messages.flatMap(e => e.data.message.content).filter(b => b.type === 'text').map(b => b.text).join('\n');
    assert.match(text, /[\u4e00-\u9fff]/);
    assert.equal(events.findLast(e => e.type === 'turn/end').data.reason.kind, 'completed');
    if (task === tasks[0] || task === tasks[1]) assert.equal(calls.length, 0);
    else {
      assert.ok(calls.some(name => ['mcp__arcmap__get_map_context', 'mcp__arcmap__context__list_layers'].includes(name)));
      const results = events.filter(e => e.type === 'tool/result');
      assert.ok(results.length > 0);
      assert.ok(results.every(e => e.data.message.content.every(b => !b.isError)));
      const documents = results.flatMap(e => e.data.message.content)
        .flatMap(b => b.content ?? []).filter(b => b.type === 'text')
        .map(b => JSON.parse(b.text));
      assert.ok(documents.some(d => d.status === 'executed' || d.status === 'ok'),
        'GIS 验收未完成：需要 ArcMap 在线并返回真实查询结果。');
      assert.ok(documents.every(d => !['failed', 'bridge_unavailable', 'unresolved'].includes(d.status)));
    }
    assert.ok(calls.length <= 5);
    reports.push({ task, sessionId: agent.id, tools: tools.length, calls, text,
      steps: events.filter(e => e.type === 'step/start').length,
      usage: messages.map(e => e.data.usage) });
    console.log(JSON.stringify(reports.at(-1)));
  }
  writeFileSync(process.env.ARCMAP_ACCEPTANCE_REPORT, JSON.stringify(reports, null, 2));
}
