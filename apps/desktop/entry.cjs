const { app, dialog } = require('electron');
const { parseArgs } = require('node:util');
const { startDesktop } = require('./main.cjs');

async function main() {
    const { values } = parseArgs({ args: process.argv.slice(2), options: {
        runtime: { type: 'string' }, dsh: { type: 'string' },
        'renderer-runtime': { type: 'string' }, 'user-data': { type: 'string' },
    } });
    await startDesktop({ runtime: values.runtime, dsh: values.dsh,
        rendererRuntime: values['renderer-runtime'], userData: values['user-data'] });
}

main().catch(error => {
    dialog.showErrorBox('规智', error.message);
    app.exit(1);
});
