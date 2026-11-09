(() => {
    const h = React.createElement;
    window.GuizhiCoordinates = function Coordinates({ entries, setEntries }) {
        const [error, setError] = React.useState('');
        const [busy, setBusy] = React.useState(false);
        async function read(kind) {
            setError('');
            setBusy(true);
            try { setEntries(await window.guizhi.importCoordinate(kind)); }
            catch (error) {
                setError(String(error.message || error).replace(/^Error invoking remote method '[^']+': Error: /, ''));
            } finally { setBusy(false); }
        }
        return h('details', { className: 'coordinate-list', open: entries.length > 0 },
            h('summary', null, '坐标系与转换'),
            h('div', { className: 'coordinate-actions' },
                h('button', { type: 'button', disabled: busy, onClick: () => read('definition') }, '从 SHP 导入坐标系'),
                h('button', { type: 'button', disabled: busy, onClick: () => read('plan') }, '导入转换方案')),
            error && h('p', { role: 'alert', className: 'error' }, error),
            ...entries.map(entry => {
                const plan = entry.plan;
                const summary = plan
                    ? `适用范围 ${plan.area.join('，')} · 标称精度 ${plan.accuracy_m} m · 控制点最大差值 ${Math.max(...entry.residuals_m).toFixed(4)} m`
                    : entry.definition.axes.map(axis => axis.name + ' · ' + axis.unit).join('；');
                return h('details', { key: entry.kind + entry.id + entry.revision },
                    h('summary', null, entry.title + ' · v' + entry.revision),
                    h('p', null, summary),
                    h('pre', null, plan ? entry.source.original + '\n\n' + entry.target.original : entry.definition.original));
            }));
    };
})();
