const { contextBridge, ipcRenderer, webUtils } = require('electron');

const api = {};
for (const name of ['state', 'configure', 'workspace', 'material', 'submit', 'cancel', 'report',
    'skills', 'capabilities', 'capability', 'skillShow', 'skillPreview', 'skillTrial', 'skillPublish', 'skillOpen', 'skillTemplate', 'delivery',
    'imageryModels', 'imageryModelAdd', 'imageryModelActivate', 'exportDelivery',
    'coordinates', 'importCoordinate', 'mapAction', 'layoutReport', 'approvalSource', 'workbench',
    'appearance', 'readDraft', 'saveDraft', 'openLauncher', 'hideLauncher', 'portraitVoice', 'voiceDraft', 'voiceSubmit',
    'workspaceReady', 'modelList', 'modelTest',
    'portraitHit', 'speechConfigure', 'speechRecognize', 'speechSynthesize', 'speechCancel', 'speechTest',
    'arcmapBridge', 'arcmapFile', 'arcmapExchange']) {
    api[name] = (...args) => ipcRenderer.invoke('guizhi:' + name, ...args);
}
api.intakeDropped = (files, kind) => ipcRenderer.invoke('guizhi:materialPaths',
    files.map(file => webUtils.getPathForFile(file)).filter(Boolean), kind);
for (const name of ['state', 'settings', 'speechPartial', 'appearance', 'draft', 'launcherFocus', 'launcherSubmit',
    'openTask', 'portraitVoice', 'portraitHide']) {
    api['on' + name[0].toUpperCase() + name.slice(1)] = callback => {
        const listener = (_, payload) => callback(payload);
        ipcRenderer.on('guizhi:' + name, listener);
        return () => ipcRenderer.removeListener('guizhi:' + name, listener);
    };
}
contextBridge.exposeInMainWorld('guizhi', api);
