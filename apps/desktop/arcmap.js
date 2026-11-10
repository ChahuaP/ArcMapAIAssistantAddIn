(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const api = window.guizhi;

    window.GuizhiArcmap = function Arcmap({ configured, task, close, attach }) {
        const dialog = useRef(null);
        const [ready, setReady] = useState(Boolean(configured));
        const [snapshot, setSnapshot] = useState(null);
        const [frame, setFrame] = useState(0);
        const [layer, setLayer] = useState('');
        const [styled, setStyled] = useState(false);
        const [busy, setBusy] = useState(false);
        const [notice, setNotice] = useState('');
        const [error, setError] = useState('');
        useEffect(() => { dialog.current.showModal(); }, []);
        const layers = snapshot?.frames[frame]?.layers || [];
        const available = layers.filter(item => item.valid && item.source && item.dataset);
        const request = action => ({ action, map: snapshot.map, map_revision: snapshot.map_revision, frame });
        async function perform(action) {
            setBusy(true);
            setError('');
            setNotice('');
            try { await action(); }
            catch (issue) {
                setError(String(issue.message || issue).replace(/^Error invoking remote method '[^']+': Error: /, ''));
            } finally { setBusy(false); }
        }
        return h('dialog', { ref: dialog, className: 'business-dialog', onCancel: event => {
            event.preventDefault();
            if (!busy) close();
        } },
            h('div', { className: 'dialog-head' }, h('h2', null, 'ArcGIS 扩展'),
                h('button', { type: 'button', disabled: busy, onClick: close }, '关闭')),
            !ready ? h('button', { type: 'button', disabled: busy, onClick: () => perform(async () => {
                if (await api.arcmapBridge()) setReady(true);
            }) }, '选择 ArcMap Bridge') : h('button', { type: 'button', disabled: busy, onClick: () => perform(async () => {
                const payload = await api.arcmapFile();
                if (!payload) return;
                const result = await api.arcmapExchange(payload);
                if (result.material) { attach(result.material); close(); }
                else { setSnapshot(result); setFrame(0); setLayer(''); setStyled(false); }
            }) }, snapshot ? '更换工程' : '打开工程或图层文件'),
            snapshot && h(React.Fragment, null,
                h('p', null, snapshot.map.split(/[\\/]/).at(-1)),
                h('label', null, '数据框', h('select', { value: frame, disabled: busy, onChange: event => {
                    setFrame(Number(event.target.value)); setLayer(''); setStyled(false);
                } }, ...snapshot.frames.map(item => h('option', { key: item.index, value: item.index }, item.name)))),
                h('label', null, '图层', h('select', { value: layer, disabled: busy,
                    onChange: event => { setLayer(event.target.value); setStyled(false); } },
                    h('option', { value: '' }, '选择图层'),
                    ...available.map(item => h('option', { key: item.index, value: item.index }, item.name)))),
                h('div', { className: 'business-actions' },
                    h('button', { type: 'button', disabled: busy || layer === '', onClick: () => perform(async () => {
                        const result = await api.arcmapExchange({ ...request('import'), layer: Number(layer) });
                        attach(result.material);
                        close();
                    }) }, '导入图层'),
                    layer !== '' && layers.find(item => item.index === Number(layer))?.selected.length > 0 &&
                        h('button', { type: 'button', disabled: busy || layer === '', onClick: () => perform(async () => {
                            const result = await api.arcmapExchange({ ...request('import'), layer: Number(layer), selected_only: true });
                            attach(result.material);
                            close();
                        }) }, '仅导入已选要素')),
                task?.deliveries?.includes('export') && h(React.Fragment, null,
                    layer !== '' && h('label', { className: 'voice-toggle' },
                        h('input', { type: 'checkbox', checked: styled, onChange: event => setStyled(event.target.checked) }),
                        '沿用选中图层样式'),
                    h('button', { type: 'button', className: 'primary', disabled: busy, onClick: () => perform(async () => {
                        const result = await api.arcmapExchange({ ...request('publish'), result_run: task.runId,
                            name: '规智成果', style: styled ? Number(layer) : null });
                        setNotice('已生成 MXD 副本 · ' + result.feature_count + ' 个要素');
                    }) }, '发布到 MXD 副本'))),
            busy && h('p', { role: 'status' }, '正在处理 ArcGIS 请求'),
            notice && h('p', { role: 'status' }, notice),
            error && h('p', { role: 'alert', className: 'error' }, error));
    };
})();
