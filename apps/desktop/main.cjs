const { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, net, protocol, safeStorage, shell, Tray } = require('electron');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { DesktopController } = require('./controller.cjs');
const { SpeechController } = require('./speech.cjs');
const { LocalSpeechRuntime } = require('./local-speech.cjs');
const { VisualSession } = require('./visual.cjs');
const { createShell } = require('./shell.cjs');
const { listModels, testModel } = require('./model-service.cjs');

const origin = 'guizhi://app';
protocol.registerSchemesAsPrivileged([{ scheme: 'guizhi', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

async function startDesktop(options) {
    const { externalPath } = await import('../../src/guizhi/infrastructure/dsh/sdk.mjs');
    app.setName('规智');
    if (options.userData) app.setPath('userData', externalPath(options.userData));
    if (!app.requestSingleInstanceLock()) {
        app.quit();
        return null;
    }
    await app.whenReady();
    const { runtime, dsh, rendererRuntime } = options;
    for (const item of [runtime, rendererRuntime]) {
        if (!item || !path.isAbsolute(item)) throw new Error('请选择有效的运行目录');
    }
    const vendor = path.join(rendererRuntime, 'node_modules');
    const localFiles = ['index.html', 'app.js', 'shell-ui.js', 'settings.js', 'map.js', 'coordinates.js', 'skills.js',
        'style.css', 'theme.css', 'mark.svg', 'model-form.js', 'onboarding.js',
        'portrait.html', 'portrait.css', 'portrait.js', 'task-commands.js', 'speech-settings.js',
        'speech-audio.js', 'voice-input.js', 'visual.html', 'visual.css', 'visual.js', 'arcmap.js'];
    const files = new Map(localFiles.map(name => ['/' + name, path.join(__dirname, name)]));
    files.set('/react.js', path.join(vendor, 'react', 'umd', 'react.production.min.js'));
    files.set('/react-dom.js', path.join(vendor, 'react-dom', 'umd', 'react-dom.production.min.js'));
    files.set('/dayjs.js', path.join(vendor, 'dayjs', 'dayjs.min.js'));
    files.set('/antd.js', path.join(vendor, 'antd', 'dist', 'antd.min.js'));
    for (const file of files.values()) {
        if (!fs.existsSync(file)) throw new Error('桌面运行依赖不完整');
    }
    const nonce = randomBytes(18).toString('base64');
    protocol.handle('guizhi', async request => {
        const url = new URL(request.url);
        if (url.hostname !== 'app') return new Response('', { status: 404 });
        const file = files.get(url.pathname);
        if (!file) return new Response('', { status: 404 });
        if (url.pathname === '/index.html') {
            return new Response(fs.readFileSync(file, 'utf8').replaceAll('STYLE_NONCE', nonce),
                { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
        }
        return net.fetch(pathToFileURL(file).href);
    });
    const desktop = createShell({ userData: app.getPath('userData'), verifyStartup: options.verifyStartup,
        icon: path.join(__dirname, 'mark.png') });
    const { window, portrait, launcher, reveal } = desktop;
    const version = fs.readFileSync(path.join(__dirname, '../../VERSION'), 'utf8').trim();
    const workbenchUrl = origin + '/index.html';
    const launcherUrl = workbenchUrl + '?surface=launcher';
    const portraitUrl = origin + '/portrait.html';
    let visual;
    let draft = {};
    const speech = new SpeechController({ userData: app.getPath('userData'), storage: safeStorage,
        local: new LocalSpeechRuntime(path.join(runtime, 'python', 'python.exe'), externalPath),
        bundledLocalRoot: options.bundledSpeech });
    const withSpeech = state => ({ ...state, speech: speech.state(), appearance: desktop.appearance(), version });
    function broadcast(state) {
        visual?.update(state);
        desktop.notify('state', withSpeech(state));
    }
    const controller = new DesktopController({ userData: app.getPath('userData'), runtime, dsh,
        safeStorage, externalPath, onChange: broadcast, bundledArcmap: options.bundledArcmap,
        installationRoot: options.installationRoot });
    visual = new VisualSession({ python: controller.runtime.python,
        masks: () => portrait.isVisible() ? [portrait.getBounds()] : [],
        cancel: () => broadcast(controller.cancel()) });
    const partition = portrait.webContents.session;
    partition.setPermissionRequestHandler((source, permission, callback, details) => callback(
        ((source === portrait.webContents && source.getURL() === portraitUrl) ||
            (source === window.webContents && source.getURL() === workbenchUrl)) &&
        (permission === 'speaker-selection' || (permission === 'media' &&
            details.mediaTypes?.length === 1 && details.mediaTypes[0] === 'audio'))));
    partition.setPermissionCheckHandler((source, permission, requestingOrigin, details) =>
        [portrait.webContents, window.webContents].includes(source) && requestingOrigin === origin &&
        (permission === 'speaker-selection' || (permission === 'media' && details.mediaType !== 'video')));
    const tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'mark.png')).resize({ width: 24, height: 24 }));
    tray.setToolTip('规智');
    tray.setContextMenu(Menu.buildFromTemplate([
        { label: '打开工作台', click: reveal },
        { label: '快捷面板', click: desktop.openLauncher },
        { label: '显示数字形象', click: desktop.showPortrait },
        { label: '隐藏数字形象', click: () => {
            portrait.webContents.send('guizhi:portraitHide');
            speech.cancel();
            portrait.hide();
        } },
        { label: '设置', click: () => { reveal(); window.webContents.send('guizhi:settings'); } },
        { label: '接管', click: () => visual.takeover() },
        { type: 'separator' }, { label: '退出', click: () => app.quit() },
    ]));
    tray.on('double-click', reveal);
    app.on('second-instance', reveal);
    let quitting = false;
    let closing = false;
    window.on('close', event => {
        if (!quitting) { event.preventDefault(); window.hide(); }
    });
    app.on('before-quit', event => {
        if (quitting) return;
        event.preventDefault();
        if (closing) return;
        closing = true;
        desktop.close();
        visual.close();
        speech.cancel();
        void controller.close().finally(() => { quitting = true; tray.destroy(); app.quit(); });
    });
    const pages = new Map([[window, workbenchUrl], [launcher, launcherUrl], [portrait, portraitUrl]]);
    for (const [surface, url] of pages) {
        surface.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
        surface.webContents.on('will-navigate', (event, target) => { if (target !== url) event.preventDefault(); });
    }

    function bind(method, handler, withSurface = false) {
        ipcMain.handle('guizhi:' + method, (event, ...args) => {
            const surface = desktop.windows.find(candidate => candidate.webContents === event.sender);
            if (!pages.has(surface) || event.senderFrame?.parent || pages.get(surface) !== event.senderFrame?.url) {
                throw new Error('请求来源无效');
            }
            return withSurface ? handler(surface, ...args) : handler(...args);
        });
    }
    bind('state', () => withSpeech(controller.state()));
    bind('appearance', desktop.configureAppearance);
    bind('readDraft', () => draft);
    bind('saveDraft', (source, next) => {
        if (!next || typeof next !== 'object' || Array.isArray(next)) throw new Error('草稿无效');
        draft = next;
        for (const surface of [window, launcher]) {
            if (surface !== source) surface.webContents.send('guizhi:draft', draft);
        }
    }, true);
    bind('openLauncher', desktop.openLauncher);
    bind('hideLauncher', () => launcher.hide());
    bind('portraitVoice', desktop.voice);
    bind('voiceSubmit', () => {
        desktop.openLauncher();
        launcher.webContents.send('guizhi:launcherSubmit');
    });
    bind('voiceDraft', prompt => {
        if (typeof prompt !== 'string') throw new Error('语音内容无效');
        draft = { ...draft, prompt: prompt.slice(0, 4000) };
        desktop.notify('draft', draft);
        desktop.openLauncher();
    });
    bind('configure', input => {
        const state = controller.configure(input);
        broadcast(state);
        return withSpeech(state);
    });
    bind('workspaceReady', input => controller.workspaceReady(input));
    bind('modelList', input => {
        const { config, key } = controller.selectModel(input, false);
        return listModels(config, key);
    });
    bind('modelTest', input => {
        const { config, key } = controller.selectModel(input);
        return testModel(controller.runtime, config, key);
    });
    bind('speechConfigure', input => {
        const settings = speech.configure(input);
        broadcast(controller.state());
        return settings;
    });
    bind('speechRecognize', audio => speech.recognize(audio, text => {
        if (!portrait.isDestroyed()) portrait.webContents.send('guizhi:speechPartial', text);
    }));
    bind('speechSynthesize', text => speech.synthesize(text));
    bind('speechTest', (kind, input, audio) => speech.test(kind, input, audio));
    bind('speechCancel', () => speech.cancel());
    bind('portraitHit', active => portrait.setIgnoreMouseEvents(!active, active ? {} : { forward: true }));
    bind('workbench', id => {
        reveal();
        if (typeof id === 'string' && controller.tasks.some(item => item.id === id)) {
            window.webContents.send('guizhi:openTask', id);
        }
    });
    bind('workspace', async () => {
        const pick = await dialog.showOpenDialog(window, { properties: ['openDirectory', 'createDirectory'] });
        return pick.canceled ? null : pick.filePaths[0];
    });
    bind('arcmapBridge', async () => {
        const pick = await dialog.showOpenDialog(window, { properties: ['openFile'],
            filters: [{ name: 'ArcMap Bridge', extensions: ['exe'] }] });
        return pick.canceled ? null : controller.selectArcmapBridge(pick.filePaths[0]);
    });
    bind('arcmapFile', async () => {
        const pick = await dialog.showOpenDialog(window, { properties: ['openFile'],
            filters: [{ name: 'ArcGIS 工程或图层文件', extensions: ['mxd', 'json'] }] });
        if (pick.canceled) return null;
        const file = pick.filePaths[0];
        if (path.extname(file).toLowerCase() === '.mxd') return { action: 'describe', map: file };
        if (fs.statSync(file).size > 2 << 20) throw new Error('ArcGIS 图层文件过大');
        const payload = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
        if (payload.action !== 'import' || !payload.map_revision) throw new Error('ArcGIS 图层文件无效');
        return payload;
    });
    bind('arcmapExchange', async request => {
        const result = await controller.arcmap(request);
        if (request.action === 'publish') shell.showItemInFolder(result.output);
        return result;
    });
    bind('material', async (kind = 'vector') => {
        const formats = { vector: { name: 'GeoPackage', extensions: ['gpkg'] },
            raster: { name: 'GeoTIFF', extensions: ['tif', 'tiff'] }, cad: { name: 'DXF', extensions: ['dxf'] },
            document: { name: '审批材料', extensions: ['pdf', 'docx', 'xlsx'] },
            model: { name: 'ONNX', extensions: ['onnx'] } };
        if (kind !== 'any' && !Object.hasOwn(formats, kind)) throw new Error('材料格式无效');
        const pick = await dialog.showOpenDialog(BrowserWindow.getFocusedWindow() || window, { properties: ['openFile'],
            filters: kind === 'any' ? Object.values(formats) : [formats[kind]] });
        return pick.canceled ? null : controller.describe(pick.filePaths[0]);
    });
    bind('materialPath', file => controller.describe(externalPath(file)));
    bind('mapAction', async (...args) => withSpeech(await controller.mapAction(...args)));
    bind('layoutReport', async () => {
        if (await shell.openPath(await controller.layoutReport())) throw new Error('无法打开版面');
    });
    bind('submit', task => {
        const state = controller.command(task);
        draft = {};
        desktop.notify('draft', draft);
        broadcast(state);
        return withSpeech(state);
    });
    bind('cancel', () => {
        const state = controller.cancel();
        broadcast(state);
        return withSpeech(state);
    });
    bind('coordinates', () => controller.coordinateCatalog());
    bind('skills', () => controller.python('skills'));
    bind('capabilities', () => controller.python('capabilities'));
    bind('capability', operation => controller.python('capability-show', operation));
    bind('skillShow', reference => controller.python('skill-show', reference));
    bind('skillPreview', markdown => controller.skillRequest('skill-preview', markdown));
    bind('skillPublish', (markdown, materials, parameters) => controller.skillRequest('skill-publish', markdown, materials, parameters));
    const skillFile = { name: '业务技能', extensions: ['md'] };
    bind('skillOpen', async () => {
        const pick = await dialog.showOpenDialog(window, { properties: ['openFile'], filters: [skillFile] });
        if (pick.canceled) return null;
        const file = pick.filePaths[0];
        if (fs.statSync(file).size > 65536) throw new Error('技能文件最多 64 KB');
        return fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
    });
    bind('skillTemplate', async () => {
        const pick = await dialog.showSaveDialog(window, { defaultPath: '业务技能模板.md', filters: [skillFile] });
        if (!pick.canceled) fs.copyFileSync(path.join(__dirname, '../../resources/skills/template.md'), externalPath(pick.filePath));
    });
    bind('delivery', async (id, kind) => {
        shell.showItemInFolder(await controller.delivery(id, kind));
    });
    bind('approvalSource', async (...args) => {
        if (await shell.openPath(await controller.approvalSource(...args))) throw new Error('无法打开依据');
    });
    bind('importCoordinate', async kind => {
        if (!['plan', 'definition'].includes(kind)) throw new Error('请选择坐标资料类型');
        const pick = await dialog.showOpenDialog(window, { properties: ['openFile'], filters: [
            kind === 'plan' ? { name: '坐标转换方案', extensions: ['json'] }
                : { name: 'Shapefile', extensions: ['shp'] },
        ] });
        if (!pick.canceled) await controller.importCoordinate(kind, pick.filePaths[0]);
        return controller.coordinateCatalog();
    });
    bind('report', async id => {
        const error = await shell.openPath(await controller.report(id));
        if (error) throw new Error('无法打开报告');
    });
    await Promise.all([...pages].map(([surface, url]) => surface.loadURL(url)));
    desktop.registerShortcut();
    if (!options.verifyStartup) {
        if (!controller.settings.workspace || !controller.settings.modelConfig) reveal();
        desktop.showPortrait();
    }
    return { window, launcher, portrait, tray, controller, speech, visual };
}

module.exports = { startDesktop };
