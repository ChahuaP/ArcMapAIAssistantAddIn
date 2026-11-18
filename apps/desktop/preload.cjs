const { contextBridge, ipcRenderer, webUtils } = require('electron');

const api = {};
for (const name of ['state', 'updateCheck', 'updatePrepare', 'updateCancel', 'updateApply', 'configure', 'workspace', 'material', 'submit', 'cancel', 'reportPage', 'businessLog', 'businessDetail', 'businessDelete',
    'skills', 'capabilities', 'capability', 'skillShow', 'skillPreview', 'skillTrial', 'skillPublish', 'skillOpen', 'skillTemplate',
    'map', 'mapSkill', 'mxdRead', 'mxdImport', 'mxdWrite', 'mapImport', 'mapReferences', 'mapCatalog', 'mapSnapshot', 'addImages', 'pasteImages', 'imageReferences', 'conversationImage', 'mapImage', 'mapTaskInputs', 'mapExport', 'copyAttribute', 'diagnostics', 'diagnosticsUpload',
    'coordinates', 'coordinateFile', 'importCoordinate', 'coordinateRename', 'coordinateRemove', 'coordinateSolve',
    'resetConversation', 'conversationPreview', 'conversationUpload', 'approvalSource', 'workbench', 'finishMapDelivery',
    'mapLayout', 'mapLayoutDraft', 'mapLayoutReady', 'mapLayoutError', 'openMapLayout',
    'appearance', 'shortcutCapture', 'readDraft', 'saveDraft', 'openLauncher', 'fitLauncher', 'hideLauncher', 'portraitVoice', 'voiceTurn',
    'workspaceReady', 'modelList', 'modelTest',
    'portraitHit', 'portraitDrag', 'speechConfigure', 'speechRecognize', 'speechSynthesize', 'speechCancel', 'speechTest', 'voiceCaption',
    'arcgisInstall', 'arcgisConfigure', 'arcgisRetry', 'arcgisCapture', 'componentInstall', 'componentCancel', 'openLink']) {
    api[name] = (...args) => ipcRenderer.invoke('guizhi:' + name, ...args);
}
api.imageDrop = files => ipcRenderer.invoke('guizhi:addImages', files.map(file => webUtils.getPathForFile(file)).filter(Boolean));
api.mapDrop = files => ipcRenderer.invoke('guizhi:mapImport', files.map(file => webUtils.getPathForFile(file)).filter(Boolean));
api.intakeDropped = (files, kind) => ipcRenderer.invoke('guizhi:materialPaths',
    files.map(file => webUtils.getPathForFile(file)).filter(Boolean), kind);
for (const name of ['state', 'settings', 'speechPartial', 'appearance', 'draft', 'launcherFocus',
    'portraitVoice', 'voiceStop', 'voiceCaption', 'mapReferences', 'imageReferences', 'mapImportProgress', 'attributeWindowClosed']) {
    api['on' + name[0].toUpperCase() + name.slice(1)] = callback => {
        const listener = (_, payload) => callback(payload);
        ipcRenderer.on('guizhi:' + name, listener);
        return () => ipcRenderer.removeListener('guizhi:' + name, listener);
    };
}
if (location.protocol === 'guizhi:' && location.hostname === 'app') contextBridge.exposeInMainWorld('guizhi', api);
