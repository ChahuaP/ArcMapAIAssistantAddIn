const { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, net, protocol, safeStorage, screen, shell, Tray } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { DesktopController } = require('./controller.cjs');
const { SpeechController } = require('./speech.cjs');
const { LocalSpeechRuntime } = require('./local-speech.cjs');
const { modelCapabilities } = require('../../src/guizhi/infrastructure/dsh/ollama.cjs');
const { VisualSession } = require('./visual.cjs');

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
    const localFiles = ['index.html', 'app.js', 'map.js', 'coordinates.js', 'skills.js', 'style.css', 'mark.svg',
        'portrait.html', 'portrait.css', 'portrait.js', 'task-commands.js', 'speech-settings.js',
        'speech-audio.js', 'voice-input.js', 'visual.html', 'visual.css', 'visual.js', 'arcmap.js'];
    const files = new Map(localFiles.map(name => ['/' + name, path.join(__dirname, name)]));
    files.set('/react.js', path.join(vendor, 'react', 'umd', 'react.production.min.js'));
    files.set('/react-dom.js', path.join(vendor, 'react-dom', 'umd', 'react-dom.production.min.js'));
    for (const file of files.values()) {
        if (!fs.existsSync(file)) throw new Error('桌面运行依赖不完整');
    }
    protocol.handle('guizhi', async request => {
        const url = new URL(request.url);
        if (url.hostname !== 'app') return new Response('', { status: 404 });
        const file = files.get(url.pathname);
        if (!file) return new Response('', { status: 404 });
        return net.fetch(pathToFileURL(file).href);
    });
    let window;
    let portrait;
    let visual;
    const speech = new SpeechController({ userData: app.getPath('userData'), storage: safeStorage,
        local: new LocalSpeechRuntime(path.join(runtime, 'python', 'python.exe'), externalPath) });
    const withSpeech = state => ({ ...state, speech: speech.state() });
    function broadcast(state) {
        visual?.update(state);
        for (const item of [window, portrait]) {
            if (item && !item.isDestroyed()) item.webContents.send('guizhi:state', withSpeech(state));
        }
    }
    const controller = new DesktopController({ userData: app.getPath('userData'), runtime, dsh,
        safeStorage, externalPath, onChange: broadcast });
    window = new BrowserWindow({ width: 1270, height: 820, minWidth: 790, minHeight: 590,
        show: false, title: '规智', autoHideMenuBar: true, backgroundColor: '#f3f7f7',
        icon: path.join(__dirname, 'mark.png'),
        webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true } });
    const area = screen.getPrimaryDisplay().workArea;
    portrait = new BrowserWindow({ width: 312, height: 275, x: area.x + area.width - 328,
        y: area.y + area.height - 292, frame: false, transparent: true, backgroundColor: '#00000000',
        resizable: false, skipTaskbar: true, alwaysOnTop: true, show: false, hasShadow: false, title: '规智数字形象',
        webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true,
            autoplayPolicy: 'no-user-gesture-required' } });
    const portraitUrl = origin + '/portrait.html';
    visual = new VisualSession({ python: controller.runtime.python,
        masks: () => portrait.isVisible() ? [portrait.getBounds()] : [],
        cancel: () => broadcast(controller.cancel()) });
    const partition = portrait.webContents.session;
    partition.setPermissionRequestHandler((source, permission, callback, details) => callback(
        source === portrait.webContents && source.getURL() === portraitUrl &&
        (permission === 'speaker-selection' || (permission === 'media' &&
            details.mediaTypes?.length === 1 && details.mediaTypes[0] === 'audio'))));
    partition.setPermissionCheckHandler((source, permission, requestingOrigin, details) =>
        [portrait.webContents, window.webContents].includes(source) && requestingOrigin === origin &&
        (permission === 'speaker-selection' || (permission === 'media' && details.mediaType !== 'video')));
    const reveal = () => { window.show(); window.focus(); };
    const tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'mark.png')).resize({ width: 24, height: 24 }));
    tray.setToolTip('规智');
    tray.setContextMenu(Menu.buildFromTemplate([
        { label: '打开规智', click: reveal },
        { label: '显示数字形象', click: () => portrait.showInactive() },
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
        visual.close();
        speech.cancel();
        void controller.close().finally(() => { quitting = true; tray.destroy(); app.quit(); });
    });
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('will-navigate', (event, url) => {
        if (url !== origin + '/index.html') event.preventDefault();
    });
    portrait.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    portrait.webContents.on('will-navigate', (event, url) => { if (url !== portraitUrl) event.preventDefault(); });

    function bind(method, handler) {
        ipcMain.handle('guizhi:' + method, (event, ...args) => {
            const source = event.senderFrame?.url;
            if (!(!window.isDestroyed() && event.sender === window.webContents && source === origin + '/index.html') &&
                    !(!portrait.isDestroyed() && event.sender === portrait.webContents && source === portraitUrl)) {
                throw new Error('请求来源无效');
            }
            return handler(...args);
        });
    }
    bind('state', () => withSpeech(controller.state()));
    bind('configure', input => {
        const state = controller.configure(input);
        broadcast(state);
        return withSpeech(state);
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
    bind('speechCancel', () => speech.cancel());
    bind('modelCheck', async (kind, url, model) => {
        if (!['chat', 'vision'].includes(kind)) throw new Error('模型类型无效');
        const result = await modelCapabilities(url, model);
        if (!result.capabilities.includes(kind === 'chat' ? 'tools' : 'vision')) {
            throw new Error(kind === 'chat' ? '所选模型不支持工具调用' : '所选模型不支持图像');
        }
        return result;
    });
    bind('speechFolder', async () => {
        const pick = await dialog.showOpenDialog(window, { properties: ['openDirectory'] });
        return pick.canceled ? null : externalPath(pick.filePaths[0]);
    });
    bind('portraitHit', active => portrait.setIgnoreMouseEvents(!active, active ? {} : { forward: true }));
    bind('portraitInput', () => {
        portrait.show();
        portrait.focus();
        portrait.webContents.send('guizhi:portraitInput');
    });
    bind('workbench', reveal);
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
            filters: [{ name: 'ArcMap 工程或交接文件', extensions: ['mxd', 'json'] }] });
        if (pick.canceled) return null;
        const file = pick.filePaths[0];
        if (path.extname(file).toLowerCase() === '.mxd') return { action: 'describe', map: file };
        if (fs.statSync(file).size > 2 << 20) throw new Error('ArcMap 交接文件过大');
        const payload = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
        if (payload.action !== 'import' || !payload.map_revision) throw new Error('ArcMap 交接文件无效');
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
            document: { name: '审批材料', extensions: ['pdf', 'docx', 'xlsx'] } };
        if (kind !== 'any' && !Object.hasOwn(formats, kind)) throw new Error('材料格式无效');
        const pick = await dialog.showOpenDialog(window, { properties: ['openFile'],
            filters: kind === 'any' ? Object.values(formats) : [formats[kind]] });
        return pick.canceled ? null : controller.describe(pick.filePaths[0]);
    });
    bind('mapAction', async (...args) => withSpeech(await controller.mapAction(...args)));
    bind('layoutReport', async () => {
        if (await shell.openPath(await controller.layoutReport())) throw new Error('无法打开版面');
    });
    bind('submit', task => {
        const state = controller.command(task);
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
    await Promise.all([window.loadURL(origin + '/index.html'), portrait.loadURL(portraitUrl)]);
    if (!controller.settings.workspace) reveal();
    portrait.showInactive();
    return { window, portrait, tray, controller, speech, visual };
}

module.exports = { startDesktop };
