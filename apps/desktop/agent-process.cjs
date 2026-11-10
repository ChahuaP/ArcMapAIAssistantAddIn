const { fork } = require('node:child_process');
const path = require('node:path');

function startAgent(runtime, workspace) {
    return fork(path.join(__dirname, 'agent.mjs'), { execPath: runtime.node, cwd: workspace,
        windowsHide: true, stdio: ['ignore', 'ignore', 'ignore', 'ipc'] });
}

module.exports = { startAgent };
