/** Checks over durable current-turn facts; generated progress is not evidence. */
export function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  }
  return value;
}

function callKey(call) {
  let args = call.arguments;
  if (typeof args === 'string') {
    try { args = JSON.parse(args); } catch { /* Preserve malformed input as evidence. */ }
  }
  return JSON.stringify([call.name, canonical(args)]);
}

function failureEvidence({ content, isError }) {
  const evidence = [];
  for (const item of content) {
    if (item.type !== 'text') continue;
    let value;
    try { value = JSON.parse(item.text); } catch {
      if (isError) evidence.push(item.text);
      continue;
    }
    if (value === null || typeof value !== 'object') {
      if (isError) evidence.push(item.text);
      continue;
    }
    if (isError || ['failed', 'violated', 'bridge_unavailable', 'verification_failed'].includes(value.status)) {
      // Operation IDs change on each attempt; only the actual rejection is
      // evidence of a repeated failure. A new cause must reach the model.
      evidence.push({ isError: Boolean(isError), status: value.status,
        violations: value.violations, message: value.message, error: value.error,
        verification: value.verification });
    }
  }
  if (isError && !content.some(item => item.type === 'text')) {
    evidence.push({ isError: true, content });
  }
  return evidence.length ? JSON.stringify(canonical(evidence)) : undefined;
}

export function stopReason(events, turn) {
  const current = events.filter(event => event.data?.turn === turn);
  const calls = current.filter(event => event.type === 'tool/call').map(event => event.data);
  const results = current.filter(event => event.type === 'tool/result').map(event => event.data.message);
  const indeterminate = results.some(result => result.content.some(item => {
    if (item.type !== 'text') return false;
    try { return JSON.parse(item.text)?.status === 'indeterminate'; } catch { return false; }
  }));
  if (indeterminate) return '操作已派发但结果尚未确认，本轮停止。请核查操作结果，禁止自动重放。';
  const keys = new Map(calls.map(call => [call.callId, callKey(call)]));
  const failures = new Map();
  for (const result of results) {
    const call = keys.get(result.toolCallId);
    const evidence = failureEvidence(result);
    const key = JSON.stringify([call, evidence]);
    if (call && evidence) failures.set(key, (failures.get(key) ?? 0) + 1);
  }
  if ([...failures.values()].some(count => count >= 2)) {
    return '同一操作以相同参数遇到同一失败原因两次，本轮已停止。请根据上方工具错误修正条件后再试。';
  }
  const tail = calls.slice(-3);
  if (tail.length === 3 && tail.every(call => callKey(call) === callKey(tail[0]))) {
    return '检测到连续三次重复调用且参数未变化，本轮已停止，避免继续空转。';
  }
  if (current.filter(event => event.type === 'step/start').length >= 32) {
    return '本轮已达到 32 步执行上限并停止。请查看已完成的操作，再提出剩余任务。';
  }
}

export function assertLocalModel(info) {
  const context = /^num_ctx\s+(\d+)\s*$/m.exec(info.parameters ?? '');
  if (!context || Number(context[1]) !== 32768) {
    throw new Error('本地模型必须实际配置 num_ctx 32768。请运行安装目录中的 configure_ollama.ps1 后再试。');
  }
  if (!info.capabilities?.includes('tools')) throw new Error('本地模型不支持工具调用，无法执行 GIS 任务。');
}
