const { app, BrowserWindow, dialog, ipcMain, Menu, nativeImage, net, protocol, safeStorage, shell, Tray } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { DesktopController } = require('./controller.cjs');

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
    const localFiles = ['index.html', 'app.js', 'map.js', 'coordinates.js', 'skills.js', 'style.css', 'mark.svg'];
    const files = new Map(localFiles.map(name => ['/' + name, path.join(__dirname, name)]));
    files.set('/react.js', path.join(vendor, 'react', 'umd', 'react.production.min.js'));
    files.set('/react-dom.js', path.join(vendor, 'react-dom', 'umd', 'react-dom.production.min.js'));
    for (const file of files.values()) {
        if (!fs.existsSync(file)) throw new Error('桌面运行依赖不完整');
    }
    protocol.handle('guizhi', request => {
        const url = new URL(request.url);
        const file = url.hostname === 'app' && files.get(url.pathname);
        return file ? net.fetch(pathToFileURL(file).href) : new Response('', { status: 404 });
    });
    let window;
    const controller = new DesktopController({ userData: app.getPath('userData'), runtime, dsh,
        safeStorage, externalPath, onChange: state => {
            if (window && !window.isDestroyed()) window.webContents.send('guizhi:state', state);
        } });
    window = new BrowserWindow({ width: 1270, height: 820, minWidth: 790, minHeight: 590,
        show: false, title: '规智', autoHideMenuBar: true, backgroundColor: '#f3f7f7',
        icon: path.join(__dirname, 'mark.png'),
        webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true, sandbox: true } });
    const reveal = () => { window.show(); window.focus(); };
    const tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'mark.png')).resize({ width: 24, height: 24 }));
    tray.setToolTip('规智');
    tray.setContextMenu(Menu.buildFromTemplate([
        { label: '打开规智', click: reveal },
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
        void controller.close().finally(() => { quitting = true; tray.destroy(); app.quit(); });
    });
    window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    window.webContents.on('will-navigate', (event, url) => {
        if (url !== origin + '/index.html') event.preventDefault();
    });

    function bind(method, handler) {
        ipcMain.handle('guizhi:' + method, (event, ...args) => {
            if (event.sender !== window.webContents || event.senderFrame?.url !== origin + '/index.html') {
                throw new Error('请求来源无效');
            }
            return handler(...args);
        });
    }
    bind('state', () => controller.state());
    bind('configure', input => controller.configure(input));
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
    bind('mapAction', (...args) => controller.mapAction(...args));
    bind('layoutReport', async () => {
        if (await shell.openPath(await controller.layoutReport())) throw new Error('无法打开版面');
    });
    bind('submit', task => controller.submit(task));
    bind('cancel', () => controller.cancel());
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
    await window.loadURL(origin + '/index.html');
    reveal();
    return { window, tray, controller };
}

module.exports = { startDesktop };
