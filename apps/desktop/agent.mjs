import { dispatch, redact } from '../../src/guizhi/infrastructure/dsh/sdk.mjs';
import { DispatchService } from '../../src/guizhi/infrastructure/dsh/service.mjs';

let service;
let modelOptions;
let stop;
let running;
let closing;
function send(message) { if (process.connected) process.send(message); }

function close() {
    if (closing) return;
    closing = true;
    stop?.abort();
    void Promise.resolve(running).finally(() => service?.close()).catch(() => {
        process.exitCode = 1;
    }).finally(() => { if (process.connected) process.disconnect(); });
}

process.on('disconnect', close);
process.on('message', message => {
    if (message.type === 'close') return close();
    if (closing) return;
    if (message.type === 'cancel') return stop?.abort();
    if (message.type === 'warm' && !service) {
        modelOptions = message.options;
        service = new DispatchService(modelOptions);
        void service.ready.then(() => send({ type: 'ready' }), error => {
            send({ type: 'error', error: redact(error.message, message.options.apiKey) });
            close();
        });
    } else if (message.type === 'run' && service && !running) {
        stop = new AbortController();
        running = dispatch({ ...message.options, ...modelOptions }, { signal: stop.signal, getService: () => service,
            onEvent: event => send({ type: 'event', event }) });
        const finish = result => { running = undefined; stop = undefined; send({ type: 'result', result }); };
        void running.then(finish, error => finish({ status: stop.signal.aborted ? 'cancelled' : 'failed',
            error: redact(error.message, modelOptions.apiKey), error_code: error.code, failureStep: 'initialize_dispatch' }));
    }
});
