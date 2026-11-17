const http = require('node:http');
const { isIP } = require('node:net');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { validReport, uuid, maxReportBytes } = require('../../apps/desktop/diagnostic-schema.cjs');
const { validConversation, maxConversationBytes, conversationRetentionDays } = require('../../apps/desktop/conversation-schema.cjs');

const root = process.env.GUIZHI_REPORT_DIRECTORY || '/data';
const conversationRoot = path.join(root, 'conversations');
fs.mkdirSync(conversationRoot, { recursive: true, mode: 0o700 });
const validName = name => name.endsWith('.json') && uuid.test(name.slice(0, -5));
const storeAt = (directory, capacity, days, maxBytes, valid) => ({
    directory, capacity, days, maxBytes, valid, count: fs.readdirSync(directory).filter(validName).length,
});
const diagnostics = storeAt(root, 10_000, 14, maxReportBytes, validReport);
const conversations = storeAt(conversationRoot, 250, conversationRetentionDays, maxConversationBytes, validConversation);
let uploading = 0;
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

async function store(report, destination) {
    const file = path.join(destination.directory, report.id + '.json');
    const pending = path.join(destination.directory, randomUUID() + '.pending');
    if (destination.count >= destination.capacity) return 503;
    destination.count++;
    try {
        const stored = { receivedAt: new Date().toISOString(), ...report };
        await fsp.writeFile(pending, JSON.stringify(stored) + '\n', { flag: 'wx', mode: 0o600 });
        await fsp.link(pending, file);
        return 202;
    } catch (error) {
        destination.count--;
        if (error.code === 'EEXIST') return 202;
        console.error('Report write failed:', error.code);
        return 503;
    } finally { await fsp.rm(pending, { force: true }); }
}

async function receive(request, response) {
    if (request.url === '/health' && request.method === 'GET') return respond(response, 204);
    const destination = request.url === '/v1/diagnostics' ? diagnostics : request.url === '/v1/conversations' ? conversations : null;
    if (!destination || request.method !== 'POST') return respond(response, 404);
    const client = request.headers['x-real-ip'];
    if (limited(isIP(client) ? client : request.socket.remoteAddress)) return respond(response, 429);
    if (request.headers['content-type'] !== 'application/json') return respond(response, 415);
    const length = Number(request.headers['content-length']);
    if (!Number.isSafeInteger(length) || length < 1 || length > destination.maxBytes) return respond(response, 413);
    const conversation = destination === conversations;
    if (conversation && uploading >= 4) return respond(response, 429);
    if (conversation) uploading++;
    try {
        const chunks = [];
        for await (const chunk of request) chunks.push(chunk);
        let report;
        try { report = JSON.parse(Buffer.concat(chunks).toString('utf8')); }
        catch { return respond(response, 400); }
        if (!destination.valid(report)) return respond(response, 400);
        return respond(response, await store(report, destination));
    } finally { if (conversation) uploading--; }
}

async function expire(destination) {
    const deadline = Date.now() - destination.days * 86_400_000;
    for (const name of await fsp.readdir(destination.directory)) {
        if (!validName(name) && !/^[a-f0-9-]{36}\.pending$/.test(name)) continue;
        const file = path.join(destination.directory, name);
        if ((await fsp.stat(file)).mtimeMs >= deadline) continue;
        await fsp.unlink(file);
        if (validName(name)) destination.count--;
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
const prune = () => {
    for (const destination of [diagnostics, conversations]) {
        void expire(destination).catch(error => console.error('Report expiration failed:', error.code));
    }
};
prune();
setInterval(prune, 60 * 60_000).unref();
if (require.main === module) server.listen(9444, '0.0.0.0');
module.exports = server;
