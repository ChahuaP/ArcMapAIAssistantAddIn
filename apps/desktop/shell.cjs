const { BrowserWindow, globalShortcut, nativeTheme, powerMonitor, screen } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');

const themes = ['system', 'light', 'dark'];
const shortcutPattern = /^(?:Ctrl(?:\+Alt)?|Alt)(?:\+Shift)?\+(?:[A-Z0-9]|F(?:[1-9]|1[0-2]))$/;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

function createShell({ userData, icon, verifyStartup }) {
    const file = path.join(userData, 'desktop-appearance.json');
    const stored = fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : {};
    const settings = {
        theme: themes.includes(stored.theme) ? stored.theme : 'system',
        shortcut: shortcutPattern.test(stored.shortcut) ? stored.shortcut : 'Ctrl+G',
        panels: [0, 1].map(index => {
            const size = stored.panels?.[index];
            return Number.isFinite(size) ? size === 0 ? 0 : clamp(size, 160, 520) : [222, 362][index];
        }),
        expandedPanels: [0, 1].map(index => {
            const size = stored.expandedPanels?.[index] || stored.panels?.[index];
            return Number.isFinite(size) && size > 0 ? clamp(size, 160, 520) : [222, 362][index];
        }),
        positions: stored.positions || {},
        displayId: stored.displayId,
    };
    const webPreferences = { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true };
    const overlay = () => nativeTheme.shouldUseDarkColors
        ? { color: '#131e2d', symbolColor: '#eef3fa', height: 50 }
        : { color: '#f1f5f9', symbolColor: '#152235', height: 50 };
    nativeTheme.themeSource = settings.theme;
    const workbench = new BrowserWindow({ width: 1376, height: 824, minWidth: 790, minHeight: 530,
        show: false, title: '规智', titleBarStyle: 'hidden', titleBarOverlay: overlay(), autoHideMenuBar: true,
        backgroundColor: overlay().color, icon, webPreferences });
    const launcher = new BrowserWindow({ width: 944, height: 560, frame: false, resizable: false,
        show: false, title: '规智快捷面板', skipTaskbar: true, alwaysOnTop: true,
        backgroundColor: overlay().color, icon, webPreferences });
    const portrait = new BrowserWindow({ width: 312, height: 244, frame: false, transparent: true,
        backgroundColor: '#00000000', resizable: false, skipTaskbar: true, alwaysOnTop: true,
        show: false, hasShadow: false, title: '规智数字形象',
        webPreferences: { ...webPreferences, autoplayPolicy: 'no-user-gesture-required' } });
    const windows = [workbench, launcher, portrait];
    let shortcutRegistered = false;
    let displayId = settings.displayId ?? screen.getPrimaryDisplay().id;
    let movingPortrait = false;

    function save() {
        const pending = file + '.' + randomUUID() + '.pending';
        try {
            fs.writeFileSync(pending, JSON.stringify(settings), { flag: 'wx' });
            fs.renameSync(pending, file);
        } finally {
            fs.rmSync(pending, { force: true });
        }
    }

    function notify(channel, value) {
        for (const window of windows) {
            if (!window.isDestroyed()) window.webContents.send('guizhi:' + channel, value);
        }
    }

    function appearance() {
        return { theme: settings.theme, dark: nativeTheme.shouldUseDarkColors,
            panels: settings.panels, expandedPanels: settings.expandedPanels,
            shortcut: settings.shortcut, shortcutRegistered };
    }

    function themeChanged() {
        workbench.setTitleBarOverlay(overlay());
        launcher.setBackgroundColor(overlay().color);
        notify('appearance', appearance());
    }

    function configureAppearance(input) {
        if (input.shortcut !== undefined) changeShortcut(input.shortcut);
        if (input.theme !== undefined) {
            if (!themes.includes(input.theme)) throw new Error('外观主题无效');
            settings.theme = input.theme;
            nativeTheme.themeSource = input.theme;
        }
        if (input.panels !== undefined) {
            if (!Array.isArray(input.panels) || input.panels.length !== 2 ||
                    input.panels.some(value => !Number.isFinite(value) || value < 0 || value > 520)) {
                throw new Error('分栏尺寸无效');
            }
            settings.panels = input.panels;
            input.panels.forEach((size, index) => { if (size > 0) settings.expandedPanels[index] = size; });
        }
        save();
        themeChanged();
        return appearance();
    }

    function placePortrait() {
        const display = screen.getAllDisplays().find(item => item.id === displayId) || screen.getPrimaryDisplay();
        displayId = display.id;
        const { x, y, width, height } = display.workArea;
        const [w, h] = portrait.getSize();
        const saved = settings.positions[displayId];
        const offsetX = Number.isFinite(saved?.x) ? saved.x : width - w - 16;
        const offsetY = Number.isFinite(saved?.y) ? saved.y : height - h - 16;
        movingPortrait = true;
        portrait.setPosition(x + clamp(offsetX, 0, Math.max(0, width - w)),
            y + clamp(offsetY, 0, Math.max(0, height - h)));
        movingPortrait = false;
        portrait.setAlwaysOnTop(true);
    }

    function recordPortrait() {
        if (movingPortrait) return;
        const bounds = portrait.getBounds();
        const display = screen.getDisplayMatching(bounds);
        displayId = display.id;
        settings.displayId = display.id;
        settings.positions[displayId] = { x: bounds.x - display.workArea.x, y: bounds.y - display.workArea.y };
        save();
    }

    function openLauncher() {
        const area = screen.getDisplayNearestPoint(screen.getCursorScreenPoint()).workArea;
        const width = Math.min(944, area.width - 32);
        const height = Math.min(560, area.height - 32);
        launcher.setBounds({ width, height, x: area.x + Math.round((area.width - width) / 2),
            y: area.y + Math.round(Math.min(area.height * 0.13, (area.height - height) / 2)) });
        launcher.show();
        launcher.focus();
        launcher.webContents.send('guizhi:launcherFocus');
    }

    function reveal() {
        launcher.hide();
        workbench.show();
        workbench.focus();
    }

    function showPortrait() {
        placePortrait();
        portrait.showInactive();
    }

    function voice() {
        launcher.hide();
        showPortrait();
        portrait.setIgnoreMouseEvents(false);
        portrait.focus();
        portrait.webContents.send('guizhi:portraitVoice');
    }

    const displayEvents = ['display-added', 'display-removed', 'display-metrics-changed'];
    for (const event of displayEvents) screen.on(event, placePortrait);
    powerMonitor.on('resume', placePortrait);
    nativeTheme.on('updated', themeChanged);
    portrait.on('moved', recordPortrait);
    placePortrait();

    function registerShortcut() {
        if (verifyStartup) return;
        shortcutRegistered = globalShortcut.register(settings.shortcut, toggleLauncher);
        themeChanged();
    }

    function captureShortcut(active) {
        if (active && shortcutRegistered) {
            globalShortcut.unregister(settings.shortcut);
            shortcutRegistered = false;
        } else if (!active && !shortcutRegistered) {
            registerShortcut();
        }
    }

    function toggleLauncher() {
        if (launcher.isVisible() && launcher.isFocused()) launcher.hide();
        else openLauncher();
    }

    function changeShortcut(input) {
        if (typeof input !== 'string') throw new Error('快捷键无效');
        const next = input.replace(/\s/g, '').toUpperCase()
            .replace('CTRL', 'Ctrl').replace('ALT', 'Alt').replace('SHIFT', 'Shift');
        if (!shortcutPattern.test(next)) throw new Error('请按 Ctrl 或 Alt 加字母、数字或功能键');
        if (next === settings.shortcut && shortcutRegistered) return;
        if (!globalShortcut.register(next, toggleLauncher)) throw new Error(next + ' 已被占用，请选择其他组合');
        if (shortcutRegistered) globalShortcut.unregister(settings.shortcut);
        shortcutRegistered = true;
        settings.shortcut = next;
    }

    function close() {
        if (shortcutRegistered) globalShortcut.unregister(settings.shortcut);
        for (const event of displayEvents) screen.removeListener(event, placePortrait);
        powerMonitor.removeListener('resume', placePortrait);
        nativeTheme.removeListener('updated', themeChanged);
    }

    return { window: workbench, launcher, portrait, windows, appearance, configureAppearance,
        openLauncher, reveal, showPortrait, voice, registerShortcut, captureShortcut, notify, close };
}

module.exports = { createShell };
