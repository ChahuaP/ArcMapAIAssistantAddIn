const { randomInt } = require('node:crypto');
const { readFileSync } = require('node:fs');
const path = require('node:path');

const probes = [
    ['RED', 'iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAIAAABt+uBvAAABC0lEQVR4nO3OMQ0AIAAEsfdvGhyw9gaSCujO9j34QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UHcBWwZ3g5gacwjAAAAAElFTkSuQmCC'],
    ['GREEN', 'iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAIAAABt+uBvAAABC0lEQVR4nO3OMQ0AIAAEsfdvGhyw9gaSCuh29r34QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UHbBY393g69gixzAAAAAElFTkSuQmCC'],
    ['BLUE', 'iVBORw0KGgoAAAANSUhEUgAAAGAAAABgCAIAAABt+uBvAAABC0lEQVR4nO3OMQ0AIAAEsfdvGhyw9gaSCui28z35QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UGcH8T5QZwfxPlBnB/E+UHaBa/h3g7Hpj8QAAAAAElFTkSuQmCC'],
];
const allowed = new Set(probes.map(([name]) => name));
const list = value => Array.isArray(value) ? value.map(entry => String(entry).toLowerCase()) : [];
const supportsVision = entry => [
    entry?.input, entry?.input_modalities, entry?.inputModalities, entry?.architecture?.input_modalities,
    entry?.modalities?.input, entry?.capabilities,
].some(value => list(value).some(name => ['image', 'vision'].includes(name))) ||
    entry?.capabilities?.vision === true || entry?.capabilities?.image_input === true;

function builtinVision(runtime, provider, api) {
    if (provider === 'custom') return new Set();
    const file = path.join(runtime.runtime, 'node_modules', '@earendil-works', 'pi-ai', 'dist',
        'providers', 'data', provider + '.json');
    const entries = JSON.parse(readFileSync(file, 'utf8'))[api] || {};
    return new Set(Object.values(entries).filter(entry => entry.type === 'chat' && supportsVision(entry)).map(entry => entry.id));
}

function probe() {
    const [expected, data] = probes[randomInt(probes.length)];
    return {
        expected,
        content: [{ type: 'text', text: 'What solid color fills the image? Answer one word: RED, GREEN or BLUE.' },
            { type: 'image', mimeType: 'image/png', data }],
    };
}

function observed(text) {
    const color = text.trim().replace(/^[*`]+|[*`.!]+$/g, '').toUpperCase();
    if (allowed.has(color)) return color;
    const matches = [...new Set(color.match(/\b(?:RED|GREEN|BLUE)\b/g) || [])];
    return matches.length === 1 ? matches[0] : '';
}

module.exports = { builtinVision, supportsVision, probe, observed };
