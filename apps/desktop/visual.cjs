const { BrowserWindow, globalShortcut, ipcMain, screen } = require('electron');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { ScreenTarget, maxAge } = require('./screen-target.cjs');

const shortcut = 'CommandOrControl+Alt+Escape';
const base = 'guizhi://app/visual.html?view=';
const thinking = { kind: 'think', label: '规智正在分析任务' };
const wait = { kind: 'stop', label: '规智正在安全停止' };

class VisualSession {
    constructor({ python, masks, cancel }) {
        this.targets = new ScreenTarget(python);
        this.masks = masks;
        this.cancel = cancel;
        this.edges = new Map();
        this.task = null;
        this.clearDisplay = () => {
            this.releaseTarget();
            if (this.task && !this.stopping && this.status?.window.isVisible()) this.ensureChrome();
        };
        this.shortcutRegistered = globalShortcut.register(shortcut, () => this.takeover());
        screen.on('display-added', this.clearDisplay);
        screen.on('display-removed', this.clearDisplay);
        screen.on('display-metrics-changed', this.clearDisplay);
        ipcMain.handle('guizhi:takeover', event => {
            if (!this.status || event.sender !== this.status.window.webContents || event.senderFrame?.url !== base + 'status') {
                throw new Error('请求来源无效');
            }
            this.takeover();
        });
    }

    view(name, pointer) {
        const window = new BrowserWindow({ x: -30000, y: -30000, width: 40, height: 40, show: false,
            frame: false, transparent: true, backgroundColor: '#00000000', resizable: false,
            skipTaskbar: true, hasShadow: false, focusable: name === 'status', title: '规智办理状态',
            webPreferences: { preload: name === 'status' ? path.join(__dirname, 'visual-preload.cjs') : undefined,
                contextIsolation: true, sandbox: true, backgroundThrottling: false } });
        window.setAlwaysOnTop(true, 'screen-saver');
        window.setIgnoreMouseEvents(!pointer);
        window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
        window.webContents.on('will-navigate', event => event.preventDefault());
        window.webContents.on('render-process-gone', () => {
            this.dismissed = this.task;
            this.hide();
        });
        return { window, ready: window.loadURL(base + name) };
    }

    ensureChrome() {
        const displays = screen.getAllDisplays();
        for (const [id, item] of this.edges) {
            if (!displays.some(d => d.id === id)) {
                item.window.destroy();
                this.edges.delete(id);
            }
        }
        for (const display of displays) {
            let item = this.edges.get(display.id);
            if (!item) {
                item = this.view('edge', false);
                this.edges.set(display.id, item);
            }
            item.window.setBounds(display.bounds);
            void item.ready.then(() => {
                if (this.task && this.dismissed !== this.task && !this.stopping && !item.window.isDestroyed()) item.window.showInactive();
            }).catch(() => this.hide());
        }
        this.status ??= this.view('status', true);
        if (!this.status.window.isVisible()) {
            const { workArea } = screen.getDisplayNearestPoint(screen.getCursorScreenPoint());
            const width = Math.min(workArea.width, 490);
            this.status.window.setBounds({ x: workArea.x + Math.round((workArea.width - width) / 2),
                y: workArea.y + workArea.height - 91, width, height: 68 });
        }
    }

    update(state) {
        if (this.closed) return;
        const task = state.tasks.find(item => ['running', 'cancelling'].includes(item.status));
        if (!task) {
            this.task = null;
            this.stopping = false;
            this.hide();
            return;
        }
        if (task.id !== this.task) {
            this.hide();
            this.task = task.id;
            this.stopping = false;
            this.last = null;
        }
        if (task.status === 'cancelling') {
            this.showStopping();
            return;
        }
        if (this.dismissed === this.task || this.stopping || state.settings.showOperation === false) return;
        const step = task.visual || thinking;
        if (step === this.last) return;
        this.last = step;
        this.ensureChrome();
        this.showStatus(step);
        this.releaseTarget();
        if (step.target && state.settings.screenHighlight !== false) {
            const signal = new AbortController();
            this.lookup = signal;
            void this.highlight(step.target, signal.signal).catch(() => {
                if (this.lookup === signal) this.releaseTarget();
            });
        }
    }

    showStatus(step) {
        const task = this.task;
        void this.status.ready.then(() => {
            if (this.task !== task || this.dismissed === task || (this.stopping && step !== wait)) return;
            this.status.window.webContents.send('guizhi:visual-step', { ...step, shortcut: this.shortcutRegistered });
            this.status.window.showInactive();
        }).catch(() => this.hide());
    }

    exclusions() {
        return [...this.masks(), ...(this.status?.window.isVisible() ? [this.status.window.getBounds()] : [])];
    }

    async highlight(label, signal) {
        const status = this.status;
        await Promise.all([status.ready, ...[...this.edges.values()].map(item => item.ready)]);
        if (!status.window.isVisible()) return;
        // Let the compositor present our chrome before freezing its surroundings.
        await delay(160, undefined, { signal });
        signal.throwIfAborted();
        const target = await this.targets.locate(label, () => this.exclusions(), signal);
        if (!target || signal.aborted) return;
        this.outline ??= this.view('outline', false);
        this.guard ??= this.view('guard', true);
        await Promise.all([this.outline.ready, this.guard.ready]);
        if (!await this.targets.current(target, () => this.exclusions(), signal)) return;
        signal.throwIfAborted();
        this.location = target;
        this.outline.window.setBounds(target.outline);
        this.guard.window.setBounds(target.bounds);
        this.outline.window.showInactive();
        this.guard.window.showInactive();
        const expires = Math.max(0, target.capturedAt + maxAge - Date.now());
        this.lease = setTimeout(() => this.releaseTarget(), expires);
        const validate = async () => {
            try {
                if (!await this.targets.current(target, () => this.exclusions(), signal)) {
                    if (!signal.aborted) this.releaseTarget();
                    return;
                }
                this.recheck = setTimeout(validate, 190);
            } catch {
                if (!signal.aborted) this.releaseTarget();
            }
        };
        this.recheck = setTimeout(validate, 190);
    }

    releaseTarget() {
        this.lookup?.abort();
        this.lookup = null;
        this.location = null;
        clearTimeout(this.recheck);
        clearTimeout(this.lease);
        this.outline?.window.hide();
        this.guard?.window.hide();
    }

    showStopping() {
        if (this.stopping) return;
        this.stopping = true;
        this.releaseTarget();
        for (const item of this.edges.values()) item.window.hide();
        if (this.status?.window.isVisible()) this.showStatus(wait);
    }

    takeover() {
        if (!this.task || this.stopping) return;
        this.showStopping();
        this.cancel();
    }

    hide() {
        this.releaseTarget();
        for (const item of this.edges.values()) item.window.hide();
        this.status?.window.hide();
    }

    close() {
        this.closed = true;
        this.task = null;
        this.hide();
        if (this.shortcutRegistered) globalShortcut.unregister(shortcut);
        ipcMain.removeHandler('guizhi:takeover');
        screen.removeListener('display-added', this.clearDisplay);
        screen.removeListener('display-removed', this.clearDisplay);
        screen.removeListener('display-metrics-changed', this.clearDisplay);
    }
}

module.exports = { VisualSession };
