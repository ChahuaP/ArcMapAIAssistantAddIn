const { contextBridge, ipcRenderer } = require('electron');

const api = {};
for (const name of ['state', 'configure', 'workspace', 'material', 'submit', 'cancel', 'report',
    'skills', 'capabilities', 'capability', 'skillShow', 'skillPreview', 'skillPublish', 'skillOpen', 'skillTemplate', 'delivery',
    'coordinates', 'importCoordinate', 'mapAction', 'layoutReport', 'approvalSource', 'workbench', 'portraitInput',
    'portraitHit', 'speechConfigure', 'speechRecognize', 'speechSynthesize', 'speechCancel']) {
    api[name] = (...args) => ipcRenderer.invoke('guizhi:' + name, ...args);
}
for (const name of ['state', 'settings', 'speechPartial', 'portraitInput', 'portraitHide']) {
    api['on' + name[0].toUpperCase() + name.slice(1)] = callback => {
        const listener = (_, payload) => callback(payload);
        ipcRenderer.on('guizhi:' + name, listener);
        return () => ipcRenderer.removeListener('guizhi:' + name, listener);
    };
}
contextBridge.exposeInMainWorld('guizhi', api);
