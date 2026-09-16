import { readFileSync } from 'node:fs';
import { assertLocalModel, stopReason } from './guard.js';

export const name = 'arcmap-agent';
export const inject = ['systemPrompt'];

export function apply(ctx) {
  ctx.systemPrompt.section({
    name: 'arcmap:instructions', order: 0, complete: true,
    text: readFileSync(new URL('./persona.txt', import.meta.url), 'utf8'),
  });
  ctx.systemPrompt.suppressRuntimeContext();
  ctx.on('system-prompt/assemble', async (assembly, context, next) => {
    const result = await next();
    const unexpected = result.tools.filter(tool =>
      !tool.name.startsWith('mcp__arcmap__') && tool.name !== 'ask_user_question');
    if (unexpected.length) throw new Error(`GIS 工具隔离失败：${unexpected.map(t => t.name).join('、')}`);
    if (context.agent && !result.tools.some(tool => tool.name === 'mcp__arcmap__get_map_context')) {
      throw new Error('GIS 工具尚未就绪，请检查边界服务连接后重试。');
    }
    return result;
  });
  ctx.on('agent/pre-step', async ({ agent, turn }, next) => {
    const reason = stopReason(agent.session.snapshotEvents(), turn);
    if (reason) throw new Error(reason);
    return next();
  });
  // Verify server-side model parameters each turn and on route changes.
  const checked = new WeakMap();
  ctx.on('agent/request', async ({ agent, turn, signal }, next) => {
    const config = await next();
    if (config.provider !== 'ollama') return config;
    const key = `${turn}:${config.model}`;
    if (checked.get(agent) !== key) {
      const response = await fetch('http://127.0.0.1:11434/api/show', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: config.model }),
        signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
      });
      if (!response.ok) throw new Error(`本地模型尚未就绪（HTTP ${response.status}）。请运行 configure_ollama.ps1。`);
      assertLocalModel(await response.json());
      checked.set(agent, key);
    }
    return { ...config, maxTokens: 2048, temperature: 0.2 };
  });
}
