const { contextBridge, ipcRenderer, webUtils } = require('electron');

const api = {};
for (const name of ['state', 'configure', 'workspace', 'material', 'submit', 'cancel', 'report',
    'skills', 'capabilities', 'capability', 'skillShow', 'skillPreview', 'skillTrial', 'skillPublish', 'skillOpen', 'skillTemplate', 'delivery',
    'exportDelivery', 'diagnostics',
    'coordinates', 'coordinateFile', 'importCoordinate', 'coordinateRename', 'coordinateRemove', 'coordinateSolve', 'coordinateApply',
    'resetConversation', 'approvalSource', 'workbench',
    'appearance', 'shortcutCapture', 'readDraft', 'saveDraft', 'openLauncher', 'fitLauncher', 'hideLauncher', 'portraitVoice', 'voiceTurn',
    'workspaceReady', 'modelList', 'modelTest',
    'portraitHit', 'portraitDrag', 'speechConfigure', 'speechRecognize', 'speechSynthesize', 'speechCancel', 'speechTest',
    'arcgisInstall', 'arcgisConfigure', 'arcgisRetry', 'arcgisCapture', 'openLink']) {
    api[name] = (...args) => ipcRenderer.invoke('guizhi:' + name, ...args);
}
api.intakeDropped = (files, kind) => ipcRenderer.invoke('guizhi:materialPaths',
    files.map(file => webUtils.getPathForFile(file)).filter(Boolean), kind);
for (const name of ['state', 'settings', 'speechPartial', 'appearance', 'draft', 'launcherFocus',
    'portraitVoice', 'voiceStop']) {
    api['on' + name[0].toUpperCase() + name.slice(1)] = callback => {
        const listener = (_, payload) => callback(payload);
        ipcRenderer.on('guizhi:' + name, listener);
        return () => ipcRenderer.removeListener('guizhi:' + name, listener);
    };
}
contextBridge.exposeInMainWorld('guizhi', api);
