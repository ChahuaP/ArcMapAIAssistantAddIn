const { app, dialog } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { parseArgs } = require('node:util');
const { startDesktop } = require('./main.cjs');
const { productName } = require('./brand.js');

let probe;
let updateReady;
async function main() {
    const { values } = parseArgs({ args: process.argv.slice(2), options: {
        runtime: { type: 'string' }, dsh: { type: 'string' },
        'renderer-runtime': { type: 'string' }, 'user-data': { type: 'string' },
        'bundled-speech': { type: 'string' }, 'bundled-arcmap': { type: 'string' },
        'installation-root': { type: 'string' },
        'verify-startup': { type: 'string' }, 'update-ready': { type: 'string' },
    } });
    probe = values['verify-startup'];
    updateReady = values['update-ready'];
    if ((probe || updateReady) && (!path.isAbsolute(probe || updateReady) || !path.isAbsolute(values['user-data'] || ''))) {
        throw new Error('启动检查需要独立的本地设置目录');
    }
    const desktop = await startDesktop({ runtime: values.runtime, dsh: values.dsh,
        rendererRuntime: values['renderer-runtime'], userData: values['user-data'],
        installationRoot: values['installation-root'],
        bundledSpeech: values['bundled-speech'], bundledArcmap: values['bundled-arcmap'], verifyStartup: Boolean(probe) });
    if ((probe || updateReady) && desktop) {
        const [workbench, launcher, portrait] = await Promise.all([
            desktop.window.webContents.executeJavaScript(`new Promise(resolve => {
                let attempts = 20;
                async function check() {
                    if (window.guizhi && window.GuizhiBusiness && document.getElementById('app')?.childElementCount) {
                        try { resolve(Boolean((await window.guizhi.state()).appearance)); }
                        catch { resolve(false); }
                    }
                    else if (attempts-- > 0) setTimeout(check, 100);
                    else resolve(false);
                }
                check();
            })`),
            desktop.launcher.webContents.executeJavaScript(`new Promise(resolve => {
                let attempts = 20;
                async function check() {
                    if (document.querySelector('.launcher textarea')) {
                        try { resolve(Boolean((await window.guizhi.state()).appearance)); }
                        catch { resolve(false); }
                    }
                    else if (attempts-- > 0) setTimeout(check, 100);
                    else resolve(false);
                }
                check();
            })`),
            desktop.portrait.webContents.executeJavaScript("Boolean(window.guizhi && document.getElementById('open'))"),
        ]);
        if (!workbench || !launcher || !portrait) throw new Error('小六界面启动检查失败');
        if (probe) {
            fs.writeFileSync(probe, JSON.stringify({ workbench, portrait }), { flag: 'wx' });
            app.exit(0);
        } else {
            const version = fs.readFileSync(path.join(__dirname, '../../VERSION'), 'utf8').trim();
            const pending = updateReady + '.pending';
            fs.writeFileSync(pending, JSON.stringify({ version, profile: app.getPath('userData') }), { flag: 'wx' });
            fs.renameSync(pending, updateReady);
        }
    }
}

main().catch(error => {
    if (probe || updateReady) process.stderr.write(error.message + '\n');
    else dialog.showErrorBox(productName, error.message);
    app.exit(1);
});
