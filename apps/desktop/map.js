(() => {
    const h = React.createElement;
    const { useState } = React;

    function geometryPath(geometry) {
        if (geometry.type === 'GeometryCollection') {
            return geometry.geometries.map(geometryPath).join(' ');
        }
        const polygons = geometry.type === 'Polygon' ? [geometry.coordinates]
            : geometry.type === 'MultiPolygon' ? geometry.coordinates : [];
        return polygons.flatMap(polygon => polygon.map(ring =>
            ring.map(([x, y], index) => (index ? 'L' : 'M') + x + ' ' + -y).join(' ') + ' Z')).join(' ');
    }

    window.GuizhiMap = function MapPanel({ map }) {
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
        const width = (right - left || 1) * 1.18 / zoom;
        const height = (top - bottom || 1) * 1.18 / zoom;
        const box = [(left + right - width) / 2, (-top - bottom - height) / 2, width, height].join(' ');
        return h('section', { className: 'map-panel', 'aria-label': '核查成果地图' },
            h('div', { className: 'map-head' }, h('h2', null, '核查地图'),
                h('span', null, map.crs)),
            h('svg', { className: 'geo-map', viewBox: box, role: 'img', 'aria-label': '项目范围与重叠区域' },
                ...map.features.filter(item => item.role !== 'coverage' || coverage).map((item, index) =>
                    h('path', { key: index, className: item.role, d: geometryPath(item.geometry),
                        vectorEffect: 'non-scaling-stroke', fillRule: 'evenodd' }))),
            h('div', { className: 'map-controls' },
                h('label', null, h('input', { type: 'checkbox', checked: coverage,
                    onChange: event => showCoverage(event.target.checked) }), '重叠区域'),
                h('button', { 'aria-label': '缩小', onClick: () => change(Math.max(0.6, zoom / 1.3)) }, '−'),
                h('button', { 'aria-label': '放大', onClick: () => change(Math.min(8, zoom * 1.3)) }, '+'),
                h('button', { onClick: () => change(1) }, '复位')));
    };
})();
