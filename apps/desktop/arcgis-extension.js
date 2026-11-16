(() => {
    const h = React.createElement;
    const { useState } = React;
    const api = window.guizhi;
    const { button, icon } = window.GuizhiShell;
    const { errorText } = window.GuizhiModel;

    window.GuizhiArcgisExtension = function Extensions({ sync }) {
        const [busy, setBusy] = useState(false);
        const [error, setError] = useState('');
        async function perform(action) {
            setBusy(true);
            setError('');
            try { await action(); }
            catch (issue) { setError(errorText(issue)); }
            finally { setBusy(false); }
        }
        return h(React.Fragment, null,
            h('section', { className: 'settings-card' }, h('h2', null, '默认扩展'),
                h(antd.Segmented, { value: sync.carrier, disabled: busy, options: [
                    { value: 'none', label: '无' },
                    { value: 'arcgis', label: 'ArcGIS', disabled: !sync.available },
                ], 'aria-label': '默认扩展', onChange: value => void perform(() => api.arcgisConfigure(value)) })),
            h('section', { className: 'settings-card arcgis-panel' },
                h('div', { className: 'section-head' }, h('h2', null, icon('map'), ' ArcGIS'),
                    h(antd.Tag, { color: sync.installed ? 'success' : 'default' }, sync.installed ? '窗口扩展已安装' : '窗口扩展未安装')),
                sync.available && !sync.installed && button('plus', '安装窗口扩展', {
                    disabled: busy, onClick: () => void perform(api.arcgisInstall),
                }),
                sync.carrier === 'arcgis' && h('p', { role: 'status', className: 'muted' },
                    sync.starting ? '正在启动数据视图' : sync.host ? '数据视图已连接' : '有空间数据时自动启动'),
                sync.carrier === 'arcgis' && sync.jobs.length > 0 && h('section', { className: 'arcgis-jobs' },
                    ...sync.jobs.map(job => h('article', { key: job.id, className: 'arcgis-job' },
                        h('div', { className: 'section-head' }, h('strong', null, job.name),
                            h(antd.Tag, { color: job.status === 'published' ? 'success' : 'default' },
                                { pending: '等待显示', published: '已显示', history: '历史图层', failed: '需重试' }[job.status])),
                        job.message && h('small', null, job.message),
                        job.status === 'failed' && button('server', '重试', { disabled: busy || sync.starting,
                            onClick: () => void perform(() => api.arcgisRetry(job.id)) })))),
                error && h('p', { role: 'alert', className: 'error' }, error)));
    };

    window.GuizhiArcgisMaterialItems = function (host) {
        const layers = host?.frames[0]?.layers.filter(layer => layer.kind === 'vector' && layer.visible) || [];
        if (!layers.length) return null;
        return { key: 'arcgis', label: '从 ArcGIS 添加', icon: icon('map'), disabled: host.editing,
            children: layers.map(layer => ({ key: 'arcgis:' + layer.index, label: layer.name, children: [
                { key: 'arcgis:' + layer.index + ':all', label: '整个图层' },
                { key: 'arcgis:' + layer.index + ':selection', label: '已选要素' },
            ] })) };
    };
})();
