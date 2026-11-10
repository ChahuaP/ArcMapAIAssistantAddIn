(() => {
    const h = React.createElement;
    const { useState } = React;
    const api = window.guizhi;
    const { Titlebar } = window.GuizhiShell;
    const { ModelForm, Workspace, initial, errorText } = window.GuizhiModel;
    const steps = ['成果位置', '模式与模型', '语音', '开始使用'];
    const providers = { off: '关闭', local: '本机语音', minimax: 'MiniMax', custom: '自定义服务' };

    window.GuizhiOnboarding = function Onboarding({ state, save }) {
        const [step, setStep] = useState(0);
        const [value, change] = useState({ workspace: state.settings.workspace,
            modelConfig: state.settings.modelConfig || initial(), key: '' });
        const [voice, setVoice] = useState(state.speech);
        const [savedVoice, setSavedVoice] = useState(state.speech);
        const [verified, setVerified] = useState(false);
        const [testing, setTesting] = useState(false);
        const [saving, setSaving] = useState(false);
        const [error, setError] = useState('');
        const fail = issue => setError(errorText(issue));
        const blocked = testing || saving;

        async function advance(skip = false) {
            setError('');
            setSaving(true);
            try {
                if (step === 0) {
                    const workspace = await api.workspaceReady(value.workspace);
                    change(previous => ({ ...previous, workspace }));
                } else if (step === 2) {
                    const next = await api.speechConfigure(skip ? { ...voice, asr: 'off', tts: 'off', autoSpeak: false } : voice);
                    setSavedVoice(next);
                    setVoice(next);
                } else if (step === 3) {
                    await save(value);
                    return;
                }
                setStep(step + 1);
            } catch (issue) { fail(issue); }
            finally { setSaving(false); }
        }

        const review = [
            ['成果保存位置', value.workspace],
            ['运行模式', value.modelConfig.mode === 'local' ? '本地服务' : '在线服务'],
            ['模型', value.modelConfig.model],
            ['语音识别', providers[voice.asr]],
            ['语音播报', providers[voice.tts]],
        ];
        const content = [
            () => h('section', { className: 'settings-card' }, h('h2', null, '成果保存位置'),
                h(Workspace, { value: value.workspace, change: workspace => change({ ...value, workspace }), fail, disabled: blocked })),
            () => h(ModelForm, { value, change, current: state.settings, verified: setVerified, busy: setTesting }),
            () => h(window.GuizhiSpeechForm, { current: savedVoice, value: voice, change: setVoice, busy: setTesting }),
            () => h('dl', { className: 'settings-card onboarding-summary' }, ...review.map(([label, text]) =>
                h('div', { key: label }, h('dt', null, label), h('dd', null, text)))),
        ];
        return h('div', { className: 'desktop-frame' }, h(Titlebar, { onboarding: true }),
            h('main', { className: 'onboarding-frame' },
                h('header', { className: 'onboarding-header' }, h('div', { className: 'onboarding-content' },
                    h('h1', null, '首次使用设置'),
                    h(antd.Steps, { current: step, size: 'small', items: steps.map(title => ({ title })) }))),
                h('div', { className: 'onboarding-scroll' },
                    h('div', { className: 'onboarding-content', key: step }, content[step]())),
                h('footer', { className: 'onboarding-footer' }, h('div', { className: 'onboarding-content setting-row' },
                    h('div', { className: 'business-actions' },
                        step > 0 && h(antd.Button, { disabled: blocked, onClick: () => { setError(''); setStep(step - 1); } }, '上一步'),
                        step === 2 && h(antd.Button, { type: 'text', disabled: blocked, onClick: () => advance(true) }, '暂时跳过语音')),
                    h('div', { className: 'business-actions' },
                        error && h('span', { className: 'error', role: 'alert' }, error),
                        h(antd.Button, { type: 'primary', disabled: blocked || (step === 0 && !value.workspace.trim()) ||
                            (step === 1 && !verified), loading: saving, onClick: () => advance() },
                        step === 3 ? '开始使用' : '下一步'))))));
    };
})();
