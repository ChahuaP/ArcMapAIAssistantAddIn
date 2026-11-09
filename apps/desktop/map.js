(() => {
    const h = React.createElement;
    const { useState } = React;

    function geometryPath(geometry) {
        if (geometry.type === 'GeometryCollection') {
            return geometry.geometries.map(geometryPath).join(' ');
        }
        const line = coordinates => coordinates.map(([x, y], index) => (index ? 'L' : 'M') + x + ' ' + -y).join(' ');
        if (geometry.type === 'LineString') return line(geometry.coordinates);
        if (geometry.type === 'MultiLineString') return geometry.coordinates.map(line).join(' ');
        const polygons = geometry.type === 'Polygon' ? [geometry.coordinates]
            : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
        return polygons.flatMap(polygon => polygon.map(ring =>
            ring.map(([x, y], index) => (index ? 'L' : 'M') + x + ' ' + -y).join(' ') + ' Z')).join(' ');
    }

    function draw(item, radius, choose, key) {
        const geometry = item.geometry;
        if (geometry.type === 'GeometryCollection') {
            return geometry.geometries.map((part, i) => draw({ ...item, geometry: part }, radius, choose, key + '-' + i));
        }
        const style = { key, className: item.role, onClick: item.layer ? () => choose(item) : undefined };
        if (geometry.type === 'Point' || geometry.type === 'MultiPoint') {
            const points = geometry.type === 'Point' ? [geometry.coordinates] : geometry.coordinates;
            return points.map(([x, y], i) => h('circle', { ...style, key: key + '-' + i, cx: x, cy: -y,
                r: radius, fill: item.color || '#08766c' }));
        }
        return h('path', { ...style, d: geometryPath(geometry), vectorEffect: 'non-scaling-stroke',
            fillRule: 'evenodd', fill: geometry.type.includes('Line') ? 'none' : item.color,
            stroke: item.color, strokeWidth: item.layer ? 1.5 : undefined,
            fillOpacity: item.layer ? 0.4 : undefined });
    }

    function MapPanel({ map, choose }) {
        const [zoom, change] = useState(1);
        const [coverage, showCoverage] = useState(true);
        if (!map) return h('section', { className: 'map-panel map-empty', 'aria-label': '地图' },
            h('div', { className: 'contours', 'aria-hidden': true },
                h('svg', { viewBox: '0 0 350 490' }, ...Array.from({ length: 12 }, (_, i) =>
                    h('ellipse', { key: i, cx: 195, cy: 230, rx: 18 + i * 12, ry: 12 + i * 15,
                        transform: 'rotate(32 195 230)' })))),
            h('div', { className: 'map-caption' }, h('span', { className: 'locator' }, '◇'), h('h2', null, '空间预览'),
                h('p', null, '完成核查后，可查看项目范围和重叠区域。')));
        const [left, bottom, right, top] = map.bounds;
        const span = Math.max(right - left, top - bottom) || 1;
        const width = Math.max(right - left, span * 0.06) * 1.18 / zoom;
        const height = Math.max(top - bottom, span * 0.06) * 1.18 / zoom;
        const box = [(left + right - width) / 2, (-top - bottom - height) / 2, width, height].join(' ');
        return h('section', { className: 'map-panel', 'aria-label': '核查成果地图' },
            h('div', { className: 'map-head' }, h('h2', null, map.mode ? '工作区地图' : '核查地图'),
                h('span', null, map.crs)),
            h('svg', { className: 'geo-map', viewBox: box, role: 'img', 'aria-label': '项目范围与重叠区域' },
                ...map.features.filter(item => item.role !== 'coverage' || coverage).map((item, index) =>
                    draw(item, Math.max(width, height) * 0.008, choose, index))),
            h('div', { className: 'map-controls' },
                map.features.some(item => item.role === 'coverage') && h('label', null, h('input', { type: 'checkbox', checked: coverage,
                    onChange: event => showCoverage(event.target.checked) }), '重叠区域'),
                h('button', { 'aria-label': '缩小', onClick: () => change(Math.max(0.6, zoom / 1.3)) }, '−'),
                h('button', { 'aria-label': '放大', onClick: () => change(Math.min(8, zoom * 1.3)) }, '+'),
                h('button', { onClick: () => change(1) }, '复位')));
    }

    window.GuizhiMap = function MapWorkspace({ map, view, onState, fail, enabled }) {
        const [editing, edit] = useState(false);
        const [busy, setBusy] = useState(false);
        const [picked, pick] = useState(null);
        const [pending, chooseMaterial] = useState(null);
        const [title, setTitle] = useState('');
        async function action(name, params, material) {
            setBusy(true);
            try { onState(await window.guizhi.mapAction(name, params, material)); }
            catch (error) { fail(error); }
            finally { setBusy(false); }
        }
        async function add() {
            try {
                const material = await window.guizhi.material();
                if (material?.layer) await action('add_layer', {}, material);
                else if (material) chooseMaterial(material);
            } catch (error) { fail(error); }
        }
        const layers = view?.layers || [];
        const chosen = layers.find(item => item.id === picked?.layer);
        const button = (label, click) => h('button', { type: 'button', disabled: busy, onClick: click }, label);
        return h('div', { className: 'map-workspace' },
            enabled && h('div', { className: 'map-toolbar' },
                button(editing ? '返回成果地图' : '地图工作区', () => edit(!editing)),
                editing && button('添加图层', add),
                editing && layers.length > 0 && button('清空', () => action('clear_layers')),
                editing && view && button(view.mode === 'layout' ? '切回地图' : '切换版面',
                    () => action('set_view_mode', { mode: view.mode === 'layout' ? 'map' : 'layout' }))),
            editing && pending && h('label', { className: 'map-toolbar' }, '图层',
                h('select', { value: '', onChange: event => {
                    void action('add_layer', {}, { ...pending, layer: event.target.value });
                    chooseMaterial(null);
                } }, h('option', { value: '' }, '选择图层'),
                ...pending.layers.map(name => h('option', { key: name }, name)))),
            editing && view?.mode === 'layout' && h('div', { className: 'map-toolbar' },
                h('input', { placeholder: view.text.title, 'aria-label': '版面标题', value: title,
                    onChange: event => setTitle(event.target.value) }),
                button('保存标题', () => action('set_layout_text', { element: 'title', text: title })),
                button('打开版面', () => window.guizhi.layoutReport().catch(fail))),
            h(MapPanel, { key: editing ? view?.id || 'new' : 'result', map: editing ? view?.data : map, choose: pick }),
            editing && layers.length > 0 && h('ul', { className: 'map-layers' },
                ...layers.map((item, index) => h('li', { key: item.id },
                    h('label', null, h('input', { type: 'checkbox', checked: item.visible, disabled: busy,
                        onChange: event => action('set_visibility', { layer_id: item.id, visible: event.target.checked }) }), item.title),
                    button('缩放', () => action('zoom_to_dataset', { layer_id: item.id })),
                    index > 0 && button('置顶', () => action('reorder_layers', { layer_id: item.id, position: 'top' })),
                    button('移除', () => action('remove_layer', { layer_id: item.id })))),
                chosen && h('li', null, button('缩放至选中要素', () => action('zoom_to_selection',
                    { layer_id: chosen.id, selection: { layer: chosen.layer, revision: chosen.dataset_revision, fids: [picked.fid] } })))));
    };
})();
