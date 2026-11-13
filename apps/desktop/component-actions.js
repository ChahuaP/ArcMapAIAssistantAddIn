(() => {
    const h = React.createElement;
    const { useState } = React;
    const { button } = window.GuizhiShell;

    window.GuizhiComponentActions = function ComponentActions({ kind, component }) {
        const [busy, setBusy] = useState(false);
        const [error, setError] = useState('');
        if (!component || component.installed) return null;
        const { size, progress } = component;
        const pending = busy || progress !== undefined;
        async function install(method) {
            setBusy(true);
            setError('');
            try { await window.guizhi.componentInstall(kind, method); }
            catch (issue) { setError(window.GuizhiErrorText(issue)); }
            finally { setBusy(false); }
        }
        return h(React.Fragment, null,
            h('div', { className: 'business-actions' },
                button('plus', '下载并安装 · ' + Math.ceil(size / 1e6) + ' MB', {
                    disabled: pending, onClick: () => void install('download'),
                }),
                button('folder', '选择离线包', { disabled: pending, onClick: () => void install('offline') }),
                typeof progress === 'number' && button('close', '取消下载', {
                    onClick: () => void window.guizhi.componentCancel(kind),
                })),
            progress !== undefined && h('p', { role: 'status', className: 'muted' },
                progress === 'installing' ? '正在校验并安装组件…' : '正在下载 · ' + progress + '%'),
            error && h('p', { role: 'alert', className: 'error' }, error));
    };
})();
