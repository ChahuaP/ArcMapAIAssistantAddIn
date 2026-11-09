const { desktopCapturer, screen } = require('electron');
const { spawn } = require('node:child_process');
const { createHash } = require('node:crypto');
const path = require('node:path');

const inset = 34;
const maxAge = 16_000;
const dimensions = ({ id, bounds, scaleFactor }) => [id, bounds.x, bounds.y, bounds.width, bounds.height, scaleFactor];
const topology = displays => JSON.stringify(displays.map(dimensions).sort((a, b) => a[0] - b[0]));
const normalize = value => value.normalize('NFKC').replace(/\s/gu, '').toLowerCase();
const intersects = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width &&
    a.y < b.y + b.height && b.y < a.y + a.height;

function pixelRect(bounds, display, size) {
    const sx = size.width / display.bounds.width;
    const sy = size.height / display.bounds.height;
    const { x, y } = display.bounds;
    const left = Math.max(0, Math.floor((bounds.x - x) * sx));
    const top = Math.max(0, Math.floor((bounds.y - y) * sy));
    const right = Math.min(size.width, Math.ceil((bounds.x + bounds.width - x) * sx));
    const bottom = Math.min(size.height, Math.ceil((bounds.y + bounds.height - y) * sy));
    return { x: left, y: top, width: right - left, height: bottom - top };
}

function visibleRect(quad, frame) {
    if (!Array.isArray(quad) || quad.length !== 4 ||
            quad.some(p => !Array.isArray(p) || p.length !== 2 || p.some(v => !Number.isFinite(v)))) return null;
    const bounds = frame.display.bounds;
    const sx = bounds.width / frame.size.width;
    const sy = bounds.height / frame.size.height;
    const xs = quad.map(p => p[0] * sx);
    const ys = quad.map(p => p[1] * sy);
    const left = Math.floor(Math.min(...xs));
    const top = Math.floor(Math.min(...ys));
    const right = Math.ceil(Math.max(...xs));
    const bottom = Math.ceil(Math.max(...ys));
    if (left < inset + 12 || top < inset + 12 || right > bounds.width - inset - 12 ||
            bottom > bounds.height - inset - 12 || right - left < 8 || bottom - top < 7) return null;
    return { x: bounds.x + left, y: bounds.y + top, width: right - left, height: bottom - top };
}

function captureDigest(frame, bounds, excluded) {
    const region = pixelRect(bounds, frame.display, frame.size);
    const masks = excluded.filter(item => intersects(item, bounds)).map(item => pixelRect(item, frame.display, frame.size))
        .sort((a, b) => a.x - b.x);
    const bytes = frame.image.toBitmap();
    if (bytes.length !== frame.size.width * frame.size.height * 4) throw new Error('Capture resolution changed');
    const digest = createHash('sha256').update(JSON.stringify([region, masks]));
    for (let row = region.y; row < region.y + region.height; row++) {
        const base = row * frame.size.width * 4;
        const end = region.x + region.width;
        let cursor = region.x;
        for (const mask of masks) {
            if (row < mask.y || row >= mask.y + mask.height) continue;
            if (mask.x > cursor) digest.update(bytes.subarray(base + cursor * 4, base + Math.min(mask.x, end) * 4));
            cursor = Math.max(cursor, mask.x + mask.width);
            if (cursor >= end) break;
        }
        if (cursor < end) digest.update(bytes.subarray(base + cursor * 4, base + end * 4));
    }
    return digest.digest('hex');
}

async function capture() {
    const displays = screen.getAllDisplays().sort((a, b) => a.id - b.id);
    const signature = topology(displays);
    const thumbnailSize = { width: Math.min(2560, Math.max(...displays.map(d => Math.round(d.bounds.width * d.scaleFactor)))),
        height: Math.min(2560, Math.max(...displays.map(d => Math.round(d.bounds.height * d.scaleFactor)))) };
    const capturedAt = Date.now();
    const sources = await desktopCapturer.getSources({ types: ['screen'], thumbnailSize });
    if (signature !== topology(screen.getAllDisplays())) throw new Error('Display layout changed');
    const frames = displays.map(display => {
        const matches = sources.filter(source => source.display_id === String(display.id));
        if (matches.length !== 1 || matches[0].thumbnail.isEmpty()) throw new Error('Display capture unavailable');
        const image = matches[0].thumbnail;
        const size = image.getSize();
        const { width, height } = display.bounds;
        if (Math.abs(size.height - height * size.width / width) > 2) throw new Error('Capture geometry changed');
        return { display, size, image };
    });
    return { frames, signature, capturedAt };
}

function recognize(python, image, signal) {
    return new Promise((resolve, reject) => {
        const child = spawn(python, ['-I', '-B', path.join(__dirname, 'screen-ocr.py')], { windowsHide: true,
            stdio: ['pipe', 'pipe', 'ignore'], signal });
        const chunks = [];
        let bytes = 0;
        child.stdout.on('data', chunk => {
            bytes += chunk.length;
            if (bytes > 2 << 20) {
                child.kill();
                reject(new Error('Screen OCR returned too many regions'));
            } else chunks.push(chunk);
        });
        child.stdin.on('error', reject);
        child.on('error', reject);
        child.on('close', code => {
            if (code !== 0) return reject(new Error('Screen OCR failed'));
            try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); }
            catch (error) { reject(error); }
        });
        child.stdin.end(image.toPNG());
    });
}

const surround = (frame, excluded) => {
    const { x, y, width, height } = frame.display.bounds;
    return captureDigest(frame, { x: x + inset, y: y + inset, width: width - 2 * inset, height: height - 2 * inset }, excluded);
};

class ScreenTarget {
    constructor(python) { this.python = python; }

    sample() {
        this.pending ??= capture().finally(() => { this.pending = null; });
        return this.pending;
    }

    async locate(text, masked, signal) {
        const key = normalize(text);
        if (key.length < 2 || key.length > 120) return null;
        const started = await this.sample();
        const excluded = masked();
        const timeout = AbortSignal.any([signal, AbortSignal.timeout(13_000)]);
        const matches = [];
        // Run at most one OCR process at a time on multi-screen desktops.
        for (const frame of started.frames) {
            timeout.throwIfAborted();
            const ratio = Math.min(1, 1440 / Math.max(frame.size.width, frame.size.height));
            const image = ratio === 1 ? frame.image : frame.image.resize({ width: Math.round(frame.size.width * ratio) });
            const found = await recognize(this.python, image, timeout);
            for (const match of found) {
                if (normalize(match.text) !== key) continue;
                const bounds = visibleRect(match.quad, { display: frame.display, size: image.getSize() });
                matches.push({ frame, bounds });
            }
        }
        if (matches.length !== 1 || !matches[0].bounds || excluded.some(area => intersects(area, matches[0].bounds))) return null;
        const { frame, bounds } = matches[0];
        const outline = { x: bounds.x - 10, y: bounds.y - 10, width: bounds.width + 20, height: bounds.height + 20 };
        const result = { bounds, outline, displayId: frame.display.id, scaleFactor: frame.display.scaleFactor,
            capturedAt: started.capturedAt, signature: started.signature, masked: JSON.stringify(excluded),
            roi: captureDigest(frame, bounds, []),
            screenDigests: started.frames.map(item => surround(item, [...excluded, outline])) };
        if (!await this.current(result, masked, signal)) return null;
        return result;
    }

    async current(target, masked, signal) {
        signal.throwIfAborted();
        if (Date.now() - target.capturedAt > maxAge) return false;
        const sample = await this.sample();
        signal.throwIfAborted();
        const excluded = masked();
        if (target.signature !== sample.signature || target.masked !== JSON.stringify(excluded)) return false;
        const frame = sample.frames.find(item => item.display.id === target.displayId);
        return !!frame && Date.now() - target.capturedAt <= maxAge &&
            target.roi === captureDigest(frame, target.bounds, []) &&
            sample.frames.every((item, index) => surround(item, [...excluded, target.outline]) === target.screenDigests[index]);
    }
}

module.exports = { ScreenTarget, visibleRect, pixelRect, maxAge };
