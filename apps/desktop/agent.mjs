import { dispatch } from '../../src/guizhi/infrastructure/dsh/sdk.mjs';

const stop = new AbortController();
process.on('disconnect', () => stop.abort());
let running = false;
process.on('message', async message => {
    if (message.type === 'cancel') {
        stop.abort();
        return;
    }
    if (message.type !== 'start' || running) return;
    running = true;
    try {
        const result = await dispatch(message.options, {
            signal: stop.signal, onEvent: event => process.connected && process.send({ type: 'event', event }),
        });
        if (process.connected) process.send({ type: 'result', result });
    } catch (error) {
        if (process.connected) process.send({ type: 'result', result: { status: 'failed', error: error.message } });
    } finally {
        if (process.connected) process.disconnect();
    }
});
