const { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, net, protocol, safeStorage, screen, shell, Tray } = require('electron');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { DesktopController } = require('./controller.cjs');
const { SpeechController } = require('./speech.cjs');

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
        'portrait.html', 'portrait.css', 'portrait.js', 'avatar.js', 'task-commands.js', 'speech-settings.js',
        'speech-audio.js', 'voice-input.js'];
    const files = new Map(localFiles.map(name => ['/' + name, path.join(__dirname, name)]));
    files.set('/react.js', path.join(vendor, 'react', 'umd', 'react.production.min.js'));
    files.set('/react-dom.js', path.join(vendor, 'react-dom', 'umd', 'react-dom.production.min.js'));
    const avatar = path.join(rendererRuntime, 'assets', 'avatar.vrm');
    const identity = require('../../packaging/desktop-runtime/avatar.lock.json');
    if (!fs.existsSync(avatar) || createHash('sha256').update(fs.readFileSync(avatar)).digest('hex') !== identity.sha256) {
        throw new Error('数字形象资源缺失或完整性检查失败');
    }
    files.set('/avatar.vrm', avatar);
    for (const file of files.values()) {
        if (!fs.existsSync(file)) throw new Error('桌面运行依赖不完整');
    }
    const importMap = fs.readFileSync(files.get('/portrait.html'), 'utf8').match(/<script type="importmap">(.*?)<\/script>/)[1];
    const importHash = createHash('sha256').update(importMap).digest('base64');
    const vendorRoots = { '/vendor/three/': path.join(vendor, 'three'),
        '/vendor/vrm/': path.join(vendor, '@pixiv', 'three-vrm') };
    protocol.handle('guizhi', async request => {
        const url = new URL(request.url);
        if (url.hostname !== 'app') return new Response('', { status: 404 });
        let file = files.get(url.pathname);
        if (!file) {
            for (const [prefix, root] of Object.entries(vendorRoots)) {
                if (!url.pathname.startsWith(prefix) || !url.pathname.endsWith('.js')) continue;
                const candidate = path.resolve(root, decodeURIComponent(url.pathname.slice(prefix.length)));
                if (candidate.startsWith(root + path.sep) && fs.existsSync(candidate)) file = candidate;
            }
        }
        if (!file) return new Response('', { status: 404 });
        const response = await net.fetch(pathToFileURL(file).href);
        if (url.pathname !== '/portrait.html') return response;
        const headers = new Headers(response.headers);
        headers.set('Content-Security-Policy', "default-src 'none'; script-src 'self' 'sha256-" + importHash +
            "'; style-src 'self'; connect-src 'self' blob:; img-src 'self' blob: data:; worker-src 'self'; base-uri 'none'; form-action 'none'");
        return new Response(response.body, { status: response.status, headers });
    });
    let window;
    let portrait;
    const speech = new SpeechController({ userData: app.getPath('userData'), storage: safeStorage });
    const withSpeech = state => ({ ...state, speech: speech.state() });
    function broadcast(state) {
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
    portrait = new BrowserWindow({ width: 334, height: 388, x: area.x + area.width - 350,
        y: area.y + area.height - 405, frame: false, transparent: true, backgroundColor: '#00000000',
        resizable: false, skipTaskbar: true, alwaysOnTop: true, show: false, hasShadow: false, title: '规智数字形象',
        webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true,
            autoplayPolicy: 'no-user-gesture-required' } });
    const portraitUrl = origin + '/portrait.html';
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
    bind('material', async (kind = 'vector') => {
        const vectors = { name: 'GeoPackage', extensions: ['gpkg'] };
        const documents = { name: '审批材料', extensions: ['pdf', 'docx', 'xlsx'] };
        if (!['vector', 'document', 'any'].includes(kind)) throw new Error('材料格式无效');
        const pick = await dialog.showOpenDialog(window, { properties: ['openFile'],
            filters: kind === 'vector' ? [vectors] : kind === 'document' ? [documents] : [vectors, documents] });
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
    bind('cancel', () => withSpeech(controller.cancel()));
    bind('coordinates', () => controller.coordinateCatalog());
    bind('skills', () => controller.python('skills'));
    bind('capabilities', () => controller.python('capabilities'));
    bind('capability', operation => controller.python('capability-show', operation));
    bind('skillShow', reference => controller.python('skill-show', reference));
    bind('skillPreview', markdown => controller.skillRequest('skill-preview', markdown));
    bind('skillPublish', (markdown, materials) => controller.skillRequest('skill-publish', markdown, materials));
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
    return { window, portrait, tray, controller, speech };
}

module.exports = { startDesktop };
