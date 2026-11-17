import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs';

export const inject = ['llm'];

export function meter(config) {
    let calls = 0;
    let charged = 0;
    let reserved = 0;
    let refused = null;
    let synthesized = false;

    function checkpoint(status, estimate = 0) {
        const pending = config.checkpoint + '.pending';
        writeFileSync(pending, JSON.stringify({ status, calls, charged, reserved, estimate,
            token_limit: config.tokens, call_limit: config.calls }));
        renameSync(pending, config.checkpoint);
    }

    checkpoint('ready');
    return async function* (options, next) {
        if (existsSync(config.operation)) {
            const receipt = config.synthesize && !synthesized && JSON.parse(readFileSync(config.operation, 'utf8'));
            const direct = receipt?.steps?.some(step => step.statistics?.area_summary || step.statistics?.lookup ||
                step.statistics?.projection || step.statistics?.selected_count !== undefined || step.statistics?.coverage_ratio !== undefined);
            if (receipt?.status !== 'succeeded' || direct) {
                yield { type: 'finish', reason: { kind: 'stop' } };
                return;
            }
            synthesized = true;
            // The workflow already ran. The remaining response only explains its verified result.
            options = { ...options, tools: [] };
        }
        const bytes = Buffer.byteLength(JSON.stringify([options.system, options.messages, options.tools, options.toolHistory]));
        const estimate = bytes + (options.maxTokens ?? 1024) + 512;
        if (refused !== null || calls >= config.calls || charged + reserved + estimate > config.tokens) {
            refused ??= estimate;
            checkpoint('exhausted', refused);
            yield { type: 'finish', reason: { kind: 'error', failure: {
                code: 'GUIZHI_BUDGET', message: '任务模型预算已用完' } } };
            return;
        }
        calls += 1;
        reserved += estimate;
        checkpoint('calling', estimate);
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
            checkpoint(refused === null ? 'ready' : 'exhausted', refused ?? 0);
        }
    };
}

export function apply(ctx, config) {
    ctx.on('llm/stream', meter(config));
}
