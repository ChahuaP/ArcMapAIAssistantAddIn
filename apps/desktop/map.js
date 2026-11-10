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

    function draw(item, radius, choose, key, selected) {
        const geometry = item.geometry;
        if (geometry.type === 'GeometryCollection') {
            return geometry.geometries.map((part, i) => draw({ ...item, geometry: part }, radius, choose, key + '-' + i, selected));
        }
        const style = { key, className: item.role + (item.id && item.id === selected ? ' focused-parcel' : ''),
            onClick: item.id ? () => choose(item) : undefined };
        if (geometry.type === 'Point' || geometry.type === 'MultiPoint') {
            const points = geometry.type === 'Point' ? [geometry.coordinates] : geometry.coordinates;
            return points.map(([x, y], i) => h('circle', { ...style, key: key + '-' + i, cx: x, cy: -y,
                r: radius, fill: item.color || '#08766c' }));
        }
        return h('path', { ...style, d: geometryPath(geometry), vectorEffect: 'non-scaling-stroke',
            fillRule: 'evenodd', fill: geometry.type.includes('Line') ? 'none' : item.color,
            stroke: item.color });
    }

    window.GuizhiMap = function ResultMap({ map, choose, selected }) {
        const [zoom, change] = useState(1);
        const [coverage, showCoverage] = useState(true);
        if (!map?.bounds) return h('section', { className: 'map-panel map-empty', 'aria-label': '成果地图' },
            h('div', { className: 'contours', 'aria-hidden': true },
                h('svg', { viewBox: '0 0 350 490' }, ...Array.from({ length: 12 }, (_, i) =>
                    h('ellipse', { key: i, cx: 195, cy: 230, rx: 18 + i * 12, ry: 12 + i * 15,
                        transform: 'rotate(32 195 230)' })))),
            h('div', { className: 'map-caption' }, h('span', { className: 'locator' }, window.GuizhiShell.icon('map')),
                h('h2', null, '暂无空间成果')));
        const [left, bottom, right, top] = map.bounds;
        const span = Math.max(right - left, top - bottom) || 1;
        const width = Math.max(right - left, span * 0.06) * 1.18 / zoom;
        const height = Math.max(top - bottom, span * 0.06) * 1.18 / zoom;
        const box = [(left + right - width) / 2, (-top - bottom - height) / 2, width, height].join(' ');
        return h('section', { className: 'map-panel', 'aria-label': '成果地图' },
            h('div', { className: 'map-head' }, h('h2', null, '成果地图'),
                h('span', null, map.crs)),
            h('svg', { className: 'geo-map', viewBox: box, role: 'img', 'aria-label': '空间成果图形' },
                ...map.features.filter(item => item.role !== 'coverage' || coverage).map((item, index) =>
                    draw(item, Math.max(width, height) * 0.008, choose, index, selected))),
            h('div', { className: 'map-controls' },
                map.features.some(item => item.role === 'coverage') && h('label', null, h('input', { type: 'checkbox', checked: coverage,
                    onChange: event => showCoverage(event.target.checked) }), '重叠区域'),
                h('button', { 'aria-label': '缩小', onClick: () => change(Math.max(0.6, zoom / 1.3)) }, '−'),
                h('button', { 'aria-label': '放大', onClick: () => change(Math.min(8, zoom * 1.3)) }, '+'),
                h('button', { onClick: () => change(1) }, '复位')));
    };
})();
