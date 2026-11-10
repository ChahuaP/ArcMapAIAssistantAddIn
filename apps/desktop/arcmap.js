(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const api = window.guizhi;
    const { button, iconButton } = window.GuizhiShell;
    const { errorText } = window.GuizhiModel;
    const basename = value => value?.split(/[\\/]/).at(-1);

    window.GuizhiArcmap = function Arcmap({ configured, sync, task, close, attach }) {
        const dialog = useRef(null);
        const [session, setSession] = useState(sync.host?.session || sync.hosts[0]?.session || '');
        const [frame, setFrame] = useState(sync.connection?.frame ?? 0);
        const [layer, setLayer] = useState('');
        const [busy, setBusy] = useState(false);
        const [error, setError] = useState('');
        const [notice, setNotice] = useState('');
        const selected = sync.hosts.find(host => host.session === session);
        const live = sync.host;
        const target = sync.connection;
        const jobs = sync.jobs.filter(job => job.map === target?.map && job.frame === target?.frame);
        const layers = live?.frames[target?.frame]?.layers.filter(item => item.kind === 'vector') || [];
        const result = task?.status === 'succeeded' && ['export', 'boundaries'].some(kind =>
            ['.gpkg', '.tif', '.tiff'].includes(task.deliveryFormats?.[kind]));
        const alreadyQueued = jobs.some(job => job.task === task?.id);
        useEffect(() => { dialog.current.showModal(); }, []);
        useEffect(() => {
            if (!selected && sync.hosts.length) {
                setSession(sync.host?.session || sync.hosts[0].session);
                setFrame(sync.connection?.frame ?? 0);
            }
        }, [selected, sync.hosts.length]);
        useEffect(() => { setLayer(''); }, [target?.map, target?.frame, live?.generation]);
        async function perform(action) {
            setBusy(true);
            setError('');
            setNotice('');
            try { await action(); }
            catch (issue) { setError(errorText(issue)); }
            finally { setBusy(false); }
        }
        const edit = action => () => perform(action);
        return h('dialog', { ref: dialog, className: 'business-dialog arcgis-dialog', onCancel: event => {
            event.preventDefault();
            if (!busy) close();
        } },
            h('div', { className: 'dialog-head' }, h('h2', null, 'ArcGIS 扩展'),
                iconButton('close', '关闭 ArcGIS 扩展', { disabled: busy, onClick: close })),
            h('div', { className: 'business-actions' },
                button('folder', '打开 MXD', { disabled: busy, onClick: edit(async () => {
                    if (await api.arcgisOpen()) setNotice('工程打开后即可连接');
                }) }),
                !configured && button('server', '配置 Bridge', { disabled: busy, onClick: edit(() => api.arcmapBridge()) }),
                !sync.hosts.length && button('plus', '安装扩展', { disabled: busy, onClick: edit(() => api.arcgisInstall()) })),
            !sync.hosts.length && h('p', { className: 'muted' }, '打开已保存的工程，并在 ArcGIS 中启用规智扩展。'),
            sync.hosts.length > 0 && h('div', { className: 'setting-fields' },
                h('label', null, '已打开的工程', h('select', { value: session, disabled: busy, onChange: event => {
                    setSession(event.target.value); setFrame(0);
                } }, ...sync.hosts.map(host => h('option', { key: host.session, value: host.session, title: host.map },
                    (basename(host.map) || host.caption || '读取当前工程') + ' · ArcGIS ' + host.version)))),
                h('label', null, '数据框', h('select', { value: frame, disabled: busy,
                    onChange: event => setFrame(Number(event.target.value)) },
                    ...((selected?.frames || []).map(item => h('option', { key: item.index, value: item.index }, item.name))))),
                selected?.state === 'waiting'
                    ? button('layers', '读取当前工程', { type: 'primary', disabled: busy,
                        onClick: edit(() => api.arcgisDiscover(session)) })
                    : button('layers', '连接工程', { type: 'primary', disabled: busy || !selected?.frames[frame],
                        onClick: edit(() => api.arcgisConfigure({ session, frame })) })),
            target && h('section', { className: 'settings-card' },
                h('div', { className: 'section-head' }, h('strong', { title: target.map }, basename(target.map) + ' · ' + target.frameName),
                    h(antd.Tag, { color: live ? 'green' : 'default' }, live ? '已连接 · ' + live.version : '等待工程打开')),
                h('label', { className: 'voice-toggle' }, h('input', { type: 'checkbox', checked: sync.auto, disabled: busy,
                    onChange: event => perform(() => api.arcgisConfigure({ auto: event.target.checked })) }),
                    '完成后在 ArcGIS 显示'),
                live?.editing && h('p', { role: 'status' }, 'ArcGIS 正在编辑，待处理成果将在编辑结束后发布。'),
                live && !live.publish_ready && h('p', { role: 'status' }, '请从规智重新打开 MXD，以启用当前宿主的成果发布。'),
                sync.newEdits && h(antd.Tag, { color: 'green' }, '有已保存的编辑'),
                layers.length > 0 && h('label', null, '工作台材料', h('select', { value: layer, disabled: busy || live.editing,
                    onChange: event => setLayer(event.target.value) }, h('option', { value: '' }, '选择图层'),
                    ...layers.map(item => h('option', { key: item.index, value: item.index }, item.name)))),
                layers.length > 0 && h('div', { className: 'business-actions' },
                    ...[[false, '使用此图层'], [true, '使用已选要素']].map(([selectedOnly, label]) =>
                        button('layers', label, { key: label, disabled: busy || live.editing || layer === '',
                            onClick: edit(async () => { attach(await api.arcgisCapture(Number(layer), selectedOnly)); close(); }) }))),
                result && !alreadyQueued && button('map', '显示此任务成果', { disabled: busy, onClick: edit(() => api.arcgisPublish(task.id)) }),
                button('close', '断开连接', { type: 'text', disabled: busy, onClick: edit(() => api.arcgisConfigure({ disconnect: true })) })),
            jobs.length > 0 && h('section', { className: 'arcgis-jobs' }, h('h3', null, '成果同步'),
                ...jobs.map(job => h('article', { key: job.id, className: 'arcgis-job' },
                    h('div', { className: 'section-head' }, h('strong', null, job.name),
                        h(antd.Tag, { color: job.status === 'published' ? 'green' : 'default' },
                            { pending: live?.editing ? '等待编辑结束' : '等待同步', published: '已显示', history: '历史图层',
                                failed: '需重试' }[job.status])),
                    job.message && h('small', null, job.message),
                    job.status === 'failed' && button('server', '重试', { disabled: busy,
                        onClick: edit(() => api.arcgisRetry(job.id)) })))),
            notice && h('p', { role: 'status' }, notice),
            error && h('p', { role: 'alert', className: 'error' }, error));
    };
})();
