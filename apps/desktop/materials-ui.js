(() => {
    const h = React.createElement;
    const { useState } = React;

    const kinds = { vector: 'SHP / GeoJSON / GeoPackage', raster: 'TIFF / IMG', cad: 'DXF',
        document: 'PDF / Word / Excel / TXT' };
    const geometry = { point: '点', line: '线', polygon: '面' };
    const materialReady = item => item.kind !== 'vector' || Boolean(item.layer);
    const mergeMaterials = (current, incoming) => [...new Map([...current, ...incoming]
        .map(item => [JSON.stringify([item.file, item.layer]), item])).values()];

    function issue(item, role) {
        if (!materialReady(item)) return '请选择图层';
        if (!role) return '';
        if (item.kind !== role.kind) return '材料格式与该角色不符';
        const info = item.kind === 'vector' ? item.layerInfo?.find(layer => layer.name === item.layer) : item.metadata;
        if (item.kind === 'vector' && role.geometry !== 'any') {
            const family = info?.geometry.toLowerCase().replace(/^multi/, '').split(' ')[0];
            if (family !== 'unknown' && (family === 'linestring' ? 'line' : family) !== role.geometry) {
                return '请选择' + geometry[role.geometry] + '图层';
            }
        }
        if (['vector', 'raster'].includes(item.kind) && role.coordinate !== 'any' && !info?.crs) return '请补齐坐标定义';
        if (['vector', 'raster'].includes(item.kind) && role.coordinate === 'projected_meters' && !info.projected_meters) {
            return '请选择使用米制投影的材料';
        }
        return '';
    }

    function roleReady(role, items = []) {
        return role.resource || (items.length >= role.minimum && items.length <= role.maximum &&
            items.every(item => !issue(item, role)));
    }

    function Material({ item, change, plans = [], role }) {
        const { iconButton, icon } = window.GuizhiShell;
        const name = item.name;
        const problem = issue(item, role);
        const info = item.kind === 'vector' ? item.layerInfo?.find(layer => layer.name === item.layer) : item.metadata;
        const detail = [item.bundle, info && (info.crs || '未定义坐标系'), info?.bands && info.bands + ' 波段',
            item.metadata?.companions?.length ? item.metadata.companions.length + ' 个配套文件' : null].filter(Boolean);
        return h('div', { className: 'material-row' },
            h('div', { className: 'material-file', title: name }, icon('file'), h('span', null, name),
                iconButton('close', '移除 ' + name, { onClick: () => change(null) })),
            item.layers.length > 1 && h('select', { 'aria-label': name + '图层', value: item.layer,
                onChange: event => change({ ...item, layer: event.target.value }) },
                h('option', { value: '' }, '选择图层'), ...item.layers.map(layer => h('option', { key: layer }, layer))),
            item.layer && plans.length > 0 && h('select', { 'aria-label': name + '坐标转换',
                value: item.plan || '', onChange: event => change({ ...item, plan: event.target.value }) },
                h('option', { value: '' }, '沿用坐标'), ...plans.map(plan => {
                    const id = plan.id + '@' + plan.revision;
                    return h('option', { key: id, value: id }, plan.title + ' · v' + plan.revision);
                })),
            problem ? h('small', { className: 'error' }, problem) : detail.length > 0 && h('small', null, detail.join(' · ')));
    }

    async function intake(promise, accept, fail) {
        try {
            const { items, issues } = await promise;
            if (items.length) accept(items);
            if (issues.length) fail(issues.map(item => item.name + ' · ' + item.message).join('\n'));
        } catch (error) { fail(error); }
    }

    function RoleMaterials({ roles, materials, change, plans = [], fail, disabled, picker, onPending }) {
        const api = window.guizhi;
        const { button } = window.GuizhiShell;
        const [pending, setPending] = useState('');
        const [sourceRole, setSourceRole] = useState('');
        return roles.filter(role => !role.resource).map(role => {
            const items = materials[role.id] || [];
            const append = next => {
                const merged = mergeMaterials(items, next);
                if (merged.length > role.maximum) return fail(role.label + '最多添加 ' + role.maximum + ' 份');
                change({ ...materials, [role.id]: merged });
            };
            const add = async source => {
                if (disabled || pending) return;
                setPending(role.id);
                onPending?.(role.id);
                try { await intake(source(), append, fail); }
                finally { setPending(''); onPending?.(''); }
            };
            const addLocal = () => { setSourceRole(''); return add(() => api.material(role.kind)); };
            const mapPicker = picker?.(role, items, next => { append(next); setSourceRole(''); }, Boolean(pending));
            const addButton = button('plus', pending === role.id ? '正在读取材料…' : '添加' + role.label, {
                disabled: disabled || Boolean(pending), loading: pending === role.id, onClick: picker ? undefined : addLocal,
            });
            return h('section', { key: role.id, className: 'role-material',
                onDragOver: event => { event.preventDefault(); event.stopPropagation(); }, onDrop: event => {
                    event.preventDefault(); event.stopPropagation();
                    const files = [...event.dataTransfer.files];
                    if (files.length) void add(() => api.intakeDropped(files, role.kind));
                } },
                h('div', { className: 'section-head' }, h('strong', null, role.label),
                    h('small', null, (role.minimum === 0 ? '选填' : '必填') + ' · ' + items.length + '/' + role.maximum)),
                h('small', null, [role.purpose, kinds[role.kind], role.geometry !== 'any' && geometry[role.geometry] + '图层',
                    role.coordinate === 'projected_meters' && '米制投影'].filter(Boolean).join(' · ')),
                ...items.map((item, index) => h(Material, { key: item.file + '#' + index, item, role,
                    plans: ['project', 'control'].includes(role.id) ? plans : [], change: next => {
                        if (disabled || pending) return;
                        const group = items.slice();
                        if (next) group[index] = next;
                        else group.splice(index, 1);
                        change({ ...materials, [role.id]: group });
                    } })),
                items.length < role.maximum && h('div', { className: 'role-picker' },
                    h('div', null, picker ? h(antd.Dropdown, { trigger: ['click'], menu: { items: [
                        { key: 'local', label: '从本地选择', icon: window.GuizhiShell.icon('folder') },
                        { key: 'map', label: '从地图图层选择', icon: window.GuizhiShell.icon('layers'), disabled: !mapPicker },
                    ], onClick: ({ key }) => key === 'local' ? addLocal() : setSourceRole(role.id) } }, addButton) : addButton),
                    sourceRole === role.id && mapPicker));
        });
    }

    window.GuizhiMaterials = { Material, RoleMaterials, materialReady, roleReady, mergeMaterials, kinds, intake };
})();
