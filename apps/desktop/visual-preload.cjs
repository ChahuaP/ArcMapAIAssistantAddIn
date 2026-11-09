const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('visual', {
    onStep: callback => ipcRenderer.on('guizhi:visual-step', (_, step) => callback(step)),
    takeover: () => ipcRenderer.invoke('guizhi:takeover'),
});
