
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs, promisify } from 'node:util';
import { externalPath, loadHarness, runtimeEnv } from './sdk.mjs';

const cli = fileURLToPath(new URL('../../__main__.py', import.meta.url));
const profile = fileURLToPath(new URL('./maintenance.yml', import.meta.url));
const exec = promisify(execFile);
const read = file => JSON.parse(readFileSync(file, 'utf8'));

async function local(options, command, ...args) {
    const { stdout } = await exec(options.python, ['-I', '-B', cli, '--workspace', options.workspace, command, ...args],
        { windowsHide: true, timeout: 245000, maxBuffer: 4 << 20 });
    return JSON.parse(stdout);
}

export async function improve(options) {
    if (!isAbsolute(options.request ?? '') || !/^MiniMax-[A-Za-z0-9.-]+$/.test(options.model ?? '')) {
        throw new Error('Use an absolute improvement request path and a MiniMax model');
    }
    options = { ...options, workspace: externalPath(options.workspace) };
    const env = runtimeEnv(options);
    const { DeepSeekHarness, versions } = await loadHarness(options.python, options.runtime);
    const plan = await local(options, 'improvement-plan', options.request);
    const budget = join(plan.directory, 'model-budget.json');
    const proposal = join(plan.directory, 'proposal.json');
    const harness = new DeepSeekHarness({
        profile: 'sdk-minimal', patches: [profile], dshHome: join(plan.directory, 'maintenance-home'),
        cwd: plan.directory, processCwd: plan.directory, provider: 'minimax-cn', model: options.model,
        maxTokens: 2048, initializeTimeoutMs: 35000,
        env: { ...env, GUIZHI_BUDGET_CONFIG: JSON.stringify({ tokens: 50000, calls: 1, checkpoint: budget, operation: proposal }) },
    });
    let answer;
    try {
        await harness.start();
        let timer;
        let result;
        try {
            result = await Promise.race([harness.run(JSON.stringify(plan.prompt), { sessionId: randomUUID() }),
                new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Maintenance model timed out')), 65000); })]);
        } finally {
            clearTimeout(timer);
        }
        const charge = read(budget);
        if (result.finishReason !== 'completed' || charge.calls !== 1 || charge.status !== 'ready') {
            throw new Error('Maintenance model did not produce a complete proposal');
        }
        const content = result.finalResponse.trim().replace(/^\x60\x60\x60(?:json)?\s*([\s\S]*?)\s*\x60\x60\x60$/i, '$1');
        answer = JSON.parse(content);
        writeFileSync(join(plan.directory, 'model.json'), JSON.stringify({ provider: 'minimax-cn', model: options.model,
            profile_sha256: createHash('sha256').update(readFileSync(profile)).digest('hex'),
            sdk_version: versions['@deepseek-ai/dsh-sdk-client'], budget: charge }), { flag: 'wx' });
        writeFileSync(proposal, JSON.stringify(answer), { flag: 'wx' });
    } catch (error) {
        const secret = process.env.MINIMAX_API_KEY;
        const message = String(error.message).replaceAll(secret, '[密钥]');
        writeFileSync(join(plan.directory, 'model-failure.json'), JSON.stringify({ status: 'failed', message }), { flag: 'wx' });
        throw new Error(message);
    } finally {
        await harness.close();
    }
    return local(options, 'improvement-apply', plan.id, proposal);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
    const { values } = parseArgs({ options: {
        workspace: { type: 'string' }, runtime: { type: 'string' }, python: { type: 'string' },
        request: { type: 'string' }, model: { type: 'string', default: 'MiniMax-M3' },
    } });
    improve(values).then(result => {
        console.log(JSON.stringify(result));
        if (result.status !== 'published') process.exitCode = 1;
    }).catch(error => {
        const secret = process.env.MINIMAX_API_KEY;
        const message = secret ? String(error.message).replaceAll(secret, '[密钥]') : error.message;
        console.error(JSON.stringify({ status: 'failed', error: message }));
        process.exitCode = 1;
    });
}
