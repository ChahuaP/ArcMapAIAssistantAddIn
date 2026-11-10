const { randomUUID } = require('node:crypto');
const { setTimeout: delay } = require('node:timers/promises');
const { gzipSync, gunzipSync } = require('node:zlib');

const endpoint = 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_nostream';
const audioChunk = 6400;
const payloadLimit = 1 << 20;

function encode(type, sequence, final, data) {
    const payload = gzipSync(data);
    const header = Buffer.alloc(12);
    header[0] = 0x11;
    header[1] = (type << 4) | (final ? 3 : 1);
    header[2] = type === 1 ? 0x11 : 0x01;
    header.writeInt32BE(final ? -sequence : sequence, 4);
    header.writeUInt32BE(payload.length, 8);
    return Buffer.concat([header, payload]);
}

function decode(bytes) {
    const invalid = () => new Error('火山引擎返回了无效的识别数据');
    if (bytes.length < 8 || bytes[0] !== 0x11) throw invalid();
    const type = bytes[1] >> 4;
    const flags = bytes[1] & 0x0f;
    if (type === 15) throw new Error('火山引擎识别失败（' + bytes.readUInt32BE(4) + '）');
    if (type !== 9 || flags > 3 || bytes[2] >> 4 !== 1) throw invalid();
    const start = flags & 1 ? 8 : 4;
    if (bytes.length < start + 4 || bytes.readUInt32BE(start) !== bytes.length - start - 4) throw invalid();
    const compression = bytes[2] & 0x0f;
    let payload = bytes.subarray(start + 4);
    if (compression === 1) payload = gunzipSync(payload, { maxOutputLength: payloadLimit });
    else if (compression !== 0) throw invalid();
    return { final: Boolean(flags & 2), text: JSON.parse(payload).result?.text };
}

function recognizeVolc(WebSocket, profile, wav, signal, partial) {
    const lifetime = AbortSignal.any([signal, AbortSignal.timeout(45_000)]);
    lifetime.throwIfAborted();
    const id = randomUUID();
    return new Promise((resolve, reject) => {
        const socket = new WebSocket(endpoint, { handshakeTimeout: 8000, maxPayload: payloadLimit,
            headers: { 'X-Api-Key': profile.key, 'X-Api-Resource-Id': profile.model, 'X-Api-Request-Id': id } });
        let finished = false;
        let text = '';
        function finish(error) {
            if (finished) return;
            finished = true;
            lifetime.removeEventListener('abort', abort);
            socket.terminate();
            if (error) reject(error);
            else resolve(text);
        }
        function abort() {
            finish(signal.aborted ? signal.reason : new Error('火山引擎语音识别超时'));
        }
        lifetime.addEventListener('abort', abort, { once: true });
        socket.on('error', error => {
            const status = /^Unexpected server response: (\d{3})$/.exec(error.message)?.[1];
            finish(new Error(status ? '火山引擎语音识别连接失败（HTTP ' + status + '），请检查密钥和资源权限'
                : '无法连接火山引擎语音识别，请检查网络'));
        });
        socket.on('close', () => finish(new Error('火山引擎在返回识别结果前断开了连接')));
        socket.on('message', (bytes, binary) => {
            try {
                if (!binary) throw new Error('火山引擎返回了无效的识别数据');
                const result = decode(bytes);
                if (typeof result.text === 'string' && result.text !== text) {
                    text = result.text;
                    if (text.length > 4000) throw new Error('识别文字过长');
                    partial(text);
                }
                if (result.final) finish();
            } catch (error) { finish(error); }
        });
        socket.once('open', async () => {
            try {
                const config = { user: { uid: id }, audio: { format: 'pcm', codec: 'raw', rate: 16000,
                    bits: 16, channel: 1, language: 'zh-CN' }, request: { model_name: 'bigmodel', enable_itn: true } };
                socket.send(encode(1, 1, false, Buffer.from(JSON.stringify(config))));
                const pcm = wav.subarray(44);
                for (let start = 0, sequence = 2; start < pcm.length && !finished; start += audioChunk, sequence++) {
                    const end = Math.min(start + audioChunk, pcm.length);
                    socket.send(encode(2, sequence, end === pcm.length, pcm.subarray(start, end)));
                    if (end < pcm.length) await delay(100, undefined, { signal: lifetime });
                }
            } catch (error) { finish(error); }
        });
    });
}

module.exports = { recognizeVolc };
