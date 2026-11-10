(() => {
    function errorText(error, device) {
        const text = String(error?.message || error || '').replace(/^Error invoking remote method '[^']+': (?:Error: )?/, '').trim();
        const name = error?.name;
        if (name === 'NotFoundError' || name === 'OverconstrainedError' || /requested device not found|no audio input|no matching device/i.test(text)) {
            if (device === 'speaker') return '找不到所选播放设备。请接好扬声器或耳机，或在语音设置中选择「系统默认」。';
            if (device === 'microphone') return '找不到所选麦克风。请接好麦克风，或在语音设置中选择「系统默认」。';
            return '找不到所选语音设备。请检查设备连接，或在语音设置中选择「系统默认」。';
        }
        if (name === 'NotAllowedError' || name === 'SecurityError' || /permission denied by system/i.test(text)) {
            if (device === 'speaker') return '系统未允许使用所选播放设备。请检查 Windows 声音设置，或选择「系统默认」。';
            return '系统未允许使用语音设备。请在 Windows 隐私设置中允许桌面应用访问麦克风。';
        }
        if (name === 'NotReadableError' || (name === 'AbortError' && device) || /could not start audio source/i.test(text)) {
            return '语音设备暂时无法使用。请关闭占用设备的应用，或重新选择设备。';
        }
        if (name === 'EncodingError') return '无法播放收到的语音。请在语音设置中试听并检查服务配置。';
        if (/ECONNREFUSED|ENOTFOUND|ETIMEDOUT|Failed to fetch|NetworkError/i.test(text)) {
            return '暂时无法连接服务。请检查网络和服务地址后重试。';
        }
        if (/\bEACCES\b|\bEPERM\b|permission denied/i.test(text)) return '无法访问该文件或目录。请选择有访问权限的位置。';
        return /\p{Script=Han}/u.test(text) ? text : '操作未完成，请重试。如仍有问题，请打开「关于与诊断」检查。';
    }

    window.GuizhiErrorText = errorText;
})();
