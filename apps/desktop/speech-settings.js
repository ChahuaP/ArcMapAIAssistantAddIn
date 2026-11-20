(() => {
    const h = React.createElement;
    const { useState, useEffect, useRef } = React;
    const api = window.guizhi;
    const { button } = window.GuizhiShell;
    const names = { off: '关闭', local: '本机语音', minimax: 'MiniMax', azure: '微软 Azure 语音',
        aliyun: '阿里云百炼 CosyVoice', custom: '自定义兼容接口', surf: 'SurfVoice 本机服务', volc: '火山引擎豆包语音' };
    const volcResources = { asr: [['volc.seedasr.sauc.duration', '识别 2.0 · 小时版'], ['volc.seedasr.sauc.concurrent', '识别 2.0 · 并发版']],
        tts: [['seed-tts-2.0', '语音合成 2.0'], ['seed-icl-2.0', '声音复刻 2.0']] };
    window.GuizhiSpeechNames = names;
    const format = entries => entries.map(entry => entry.heard + '=' + entry.correct).join('\n');
    const errorText = window.GuizhiErrorText;
    const needsLocal = (value, current) => !current.localAvailable && (value.asr === 'local' || value.tts === 'local');
    window.GuizhiSpeechNeedsLocal = needsLocal;

    function SpeechForm({ value, change, current, busy = () => {} }) {
        const [devices, setDevices] = useState([]);
        const [testing, setTesting] = useState('');
        const [message, setMessage] = useState('');
        const [error, setError] = useState('');
        const recorder = useRef(null);
        const playback = useRef(null);
        const reload = () => navigator.mediaDevices.enumerateDevices().then(setDevices).catch(fail);
        const update = (field, next) => change({ ...value, [field]: next });
        const fail = issue => setError(errorText(issue));
        useEffect(() => {
            void reload();
            return () => {
                recorder.current?.releaseCapture();
                if (playback.current && playback.current.state !== 'closed') void playback.current.close();
            };
        }, []);
        function testState(next) { setTesting(next); busy(Boolean(next)); }
        async function finish(voice) {
            testState('asr');
            try {
                const result = await voice.finish(() => {});
                if (result) setMessage('识别结果 · ' + result);
            } catch (issue) { fail(issue); }
            finally { testState(''); }
        }
        async function record() {
            setError('');
            setMessage('');
            if (testing === 'recording') return finish(recorder.current);
            testState('opening');
            try {
                const { VoiceAudio } = await import('/speech-audio.js');
                const voice = new VoiceAudio({ speechCancel: api.speechCancel, onSpeechPartial: api.onSpeechPartial,
                    speechRecognize: audio => api.speechTest('asr', value, audio) }, () => {});
                recorder.current = voice;
                const active = await voice.listen(value.microphone, () => finish(voice));
                testState(active ? 'recording' : '');
            } catch (issue) { fail(issue); testState(''); }
        }
        async function listen() {
            setMessage('');
            setError('');
            testState('tts');
            try {
                const context = playback.current ??= new AudioContext();
                if (value.speaker && !context.setSinkId) throw new Error('当前系统无法选择播放设备');
                if (context.setSinkId) await context.setSinkId(value.speaker || '').catch(error => {
                    throw new Error(errorText(error, 'speaker'));
                });
                await context.resume();
                const wav = await api.speechTest('tts', value);
                const source = context.createBufferSource();
                source.buffer = await context.decodeAudioData(wav);
                source.connect(context.destination);
                await new Promise(resolve => { source.onended = resolve; source.start(); });
                setMessage('试听完成');
            } catch (issue) { fail(issue); }
            finally { testState(''); }
        }
        const byKind = (kind, selected) => [['', '系统默认'], ...(selected && !devices.some(device => device.kind === kind && device.deviceId === selected)
            ? [[selected, '原设备不可用']] : []), ...devices
            .filter(device => device.kind === kind && device.deviceId && device.deviceId !== 'default')
            .map((device, index) => [device.deviceId, device.label || '设备 ' + (index + 1)])];
        const select = (label, selected, options, onChange, disabled = false) => h('label', null, label,
            h('select', { disabled: Boolean(testing) || disabled, value: selected, onChange: event => onChange(event.target.value) },
                ...options.map(([id, title]) => h('option', { key: id, value: id }, title))));
        const field = (label, selected, onChange, props = {}) => h('label', null, label,
            h('input', { ...props, disabled: Boolean(testing), value: selected || '',
                onChange: event => onChange(event.target.value) }));
        function section(kind) {
            const asr = kind === 'asr';
            const title = asr ? '语音识别' : '语音回答';
            const device = asr ? 'microphone' : 'speaker';
            const name = kind + 'Profile';
            const profile = value[name] || {};
            const provider = value[kind];
            const remote = !['off', 'local'].includes(provider);
            const missing = provider === 'local' && !current.localAvailable;
            const choices = ['off', ...(current.localAvailable || current.component ? ['local'] : []), ...current.providers[kind]]
                .map(id => [id, names[id]]);
            const saved = current[name];
            const configured = provider === current[kind] &&
                ['baseUrl', 'region', 'workspace'].every(key => saved?.[key] === profile[key]) &&
                saved?.keyConfigured;
            const updateProfile = (key, next) => update(name, { ...profile, [key]: next,
                ...(['baseUrl', 'region', 'workspace'].includes(key) ? { key: '' } : {}) });
            return h('section', { className: 'settings-card speech-channel', 'aria-labelledby': kind + '-heading' },
                h('h2', { id: kind + '-heading' }, title),
                h('div', { className: 'setting-fields' },
                    select('语音服务', provider, choices, next => change({ ...value, [kind]: next,
                        [name]: next === current[kind] ? current[name] : {},
                        ...(asr ? {} : { voice: next === current.tts ? current.voice : '' }) }),
                        current.component?.progress !== undefined),
                    missing && (asr || value.asr !== 'local') && h('div', { className: 'speech-component' },
                        h('p', { className: 'muted' }, '使用本机语音前，请先安装语音模块。'),
                        h(window.GuizhiComponentActions, { kind: 'speech', component: current.component })),
                    select(asr ? '麦克风' : '播放设备', value[device], byKind(asr ? 'audioinput' : 'audiooutput', value[device]),
                        next => update(device, next)),
                    provider === 'custom' && field(title + '地址', profile.baseUrl, next => updateProfile('baseUrl', next),
                        { placeholder: 'https://speech.example.org/v1' }),
                    provider === 'custom' && field(title + '模型', profile.model, next => updateProfile('model', next)),
                    provider === 'azure' && field('资源区域', profile.region, next => updateProfile('region', next),
                        { placeholder: 'eastasia' }),
                    provider === 'aliyun' && field('百炼工作空间 ID', profile.workspace, next => updateProfile('workspace', next)),
                    provider === 'aliyun' && field('语音模型', profile.model, next => updateProfile('model', next),
                        { placeholder: 'cosyvoice-v3-plus' }),
                    provider === 'surf' && field('本机服务地址', profile.baseUrl, next => updateProfile('baseUrl', next),
                        { placeholder: 'http://127.0.0.1:8765' }),
                    provider === 'volc' && select('语音资源', profile.model || volcResources[kind][0][0], volcResources[kind],
                        next => updateProfile('model', next)),
                    remote && field(provider === 'surf' ? '访问令牌（选填）' : title + '密钥', profile.key,
                        next => updateProfile('key', next), { type: 'password', autoComplete: 'off',
                            placeholder: configured ? '已配置' : provider === 'surf' ? '按本机服务设置填写' : '输入 API 密钥' }),
                    !asr && remote && field('回答音色', value.voice, next => update('voice', next),
                        { placeholder: { custom: 'alloy', azure: 'zh-CN-XiaoxiaoNeural',
                            aliyun: 'longanyang', minimax: 'female-shaonv', surf: 'huayan',
                            volc: profile.model === 'seed-icl-2.0' ? '控制台中的音色 ID' : '使用默认音色' }[provider] })),
                asr ? button('mic', testing === 'recording' ? '结束录音' : '录音测试',
                    { disabled: provider === 'off' || missing || Boolean(testing && testing !== 'recording'), onClick: record })
                    : button('speaker', '声音试听', { disabled: provider === 'off' || missing || Boolean(testing),
                        loading: testing === 'tts', onClick: listen }));
        }
        return h('div', { className: 'speech-form' },
            section('asr'), section('tts'),
            testing === 'recording' && h('p', { role: 'status', className: 'muted' }, '正在录音'),
            testing === 'asr' && h('p', { role: 'status', className: 'muted' }, '正在识别'),
            message && h('p', { role: 'status', className: 'muted' }, message),
            error && h('p', { role: 'alert', className: 'error' }, error));
    }

    window.GuizhiSpeechForm = SpeechForm;
    window.GuizhiSpeechSettings = function SpeechSettings({ current }) {
        const [value, setValue] = useState(current);
        const [words, setWords] = useState(() => format(current.dictionary));
        const [pronunciation, setPronunciation] = useState(() => format(current.pronunciation));
        const [testing, setTesting] = useState(false);
        const [saving, setSaving] = useState(false);
        const [message, setMessage] = useState('');
        const [error, setError] = useState('');
        function parse(text) {
            return text.split('\n').filter(line => line.trim()).map(line => {
                const split = line.indexOf('=');
                if (split < 1) throw new Error('词表每行使用“原词=替换词”');
                return { heard: line.slice(0, split), correct: line.slice(split + 1) };
            });
        }
        async function save() {
            setMessage('');
            setError('');
            setSaving(true);
            try {
                setValue(await api.speechConfigure({ ...value, dictionary: parse(words), pronunciation: parse(pronunciation) }));
                setMessage('语音设置已保存');
            } catch (issue) { setError(errorText(issue)); }
            finally { setSaving(false); }
        }
        return h('div', { className: 'voice-settings-page' }, h(SpeechForm, { current, value, change: setValue, busy: setTesting }),
            h('details', { className: 'voice-settings' }, h('summary', null, '专业词汇'),
                h('div', { className: 'voice-fields' },
                    h('label', null, '术语纠错', h('textarea', { rows: 2, placeholder: '原词=正确词', value: words,
                        onChange: event => setWords(event.target.value) })),
                    h('label', null, '发音词表', h('textarea', { rows: 2, placeholder: '原词=发音读法', value: pronunciation,
                        onChange: event => setPronunciation(event.target.value) })))),
            h('div', { className: 'setting-row' },
                h('div', null, message && h('p', { role: 'status' }, message),
                    error && h('p', { role: 'alert', className: 'error' }, error)),
                h(antd.Button, { type: 'primary', disabled: testing || needsLocal(value, current), loading: saving, onClick: save }, '保存设置')));
    };
})();
