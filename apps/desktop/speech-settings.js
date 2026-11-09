(() => {
    const h = React.createElement;
    const { useState, useEffect } = React;
    const api = window.guizhi;

    window.GuizhiSpeechSettings = function SpeechSettings({ current }) {
        const [value, setValue] = useState({ ...current, key: '' });
        const [devices, setDevices] = useState([]);
        const [message, setMessage] = useState('');
        const format = entries => entries.map(entry => entry.heard + '=' + entry.correct).join('\n');
        const [words, setWords] = useState(() => format(current.dictionary));
        const [pronunciation, setPronunciation] = useState(() => format(current.pronunciation));
        const update = (field, next) => setValue(old => ({ ...old, [field]: next }));
        const reload = () => navigator.mediaDevices.enumerateDevices().then(setDevices).catch(() => setDevices([]));
        useEffect(() => { void reload(); }, []);
        const input = (field, attrs = {}) => h('input', { ...attrs, value: value[field] ?? '',
            onChange: event => update(field, event.target.value) });
        const pick = (field, label, options) => h('label', null, label,
            h('select', { value: value[field] ?? '', onChange: event => update(field, event.target.value) },
                ...options.map(([id, title]) => h('option', { key: id, value: id }, title))));
        const source = [['off', '关闭'], ...current.providers.map(provider => [provider,
            { minimax: 'MiniMax 在线', local: '本地语音' }[provider]])];
        const byKind = kind => [['', '系统默认'], ...devices.filter(device => device.kind === kind && device.deviceId && device.deviceId !== 'default')
            .map((device, index) => [device.deviceId, device.label || '设备 ' + (index + 1)])];
        const fail = error => setMessage(String(error.message || error)
            .replace(/^Error invoking remote method '[^']+': Error: /, ''));
        const parse = text => text.split('\n').filter(line => line.trim()).map(line => {
            const split = line.indexOf('=');
            if (split < 1) throw new Error('词表每行使用“原词=替换词”');
            return { heard: line.slice(0, split), correct: line.slice(split + 1) };
        });
        async function save() {
            try {
                const settings = await api.speechConfigure({ ...value, dictionary: parse(words),
                    pronunciation: parse(pronunciation) });
                setValue({ ...settings, key: '' });
                setMessage('语音设置已保存');
            } catch (error) { fail(error); }
        }
        return h('details', { className: 'voice-settings' },
            h('summary', null, '语音'),
            h('div', { className: 'voice-fields' },
                pick('asr', '语音识别', source), pick('tts', '语音播报', source),
                pick('microphone', '麦克风', byKind('audioinput')),
                pick('speaker', '播放设备', byKind('audiooutput')),
                h('button', { type: 'button', onClick: reload }, '刷新设备'),
                value.tts === 'minimax' && h('label', null, '播报音色', input('voice')),
                (value.asr === 'local' || value.tts === 'local') && h('label', null, '本地语音目录',
                    h('div', { className: 'field-row' }, input('localRoot', {
                        readOnly: true, placeholder: value.bundledLocal ? '随应用安装' : undefined }),
                        h('button', { type: 'button', onClick: () => api.speechFolder().then(folder => {
                            if (folder) update('localRoot', folder);
                        }).catch(fail) }, '选择'))),
                (value.asr === 'minimax' || value.tts === 'minimax') && h('label', null, 'MiniMax 语音密钥',
                    input('key', { type: 'password', autoComplete: 'off', placeholder: current.onlineReady ? '已配置' : '输入语音密钥' })),
                h('label', null, '术语纠错', h('textarea', { rows: 2, placeholder: '原词=正确词', value: words,
                    onChange: event => setWords(event.target.value) })),
                h('label', null, '发音词表', h('textarea', { rows: 2, placeholder: '原词=播报读法', value: pronunciation,
                    onChange: event => setPronunciation(event.target.value) })),
                h('label', { className: 'voice-toggle' }, h('input', { type: 'checkbox', checked: value.autoSpeak,
                    onChange: event => update('autoSpeak', event.target.checked) }), '完成后自动播报'),
                h('button', { type: 'button', onClick: save }, '保存语音设置'),
                message && h('span', { role: 'status' }, message)));
    };
})();
