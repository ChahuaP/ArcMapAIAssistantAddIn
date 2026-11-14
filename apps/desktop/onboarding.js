(() => {
    const h = React.createElement;
    const { useEffect, useState } = React;
    const api = window.guizhi;
    const { Titlebar } = window.GuizhiShell;
    const { ModelForm, Workspace, initial, errorText } = window.GuizhiModel;
    const steps = [
        ['name', '姓名'], ['workspace', '成果位置'], ['model', '模型设置'],
        ['voice', '语音设置'], ['review', '确认配置'],
    ];

    window.GuizhiOnboarding = function Onboarding({ state, save }) {
        const [step, setStep] = useState(0);
        const [page] = steps[step];
        const [value, change] = useState({ workspace: state.settings.workspace,
            modelConfig: state.settings.modelConfig || initial(), operator: state.settings.operator || '', key: '' });
        const [voice, setVoice] = useState(state.speech);
        const [savedVoice, setSavedVoice] = useState(state.speech);
        const [verified, setVerified] = useState(false);
        const [testing, setTesting] = useState(false);
        const [saving, setSaving] = useState(false);
        const [error, setError] = useState('');
        const fail = issue => setError(errorText(issue));
        const blocked = testing || saving;
        useEffect(() => {
            if (page !== 'voice') return;
            const image = new Image();
            image.src = '/luhe-banner.png';
            void image.decode().catch(issue => console.error('首页背景预加载失败', issue));
        }, [page]);
        const currentVoice = { ...savedVoice, localAvailable: state.speech.localAvailable, component: state.speech.component };
        const needsLocal = window.GuizhiSpeechNeedsLocal(voice, currentVoice);

        const canAdvance = page === 'name' ? Boolean(value.operator.trim())
            : page === 'workspace' ? Boolean(value.workspace.trim())
            : page === 'model' ? verified : page !== 'voice' || !needsLocal;

        async function advance(skip = false) {
            if (blocked || (!skip && !canAdvance)) return;
            setError('');
            setSaving(true);
            try {
                if (page === 'workspace') {
                    const workspace = await api.workspaceReady(value.workspace);
                    change(previous => ({ ...previous, workspace }));
                } else if (page === 'voice') {
                    const next = await api.speechConfigure(skip ? { ...voice, asr: 'off', tts: 'off' } : voice);
                    setSavedVoice(next);
                    setVoice(next);
                } else if (page === 'review') {
                    await save({ ...value, operator: value.operator.trim() });
                    return;
                }
                setStep(step + 1);
            } catch (issue) { fail(issue); }
            finally { setSaving(false); }
        }

        const review = [
            ['姓名', value.operator.trim()],
            ['成果保存位置', value.workspace],
            ['运行模式', value.modelConfig.mode === 'local' ? '本地服务' : '在线服务'],
            ['模型', value.modelConfig.model],
            ['语音识别', window.GuizhiSpeechNames[voice.asr]],
            ['语音回答', window.GuizhiSpeechNames[voice.tts]],
        ];
        const content = {
            name: () => h('section', { className: 'settings-card' },
                h('label', { className: 'onboarding-name' }, h('h2', null, '姓名'),
                    h(antd.Input, { value: value.operator, maxLength: 60, disabled: blocked, autoFocus: true,
                        autoComplete: 'name', placeholder: '请输入姓名', 'aria-label': '姓名',
                        onChange: event => change(previous => ({ ...previous, operator: event.target.value })),
                        onPressEnter: () => advance() }))),
            workspace: () => h('section', { className: 'settings-card' }, h('h2', null, '成果保存位置'),
                h(Workspace, { value: value.workspace, change: workspace => change({ ...value, workspace }), fail, disabled: blocked })),
            model: () => h(ModelForm, { value, change, current: state.settings, verified: setVerified, busy: setTesting }),
            voice: () => h(window.GuizhiSpeechForm, { current: currentVoice, value: voice, change: setVoice, busy: setTesting }),
            review: () => h('dl', { className: 'settings-card onboarding-summary' }, ...review.map(([label, text]) =>
                h('div', { key: label }, h('dt', null, label), h('dd', null, text)))),
        };
        return h('div', { className: 'desktop-frame' }, h(Titlebar, { onboarding: true }),
            h('main', { className: 'onboarding-frame' },
                h('header', { className: 'onboarding-header' }, h('div', { className: 'onboarding-content' },
                    h('h1', null, '首次使用设置'),
                    h(antd.Steps, { current: step, size: 'small', responsive: false, items: steps.map(([, title]) => ({ title })) }))),
                h('div', { className: 'onboarding-scroll' },
                    h('div', { className: 'onboarding-content', key: page }, content[page]())),
                h('footer', { className: 'onboarding-footer' }, h('div', { className: 'onboarding-content setting-row' },
                    h('div', { className: 'business-actions' },
                        step > 0 && h(antd.Button, { disabled: blocked, onClick: () => { setError(''); setStep(step - 1); } }, '上一步'),
                        page === 'voice' && h(antd.Button, { type: 'text', disabled: blocked, onClick: () => advance(true) }, '跳过语音')),
                    h('div', { className: 'business-actions' },
                        error && h('span', { className: 'error', role: 'alert' }, error),
                        h(antd.Button, { type: 'primary', disabled: blocked || !canAdvance, loading: saving, onClick: () => advance() },
                        page === 'review' ? '开始使用' : '下一步'))))));
    };
})();
