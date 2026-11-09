/* global AudioWorkletProcessor, registerProcessor */
class GuizhiPcm extends AudioWorkletProcessor {
    constructor() {
        super();
        this.frames = new Float32Array(2048);
        this.count = 0;
        this.port.onmessage = () => this.flush();
    }

    flush() {
        if (this.count) {
            const block = this.frames.slice(0, this.count);
            this.port.postMessage(block, [block.buffer]);
            this.count = 0;
        }
    }

    process(inputs) {
        const samples = inputs[0]?.[0];
        if (samples) {
            for (const sample of samples) {
                this.frames[this.count++] = sample;
                if (this.count === this.frames.length) this.flush();
            }
        }
        return true;
    }
}

registerProcessor('guizhi-pcm', GuizhiPcm);
