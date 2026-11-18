import { existsSync, renameSync, writeFileSync } from 'node:fs';

export const inject = ['llm', 'agents', 'tokenMeter'];

export function meter(config, measure) {
    let calls = 0;
    let charged = 0;
    let reserved = 0;
    let compactions = 0;
    let refused = null;

    function checkpoint(status) {
        const pending = config.checkpoint + '.pending';
        writeFileSync(pending, JSON.stringify({ status, calls, charged, reserved, compactions,
            reason: refused, token_limit: config.tokens ?? null, call_limit: config.calls ?? null }));
        renameSync(pending, config.checkpoint);
    }

    checkpoint('ready');
    return async function* (options, next) {
        if (config.operation && existsSync(config.operation)) {
            yield { type: 'finish', reason: { kind: 'stop' } };
            return;
        }
        // Explicit QA/caller quotas concern cumulative usage. Context pressure is
        // managed independently by the native DSH compactor at step boundaries.
        const estimate = config.tokens == null ? 0 : measure(options) + (options.maxTokens ?? 0);
        if (config.calls != null && calls >= config.calls) refused = 'call_limit';
        if (config.tokens != null && charged + reserved + estimate > config.tokens) refused = 'token_limit';
        if (refused) {
            checkpoint('exhausted');
            yield { type: 'finish', reason: { kind: 'error', failure: {
                code: 'GUIZHI_BUDGET', message: refused === 'call_limit' ? '达到本次指定的模型调用次数' : '达到本次指定的模型用量限额' } } };
            return;
        }
        calls++;
        if (options.purpose === 'compaction') compactions++;
        reserved += estimate;
        checkpoint('calling');
        let billed;
        try {
            for await (const chunk of next()) {
                if (chunk.type === 'usage') {
                    const usage = chunk.usage;
                    billed = usage.totalTokens ?? (usage.inputTokens + usage.outputTokens +
                        (usage.cacheReadTokens ?? 0) + (usage.cacheWriteTokens ?? 0));
                }
                yield chunk;
            }
        } finally {
            reserved -= estimate;
            charged += Number.isFinite(billed) ? billed : estimate;
            checkpoint(refused ? 'exhausted' : 'ready');
        }
    };
}

export function apply(ctx, config) {
    ctx.on('llm/stream', meter(config, options => ctx.tokenMeter.measure(ctx.agents.get(options.sessionId).session).totalTokens));
}
