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
    return text.trim().match(/[^。！？；\n]{1,140}[。！？；\n]?|[。！？；]/gu)?.map(item => item.trim()).filter(Boolean) ?? [];
}

export class VoiceAudio {
    constructor(api, status) {
        this.api = api;
        this.status = status;
        this.generation = 0;
        this.source = null;
        this.capture = null;
        this.context = null;
        this.analyser = null;
    }

    async stop() {
        this.generation += 1;
        this.releaseCapture();
        if (this.source) {
            this.source.stop();
            this.source = null;
        }
        await this.api.speechCancel();
        this.status('idle');
    }

    releaseCapture() {
        const capture = this.capture;
        this.capture = null;
        if (!capture) return;
        clearTimeout(capture.timer);
        capture.stream.getTracks().forEach(track => track.stop());
        void capture.context.close();
    }

    async listen(microphone, limitReached) {
        await this.stop();
        const token = this.generation;
        this.status('opening');
        const stream = await navigator.mediaDevices.getUserMedia({ audio: {
            deviceId: microphone ? { exact: microphone } : undefined,
            echoCancellation: true, noiseSuppression: true, channelCount: 1,
        } });
        if (token !== this.generation) {
            stream.getTracks().forEach(track => track.stop());
            return false;
        }
        const context = new AudioContext({ sampleRate: 16000 });
        const frames = [];
        this.capture = { stream, context, frames };
        try {
            await context.audioWorklet.addModule('/voice-input.js');
            const worklet = new AudioWorkletNode(context, 'guizhi-pcm');
            const silent = context.createGain();
            silent.gain.value = 0;
            worklet.port.onmessage = event => frames.push(event.data);
            context.createMediaStreamSource(stream).connect(worklet).connect(silent).connect(context.destination);
            await context.resume();
            if (!this.capture || token !== this.generation) return false;
            this.capture.worklet = worklet;
            this.capture.timer = setTimeout(limitReached, 29_000);
            this.status('listening');
            return true;
        } catch (error) {
            this.releaseCapture();
            throw error;
        }
    }

    async finish(onPartial) {
        const capture = this.capture;
        if (!capture?.worklet) {
            await this.stop();
            return null;
        }
        const token = this.generation;
        capture.worklet.port.postMessage('flush');
        await new Promise(resolve => setTimeout(resolve, 32));
        if (token !== this.generation) return null;
        this.releaseCapture();
        this.status('recognizing');
        const off = this.api.onSpeechPartial(text => {
            if (token === this.generation) onPartial(text);
        });
        try {
            const text = await this.api.speechRecognize(wavFromFrames(capture.frames));
            return token === this.generation ? text : null;
        } finally {
            off();
            if (token === this.generation) this.status('idle');
        }
    }

    async speak(text, speaker) {
        await this.stop();
        const token = this.generation;
        this.context ??= new AudioContext();
        if (speaker) {
            if (!this.context.setSinkId) throw new Error('当前系统无法选择播放设备');
            await this.context.setSinkId(speaker);
        } else if (this.context.setSinkId && this.context.sinkId) {
            await this.context.setSinkId('');
        }
        await this.context.resume();
        this.analyser ??= this.context.createAnalyser();
        this.analyser.fftSize = 128;
        this.analyser.connect(this.context.destination);
        try {
            for (const piece of speechPieces(text)) {
                if (token !== this.generation) return;
                this.status('synthesizing');
                const wav = await this.api.speechSynthesize(piece);
                if (!wav || token !== this.generation) return;
                const buffer = await this.context.decodeAudioData(wav);
                if (token !== this.generation) return;
                const source = this.context.createBufferSource();
                source.buffer = buffer;
                source.connect(this.analyser);
                this.source = source;
                this.status('speaking');
                await new Promise(resolve => {
                    source.onended = resolve;
                    source.start();
                });
                if (this.source === source) this.source = null;
            }
        } finally {
            if (token === this.generation) this.status('idle');
        }
    }

    level() {
        if (!this.source || !this.analyser) return 0;
        this.samples ??= new Uint8Array(this.analyser.fftSize);
        this.analyser.getByteTimeDomainData(this.samples);
        const rms = Math.sqrt(this.samples.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / this.samples.length);
        return Math.min(1, rms * 5);
    }
}
