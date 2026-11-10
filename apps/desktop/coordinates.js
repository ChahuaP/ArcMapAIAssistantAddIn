(() => {
    const h = React.createElement;
    const { useState } = React;
    const api = window.guizhi;
    const { button } = window.GuizhiShell;
    const { errorText } = window.GuizhiModel;
    const ref = entry => entry.id + '@' + entry.revision;
    const latest = (entries, kind) => [...new Map(entries.filter(item => item.kind === kind)
        .slice().reverse().map(item => [item.id, item])).values()].sort((a, b) => a.title.localeCompare(b.title, 'zh-CN'));
    const modes = { official: '已有转换', 'similarity-2d': '二维相似',
        'affine-2d': '二维仿射', 'similarity-3d': '三维相似' };
    const field = (label, control) => h('label', null, label, control);
    const select = (value, options, change) => h('select', { value, onChange: event => change(event.target.value) },
        h('option', { value: '' }, '请选择'), ...options.map(([id, label]) => h('option', { key: id, value: id }, label)));
    const precision = value => Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 6 });

    function CoordinateFile({ label, action, item, change, pick, busy }) {
        return h('div', { className: 'coordinate-file' },
            button('folder', item ? '更换' + label : action || '选择' + label, { disabled: busy, onClick: pick }),
            item && h('span', { title: item.file }, item.name),
            item?.layers?.length > 1 && field(label + '图层', select(item.layer, item.layers.map(name => [name, name]),
                layer => change({ ...item, layer }))));
    }

    function PlanWizard({ definitions, plans, done, back }) {
        const [input, setInput] = useState({ source: '', target: '', method: 'official', tolerance: '0.05',
            title: '', source_key: '', target_key: '', check_field: '', operation: '', replace: '' });
        const [sourceFile, setSourceFile] = useState(null);
        const [targetFile, setTargetFile] = useState(null);
        const [choices, setChoices] = useState(null);
        const [preview, setPreview] = useState(null);
        const [busy, setBusy] = useState(false);
        const [error, setError] = useState('');
        const patch = next => { setInput(old => ({ ...old, ...next })); setPreview(null); setError(''); };
        const chooseFile = (side, value) => {
            (side === 'source' ? setSourceFile : setTargetFile)(value);
            setChoices(null);
            patch({ source_key: '', target_key: '', check_field: '', operation: '' });
        };
        const allFiles = sourceFile?.layer && targetFile?.layer;
        const payload = action => ({ ...input, action, source_points: sourceFile, target_points: targetFile });
        async function perform(action) {
            setBusy(true);
            setError('');
            try { await action(); } catch (issue) { setError(errorText(issue)); }
            finally { setBusy(false); }
        }
        function inspect() {
            return perform(async () => {
                const result = await api.coordinateSolve(payload('inspect'));
                setChoices(result);
                const pair = result.matches[0];
                patch({ source_key: pair?.source || '', target_key: pair?.target || '',
                    check_field: result.check_fields.find(name => ['role', '点类', '用途'].includes(name.toLowerCase())) || '' });
            });
        }
        const definitionOptions = definitions.map(item => [ref(item), item.title]);
        const ready = allFiles && choices && input.source && input.target && input.source_key && input.target_key &&
            Number(input.tolerance) > 0;
        const previewResult = preview?.metrics;
        return h('div', { className: 'coordinate-wizard' },
            h('div', { className: 'section-head' }, h('h2', null, '创建转换'),
                button('back', '返回转换方案', { type: 'text', disabled: busy, onClick: back })),
            h('fieldset', { disabled: busy },
                h('div', { className: 'setting-fields' },
                    field('源坐标系', select(input.source, definitionOptions, source => patch({ source, operation: '' }))),
                    field('目标坐标系', select(input.target, definitionOptions, target => patch({ target, operation: '' }))),
                    field('转换方法', select(input.method, Object.entries(modes), method => patch({ method, operation: '' }))),
                    field('独立检查容差（米）', h('input', { type: 'number', min: 0.001, max: 1000, step: 'any',
                        value: input.tolerance, onChange: event => patch({ tolerance: event.target.value }) })),
                    field('方案名称', h('input', { value: input.title, maxLength: 120,
                        onChange: event => patch({ title: event.target.value }) })),
                    plans.length > 0 && field('方案版本', h('select', { value: input.replace,
                        onChange: event => patch({ replace: event.target.value }) }, h('option', { value: '' }, '新建方案'),
                        ...plans.map(item => h('option', { key: ref(item), value: ref(item) }, '更新 ' + item.title))))),
                h('div', { className: 'setting-fields' },
                    ...[['source', '源控制点', sourceFile], ['target', '目标控制点', targetFile]].map(([side, label, item]) =>
                        h(CoordinateFile, { key: side, label, item, busy, change: value => chooseFile(side, value),
                            pick: () => perform(async () => {
                                const value = await api.coordinateFile('points');
                                if (value) chooseFile(side, value);
                            }) }))),
                button('layers', '匹配对应点', { disabled: !allFiles || busy, onClick: inspect }),
                choices && h(React.Fragment, null,
                    h('div', { className: 'setting-fields' },
                        field('源点编号字段', select(input.source_key, choices.source_fields.map(name => [name, name]),
                            source_key => patch({ source_key }))),
                        field('目标点编号字段', select(input.target_key, choices.target_fields.map(name => [name, name]),
                            target_key => patch({ target_key }))),
                        choices.check_fields.length > 0 && field('检查点标识', h('select', { value: input.check_field,
                            onChange: event => patch({ check_field: event.target.value }) },
                            h('option', { value: '' }, '自动留出独立检查点'),
                            ...choices.check_fields.map(name => h('option', { key: name, value: name }, name))))),
                    !choices.matches.length && h('p', { className: 'muted' }, '请选取两份点图层中对应的编号字段。')),
                preview?.operations?.length > 0 && field('适用转换', select(input.operation,
                    preview.operations.map(item => [item.id, item.name + ' · ' + item.accuracy_m + ' m']),
                    operation => setInput(old => ({ ...old, operation })))),
                h('div', { className: 'business-actions' },
                    button('server', '检查转换', { disabled: !ready || busy, loading: busy,
                        onClick: () => perform(async () => setPreview(await api.coordinateSolve(payload('preview')))) }),
                    button('plus', '保存方案', { type: 'primary', disabled: busy || preview?.status !== 'verified',
                        onClick: () => perform(async () => {
                            await api.coordinateSolve({ ...payload('save'), token: preview.token });
                            await done();
                        }) }))),
            preview?.status === 'unavailable' && h('p', { role: 'status' }, '当前区域没有可用的已有转换。可检查格网或提供地方控制点。'),
            preview?.missing_grids?.length > 0 && h('p', { className: 'muted' }, '缺少格网 ' + preview.missing_grids.join('、')),
            previewResult && h('div', { className: 'coordinate-result' },
                h('strong', { className: preview.status === 'failed' ? 'error' : '' },
                    preview.status === 'verified' ? '独立检查通过' : '独立检查超过容差'),
                h('span', null, '检查点 ' + previewResult.check_count + ' 个 · 最大误差 ' + precision(previewResult.max_m) +
                    ' m · 均方根误差 ' + precision(previewResult.rmse_m) + ' m'),
                previewResult.rejected.length > 0 && h('small', null, '拟合剔除 ' + previewResult.rejected.join('、')),
                h('small', null, '适用范围 ' + preview.area.map(precision).join('，') + ' · 单位 ' + preview.unit)),
            error && h('p', { role: 'alert', className: 'error' }, error));
    }

    window.GuizhiCoordinates = function Coordinates({ entries, setEntries }) {
        const [tab, setTab] = useState('definition');
        const [wizard, setWizard] = useState(false);
        const [file, setFile] = useState(null);
        const [editing, setEditing] = useState(null);
        const [applying, setApplying] = useState(null);
        const [applyFile, setApplyFile] = useState(null);
        const [title, setTitle] = useState('');
        const [busy, setBusy] = useState(false);
        const [error, setError] = useState('');
        const refresh = () => api.coordinates().then(setEntries);
        const definitions = latest(entries, 'definition');
        const plans = latest(entries, 'plan');
        async function perform(action) {
            setBusy(true);
            setError('');
            try { await action(); } catch (issue) { setError(errorText(issue)); }
            finally { setBusy(false); }
        }
        const importFile = async item => {
            if (item.layers?.length > 1 && !item.layer) { setFile(item); return; }
            setEntries(await api.importCoordinate(item.file, item.layer));
            setFile(null);
        };
        const list = tab === 'definition' ? definitions : plans;
        if (wizard) return h(PlanWizard, { definitions, plans, back: () => setWizard(false), done: async () => {
            await refresh(); setWizard(false);
        } });
        return h('div', { className: 'coordinate-page' },
            h(antd.Tabs, { animated: false, className: 'coordinate-tabs', activeKey: tab,
                items: [{ key: 'definition', label: '坐标系' }, { key: 'plan', label: '转换方案' }],
                onChange: value => { setTab(value); setError(''); } }),
            tab === 'definition' ? h(CoordinateFile, { label: '坐标系文件', action: '添加坐标系', item: file, busy, change: setFile,
                pick: () => perform(async () => { const picked = await api.coordinateFile('definition'); if (picked) await importFile(picked); }) }) :
                button('plus', '创建转换', { disabled: busy || !definitions.length, onClick: () => setWizard(true) }),
            file?.layer && tab === 'definition' && button('folder', '导入坐标系', {
                disabled: busy, onClick: () => perform(() => importFile(file)) }),
            ...list.map(entry => {
                const technical = entry.plan ? entry.plan.pipeline : entry.definition.original;
                const summary = entry.plan
                    ? [entry.source.name + ' → ' + entry.target.name, '最大检查误差 ' + precision(Math.max(...entry.residuals_m)) + ' m',
                        '容差 ' + precision(entry.plan.tolerance_m) + ' m']
                    : [entry.definition.name, ...entry.definition.axes.map(axis => axis.unit), entry.source_file?.split(/[\\/]/).at(-1)];
                return h('article', { className: 'coordinate-record', key: ref(entry) },
                    h('div', { className: 'section-head' }, h('strong', null, entry.title),
                        h('div', { className: 'coordinate-actions' }, h(antd.Tag, null, 'v' + entry.revision),
                            button('layers', '重命名', { type: 'text', disabled: busy, onClick: () => { setEditing(ref(entry)); setTitle(entry.title); } }),
                            !entry.plan && h(antd.Popconfirm, { title: '删除「' + entry.title + '」？',
                                description: '已有任务和已保存的转换方案仍可追溯。',
                                okText: '删除', cancelText: '取消', okButtonProps: { danger: true },
                                onConfirm: () => perform(async () => { setEntries(await api.coordinateRemove(ref(entry))); setEditing(null); }) },
                                button('trash', '删除', { type: 'text', danger: true, disabled: busy })))),
                    h('small', null, summary.filter(Boolean).join(' · ')),
                    entry.plan && applying !== ref(entry) && button('map', '转换材料', { disabled: busy, onClick: () => {
                        setApplying(ref(entry)); setApplyFile(null);
                    } }),
                    applying === ref(entry) && h(React.Fragment, null,
                        h(CoordinateFile, { label: '矢量材料', item: applyFile, busy, change: setApplyFile,
                            pick: () => perform(async () => { const picked = await api.coordinateFile('points'); if (picked) setApplyFile(picked); }) }),
                        button('map', '执行转换', { type: 'primary', disabled: busy || !applyFile?.layer,
                            onClick: () => perform(async () => { await api.coordinateApply(applyFile, ref(entry)); setApplying(null); }) })),
                    editing === ref(entry) && h('div', { className: 'field-row' },
                        h('input', { 'aria-label': '新名称', value: title, maxLength: 120, onChange: event => setTitle(event.target.value) }),
                        button('plus', '保存名称', { disabled: !title.trim() || busy, onClick: () => perform(async () => {
                            await api.coordinateRename({ kind: entry.kind, reference: ref(entry), title });
                            await refresh(); setEditing(null);
                        }) })),
                    h('details', null, h('summary', null, '详情'),
                        entry.plan && h('small', null, '适用范围 ' + entry.plan.area.map(precision).join('，') +
                            ' · ' + (entry.plan.grids.map(grid => grid.name).join('、') || '无需格网')),
                        h('pre', null, technical)));
            }),
            !list.length && h('p', { className: 'muted' }, tab === 'definition' ? '从带坐标信息的文件导入坐标系。' : '暂无转换方案。'),
            error && h('p', { role: 'alert', className: 'error' }, error));
    };
})();
