const fs = require('node:fs');
const fsp = require('node:fs/promises');
const https = require('node:https');
const { release } = require('node:os');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { validReport, uuid, run, identifier, maxReportBytes, maxErrorBytes, formats, modelNames, skills } = require('./diagnostic-schema.cjs');

const endpoint = 'https://123.56.108.57:8443/guizhi/v1/diagnostics';
const retentionMs = 14 * 86_400_000;

function reportError(error, secret) {
    if (typeof error !== 'string' || !error.trim()) return;
    let text = secret ? error.replaceAll(secret, '[已隐藏凭据]') : error;
    // Child-process exceptions may include the full command and a JSON response from the provider.
    text = text.replace(/^Command failed(?: with exit code [0-9]+)?: [^\r\n]*/i, '子进程执行失败')
        .replace(/\b(Bearer|Basic)\s+[A-Za-z0-9+/_.~=-]+/gi, '$1 [已隐藏凭据]')
        .replace(/(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization|cookie)["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;&}\]]+)/gi, '$1[已隐藏凭据]')
        .replace(/\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}/g, '[已隐藏凭据]')
        .replace(/(["'](?:prompt|messages|request_body|response_body|payload)["']\s*:\s*)[^\r\n]*/gi, '$1[已隐藏请求内容]')
        .replace(/\b(?:https?|file):\/\/[^\s"'<>]+/gi, '[已隐藏地址]')
        .replace(/(["'])(?:[a-z]:[\\/]|\\\\|\/)[^"'\r\n]*\1/gi, '$1[已隐藏路径]$1')
        .replace(/(?:[a-z]:[\\/]|\\\\)[^\r\n"'<>|]+|(?:^|(?<=\s))\/[^\s"'<>|,;]+/gi, '[已隐藏路径]')
        .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '').trim();
    if (Buffer.byteLength(JSON.stringify(text)) <= maxErrorBytes) return text || undefined;
    let output = '', bytes = Buffer.byteLength(JSON.stringify('…'));
    for (const value of text) {
        bytes += Buffer.byteLength(JSON.stringify(value)) - 2;
        if (bytes > maxErrorBytes) break;
        output += value;
    }
    return output + '…';
}

const validId = (value, pattern) => typeof value === 'string' && pattern.test(value) ? value : undefined;

class DiagnosticsUpload {
    constructor(userData, version, uploadUrl = endpoint) {
        this.version = version;
        this.uploadUrl = uploadUrl;
        this.root = path.join(userData, 'diagnostic-outbox');
        this.preference = path.join(userData, 'diagnostic-upload-disabled');
        this.enabled = !fs.existsSync(this.preference);
        this.timer = setInterval(() => void this.flush(), 60_000);
        this.timer.unref();
        void this.flush();
    }

    state() {
        return { enabled: this.enabled, latest: this.latest };
    }

    enable(enabled) {
        if (typeof enabled !== 'boolean') throw new Error('故障上报设置无效');
        if (enabled) fs.rmSync(this.preference, { force: true });
        else fs.writeFileSync(this.preference, '');
        this.enabled = enabled;
        if (enabled) void this.flush();
        else this.active?.abort();
        return this.state();
    }

    record({ stage, status, code, taskId, dispatchId, runId, childRunId, step, stepId, operation, error, errorCode,
        elapsedMs, config, skill, files = [], secret }) {
        if (!this.enabled || this.closed) return;
        const id = randomUUID();
        const extensions = files.map(file => path.extname(file).slice(1).toLowerCase());
        const workflow = skill?.split('@')[0];
        const report = { schema: 1, id, time: new Date().toISOString(), version: this.version, os: release(), arch: process.arch,
            stage, status, code, taskId, dispatchId: validId(dispatchId, uuid), runId: validId(runId, run),
            childRunId: validId(childRunId, run), step: validId(step, identifier), stepId: validId(stepId, identifier),
            operation: validId(operation, identifier), errorCode: validId(errorCode, identifier), error: reportError(error, secret),
            elapsedMs: Number.isFinite(elapsedMs) ? Math.min(900_000, Math.max(0, Math.round(elapsedMs))) : undefined,
            provider: config?.provider, mode: config?.mode,
            model: config?.provider !== 'custom' && modelNames.test(config?.model || '') ? config.model : undefined,
            skill: workflow ? (skills.includes(workflow) ? workflow : 'custom') : undefined,
            formats: [...new Set(extensions.map(ext => formats.includes(ext) ? ext : 'other'))], materialCount: files.length };
        if (!validReport(report) || Buffer.byteLength(JSON.stringify(report)) > maxReportBytes) return;
        this.latest = { id, state: 'pending' };
        void this.store(report);
        return id;
    }

    async store(report) {
        const file = path.join(this.root, report.id + '.json');
        try {
            await fsp.mkdir(this.root, { recursive: true });
            await fsp.writeFile(file + '.pending', JSON.stringify(report), { flag: 'wx' });
            await fsp.rename(file + '.pending', file);
            if (this.active) this.wake = true;
            else void this.flush();
        } catch {
            await fsp.rm(file + '.pending', { force: true }).catch(() => {});
            if (this.latest?.id === report.id) this.latest.state = 'local_error';
        }
    }

    async flush() {
        if (!this.enabled || this.closed || this.active) return;
        const active = new AbortController();
        this.active = active;
        try {
            const entries = await fsp.readdir(this.root).catch(error => error.code === 'ENOENT' ? [] : Promise.reject(error));
            const queue = [];
            for (const name of entries) {
                if (!name.endsWith('.json') || !uuid.test(name.slice(0, -5))) continue;
                const file = path.join(this.root, name);
                const info = await fsp.lstat(file);
                if (!info.isFile()) continue;
                if (Date.now() - info.mtimeMs > retentionMs || info.size > maxReportBytes) await fsp.unlink(file);
                else queue.push({ file, time: info.mtimeMs });
            }
            queue.sort((a, b) => b.time - a.time);
            for (const item of queue.splice(100)) await fsp.unlink(item.file);
            for (const item of queue.slice(0, 10)) {
                active.signal.throwIfAborted();
                const data = await fsp.readFile(item.file);
                let report;
                try { report = JSON.parse(data); } catch { /* A malformed file is not a queued report. */ }
                if (!validReport(report)) { await fsp.unlink(item.file); continue; }
                this.latest ??= { id: report.id, state: 'pending' };
                await this.send(data, active.signal);
                await fsp.unlink(item.file);
                if (this.latest.id === report.id) this.latest.state = 'sent';
            }
        } catch { /* The queued receipt remains on disk for the next attempt. */ }
        finally {
            this.active = null;
            if (this.wake) { this.wake = false; void this.flush(); }
        }
    }

    send(data, signal) {
        return new Promise((resolve, reject) => {
            const request = https.request(this.uploadUrl, { method: 'POST',
                signal: AbortSignal.any([signal, AbortSignal.timeout(5000)]),
                headers: { 'content-type': 'application/json', 'content-length': data.length } }, response => {
                response.resume();
                if (response.statusCode === 202) resolve();
                else reject(new Error('Diagnostics receipt HTTP ' + response.statusCode));
            });
            request.once('error', reject);
            request.end(data);
        });
    }

    close() {
        this.closed = true;
        clearInterval(this.timer);
        this.active?.abort();
    }
}

module.exports = { DiagnosticsUpload };
