import Draw from 'ol/interaction/Draw.js';
import VectorLayer from 'ol/layer/Vector.js';
import VectorSource from 'ol/source/Vector.js';
import { unByKey } from 'ol/Observable.js';
import { getArea, getLength } from 'ol/sphere.js';
import { Fill, Stroke, Style } from 'ol/style.js';

export function measured(geometry, geographic, metresPerUnit) {
    const area = geometry.getType() === 'Polygon';
    const value = geographic ? (area ? getArea : getLength)(geometry, { projection: 'EPSG:4326' }) :
        area ? geometry.getArea() * metresPerUnit ** 2 : geometry.getLength() * metresPerUnit;
    const large = value >= (area ? 1000000 : 1000);
    const unit = area ? (large ? '平方千米' : '平方米') : (large ? '千米' : '米');
    const amount = large ? value / (area ? 1000000 : 1000) : value;
    return amount.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' ' + unit;
}

export function attachMeasurement(map, mode, geographic, metresPerUnit, report) {
    const source = new VectorSource();
    const layer = new VectorLayer({ source, zIndex: 100004, style: new Style({
        stroke: new Stroke({ color: '#0979cf', width: 3 }), fill: new Fill({ color: 'rgba(36,145,230,.17)' }),
    }) });
    // The scratch overlay stays outside the workspace layer collection and map exports.
    layer.setMap(map);
    const draw = new Draw({ type: mode, source, stopClick: true });
    let subscription;
    function stopTracking() {
        if (subscription) unByKey(subscription);
        subscription = null;
    }
    draw.on('drawstart', ({ feature }) => {
        stopTracking();
        source.clear();
        const geometry = feature.getGeometry();
        subscription = geometry.on('change', () => report(measured(geometry, geographic, metresPerUnit)));
        report(measured(geometry, geographic, metresPerUnit));
    });
    draw.on('drawend', ({ feature }) => {
        stopTracking();
        report(measured(feature.getGeometry(), geographic, metresPerUnit));
    });
    map.addInteraction(draw);
    return () => {
        stopTracking();
        map.removeInteraction(draw);
        layer.setMap(null);
        layer.dispose();
        source.clear();
    };
}
