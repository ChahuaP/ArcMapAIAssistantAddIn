const http = require('node:http');
const { isIP } = require('node:net');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { validReport, uuid } = require('../../apps/desktop/diagnostic-schema.cjs');

const root = '/data';
const maxBytes = 4096;
const capacity = 10_000;
const retention = 14 * 86_400_000;
const validName = name => name.endsWith('.json') && uuid.test(name.slice(0, -5));
let count = fs.readdirSync(root).filter(validName).length;
let window = 0;
let received = 0;
const clients = new Map();

function limited(ip) {
    const minute = Math.floor(Date.now() / 60_000);
    if (minute !== window) {
        window = minute;
        received = 0;
        clients.clear();
    }
    if (received >= 120 || (clients.get(ip) || 0) >= 20) return true;
    received++;
    clients.set(ip, (clients.get(ip) || 0) + 1);
    return false;
}

function respond(response, status) {
    response.writeHead(status, { 'cache-control': 'no-store', connection: 'close' });
    response.end();
}

async function store(report) {
    const file = path.join(root, report.id + '.json');
    const pending = path.join(root, randomUUID() + '.pending');
    if (count >= capacity) return 503;
    count++;
    try {
        const stored = { receivedAt: new Date().toISOString(), ...report };
        await fsp.writeFile(pending, JSON.stringify(stored) + '\n', { flag: 'wx', mode: 0o600 });
        await fsp.link(pending, file);
        return 202;
    } catch (error) {
        count--;
        if (error.code === 'EEXIST') return 202;
        console.error('Report write failed:', error.code);
        return 503;
    } finally { await fsp.rm(pending, { force: true }); }
}

async function receive(request, response) {
    if (request.url === '/health' && request.method === 'GET') return respond(response, 204);
    if (request.url !== '/v1/diagnostics' || request.method !== 'POST') return respond(response, 404);
    const client = request.headers['x-real-ip'];
    if (limited(isIP(client) ? client : request.socket.remoteAddress)) return respond(response, 429);
    if (request.headers['content-type'] !== 'application/json') return respond(response, 415);
    const length = Number(request.headers['content-length']);
    if (!Number.isSafeInteger(length) || length < 1 || length > maxBytes) return respond(response, 413);
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    let report;
    try { report = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
    catch { return respond(response, 400); }
    if (!validReport(report)) return respond(response, 400);
    return respond(response, await store(report));
}

async function expire() {
    const deadline = Date.now() - retention;
    for (const name of await fsp.readdir(root)) {
        if (!validName(name) && !/^[a-f0-9-]{36}\.pending$/.test(name)) continue;
        const file = path.join(root, name);
        if ((await fsp.stat(file)).mtimeMs >= deadline) continue;
        await fsp.unlink(file);
        if (validName(name)) count--;
    }
}

const server = http.createServer({
    maxHeaderSize: 8192, requestTimeout: 5000, headersTimeout: 5000, connectionsCheckingInterval: 1000,
}, (request, response) => {
    void receive(request, response).catch(error => {
        if (!response.destroyed) respond(response, 400);
        if (error.code !== 'ECONNRESET') console.error('Report request failed:', error.code || error.name);
    });
});
server.maxConnections = 64;
server.setTimeout(5000, socket => socket.destroy());
const prune = () => void expire().catch(error => console.error('Report expiration failed:', error.code));
prune();
setInterval(prune, 60 * 60_000).unref();
server.listen(9444, '0.0.0.0');
