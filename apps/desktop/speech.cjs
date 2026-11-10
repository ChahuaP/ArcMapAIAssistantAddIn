const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { endpoint, normalizeKey } = require('../../src/guizhi/infrastructure/dsh/model-config.cjs');

const onlineHost = 'https://api.minimaxi.com';
const voiceDefault = 'female-shaonv';
const voices = { custom: 'alloy', azure: 'zh-CN-XiaoxiaoNeural', aliyun: 'longanyang', minimax: voiceDefault };
const defaults = { asr: 'off', tts: 'off', voice: voiceDefault,
    microphone: '', speaker: '', dictionary: [], pronunciation: [] };
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

class HttpSpeech {
    constructor(key, baseUrl) {
        this.credentials = { authorization: 'Bearer ' + key };
        this.baseUrl = baseUrl;
    }

    async request(endpoint, options, signal) {
        try {
            const response = await fetch(this.baseUrl + endpoint, { ...options, method: 'POST', redirect: 'error',
                signal: AbortSignal.any([signal, AbortSignal.timeout(40_000)]),
                headers: { ...options.headers, ...this.credentials } });
            if (!response.ok) throw new Error('在线语音服务响应异常（HTTP ' + response.status + '）');
            return response;
        } catch (error) {
            if (signal.aborted) throw signal.reason;
            if (error instanceof TypeError || error.name === 'TimeoutError') throw new Error('在线语音连接失败，请检查网络');
            throw error;
        }
    }
}

class MiniMaxSpeech extends HttpSpeech {
    constructor(key) {
        super(key, onlineHost);
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

class CompatibleSpeech extends HttpSpeech {
    constructor(profile) {
        super(profile.key, profile.baseUrl);
        this.model = profile.model;
    }

    async recognize(wav, signal, partial) {
        const form = new FormData();
        form.set('model', this.model);
        form.set('file', new Blob([wav], { type: 'audio/wav' }), 'utterance.wav');
        const response = await this.request('/audio/transcriptions', { body: form }, signal);
        const { text } = await response.json();
        if (typeof text !== 'string') throw new Error('在线识别未返回文字');
        partial(text);
        return text;
    }

    async synthesize(text, voice, signal) {
        const response = await this.request('/audio/speech', { headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ model: this.model, input: text, voice, response_format: 'wav' }) }, signal);
        return readAudio(response);
    }
}

class AzureSpeech extends HttpSpeech {
    constructor(profile) {
        super(profile.key, profile.baseUrl);
        this.credentials = { 'Ocp-Apim-Subscription-Key': profile.key };
    }

    async recognize(wav, signal, partial) {
        const response = await this.request('/speech/recognition/conversation/cognitiveservices/v1?language=zh-CN', {
            body: wav, headers: { 'content-type': 'audio/wav; codecs=audio/pcm; samplerate=16000' },
        }, signal);
        const result = await response.json();
        if (result.RecognitionStatus !== 'Success' || typeof result.DisplayText !== 'string') {
            throw new Error('Azure 未识别到可用语音');
        }
        partial(result.DisplayText);
        return result.DisplayText;
    }

    async synthesize(text, voice, signal) {
        if (!/^[A-Za-z0-9-]{1,100}$/.test(voice)) throw new Error('Azure 音色标识无效');
        const escaped = text.replace(/[&<>]/g, mark => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[mark]);
        const response = await this.request('/cognitiveservices/v1', {
            headers: { 'content-type': 'application/ssml+xml', 'X-Microsoft-OutputFormat': 'riff-24khz-16bit-mono-pcm' },
            body: '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="zh-CN"><voice name="' +
                voice + '">' + escaped + '</voice></speak>',
        }, signal);
        return readAudio(response);
    }
}

class AliyunSpeech extends HttpSpeech {
    constructor(profile) {
        super(profile.key, profile.baseUrl);
        this.model = profile.model;
    }

    async synthesize(text, voice, signal) {
        const response = await this.request('/api/v1/services/audio/tts/SpeechSynthesizer', {
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ model: this.model, input: { text, voice, format: 'wav', sample_rate: 24_000 } }),
        }, signal);
        const { output } = await response.json();
        if (typeof output?.audio?.url !== 'string') throw new Error('阿里云未返回可用音频');
        const url = new URL(output.audio.url);
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port ||
            !/^dashscope-result-[a-z0-9-]+\.oss-cn-[a-z0-9-]+\.aliyuncs\.com$/.test(url.hostname)) {
            throw new Error('阿里云返回了无效的音频地址');
        }
        url.protocol = 'https:';
        const audio = await fetch(url, { redirect: 'error', signal: AbortSignal.any([signal, AbortSignal.timeout(20_000)]) });
        if (!audio.ok) throw new Error('阿里云音频读取失败（HTTP ' + audio.status + '）');
        return readAudio(audio);
    }
}

async function readAudio(response) {
    const chunks = [];
    let size = 0;
    for await (const chunk of response.body) {
        size += chunk.length;
        if (size > 4 << 20) throw new Error('在线合成音频过长');
        chunks.push(chunk);
    }
    return Buffer.concat(chunks);
}

function wavBuffer(bytes) {
    if (bytes.length < 44 || bytes.toString('ascii', 0, 4) !== 'RIFF') throw new Error('合成音频格式无效');
    return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
}

function selectedVoice(input) {
    const voice = input.voice?.trim() || voices[input.tts] || voiceDefault;
    if (voice.length > 100 || /[\r\n]/.test(voice)) throw new Error('音色标识无效');
    return voice;
}

class SpeechController {
    constructor({ userData, storage, local, bundledLocalRoot }) {
        this.storage = storage;
        this.file = path.join(userData, 'speech.json');
        this.usageFile = path.join(userData, 'speech-usage.json');
        this.settings = { ...defaults, ...(fs.existsSync(this.file) ? JSON.parse(fs.readFileSync(this.file, 'utf8')) : {}) };
        this.local = local;
        this.bundledLocalRoot = bundledLocalRoot;
        this.usage = fs.existsSync(this.usageFile) ? JSON.parse(fs.readFileSync(this.usageFile, 'utf8')) : [];
        this.clips = new Map();
        this.clipBytes = 0;
        this.active = null;
        this.status = {};
    }

    state() {
        const publicProfile = kind => {
            const { baseUrl, model, region, workspace, encryptedKey } = this.settings[kind + 'Profile'] || {};
            return { baseUrl, model, region, workspace, keyConfigured: Boolean(encryptedKey) };
        };
        const { asr, tts, voice, microphone, speaker, dictionary, pronunciation } = this.settings;
        return { asr, tts, voice, microphone, speaker, dictionary, pronunciation,
            asrProfile: publicProfile('asr'), ttsProfile: publicProfile('tts'),
            providers: { asr: ['minimax', 'azure', 'custom'], tts: ['minimax', 'azure', 'aliyun', 'custom'] },
            localAvailable: Boolean(this.bundledLocalRoot) };
    }

    profile(kind, input) {
        const provider = input[kind];
        if (provider === 'off') return { provider };
        if (provider === 'local') {
            if (!this.bundledLocalRoot) throw new Error('本机语音组件未安装');
            return { provider };
        }
        const form = input[kind + 'Profile'] || {};
        const region = provider === 'azure' ? form.region?.trim().toLowerCase() : undefined;
        const workspace = provider === 'aliyun' ? form.workspace?.trim().toLowerCase() : undefined;
        let baseUrl;
        if (provider === 'azure') {
            if (!/^[a-z][a-z0-9]{1,31}$/.test(region || '')) throw new Error('请填写 Azure 语音资源的区域，如 eastasia');
            baseUrl = 'https://' + region + '.' + (kind === 'asr' ? 'stt' : 'tts') + '.speech.microsoft.com';
        } else if (provider === 'aliyun' && kind === 'tts') {
            if (!/^[a-z0-9][a-z0-9-]{1,79}$/.test(workspace || '')) throw new Error('请填写阿里云百炼工作空间 ID');
            baseUrl = 'https://' + workspace + '.cn-beijing.maas.aliyuncs.com';
        } else if (provider === 'custom') baseUrl = endpoint(form.baseUrl);
        else if (provider === 'minimax') baseUrl = onlineHost;
        else throw new Error('语音服务不可用');
        const model = provider === 'aliyun' ? form.model?.trim() || 'cosyvoice-v3-plus' : form.model?.trim();
        if ((provider === 'custom' || provider === 'aliyun') && (!model || model.length > 160 || /[\x00-\x20]/.test(model))) {
            throw new Error('请输入有效的语音模型名称');
        }
        const saved = this.settings[kind + 'Profile'];
        const encryptedKey = saved?.provider === provider && saved.baseUrl === baseUrl ? saved.encryptedKey : undefined;
        const key = normalizeKey(form.key) || (encryptedKey
            ? this.storage.decryptString(Buffer.from(encryptedKey, 'base64')) : '');
        if (!key) throw new Error(kind === 'asr' ? '请配置识别服务的 API 密钥' : '请配置语音回答服务的 API 密钥');
        return { provider, baseUrl, model, region, workspace, encryptedKey, key };
    }

    saveProfile(kind, input) {
        const { key, ...profile } = this.profile(kind, input);
        if (profile.provider === 'local') this.local.verify(this.bundledLocalRoot, kind);
        if (key && normalizeKey(input[kind + 'Profile']?.key)) {
            if (!this.storage.isEncryptionAvailable()) throw new Error('系统凭据加密不可用');
            profile.encryptedKey = this.storage.encryptString(key).toString('base64');
        }
        return profile;
    }

    adapter(profile) {
        if (profile.provider === 'local') return this.local.provider({ localRoot: this.bundledLocalRoot });
        if (profile.provider === 'custom') return new CompatibleSpeech(profile);
        if (profile.provider === 'minimax') return new MiniMaxSpeech(profile.key);
        if (profile.provider === 'azure') return new AzureSpeech(profile);
        if (profile.provider === 'aliyun') return new AliyunSpeech(profile);
        throw new Error('此语音功能已关闭');
    }

    diagnostic(kind) {
        const provider = this.settings[kind];
        let state = 'configured';
        if (provider === 'off') state = 'off';
        else {
            try {
                this.profile(kind, this.settings);
                if (provider === 'local') {
                    this.local.verify(this.bundledLocalRoot, kind);
                    state = 'ready';
                }
            } catch { return { provider, state: 'failed' }; }
        }
        return { provider, state, ...this.status[kind] };
    }

    configure(input) {
        const voice = selectedVoice(input);
        const device = id => typeof id === 'string' && id.length <= 200 ? id : '';
        const settings = { asr: input.asr, tts: input.tts, voice,
            microphone: device(input.microphone), speaker: device(input.speaker),
            dictionary: dictionary(input.dictionary ?? []), pronunciation: dictionary(input.pronunciation ?? []),
            asrProfile: this.saveProfile('asr', input), ttsProfile: this.saveProfile('tts', input) };
        writeRecord(this.file, settings);
        this.cancel();
        this.clips.clear();
        this.clipBytes = 0;
        this.settings = settings;
        this.status = {};
        return this.state();
    }

    cancel() {
        this.active?.abort(new DOMException('语音已停止', 'AbortError'));
        this.active = null;
    }

    async perform(kind, units, execute) {
        this.cancel();
        const profile = this.profile(kind, this.settings);
        const provider = this.adapter(profile);
        const controller = new AbortController();
        this.active = controller;
        const started = performance.now();
        try {
            const { result, cached = false } = await execute(provider, controller.signal);
            controller.signal.throwIfAborted();
            const records = [...this.usage.slice(-199), { kind, provider: profile.provider, units, cached,
                elapsedMs: Math.round(performance.now() - started), at: new Date().toISOString() }];
            writeRecord(this.usageFile, records);
            this.usage = records;
            this.status[kind] = { state: 'verified', at: new Date().toISOString() };
            return result;
        } catch (error) {
            if (controller.signal.aborted) return null;
            this.status[kind] = { state: 'failed', at: new Date().toISOString() };
            throw new Error(profile.key ? String(error.message).replaceAll(profile.key, '[密钥]') : error.message);
        } finally {
            if (this.active === controller) this.active = null;
        }
    }

    recognize(data, onPartial = () => {}) {
        const wav = readPcmWav(data);
        const terms = this.settings.dictionary;
        return this.perform('asr', { seconds: (wav.length - 44) / 32000 }, async (provider, signal) => {
            const update = text => { if (!signal.aborted) onPartial(correctTerms(text, terms)); };
            const text = correctTerms(await provider.recognize(wav, signal, update), terms).trim();
            if (!text || text.length > 4000) throw new Error('没有识别到可发送的文字');
            return { result: text };
        });
    }

    synthesize(text) {
        if (typeof text !== 'string' || !text.trim() || text.length > 360) throw new Error('语音片段长度无效');
        const spoken = correctTerms(text.trim(), this.settings.pronunciation);
        if (spoken.length > 360) throw new Error('应用发音词表后语音片段过长');
        return this.perform('tts', { characters: [...spoken].length }, async (provider, signal) => {
            const cached = this.clips.has(spoken);
            const bytes = this.clips.get(spoken) ?? await provider.synthesize(spoken, this.settings.voice, signal);
            const result = wavBuffer(bytes);
            signal.throwIfAborted();
            if (!cached) {
                while (this.clips.size >= 64 || this.clipBytes + bytes.length > 16 * 1024 * 1024) {
                    const oldest = this.clips.keys().next().value;
                    this.clipBytes -= this.clips.get(oldest).length;
                    this.clips.delete(oldest);
                }
                this.clips.set(spoken, bytes);
                this.clipBytes += bytes.length;
            }
            return { result, cached };
        });
    }

    async test(kind, input, audio) {
        if (!['asr', 'tts'].includes(kind)) throw new Error('语音测试类型无效');
        const profile = this.profile(kind, input);
        const adapter = this.adapter(profile);
        const signal = AbortSignal.timeout(55_000);
        const saved = this.settings[kind + 'Profile'];
        const same = input[kind] === this.settings[kind] && saved?.baseUrl === profile.baseUrl &&
            saved?.model === profile.model && !normalizeKey(input[kind + 'Profile']?.key) &&
            (kind === 'asr' || selectedVoice(input) === this.settings.voice);
        try {
            let output;
            if (kind === 'asr') {
                output = await adapter.recognize(readPcmWav(audio), signal, () => {});
                if (!output.trim() || output.length > 4000) throw new Error('没有识别到可用的文字');
            } else {
                output = wavBuffer(await adapter.synthesize('你好，我是规智。我们可以开始对话了。', selectedVoice(input), signal));
            }
            if (same) this.status[kind] = { state: 'verified', at: new Date().toISOString() };
            return output;
        } catch (error) {
            if (same) this.status[kind] = { state: 'failed', at: new Date().toISOString() };
            throw new Error(profile.key ? String(error.message).replaceAll(profile.key, '[密钥]') : error.message);
        }
    }
}

function writeRecord(file, value) {
    const pending = file + '.' + randomUUID();
    fs.writeFileSync(pending, JSON.stringify(value), { flag: 'wx' });
    try { fs.renameSync(pending, file); }
    finally { fs.rmSync(pending, { force: true }); }
}

module.exports = { MiniMaxSpeech, SpeechController, readPcmWav, correctTerms };
