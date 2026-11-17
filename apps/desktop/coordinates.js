(() => {
    const h = React.createElement;
    const { useState } = React;
    const api = window.guizhi;
    const { button } = window.GuizhiShell;
    const { errorText } = window.GuizhiModel;
    const ref = entry => entry.id + '@' + entry.revision;
    const latest = (entries, kind) => [...new Map(entries.filter(item => item.kind === kind)
        .slice().reverse().map(item => [item.id, item])).values()].sort((a, b) => a.title.localeCompare(b.title, 'zh-CN'));
    const modes = { official: '标准转换', 'similarity-2d': '二维相似',
        'affine-2d': '二维仿射', 'similarity-3d': '三维相似' };
    const methodHelp = {
        official: '按所选坐标系和控制点位置，从本机标准库（PROJ）查找适用的转换，再用至少 3 对实测点核验。',
        'similarity-2d': '根据平移、旋转和统一缩放拟合地方坐标。两份数据须使用线性坐标（如米）。至少用 4 对点拟合，另留 3 对点核验。',
        'affine-2d': '可沿不同方向缩放，并处理倾斜变形。两份数据须使用线性坐标（如米）。至少用 5 对点拟合，另留 3 对点核验。',
        'similarity-3d': '两份数据须同为三维地心坐标，或同为三维工程坐标，且每个点都有 Z 坐标。至少用 5 对点拟合，另留 3 对点核验。',
    };
    const field = (label, control) => h('label', null, label, control);
    const select = (value, options, change) => h('select', { value, onChange: event => change(event.target.value) },
        h('option', { value: '' }, '请选择'), ...options.map(([id, label]) => h('option', { key: id, value: id }, label)));
    const precision = value => Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 6 });
    const help = text => h('p', { className: 'coordinate-help muted' }, text);
    function areaText(area, kind) {
        const [xmin, ymin, xmax, ymax] = area;
        const range = (label, low, high) => label + ' ' + precision(low) + '～' + precision(high);
        return kind === 'geographic'
            ? range('经度', xmin, xmax) + '，' + range('纬度', ymin, ymax) + '（度）'
            : range('X', xmin, xmax) + '，' + range('Y', ymin, ymax) + '（转换前坐标）';
    }

    function ControlPointGuide() {
        return h('details', { className: 'coordinate-guide' },
            h('summary', null, '查看控制点示例'),
            help('例如，两个点图层都用 point_id 存点号，P01 在两边代表同一个界址点。转换前图层用 role 标记拟合点和检查点。'),
            h('table', null, h('thead', null, h('tr', null,
                h('th', { scope: 'col' }, '转换前点号'), h('th', { scope: 'col' }, '转换后点号'),
                h('th', { scope: 'col' }, '转换前图层的用途'))),
                h('tbody', null, ...[['P01', 'fit（拟合）'], ['P02', 'check（检查）']].map(([id, role]) =>
                    h('tr', { key: id }, h('td', null, id), h('td', null, id), h('td', null, role))))),
            help('点号在各自图层中须唯一且非空。坐标取自各自图层的点几何。两边的记录顺序可以不同，系统只配对两边都有的点号。'));
    }

    function CoordinateFile({ label, action, layerLabel, item, change, pick, busy }) {
        return h('div', { className: 'coordinate-file' },
            button('folder', item ? '更换' + label : action || '选择' + label, { disabled: busy, onClick: pick }),
            item && h('span', { title: item.file }, item.name),
            item?.layers?.length > 1 && field(layerLabel, select(item.layer, item.layers.map(name => [name, name]),
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
        const tolerance = Number(input.tolerance);
        const ready = allFiles && choices && input.source && input.target && input.method && input.source_key && input.target_key &&
            tolerance > 0 && tolerance <= 1000;
        const previewResult = preview?.metrics;
        return h('div', { className: 'coordinate-wizard' },
            h('div', { className: 'section-head' }, h('h2', null, '创建转换方案'),
                button('back', '返回转换方案', { type: 'text', disabled: busy, onClick: back })),
            h('fieldset', { disabled: busy },
                help('从自定义坐标系库选择转换前、后的坐标系，两项须与下面两份点图层标注的坐标系对应。'),
                h('div', { className: 'setting-fields' },
                    field('转换前坐标系', select(input.source, definitionOptions, source => patch({ source, operation: '' }))),
                    field('转换后坐标系', select(input.target, definitionOptions, target => patch({ target, operation: '' }))),
                    h('div', null, field('转换方法', select(input.method, Object.entries(modes), method => patch({ method, operation: '' }))),
                        input.method && help(methodHelp[input.method])),
                    h('div', null, field('允许的最大误差（米）', h('input', { type: 'number', min: 0.001, max: 1000, step: 'any',
                        value: input.tolerance, onChange: event => patch({ tolerance: event.target.value }) })),
                        help('比较转换结果与已知目标点的位置。所有检查点的误差都在限值内时才能保存。')),
                    field('方案名称', h('input', { value: input.title, maxLength: 120,
                        onChange: event => patch({ title: event.target.value }) })),
                    plans.length > 0 && field('方案版本', h('select', { value: input.replace,
                        onChange: event => patch({ replace: event.target.value }) }, h('option', { value: '' }, '新建方案'),
                        ...plans.map(item => h('option', { key: ref(item), value: ref(item) }, '更新 ' + item.title))))),
                h('div', { className: 'coordinate-controls' },
                    h('h3', null, '配对控制点'),
                    help('准备同一批实地位置在转换前、后的两份单点图层，例如同一批界址点在两套坐标系下的数据。支持 SHP、GeoPackage 或 GeoJSON，每份最多 2,000 个点，且须带坐标系定义。'),
                    h('div', { className: 'setting-fields' },
                        ...[['source', '转换前', sourceFile], ['target', '转换后', targetFile]].map(([side, label, item]) =>
                            h(CoordinateFile, { key: side, label: label + '点文件', layerLabel: label + '点图层',
                                item, busy, change: value => chooseFile(side, value),
                                pick: () => perform(async () => {
                                    const value = await api.coordinateFile('points');
                                    if (value) chooseFile(side, value);
                                }) }))),
                    h(ControlPointGuide)),
                button('layers', '读取点号字段', { disabled: !allFiles || busy, onClick: inspect }),
                choices && h(React.Fragment, null,
                    h('div', { className: 'setting-fields' },
                        field('转换前点号字段', select(input.source_key, choices.source_fields.map(name => [name, name]),
                            source_key => patch({ source_key }))),
                        field('转换后点号字段', select(input.target_key, choices.target_fields.map(name => [name, name]),
                            target_key => patch({ target_key }))),
                        choices.check_fields.length > 0 && field('转换前图层的用途字段', h('select', { value: input.check_field,
                            onChange: event => patch({ check_field: event.target.value }) },
                            h('option', { value: '' }, input.method === 'official' ? '全部配对点用于核验' : '自动留出检查点'),
                            ...choices.check_fields.map(name => h('option', { key: name, value: name }, name))))),
                    help(input.method === 'official'
                        ? '未指定检查点时，全部配对点用于核验。如标记了「检查」或 check，至少要有 3 对检查点。'
                        : '用途字段中的「拟合」或 fit 用来求参数，「检查」或 check 用来独立核验。留空时由系统自动划分。'),
                    !choices.matches.length && help('请在两份图层中分别选择存放同一组点号的字段，字段名称可以不同。')),
                preview?.operations?.length > 0 && h('div', null,
                    field('候选标准转换', select(input.operation,
                        preview.operations.map(item => [item.id, item.name + ' · 标称精度 ' + item.accuracy_m + ' 米']),
                        operation => setInput(old => ({ ...old, operation })))),
                    help('找到多种适用的标准转换。选定一项后，再核验实际误差。')),
                h('div', { className: 'business-actions' },
                    button('server', '核验误差', { disabled: !ready || busy || preview?.status === 'choose' && !input.operation, loading: busy,
                        onClick: () => perform(async () => setPreview(await api.coordinateSolve(payload('preview')))) }),
                    button('plus', '保存方案', { type: 'primary', disabled: busy || preview?.status !== 'verified',
                        onClick: () => perform(async () => {
                            await api.coordinateSolve({ ...payload('save'), token: preview.token });
                            await done();
                        }) }))),
            preview?.status === 'unavailable' && h('p', { role: 'status' }, '本机标准库中没有适用于这两个坐标系和控制点区域的可用转换。请核对坐标系，或用地方控制点拟合。'),
            preview?.missing_grids?.length > 0 && help('部分候选转换需要补充格网文件 ' + preview.missing_grids.join('、')),
            previewResult && h('div', { className: 'coordinate-result' },
                h('strong', { className: preview.status === 'failed' ? 'error' : '' },
                    preview.status === 'verified' ? '误差核验通过' : '检查点误差超过限值'),
                h('span', null, '配对检查点 ' + previewResult.check_count + ' 对 · 最大误差 ' + precision(previewResult.max_m) +
                    ' 米 · 均方根误差 ' + precision(previewResult.rmse_m) + ' 米'),
                previewResult.rejected.length > 0 && h('small', null, '拟合剔除 ' + previewResult.rejected.join('、')),
                h('small', null, '适用范围 ' + areaText(preview.area, preview.area_kind))),
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
                items: [{ key: 'definition', label: '自定义坐标系库' }, { key: 'plan', label: '转换方案' }],
                onChange: value => { setTab(value); setError(''); } }),
            tab === 'definition' ? h('div', { className: 'coordinate-controls' },
                help('从地图数据读取坐标系定义并保存到库中，供地图工具和转换方案选用。当前地图的显示坐标系在「地图 → 地图属性」中设置。'),
                h(CoordinateFile, { label: '数据文件', action: '从数据导入', layerLabel: '读取坐标系的图层',
                    item: file, busy, change: setFile, pick: () => perform(async () => {
                        const picked = await api.coordinateFile('definition');
                        if (picked) await importFile(picked);
                    }) }),
                help('可直接选择带坐标系定义的 SHP、GeoPackage、GeoJSON、GeoTIFF 或 IMG。SHP 需和同名的 .prj 放在同一文件夹。GeoTIFF 和 IMG 需要栅格处理组件。'),
                file && button('folder', '导入坐标系', { disabled: busy || !file.layer, onClick: () => perform(() => importFile(file)) })) :
                h('div', { className: 'coordinate-controls' },
                    help('转换方案记录转换方向、适用范围和核验误差。保存后可在此转换矢量文件，也可在地图中使用「按方案转换」。'),
                    button('plus', '创建转换方案', { disabled: busy || !definitions.length, onClick: () => setWizard(true) }),
                    !definitions.length && help('创建方案前，请先从转换前、后的数据中导入坐标系。')),
            ...list.map(entry => {
                const technical = entry.plan ? entry.plan.pipeline : entry.definition.original;
                const summary = entry.plan
                    ? [entry.source.name + ' → ' + entry.target.name, '最大检查误差 ' + precision(Math.max(...entry.residuals_m)) + ' m',
                        '允许误差 ' + precision(entry.plan.tolerance_m) + ' 米']
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
                        help('选择处于转换前坐标系和方案适用范围内的矢量图层。转换完成后会打开成果所在文件夹。'),
                        h(CoordinateFile, { label: '矢量文件', layerLabel: '待转换图层', item: applyFile, busy, change: setApplyFile,
                            pick: () => perform(async () => { const picked = await api.coordinateFile('vector'); if (picked) setApplyFile(picked); }) }),
                        button('map', '执行转换', { type: 'primary', disabled: busy || !applyFile?.layer,
                            onClick: () => perform(async () => { await api.coordinateApply(applyFile, ref(entry)); setApplying(null); }) })),
                    editing === ref(entry) && h('div', { className: 'field-row' },
                        h('input', { 'aria-label': '新名称', value: title, maxLength: 120, onChange: event => setTitle(event.target.value) }),
                        button('plus', '保存名称', { disabled: !title.trim() || busy, onClick: () => perform(async () => {
                            await api.coordinateRename({ kind: entry.kind, reference: ref(entry), title });
                            await refresh(); setEditing(null);
                        }) })),
                    h('details', null, h('summary', null, '详情'),
                        entry.plan && h('small', null, '适用范围 ' + areaText(entry.plan.area, entry.plan.area_kind) +
                            ' · ' + (entry.plan.grids.map(grid => grid.name).join('、') || '无需格网')),
                        h('pre', null, technical)));
            }),
            !list.length && help(tab === 'definition' ? '库中暂无自定义坐标系。' : '暂无已保存的转换方案。'),
            error && h('p', { role: 'alert', className: 'error' }, error));
    };
})();
