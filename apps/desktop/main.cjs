const { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, nativeImage, net, protocol, safeStorage, shell, Tray } = require('electron');
const { randomBytes, randomUUID, createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { inputReferences } = require('./business-log.cjs');
const { DesktopController } = require('./controller.cjs');
const { SpeechController } = require('./speech.cjs');
const { LocalSpeechRuntime } = require('./local-speech.cjs');
const { createShell } = require('./shell.cjs');
const { productName } = require('./brand.js');
const { listModels, checkModelConnection, testModel } = require('./model-service.cjs');
const { formats, materialBindings } = require('./materials.cjs');
const { ArcGisSync } = require('./arcgis-sync.cjs');
const { DiagnosticsUpload } = require('./diagnostics-upload.cjs');
const { Updater } = require('./updater.cjs');
const { Components } = require('./components.cjs');
const { action, parameterValues, parametersReady, isBusinessRequest, matchBusiness, reference } = require('./task-commands.js');

const origin = 'guizhi://app';
protocol.registerSchemesAsPrivileged([{ scheme: 'guizhi', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);

async function startDesktop(options) {
    const { externalPath } = await import('../../src/guizhi/infrastructure/dsh/sdk.mjs');
    app.setName(productName);
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
    const localFiles = ['index.html', 'app.js', 'brand.js', 'shell-ui.js', 'settings.js', 'coordinates.js', 'skills.js',
        'materials-ui.js', 'skill-editor.js',
        'style.css', 'theme.css', 'mark.svg', 'luhe-banner.png', 'model-form.js', 'onboarding.js',
        'portrait.html', 'portrait.css', 'portrait.js', 'task-commands.js', 'speech-settings.js',
        'speech-audio.js', 'voice-input.js', 'arcgis-extension.js', 'markdown.js', 'messages.js',
        'component-actions.js', 'business-log.js', 'business-log.css', 'composer-media.js'];
    const files = new Map(localFiles.map(name => ['/' + name, path.join(__dirname, name)]));
    files.set('/map.js', path.join(rendererRuntime, 'map.js'));
    files.set('/map.css', path.join(rendererRuntime, 'map.css'));
    files.set('/react.js', path.join(vendor, 'react', 'umd', 'react.production.min.js'));
    files.set('/react-dom.js', path.join(vendor, 'react-dom', 'umd', 'react-dom.production.min.js'));
    files.set('/dayjs.js', path.join(vendor, 'dayjs', 'dayjs.min.js'));
    files.set('/antd.js', path.join(vendor, 'antd', 'dist', 'antd.min.js'));
    files.set('/markdown-it.js', path.join(vendor, 'markdown-it', 'dist', 'browser', 'markdown-it.umd.min.js'));
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
    const diagnostics = new DiagnosticsUpload(app.getPath('userData'), version);
    const workbenchUrl = origin + '/index.html';
    const launcherUrl = workbenchUrl + '?surface=launcher';
    const portraitUrl = origin + '/portrait.html';
    let sync;
    let draft = {};
    const imageInputs = new Map();
    let imageSequence = 0;
    let mapLayers = new Map();
    let mapSelection;
    let mapCatalogVersion = 0;
    const mapKey = ref => ref.run + ':' + ref.artifact;
    const knownLayers = () => [...mapLayers.values()]
        .map(({ ref, name, kind, geometry, crs, units }) => ({ id: mapKey(ref), ref, name, kind, geometry, crs, units }));
    let modelCheck;
    let verifiedVision;
    const visionIdentity = (config, key) => JSON.stringify([config, createHash('sha256').update(key).digest('hex')]);
    let voiceReply = null;
    const python = path.join(runtime, 'python', 'python.exe');
    const components = new Components(app.getPath('userData'), python, () => {
        speech.bundledLocalRoot = options.bundledSpeech || components.installed('speech');
        broadcast(controller.state());
    });
    function bridge() {
        const bundled = path.join(__dirname, '../../packaging/arcgis/ArcMapBridge.exe');
        return options.bundledArcmap || (fs.existsSync(bundled) ? bundled : undefined);
    }
    const speech = new SpeechController({ userData: app.getPath('userData'), storage: safeStorage,
        local: new LocalSpeechRuntime(python, externalPath),
        bundledLocalRoot: options.bundledSpeech || components.installed('speech'),
        websocket: () => require(path.join(dsh || path.join(runtime, 'dsh'), 'node_modules', 'ws')) });
    const updater = new Updater({ root: options.installationRoot, userData: app.getPath('userData'), version,
        changed: () => broadcast(controller.state()) });
    const withSpeech = state => {
        const available = components.state();
        return { ...state, speech: { ...speech.state(), component: available.speech }, voiceReply,
            appearance: desktop.appearance(), arcgis: sync?.state(),
            components: available, update: updater.state(), version };
    };
    function setVoiceReply(value) {
        if (!voiceReply && !value) return;
        voiceReply = value;
        desktop.notify('voiceCaption', voiceReply);
    }
    function broadcast(state) {
        desktop.notify('state', withSpeech(state));
    }
    const controller = new DesktopController({ userData: app.getPath('userData'), runtime, dsh,
        safeStorage, externalPath, onChange: broadcast, bundledArcmap: bridge(),
        installationRoot: options.installationRoot, diagnostics });
    if (!options.verifyStartup) {
        controller.reader.start();
        controller.warmModel();
    }
    const hostRoot = process.env.GUIZHI_ARCGIS_HOSTS || path.join(process.env.LOCALAPPDATA, 'Guizhi', 'ArcGIS', 'hosts');
    sync = new ArcGisSync(controller, hostRoot, app.getPath('documents'), () => broadcast(controller.state()));
    controller.onStarted = task => sync.started(task);
    controller.onCompleted = (task, previousTask) => sync.completed(task, previousTask);
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
    tray.setToolTip(productName);
    tray.setContextMenu(Menu.buildFromTemplate([
        { label: '工作台', click: reveal },
        { label: '快捷面板', click: desktop.openLauncher },
        { label: '显示数字形象', click: desktop.showPortrait },
        { label: '隐藏数字形象', click: () => {
            portrait.webContents.send('guizhi:voiceStop');
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
        desktop.close();
        sync.close();
        speech.cancel();
        diagnostics.close();
        void controller.close().finally(() => { quitting = true; tray.destroy(); app.quit(); });
    });
    const pages = new Map([[window, workbenchUrl], [launcher, launcherUrl], [portrait, portraitUrl]]);
    for (const [surface, url] of pages) {
        surface.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
        surface.webContents.on('will-navigate', (event, target) => { if (target !== url) event.preventDefault(); });
        surface.webContents.on('render-process-gone', (_, details) => {
            if (!closing && !['clean-exit', 'killed'].includes(details.reason)) {
                diagnostics.record({ stage: 'desktop', status: 'failed', code: 'renderer_gone' });
            }
        });
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
    bind('updateCheck', () => updater.check());
    bind('updatePrepare', () => updater.prepare());
    bind('updateCancel', () => updater.cancel());
    bind('updateApply', async () => {
        if (controller.active || controller.modelTesting || controller.hostBusy || controller.skillBusy || components.active.size) {
            throw new Error('请在当前操作结束后重启更新');
        }
        await updater.apply();
        app.quit();
    });
    bind('openLink', value => {
        if (typeof value !== 'string' || value.length > 2048) throw new Error('链接无效');
        const url = new URL(value);
        if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error('链接无效');
        return shell.openExternal(url.href);
    });
    bind('appearance', desktop.configureAppearance);
    bind('shortcutCapture', (surface, enabled) => {
        if (surface !== window || typeof enabled !== 'boolean') throw new Error('快捷键录入请求无效');
        desktop.captureShortcut(enabled);
    }, true);
    bind('readDraft', () => draft);
    bind('mapReferences', () => knownLayers());
    const maxImageBytes = 12 * 1024 * 1024;
    function requireImageComposer(surface) {
        if (![window, launcher].includes(surface)) throw new Error('请从对话添加图片');
    }
    const imageReferences = () => Array.from(imageInputs.values(), entry => entry.reference).reverse();
    function clearImages() {
        imageInputs.clear();
        imageSequence = 0;
        desktop.notify('imageReferences', []);
    }
    bind('imageReferences', surface => {
        requireImageComposer(surface);
        return imageReferences();
    }, true);
    bind('conversationImage', (surface, id) => {
        requireImageComposer(surface);
        const entry = imageInputs.get(id);
        return entry ? 'data:' + entry.block.mimeType + ';base64,' + entry.block.data : null;
    }, true);
    function requireImageBatch(items) {
        if (!Array.isArray(items) || items.length > 4) throw new Error('每轮最多添加 4 张图片');
    }
    function registerImages(inputs) {
        const names = new Set(imageReferences().map(item => item.name));
        const blocks = inputs.map(({ image, name }) => {
            if (image.isEmpty()) throw new Error(name ? '无法读取图片 ' + name : '无法读取剪贴板图片');
            const { width, height } = image.getSize();
            if (Math.max(width, height) > 1500) {
                image = image.resize(width > height ? { width: 1500 } : { height: 1500 });
            }
            const id = randomBytes(16).toString('hex');
            const png = image.toPNG();
            const block = png.length <= 1024 * 1024
                ? { mimeType: 'image/png', data: png.toString('base64') }
                : { mimeType: 'image/jpeg', data: image.toJPEG(82).toString('base64') };
            const base = name || '剪贴板图片 ' + (++imageSequence);
            name = base;
            for (let count = 2; names.has(name); count++) name = base + '（' + count + '）';
            names.add(name);
            const thumb = image.resize(width > height ? { width: 144 } : { height: 144 });
            const reference = { id, name, thumbnail: 'data:image/jpeg;base64,' + thumb.toJPEG(83).toString('base64') };
            return { reference, block: { type: 'image', ...block, name } };
        });
        for (const entry of blocks) imageInputs.set(entry.reference.id, entry);
        const attached = new Set(Array.isArray(draft.images) ? draft.images.slice(0, 4).map(item => item?.id) : []);
        while (imageInputs.size > 24) {
            const oldest = [...imageInputs.keys()].find(id => !attached.has(id));
            imageInputs.delete(oldest);
        }
        desktop.notify('imageReferences', imageReferences());
        return blocks.map(({ reference }) => reference);
    }
    bind('addImages', async (surface, paths) => {
        requireImageComposer(surface);
        const files = paths || (await dialog.showOpenDialog(surface, { properties: ['openFile', 'multiSelections'],
            filters: [{ name: '图片', extensions: ['png', 'jpg', 'jpeg', 'webp'] }] })).filePaths;
        requireImageBatch(files);
        return registerImages(files.map(input => {
            const file = externalPath(input);
            if (!/\.(png|jpe?g|webp)$/i.test(file) || fs.statSync(file).size > maxImageBytes) {
                throw new Error('请添加不超过 12 MB 的 PNG、JPEG 或 WebP 图片');
            }
            return { image: nativeImage.createFromPath(file), name: path.basename(file) };
        }));
    }, true);
    bind('pasteImages', (surface, images) => {
        requireImageComposer(surface);
        requireImageBatch(images);
        return registerImages(images.map(bytes => {
            if (!(bytes instanceof ArrayBuffer) || !bytes.byteLength || bytes.byteLength > maxImageBytes) {
                throw new Error('请粘贴不超过 12 MB 的图片');
            }
            return { image: nativeImage.createFromBuffer(Buffer.from(bytes)) };
        }));
    }, true);
    bind('mapImage', async (surface, ref) => {
        if (![window, launcher].includes(surface) || !ref || !/^[a-f0-9]{32}$/.test(ref.run) || ref.artifact !== 'raster_preview') {
            throw new Error('影像引用无效');
        }
        const data = await controller.reader.map(controller.settings.workspace, { action: 'raster-preview', ref });
        return 'data:image/png;base64,' + data;
    }, true);
    bind('mapCatalog', async (surface, items, selection) => {
        if (surface !== window || !Array.isArray(items) || items.length > 256 ||
            items.some(item => !item || typeof item.name !== 'string' || !item.name.trim() || item.name.length > 255 ||
                !/^[a-f0-9]{32}$/.test(item.ref?.run) ||
                !['import', 'export', 'boundaries', 'transfer-source', 'raster_preview'].includes(item.ref.artifact))) {
            throw new Error('地图图层引用无效');
        }
        mapCatalogVersion += 1;
        mapLayers = new Map(items.map(item => [mapKey(item.ref), item]));
        mapSelection = selection && mapLayers.has(selection.key) && Array.isArray(selection.ids) && selection.ids.length <= 500
            && selection.ids.every(Number.isSafeInteger) ? selection : undefined;
        desktop.notify('mapReferences', knownLayers());
    }, true);
    bind('saveDraft', (source, next) => {
        if (!next || typeof next !== 'object' || Array.isArray(next)) throw new Error('草稿无效');
        draft = next;
        for (const surface of [window, launcher]) {
            if (surface !== source) surface.webContents.send('guizhi:draft', draft);
        }
    }, true);
    bind('openLauncher', desktop.openLauncher);
    bind('fitLauncher', (surface, height) => {
        if (surface !== launcher || !Number.isSafeInteger(height) || height < 0) throw new Error('快捷面板尺寸无效');
        desktop.fitLauncher(height);
    }, true);
    bind('hideLauncher', () => launcher.hide());
    bind('portraitVoice', desktop.voice);
    bind('voiceCaption', (surface, task, text) => {
        if (surface !== portrait || (task !== null && typeof task !== 'string') ||
                (text !== null && (typeof text !== 'string' || text.length > 65536))) throw new Error('语音字幕无效');
        if (task === null && text === null) setVoiceReply(null);
        else if (voiceReply?.task === task) setVoiceReply(text === null ? null : { task, text });
    }, true);
    portrait.webContents.on('render-process-gone', () => setVoiceReply(null));
    bind('voiceTurn', async (surface, text) => {
        if (surface !== portrait || typeof text !== 'string' || !text.trim() || text.length > 4000) {
            throw new Error('语音对话内容无效');
        }
        const command = action(text);
        if (command === 'workbench') { reveal(); return { reply: '好，工作台已经打开。' }; }
        if (command === 'reset') { resetConversation({}, true); return { reply: '好，我们开始新的对话。' }; }
        const prompt = command === 'send' ? draft.prompt : text;
        if (!prompt?.trim()) return { reply: '你可以直接告诉我想办理什么。' };
        if (command !== 'send' && isBusinessRequest(prompt) && !namedLayers(prompt).length) {
            const current = draft;
            const workspace = controller.settings.workspace;
            const { exact, items } = matchBusiness(prompt, await controller.python('skills'));
            if (current !== draft || workspace !== controller.settings.workspace) throw new Error('对话已更新，请重新说一遍');
            if (exact && reference(items[0]) !== draft.skill) {
                const item = items[0];
                resetConversation({ prompt, skill: reference(item), skillDefinition: item }, true);
                reveal();
                return { reply: '好，已开始办理' + item.title + '。请在工作台补充材料。' };
            }
        }
        if (draft.unassigned?.length) return { reply: '请先在工作台为材料选择用途。' };
        const fields = draft.skillDefinition?.parameters || {};
        const values = draft.parameterText || {};
        const skillParameters = draft.skill && Object.keys(fields).length && parametersReady(fields, values)
            ? parameterValues(fields, values) : undefined;
        const state = await submitTask({ ...draft, prompt,
            skillParameters, dialogue: true });
        return { task: state.tasks[0].id };
    }, true);
    bind('configure', input => {
        if (input.modelConfig) {
            const { config, key } = controller.selectModel(input);
            if (!controller.settings.modelVision || input.key?.trim() ||
                    JSON.stringify(config) !== JSON.stringify(controller.settings.modelConfig)) {
                if (verifiedVision !== visionIdentity(config, key)) throw new Error('请先通过所选模型的读图测试');
            }
        } else if (controller.settings.modelConfig) {
            throw new Error('请保留当前模型配置');
        }
        const workspace = controller.settings.workspace;
        const state = controller.configure({ ...input, modelVision: Boolean(input.modelConfig) });
        if (state.settings.workspace !== workspace) {
            clearImages();
            mapLayers.clear();
            mapCatalogVersion += 1;
            desktop.notify('mapReferences', []);
            mapSelection = undefined;
            setVoiceReply(null);
            replaceDraft();
            portrait.webContents.send('guizhi:voiceStop');
        }
        modelCheck = undefined;
        sync.refresh();
        broadcast(state);
        return withSpeech(state);
    });
    bind('workspaceReady', input => controller.workspaceReady(input));
    bind('modelList', input => {
        const { config, key } = controller.selectModel(input, false);
        return listModels(controller.runtime, config, key);
    });
    bind('modelTest', async input => {
        const { config, key } = controller.selectModel(input);
        const result = await checkModelConnection(controller.runtime, config, key);
        verifiedVision = visionIdentity(config, key);
        return result;
    });
    bind('diagnostics', async (surface, check) => {
        if (surface !== window || typeof check !== 'boolean') throw new Error('诊断请求无效');
        const settings = controller.state().settings;
        const config = settings.modelConfig;
        if (check && config) {
            if (controller.active || controller.modelTesting) throw new Error('请在当前任务结束后检查模型');
            const at = new Date().toISOString();
            try {
                const { key } = controller.selectModel({ modelConfig: config });
                const stop = new AbortController();
                controller.modelTesting = { stop, done: testModel(controller, config, key, stop.signal) };
                await controller.modelTesting.done;
                if (controller.settings.modelConfig === config) modelCheck = { state: 'verified', at };
            } catch (error) {
                if (controller.settings.modelConfig === config) modelCheck = { state: 'failed', at, detail: error.message };
            } finally { controller.modelTesting = undefined; }
        }
        const appearance = desktop.appearance();
        const folder = settings.workspace;
        const workspace = { path: folder, state: folder ? 'configured' : 'missing' };
        if (check && folder) {
            try {
                if (!fs.statSync(folder).isDirectory()) throw new Error('成果位置不是目录');
                controller.workspaceReady(folder);
                workspace.state = 'verified';
            } catch (error) {
                workspace.state = 'failed';
                workspace.detail = error.code === 'ENOENT' ? '目录不存在' : '请检查目录和写入权限';
            }
        }
        return { model: { mode: config?.mode, name: config?.model, state: config ? 'configured' : 'missing', ...modelCheck },
            upload: diagnostics.state(),
            voice: { asr: speech.diagnostic('asr'), tts: speech.diagnostic('tts') },
            bridge: Boolean(sync.bridge && fs.existsSync(sync.bridge)),
            workspace,
            shortcut: { registered: appearance.shortcutRegistered, value: appearance.shortcut } };
    }, true);
    bind('diagnosticsUpload', (surface, enabled) => {
        if (surface !== window) throw new Error('请在工作台修改故障上报设置');
        return diagnostics.enable(enabled);
    }, true);
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
    bind('portraitHit', (surface, active) => {
        if (surface !== portrait || typeof active !== 'boolean') throw new Error('数字形象交互请求无效');
        desktop.portraitHit(active);
    }, true);
    bind('portraitDrag', (surface, phase, point) => {
        if (surface !== portrait || !['start', 'move', 'end'].includes(phase) ||
            (phase !== 'end' && ![point?.x, point?.y].every(value => Number.isFinite(value) && Math.abs(value) < 100000))) {
            throw new Error('拖动请求无效');
        }
        desktop.dragPortrait(phase, point);
    }, true);
    bind('workbench', section => {
        reveal();
        if (['components', 'model'].includes(section)) window.webContents.send('guizhi:settings', section);
    });
    bind('workspace', async () => {
        const pick = await dialog.showOpenDialog(window, { properties: ['openDirectory', 'createDirectory'] });
        return pick.canceled ? null : pick.filePaths[0];
    });
    bind('componentInstall', async (surface, kind, method) => {
        if (surface !== window || !Object.hasOwn(components.entries, kind) || !['download', 'offline'].includes(method)) {
            throw new Error('组件安装请求无效');
        }
        let archive;
        if (method === 'offline') {
            const pick = await dialog.showOpenDialog(window, { properties: ['openFile'],
                filters: [{ name: '小六组件包', extensions: ['zip'] }] });
            if (pick.canceled) return;
            archive = pick.filePaths[0];
        }
        await components.install(kind, archive);
    }, true);
    bind('componentCancel', (surface, kind) => {
        if (surface !== window || !Object.hasOwn(components.entries, kind)) throw new Error('组件安装请求无效');
        components.cancel(kind);
    }, true);
    bind('arcgisInstall', async () => {
        const source = bridge();
        const file = source && path.join(path.dirname(source), 'Guizhi.esriaddin');
        if (!file || !fs.existsSync(file)) throw new Error('请先安装小六的 ArcGIS 组件');
        if (await shell.openPath(file)) throw new Error('无法打开 ArcGIS 扩展安装程序');
    });
    bind('arcgisConfigure', carrier => sync.configure(carrier));
    bind('arcgisRetry', id => sync.retry(id));
    bind('arcgisCapture', (index, selected) => sync.capture(index, selected));
    bind('material', async (kind = 'any', folder = false) => {
        if (kind !== 'any' && !Object.hasOwn(formats, kind)) throw new Error('材料格式无效');
        const groups = kind === 'any' ? Object.values(formats) : [formats[kind]];
        const pick = await dialog.showOpenDialog(BrowserWindow.getFocusedWindow() || window, folder ? { properties: ['openDirectory'] }
            : { properties: ['openFile', 'multiSelections'], filters: [
                { name: '可用材料', extensions: [...groups.flatMap(group => group.extensions), 'zip'] }, ...groups] });
        return pick.canceled ? { items: [], issues: [] } : controller.intake(pick.filePaths, kind);
    });
    bind('materialPaths', (paths, kind) => controller.intake(paths, kind));
    const mapReads = new Set(['describe', 'features', 'table', 'table-complement', 'table-statistics', 'table-unique-values', 'table-feature',
        'crs-search', 'raster-preview', 'raster-display', 'categories']);
    bind('map', async (surface, input) => {
        if (surface !== window || !input || !mapReads.has(input.action) &&
                !['assign', 'reproject', 'transform', 'edit', 'edit-sketch', 'edit-transfer', 'tool', 'step-layers'].includes(input.action)) {
            throw new Error('地图请求无效');
        }
        if (!controller.settings.workspace) throw new Error('请先选择成果保存位置');
        return mapReads.has(input.action)
            ? controller.reader.map(controller.settings.workspace, input) : controller.request('map', input);
    }, true);
    bind('mapSkill', async (surface, input) => {
        if (surface !== window || !input || typeof input.skill !== 'string' ||
                !input.materials || typeof input.materials !== 'object') throw new Error('地图业务请求无效');
        const materials = Object.fromEntries(Object.entries(input.materials).map(([role, group]) => [role,
            group.map(item => {
                if (item.ref) {
                    const layer = mapLayers.get(mapKey(item.ref));
                    if (!layer) throw new Error('业务所选图层已移除');
                    return { ref: layer.ref };
                }
                return materialBindings({ input: [{ ...item, file: externalPath(item.file) }] }).input[0];
            })]));
        const id = randomUUID(), startedAt = new Date().toISOString();
        const display = Object.fromEntries(Object.entries(input.materials).map(([role, group]) => [role,
            group.map(item => item.ref ? { ...item, name: mapLayers.get(mapKey(item.ref)).name } : item)]));
        const output = await controller.request('map', { action: 'skill', skill: input.skill, materials,
            parameters: input.parameters, coordinatePlans: input.coordinatePlans });
        const business = await controller.mapBusiness({ id, startedAt, materials: display, output });
        broadcast(controller.state());
        return { ...output, business };
    }, true);
    let currentMxd;
    function requireMxdBridge() {
        const executable = bridge();
        if (!executable) throw new Error('本版缺少 MXD 桥接程序，请使用完整的小六发行包');
        return executable;
    }
    bind('mxdRead', async surface => {
        if (surface !== window) throw new Error('请在地图工作区打开 MXD');
        const executable = requireMxdBridge();
        const pick = await dialog.showOpenDialog(window, { properties: ['openFile'], filters: [{ name: 'ArcGIS 地图工程', extensions: ['mxd'] }] });
        if (pick.canceled) return null;
        const source = externalPath(pick.filePaths[0]);
        const manifest = await controller.request('map', { action: 'mxd-read', bridge: executable, source });
        currentMxd = { source, manifest, workspace: controller.settings.workspace };
        return { name: path.basename(source, '.mxd'), frames: manifest.frames.map(frame => ({
            name: frame.name, count: frame.layers.length,
            missing: frame.layers.filter(item => !item.valid && (item.source || item.workspace && item.dataset)).length,
            unsupported: frame.layers.filter(item => item.kind === 'unsupported' ||
                !(item.source || item.workspace && item.dataset)).length,
        })) };
    }, true);
    bind('mxdImport', async (surface, index, relocate) => {
        if (surface !== window || !currentMxd || currentMxd.workspace !== controller.settings.workspace ||
                !Number.isInteger(index) || !currentMxd.manifest.frames[index]) throw new Error('请先打开 MXD 并选择数据框');
        const { source, manifest } = currentMxd;
        let search;
        if (relocate) {
            const pick = await dialog.showOpenDialog(window, { properties: ['openDirectory'] });
            if (pick.canceled) return null;
            search = externalPath(pick.filePaths[0]);
        }
        return controller.request('map', { action: 'mxd-import', source, frame: manifest.frames[index], search });
    }, true);
    bind('mxdWrite', async (surface, input) => {
        if (surface !== window || !Array.isArray(input?.layers) || input.layers.length > 256 ||
                input.layers.some(item => !mapLayers.has(mapKey(item.ref)))) throw new Error('请从地图工作区保存有效图层');
        const executable = requireMxdBridge();
        const pick = await dialog.showSaveDialog(window, { defaultPath: '小六地图.mxd',
            filters: [{ name: 'ArcGIS 地图工程', extensions: ['mxd'] }] });
        if (pick.canceled) return null;
        const output = externalPath(pick.filePath);
        const relative = path.relative(path.join(controller.settings.workspace, 'runs'), output);
        if (!path.isAbsolute(relative) && relative.split(path.sep)[0] !== '..') throw new Error('请选择独立的地图工程保存位置');
        const result = await controller.request('map', { action: 'mxd-write', bridge: executable, output,
            name: path.basename(output, '.mxd'), layers: input.layers, crs: input.crs, bounds: input.bounds, background: input.background });
        shell.showItemInFolder(output);
        return result;
    }, true);
    bind('mapImport', async (surface, paths) => {
        if (surface !== window) throw new Error('请从工作台添加图层');
        const files = paths || (await dialog.showOpenDialog(window, { properties: ['openFile', 'multiSelections'],
            filters: [{ name: '空间数据', extensions: [...formats.vector.extensions, ...formats.raster.extensions, 'zip'] }] })).filePaths;
        if (!files.length) return { layers: [], issues: [] };
        window.webContents.send('guizhi:mapImportProgress', { label: '正在读取文件信息' });
        const intake = await controller.intake(files, 'any');
        const items = intake.items.filter(item => item.kind === 'vector')
            .flatMap(item => item.layers.map(layer => ({ file: item.file, layer, revision: null })));
        const images = intake.items.filter(item => item.kind === 'raster');
        const jobs = [...items.map(item => ['import', item]), ...images.map(item => ['raster-import', item])];
        const layers = [];
        for (const [completed, [action, item]] of jobs.entries()) {
            const name = item.name || path.basename(item.file);
            window.webContents.send('guizhi:mapImportProgress', { label: '正在加载 ' + name, total: jobs.length > 1 ? jobs.length : 0, completed });
            try { layers.push(...await controller.request('map', { action, items: [item] })); }
            catch (error) { intake.issues.push({ name, message: error.message }); }
        }
        if (jobs.length) window.webContents.send('guizhi:mapImportProgress', { label: '正在绘制图层',
            total: jobs.length, completed: jobs.length });
        for (const item of intake.items) if (!['vector', 'raster'].includes(item.kind)) {
            intake.issues.push({ name: item.name, message: '地图支持矢量和影像图层' });
        }
        return { layers, issues: intake.issues };
    }, true);
    bind('mapTaskInputs', async (surface, id) => {
        if (surface !== window) throw new Error('请从工作台查看材料');
        const task = controller.tasks.find(item => item.id === id && item.status === 'succeeded');
        if (!task?.runId) throw new Error('办理成果尚未发布');
        const groups = { ...task.materials, ...(task.project && { project: [task.project] }),
            ...(task.control && { control: [task.control] }) };
        return controller.request('map', { action: 'task-inputs', run: task.runId,
            existing: inputReferences(groups, task.skill) });
    }, true);
    bind('copyAttribute', (surface, value) => {
        if (surface !== window || typeof value !== 'string' || value.length > 1_000_000) throw new Error('属性值无法复制');
        clipboard.writeText(value);
    }, true);
    bind('mapExport', async (surface, ref, table, style, pointIcon) => {
        if (surface !== window) throw new Error('请从工作台导出图层');
        const item = mapLayers.get(mapKey(ref));
        if (!item) throw new Error('请选择地图中的图层');
        const raster = item.kind === 'raster';
        const filters = table && !raster ? [{ name: '属性表', extensions: ['csv'] }]
            : raster ? [{ name: 'GeoTIFF', extensions: ['tif'] }, { name: 'JPEG 影像', extensions: ['jpg'] }]
                : [{ name: 'SHP 成果包', extensions: ['zip'] }, { name: 'GeoPackage', extensions: ['gpkg'] },
                    { name: 'GeoJSON', extensions: ['geojson'] }, { name: 'KML', extensions: ['kml'] }];
        const name = item.name.replace(/[<>:"/\\|?*\x00-\x1f]/g, '-').replace(/[ .]+$/, '') || '图层';
        const pick = await dialog.showSaveDialog(window, { defaultPath: name + '.' + filters[0].extensions[0], filters });
        if (pick.canceled) return;
        const output = externalPath(pick.filePath);
        const extension = path.extname(output).slice(1).toLowerCase();
        const relative = path.relative(path.join(controller.settings.workspace, 'runs'), output);
        if (!filters.some(filter => filter.extensions.includes(extension)) ||
                !path.isAbsolute(relative) && relative.split(path.sep)[0] !== '..') throw new Error('请选择有效的导出位置与格式');
        const result = await controller.request('map', { action: 'export', ref, output, format: extension,
            ...(extension === 'kml' && { name: item.name, style, pointIcon }) });
        shell.showItemInFolder(output);
        return result;
    }, true);
    bind('mapLayout', async (surface, { format, data, name, taskId }) => {
        if (surface !== window || !['png', 'jpg'].includes(format) || typeof data !== 'string' || data.length > 24 << 20) {
            throw new Error('地图图片无效');
        }
        const prefix = 'data:image/' + (format === 'jpg' ? 'jpeg' : 'png') + ';base64,';
        if (!data.startsWith(prefix)) throw new Error('地图图片格式无效');
        const bytes = Buffer.from(data.slice(prefix.length), 'base64');
        if (nativeImage.createFromBuffer(bytes).isEmpty()) throw new Error('地图图片无法读取');
        if (taskId) {
            const task = controller.tasks.find(item => item.id === taskId && item.status === 'succeeded' && item.layout?.format === format);
            if (!task || !/^[a-f0-9]{32}$/.test(task.runId)) throw new Error('制图任务已经失效');
            const file = path.join(controller.settings.workspace, 'runs', task.runId, 'map-layout.' + format);
            fs.writeFileSync(file, bytes, { flag: 'wx' });
            task.layoutImage = createHash('sha256').update(bytes).digest('hex');
            controller.persist();
            broadcast(controller.state());
            return true;
        }
        const title = String(name || '').slice(0, 80).replace(/[<>:"/\\|?*\x00-\x1f]/g, '-').replace(/[ .]+$/, '') || '小六地图';
        const pick = await dialog.showSaveDialog(window, { defaultPath: title + '.' + format,
            filters: [{ name: '地图图片', extensions: [format] }] });
        if (pick.canceled) return false;
        const output = externalPath(pick.filePath);
        const relative = path.relative(path.join(controller.settings.workspace, 'runs'), output);
        if (path.extname(output).toLowerCase() !== '.' + format ||
                !path.isAbsolute(relative) && relative.split(path.sep)[0] !== '..') throw new Error('请选择独立的图片保存位置');
        fs.writeFileSync(output, bytes, { flag: 'wx' });
        shell.showItemInFolder(output);
        return true;
    }, true);
    bind('mapLayoutError', (surface, id, message) => {
        const task = controller.tasks.find(item => item.id === id && item.layout);
        if (surface !== window || !task || typeof message !== 'string') return;
        task.layoutError = message.slice(0, 260);
        controller.persist();
        broadcast(controller.state());
    }, true);
    bind('openMapLayout', async id => {
        const task = controller.tasks.find(item => item.id === id && item.layoutImage);
        if (!task || !/^[a-f0-9]{32}$/.test(task.runId) || !['png', 'jpg'].includes(task.layout?.format)) throw new Error('地图图片尚未生成');
        const file = path.join(controller.settings.workspace, 'runs', task.runId, 'map-layout.' + task.layout.format);
        if (createHash('sha256').update(fs.readFileSync(file)).digest('hex') !== task.layoutImage) throw new Error('地图图片已变化');
        const error = await shell.openPath(file);
        if (error) throw new Error('无法打开地图图片');
    });
    function replaceDraft(next = {}) {
        draft = next;
        desktop.notify('draft', draft);
    }
    function resetConversation(next = {}, fromVoice = false) {
        const state = controller.resetConversation();
        clearImages();
        setVoiceReply(null);
        replaceDraft(next);
        if (!fromVoice) portrait.webContents.send('guizhi:voiceStop');
        broadcast(state);
        return withSpeech(state);
    }
    bind('resetConversation', next => {
        if (!next || typeof next !== 'object' || Array.isArray(next)) throw new Error('草稿无效');
        return resetConversation(next);
    });
    function namedLayers(prompt) {
        return [...mapLayers.values()].filter(item => prompt.includes(item.name))
            .filter((item, _, all) => !all.some(other => other.name.length > item.name.length && other.name.includes(item.name)))
            .sort((a, b) => prompt.indexOf(a.name) - prompt.indexOf(b.name));
    }
    async function submitTask(task) {
        if (updater.handoff) throw new Error('小六正在重启更新');
        if (controller.active || controller.modelTesting) throw new Error('已有任务在运行');
        if (!controller.settings.modelVision) {
            reveal();
            window.webContents.send('guizhi:settings', 'model');
            throw new Error('请先在设置中通过模型读图测试');
        }
        if (task.skill && task.materials) {
            const workspace = controller.settings.workspace;
            const version = mapCatalogVersion;
            const materials = await Promise.all(Object.entries(task.materials).map(async ([role, group]) =>
                [role, await Promise.all(group.map(async item => {
                    if (!item.ref) return item;
                    const layer = mapLayers.get(mapKey(item.ref));
                    if (!layer) throw new Error('业务所选图层已移除');
                    const material = await controller.reader.map(workspace, { action: 'material', ref: layer.ref, name: layer.name });
                    return { ...material, ref: layer.ref, plan: item.plan };
                }))]));
            if (workspace !== controller.settings.workspace || version !== mapCatalogVersion) throw new Error('地图图层已变更，请重新提交');
            task = { ...task, materials: Object.fromEntries(materials) };
        }
        if (!task.skill) {
            const explicit = [...task.prompt.matchAll(/[@＠]「([^」]+)」/gu)].map(match => match[1]);
            const mentions = new Map(Object.entries(task.mentions || {}));
            const picks = explicit.length ? [...new Set(explicit)].flatMap(name => {
                const mention = mentions.get(name);
                if (mention) {
                    if (mention.type !== 'layer') return [];
                    const layer = mapLayers.get(mention.key);
                    if (!layer) throw new Error('所引用的图层已移除，请重新选择');
                    return [layer];
                }
                const matches = [...mapLayers.values()].filter(item => item.name === name);
                if (matches.length !== 1) throw new Error('请确认地图图层「' + name + '」存在且名称唯一');
                return matches;
            }) : namedLayers(task.prompt);
            if (new Set(picks.map(item => item.name)).size !== picks.length) {
                throw new Error('地图中有同名图层，请先移除不使用的图层');
            }
            const selected = mapSelection && mapLayers.get(mapSelection.key);
            const asksSelection = /已选|选中|所选|选区|这[几两三四五一]块/u.test(task.prompt);
            const useSelected = selected && mapSelection.ids.length && asksSelection && (!picks.length || picks.includes(selected));
            if (asksSelection && !useSelected && (picks.length || /地图|图层/u.test(task.prompt))) {
                throw new Error('请先在地图中选中对应图层的要素');
            }
            if (useSelected && !picks.includes(selected)) picks.unshift(selected);
            if (picks.length) {
                const workspace = controller.settings.workspace;
                const selectedIds = mapSelection?.ids;
                const version = mapCatalogVersion;
                const inputs = await Promise.all(picks.map(item => {
                    const bySelection = useSelected && selected === item;
                    return controller.request('map', { action: bySelection ? 'selection' : 'material',
                        ref: item.ref, name: item.name, ...(bySelection ? { revision: item.revision, fids: selectedIds } : {}) });
                }));
                if (controller.settings.workspace !== workspace || mapCatalogVersion !== version) {
                    throw new Error('地图图层或选区已变更，请重新提交');
                }
                task = { ...task, materials: { ...task.materials, inputs: [...new Map(
                    [...(task.materials?.inputs || []), ...inputs].map(item => [item.file + '#' + item.layer, item])).values()] } };
            }
        }
        const imageIds = task.images || [];
        if (!Array.isArray(imageIds) || imageIds.length > 4) throw new Error('每轮最多使用 4 张图片');
        const imageBlocks = imageIds.map(item => {
            if (!imageInputs.has(item?.id)) throw new Error('图片已失效，请重新添加');
            return imageInputs.get(item.id).block;
        });
        const images = imageIds.map(item => imageInputs.get(item.id).reference);
        const state = controller.submit({ ...task, images, imageBlocks });
        if (task.dialogue && speech.state().tts !== 'off') setVoiceReply({ task: state.tasks[0].id, text: '' });
        replaceDraft();
        broadcast(state);
        return withSpeech(state);
    }
    bind('submit', task => action(task.prompt) === 'reset' ? resetConversation() : submitTask({ ...task, dialogue: false }));
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
    bind('skillTrial', (markdown, materials, parameters) => controller.skillRequest('skill-trial', markdown,
        { materials: materialBindings(materials), skill_parameters: parameters, coordinate_plans: Object.fromEntries(
            ['project', 'control'].filter(key => materials[key]?.[0]?.plan).map(key => [key + '_plan', materials[key][0].plan])) }));
    bind('skillPublish', (markdown, run) => controller.skillRequest('skill-publish', markdown, { trial_run: run }));
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
    bind('approvalSource', async (...args) => {
        if (await shell.openPath(await controller.approvalSource(...args))) throw new Error('无法打开依据');
    });
    bind('coordinateFile', async kind => {
        if (!['definition', 'points'].includes(kind)) throw new Error('请选择坐标材料类型');
        const pick = await dialog.showOpenDialog(window, { properties: ['openFile'], filters: [
            { name: '坐标材料', extensions: ['shp', 'gpkg', 'geojson', ...(kind === 'definition' ? ['tif', 'tiff', 'img'] : [])] },
        ] });
        if (pick.canceled) return null;
        const file = externalPath(pick.filePaths[0]);
        return kind === 'points' || ['.gpkg', '.geojson'].includes(path.extname(file).toLowerCase())
            ? controller.describe(file) : { file, name: path.basename(file), layers: [], layer: null };
    });
    bind('importCoordinate', async (file, layer) => {
        await controller.importCoordinate(file, layer);
        return controller.coordinateCatalog();
    });
    bind('coordinateRename', input => controller.request('crs-rename', input));
    bind('coordinateRemove', reference => {
        if (typeof reference !== 'string' || !/^[a-f0-9]{64}@[1-9][0-9]{0,7}$/.test(reference)) {
            throw new Error('请选择有效的坐标系');
        }
        return controller.python('crs-remove', reference);
    });
    bind('coordinateApply', async (input, plan) => shell.showItemInFolder(await controller.transformMaterial(input, plan)));
    bind('coordinateSolve', input => controller.request('crs-solve', { ...input,
        ...Object.fromEntries(['source_points', 'target_points'].map(key => [key, {
            file: externalPath(input[key].file), layer: input[key].layer,
        }])) }));
    bind('reportPage', (id, page) => controller.reportPage(id, page));
    bind('businessLog', page => controller.businessLog().page(page));
    bind('businessDetail', id => {
        const item = controller.businessLog().get(id);
        if (!item) throw new Error('办理记录不存在');
        return item;
    });
    bind('businessDelete', (id, phrase) => {
        controller.businessLog().remove(id, phrase);
        broadcast(controller.state());
    });
    await Promise.all([...pages].map(([surface, url]) => surface.loadURL(url)));
    desktop.registerShortcut();
    if (!options.verifyStartup) {
        if (!controller.settings.workspace || !controller.settings.modelConfig) reveal();
        desktop.showPortrait();
    }
    return { window, launcher, portrait, tray, controller, speech, arcgis: sync, components,
        updateActivated: () => updater.set('complete') };
}

module.exports = { startDesktop };
