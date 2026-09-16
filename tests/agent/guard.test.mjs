import test from 'node:test';
import assert from 'node:assert/strict';
import { stopReason, assertLocalModel } from '../../dsh/plugins/arcmap-agent/lib/guard.js';

const call = (id, args = { layer: '道路' }, turn = 1) => ({
  type: 'tool/call', data: { turn, callId: id, name: 'mcp__arcmap__context__describe_layer', arguments: JSON.stringify(args) },
});
const result = (id, error = false, status = 'ok', turn = 1) => ({
  type: 'tool/result', data: { turn, message: { source: { callId: id }, content: [
    { type: 'tool-result', isError: error, content: [{ type: 'text', text: JSON.stringify({ status }) }] },
  ] } },
});

test('repeated calls stop even when all tool results report success', () => {
  const events = [call('a'), result('a'), call('b'), result('b'), call('c'), result('c')];
  assert.match(stopReason(events, 1), /连续三次/);
  assert.equal(stopReason(events, 2), undefined);
});
test('argument key ordering cannot evade repeat detection', () => {
  assert.match(stopReason([call('a', { a: 1, b: 2 }), call('b', { b: 2, a: 1 }), call('c', { a: 1, b: 2 })], 1), /连续三次/);
});
test('distinct work is allowed', () => {
  assert.equal(stopReason([call('a'), call('b', { layer: '河流' }), call('c')], 1), undefined);
});
test('an indeterminate dispatch is never automatically replayed', () => {
  assert.match(stopReason([call('a'), result('a', false, 'indeterminate')], 1), /禁止自动重放/);
});
test('two transport or domain failures stop the turn', () => {
  for (const [error, status] of [[true, 'ok'], [false, 'failed'], [false, 'violated'], [false, 'bridge_unavailable']]) {
    assert.match(stopReason([call('a'), result('a', error, status), call('b'), result('b', error, status)], 1), /失败两次/);
  }
});
test('alternating unproductive calls still have a hard turn limit', () => {
  const events = Array.from({ length: 32 }, (_, i) => ({ type: 'step/start', data: { turn: 1, step: i + 1 } }));
  assert.match(stopReason(events, 1), /32 步/);
});
test('advertised capacity alone cannot pass the local model check', () => {
  assert.throws(() => assertLocalModel({ capabilities: ['tools'], model_info: { context_length: 32768 } }), /num_ctx/);
  assert.throws(() => assertLocalModel({ capabilities: ['tools'], parameters: 'num_ctx 4096' }), /num_ctx/);
  assert.throws(() => assertLocalModel({ capabilities: ['completion'], parameters: 'num_ctx 32768' }), /工具调用/);
  assert.doesNotThrow(() => assertLocalModel({ capabilities: ['tools'], parameters: 'temperature 0.2\nnum_ctx 32768\nnum_predict 2048' }));
});
