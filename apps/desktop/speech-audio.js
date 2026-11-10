export function wavFromFrames(frames) {
    const count = Math.min(frames.reduce((sum, frame) => sum + frame.length, 0), 480_000);
    const buffer = new ArrayBuffer(44 + count * 2);
    const header = new DataView(buffer);
    const label = (offset, value) => [...value].forEach((letter, i) => header.setUint8(offset + i, letter.charCodeAt(0)));
    label(0, 'RIFF');
    header.setUint32(4, buffer.byteLength - 8, true);
    label(8, 'WAVEfmt ');
    header.setUint32(16, 16, true);
    header.setUint16(20, 1, true);
    header.setUint16(22, 1, true);
    header.setUint32(24, 16000, true);
    header.setUint32(28, 32000, true);
    header.setUint16(32, 2, true);
    header.setUint16(34, 16, true);
    label(36, 'data');
    header.setUint32(40, count * 2, true);
    let index = 0;
    for (const frame of frames) {
        for (const sample of frame) {
            if (index === count) return buffer;
            header.setInt16(44 + index++ * 2, Math.round(Math.max(-1, Math.min(1, sample)) * 32767), true);
        }
    }
    return buffer;
}

export function speechPieces(text) {
    const clauses = text.trim().match(/[^。！？；，\n]+[。！？；，\n]*|[。！？；，\n]+/gu) ?? [];
    const pieces = [];
    for (const clause of clauses) {
        if (pieces.length > 1 && pieces.at(-1).length + clause.length <= 96) {
            pieces[pieces.length - 1] += clause;
            continue;
        }
        const characters = [...clause];
        while (characters.length) pieces.push(characters.splice(0, pieces.length ? 96 : 36).join(''));
    }
    return pieces;
}

export function conversationFrames({ isPlaying, onSpeech, onTurn }) {
    let lead = [];
    let utterance = null;
    let voiced = 0;
    let quiet = 0;
    let length = 0;
    return samples => {
        const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
        if (!utterance) {
            lead.push(samples);
            if (lead.length > 4) lead.shift();
            const playing = isPlaying();
            voiced = rms >= (playing ? 0.028 : 0.012) ? voiced + samples.length : 0;
            if (voiced < (playing ? 4800 : 2600)) return;
            utterance = lead;
            lead = [];
            length = utterance.reduce((total, frame) => total + frame.length, 0);
            quiet = 0;
            onSpeech();
            return;
        }
        utterance.push(samples);
        length += samples.length;
        quiet = rms < 0.012 ? quiet + samples.length : 0;
        if (quiet < 11520 && length < 460800) return;
        const complete = utterance;
        utterance = null;
        voiced = 0;
        onTurn(complete);
    };
}

export class VoiceAudio {
    constructor(api, status) {
        this.api = api;
        this.status = status;
        this.generation = 0;
        this.captureGeneration = 0;
        this.source = null;
        this.capture = null;
        this.context = null;
        this.analyser = null;
    }

    stop() {
        this.releaseCapture();
        return this.interrupt();
    }

    async interrupt() {
        const token = ++this.generation;
        if (this.source) {
            this.source.stop();
            this.source = null;
        }
        await this.api.speechCancel();
        if (token === this.generation) this.status(this.capture ? 'listening' : 'idle');
        return token;
    }

    releaseCapture() {
        this.captureGeneration++;
        const capture = this.capture;
        this.capture = null;
        if (!capture) return;
        clearTimeout(capture.timer);
        capture.stream.getTracks().forEach(track => track.stop());
        void capture.context.close();
    }

    async openCapture(microphone, onFrame) {
        const token = ++this.captureGeneration;
        this.status('opening');
        const stream = await navigator.mediaDevices.getUserMedia({ audio: {
            deviceId: microphone ? { exact: microphone } : undefined,
            echoCancellation: true, noiseSuppression: true, channelCount: 1,
        } }).catch(error => {
            throw new Error(window.GuizhiErrorText(error, 'microphone'));
        });
        if (token !== this.captureGeneration) {
            stream.getTracks().forEach(track => track.stop());
            return null;
        }
        const context = new AudioContext({ sampleRate: 16000 });
        const capture = { stream, context };
        this.capture = capture;
        try {
            await context.audioWorklet.addModule('/voice-input.js');
            const worklet = new AudioWorkletNode(context, 'guizhi-pcm');
            const silent = context.createGain();
            silent.gain.value = 0;
            worklet.port.onmessage = ({ data }) => {
                if (this.capture === capture) onFrame(data);
            };
            context.createMediaStreamSource(stream).connect(worklet).connect(silent).connect(context.destination);
            await context.resume();
            if (this.capture !== capture) return null;
            capture.worklet = worklet;
            this.status('listening');
            return capture;
        } catch (error) {
            if (this.capture === capture) this.releaseCapture();
            throw error;
        }
    }

    async listen(microphone, limitReached) {
        const token = await this.stop();
        if (token !== this.generation) return false;
        const frames = [];
        const capture = await this.openCapture(microphone, samples => frames.push(samples));
        if (!capture) return false;
        capture.frames = frames;
        capture.timer = setTimeout(limitReached, 29_000);
        return true;
    }

    async converse(microphone, { onSpeech, onTurn, onDisconnect }) {
        const token = await this.stop();
        if (token !== this.generation) return;
        const capture = await this.openCapture(microphone,
            conversationFrames({ isPlaying: () => Boolean(this.source), onSpeech, onTurn }));
        capture?.stream.getAudioTracks()[0]?.addEventListener('ended', () => {
            if (this.capture === capture) onDisconnect();
        }, { once: true });
    }

    async finish(onPartial = () => {}) {
        const capture = this.capture;
        if (!capture?.worklet) return null;
        capture.worklet.port.postMessage('flush');
        await new Promise(resolve => setTimeout(resolve, 32));
        if (this.capture !== capture) return null;
        this.releaseCapture();
        return this.recognize(capture.frames, onPartial);
    }

    async recognize(frames, onPartial = () => {}) {
        const token = this.generation;
        this.status('recognizing');
        const off = this.api.onSpeechPartial(text => {
            if (token === this.generation) onPartial(text);
        });
        try {
            const text = await this.api.speechRecognize(wavFromFrames(frames));
            return token === this.generation ? text : null;
        } finally {
            off();
            if (token === this.generation) this.status(this.capture ? 'listening' : 'idle');
        }
    }

    async speak(text, speaker, onCaption = () => {}) {
        const token = await this.interrupt();
        if (token !== this.generation) return;
        this.context ??= new AudioContext();
        if (speaker) {
            if (!this.context.setSinkId) throw new Error('当前系统无法选择播放设备');
            await this.context.setSinkId(speaker).catch(error => {
                throw new Error(window.GuizhiErrorText(error, 'speaker'));
            });
        } else if (this.context.setSinkId && this.context.sinkId) {
            await this.context.setSinkId('');
        }
        await this.context.resume();
        if (!this.analyser) {
            this.analyser = this.context.createAnalyser();
            this.analyser.fftSize = 128;
            this.analyser.connect(this.context.destination);
        }
        const pieces = speechPieces(text);
        const prepare = piece => {
            const clip = { ready: false };
            clip.result = this.api.speechSynthesize(piece)
                .then(wav => wav && token === this.generation ? this.context.decodeAudioData(wav) : null)
                .then(buffer => ({ buffer }), error => ({ error }))
                .finally(() => { clip.ready = true; });
            return clip;
        };
        try {
            if (!pieces.length || token !== this.generation) return;
            let spoken = '';
            let pending = prepare(pieces[0]);
            for (const [index, piece] of pieces.entries()) {
                if (token !== this.generation) return;
                if (!pending.ready) this.status('synthesizing');
                const { buffer, error } = await pending.result;
                if (token !== this.generation) return;
                if (error) throw error;
                if (!buffer) return;
                const playing = this.play(buffer, piece, token, caption => onCaption(spoken + caption));
                if (index + 1 < pieces.length) pending = prepare(pieces[index + 1]);
                await playing;
                spoken += piece;
            }
            return token === this.generation;
        } finally {
            if (token === this.generation) this.status(this.capture ? 'listening' : 'idle');
        }
    }

    play(buffer, text, token, onCaption) {
        const source = this.context.createBufferSource();
        source.buffer = buffer;
        source.connect(this.analyser);
        this.source = source;
        const characters = Array.from(new Intl.Segmenter().segment(text), part => part.segment);
        this.status('speaking');
        return new Promise(resolve => {
            const started = this.context.currentTime;
            let visible = 0;
            let frame;
            const reveal = count => {
                if (token !== this.generation || count <= visible) return;
                visible = count;
                onCaption(characters.slice(0, visible).join(''));
            };
            const update = () => {
                if (token !== this.generation) return;
                const fraction = (this.context.currentTime - started) / buffer.duration;
                reveal(Math.min(characters.length, Math.ceil(fraction * characters.length)));
                frame = requestAnimationFrame(update);
            };
            source.onended = () => {
                cancelAnimationFrame(frame);
                reveal(characters.length);
                if (this.source === source) this.source = null;
                resolve();
            };
            source.start(started);
            frame = requestAnimationFrame(update);
        });
    }

    level() {
        if (!this.source || !this.analyser) return 0;
        this.samples ??= new Uint8Array(this.analyser.fftSize);
        this.analyser.getByteTimeDomainData(this.samples);
        const rms = Math.sqrt(this.samples.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / this.samples.length);
        return Math.min(1, rms * 5);
    }
}
