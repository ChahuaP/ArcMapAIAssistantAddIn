const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const onlineHost = 'https://api.minimaxi.com';
const voiceDefault = 'female-shaonv';
const defaults = { asr: 'minimax', tts: 'minimax', voice: voiceDefault, autoSpeak: true,
    microphone: '', speaker: '', dictionary: [] };
const audioLimit = 16_000 * 2 * 30 + 44;

function readPcmWav(data) {
    if (!(data instanceof ArrayBuffer || ArrayBuffer.isView(data))) throw new Error('录音格式无效');
    const bytes = data instanceof ArrayBuffer ? Buffer.from(data)
        : Buffer.from(data.buffer, data.byteOffset, data.byteLength);
    if (bytes.length < 3244 || bytes.length > audioLimit || bytes.toString('ascii', 0, 4) !== 'RIFF'
            || bytes.toString('ascii', 8, 16) !== 'WAVEfmt ' || bytes.readUInt32LE(16) !== 16
            || bytes.readUInt16LE(20) !== 1 || bytes.readUInt16LE(22) !== 1
            || bytes.readUInt32LE(24) !== 16_000 || bytes.readUInt16LE(34) !== 16
            || bytes.toString('ascii', 36, 40) !== 'data' || bytes.readUInt32LE(40) !== bytes.length - 44) {
        throw new Error('录音须为 0.1 至 30 秒的单声道 PCM 音频');
    }
    return bytes;
}

function dictionary(entries) {
    if (!Array.isArray(entries) || entries.length > 50) throw new Error('词表最多可保存 50 条');
    const seen = new Set();
    return entries.map(entry => {
        const heard = entry?.heard?.trim();
        const correct = entry?.correct?.trim();
        if (!heard || !correct || heard.length > 40 || correct.length > 40 || seen.has(heard)) {
            throw new Error('请检查词表中的原词、替换词和重复项');
        }
        seen.add(heard);
        return { heard, correct };
    });
}

function correctTerms(text, entries) {
    const ordered = [...entries].sort((a, b) => b.heard.length - a.heard.length);
    if (!ordered.length) return text;
    const replacements = new Map(ordered.map(item => [item.heard, item.correct]));
    const pattern = ordered.map(item => item.heard.replace(/[.*+?^{}()|[\]\\]/g, '\\$&')).join('|');
    return text.replace(new RegExp(pattern, 'gu'), word => replacements.get(word));
}

function validateReply(json) {
    if (json.base_resp?.status_code) throw new Error('在线语音服务拒绝了请求（' + json.base_resp.status_code + '）');
    return json;
}

async function* sse(response) {
    const decoder = new TextDecoder();
    let buffered = '';
    for await (const chunk of response.body) {
        buffered += decoder.decode(chunk, { stream: true }).replace(/\r/g, '');
        let position;
        while ((position = buffered.indexOf('\n\n')) !== -1) {
            const frame = buffered.slice(0, position);
            buffered = buffered.slice(position + 2);
            const data = frame.split('\n').filter(line => line.startsWith('data:'))
                .map(line => line.slice(5).trimStart()).join('\n');
            if (data === '[DONE]') return;
            if (data) yield validateReply(JSON.parse(data));
        }
        if (buffered.length > 1 << 20) throw new Error('在线识别返回数据过长');
    }
}

class MiniMaxSpeech {
    constructor(key) {
        if (!key) throw new Error('请在设置中配置 MiniMax 语音密钥');
        this.authorization = 'Bearer ' + key;
    }

    async request(endpoint, options, signal) {
        try {
            const response = await fetch(onlineHost + endpoint, { ...options, method: 'POST', redirect: 'error',
                signal: AbortSignal.any([signal, AbortSignal.timeout(40_000)]),
                headers: { ...options.headers, authorization: this.authorization } });
            if (!response.ok) throw new Error('在线语音服务响应异常（HTTP ' + response.status + '）');
            return response;
        } catch (error) {
            if (signal.aborted) throw signal.reason;
            if (error instanceof TypeError || error.name === 'TimeoutError') throw new Error('在线语音连接失败，请检查网络');
            throw error;
        }
    }

    async recognize(wav, signal, partial) {
        const form = new FormData();
        form.set('model', 'asr-1.0');
        form.set('response_format', 'json');
        form.set('stream', 'true');
        form.set('file', new Blob([wav], { type: 'audio/wav' }), 'utterance.wav');
        const response = await this.request('/v1/speech_to_text', {
            body: form, headers: { language: 'zh', accept: 'text/event-stream' },
        }, signal);
        if (!response.headers.get('content-type')?.includes('text/event-stream')) {
            const result = validateReply(await response.json());
            if (typeof result.text !== 'string') throw new Error('在线识别未返回文字');
            return result.text;
        }
        let text = '';
        for await (const event of sse(response)) {
            if (typeof event.delta === 'string') {
                text += event.delta;
                if (text.length > 4000) throw new Error('识别文字过长');
                partial(text);
            }
            if (event.finish_reason === 'stop' || event.type === 'done') break;
        }
        return text;
    }

    async synthesize(text, voice, signal) {
        const response = await this.request('/v1/t2a_v2', {
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ model: 'speech-2.8-turbo', text, stream: false, output_format: 'hex',
                language_boost: 'Chinese', voice_setting: { voice_id: voice, speed: 1.0 },
                audio_setting: { sample_rate: 24_000, format: 'wav', channel: 1 } }),
        }, signal);
        const reply = validateReply(await response.json());
        const audio = reply.data?.audio;
        if (typeof audio !== 'string' || audio.length > 8 << 20 || !/^(?:[a-f\d]{2})+$/i.test(audio)) {
            throw new Error('在线合成未返回可用音频');
        }
        return Buffer.from(audio, 'hex');
    }
}

class SpeechController {
    constructor({ userData, storage, backends = {} }) {
        this.storage = storage;
        this.file = path.join(userData, 'speech.json');
        this.settings = { ...defaults, ...(fs.existsSync(this.file) ? JSON.parse(fs.readFileSync(this.file, 'utf8')) : {}) };
        this.backends = { minimax: () => new MiniMaxSpeech(this.key()), ...backends };
        this.active = null;
    }

    key() {
        const encrypted = this.settings.encryptedKey;
        return encrypted ? this.storage.decryptString(Buffer.from(encrypted, 'base64')) : process.env.MINIMAX_SPEECH_KEY?.trim();
    }

    state() {
        const { encryptedKey, ...settings } = this.settings;
        return { ...settings, onlineReady: Boolean(encryptedKey || process.env.MINIMAX_SPEECH_KEY),
            providers: Object.keys(this.backends) };
    }

    configure(input) {
        for (const mode of [input.asr, input.tts]) {
            if (mode !== 'off' && !Object.hasOwn(this.backends, mode)) throw new Error('语音服务不可用');
        }
        let encryptedKey = this.settings.encryptedKey;
        if (input.key?.trim()) {
            if (!this.storage.isEncryptionAvailable()) throw new Error('系统凭据加密不可用');
            encryptedKey = this.storage.encryptString(input.key.trim()).toString('base64');
        }
        const voice = input.voice?.trim() || voiceDefault;
        if (voice.length > 100 || /[\r\n]/.test(voice)) throw new Error('音色标识无效');
        const device = id => typeof id === 'string' && id.length <= 200 ? id : '';
        const settings = { asr: input.asr, tts: input.tts, voice, autoSpeak: Boolean(input.autoSpeak),
            microphone: device(input.microphone), speaker: device(input.speaker),
            dictionary: dictionary(input.dictionary ?? []), encryptedKey };
        const pending = this.file + '.' + randomUUID();
        fs.writeFileSync(pending, JSON.stringify(settings), { flag: 'wx' });
        fs.renameSync(pending, this.file);
        this.cancel();
        this.settings = settings;
        return this.state();
    }

    cancel() {
        this.active?.abort(new DOMException('语音已停止', 'AbortError'));
        this.active = null;
    }

    async perform(kind, execute) {
        this.cancel();
        const mode = this.settings[kind];
        if (mode === 'off') throw new Error('此语音功能已关闭');
        const provider = this.backends[mode]();
        const controller = new AbortController();
        this.active = controller;
        try {
            const result = await execute(provider, controller.signal);
            controller.signal.throwIfAborted();
            return result;
        } catch (error) {
            if (controller.signal.aborted) return null;
            throw error;
        } finally {
            if (this.active === controller) this.active = null;
        }
    }

    recognize(data, onPartial = () => {}) {
        const wav = readPcmWav(data);
        const terms = this.settings.dictionary;
        return this.perform('asr', async (provider, signal) => {
            const update = text => { if (!signal.aborted) onPartial(correctTerms(text, terms)); };
            const text = correctTerms(await provider.recognize(wav, signal, update), terms).trim();
            if (!text || text.length > 4000) throw new Error('没有识别到可发送的文字');
            return text;
        });
    }

    synthesize(text) {
        if (typeof text !== 'string' || !text.trim() || text.length > 360) throw new Error('播报片段长度无效');
        return this.perform('tts', async (provider, signal) => {
            const bytes = await provider.synthesize(text.trim(), this.settings.voice, signal);
            if (bytes.length < 44 || bytes.toString('ascii', 0, 4) !== 'RIFF') throw new Error('合成音频格式无效');
            return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
        });
    }
}

module.exports = { MiniMaxSpeech, SpeechController, readPcmWav, correctTerms };
