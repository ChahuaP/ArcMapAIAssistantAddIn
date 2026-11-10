const path = require('node:path');

const formats = {
    vector: { name: '矢量数据', extensions: ['shp', 'gpkg', 'geojson'] },
    raster: { name: '影像和栅格', extensions: ['tif', 'tiff', 'img'] },
    cad: { name: 'CAD 图纸', extensions: ['dxf', 'dwg'] },
    document: { name: '审批文档', extensions: ['pdf', 'docx', 'xlsx', 'txt', 'doc', 'xls'] },
};
const kinds = new Map(Object.entries(formats).flatMap(([kind, group]) =>
    group.extensions.map(extension => ['.' + extension, kind])));

function materialBindings(groups) {
    if (!groups || typeof groups !== 'object' || Array.isArray(groups)) throw new Error('材料分组无效');
    if (!Object.values(groups).every(Array.isArray) || Object.values(groups).flat().length > 32) {
        throw new Error('一次最多添加 32 份材料');
    }
    return Object.fromEntries(Object.entries(groups).filter(([, items]) => items.length).map(([role, items]) =>
        [role, items.map(item => {
            const kind = typeof item?.file === 'string' && kinds.get(path.extname(item.file).toLowerCase());
            if (!kind || !path.isAbsolute(item.file) ||
                    (kind === 'vector' ? !item.layers?.includes(item.layer) : item.layer !== null)) {
                throw new Error('请补齐材料文件及矢量图层');
            }
            if (item.revision && !/^[a-f0-9]{64}$/.test(item.revision)) throw new Error('材料快照版本无效');
            return { file: item.file, layer: item.layer, ...(item.revision ? { revision: item.revision } : {}) };
        })]));
}

module.exports = { formats, materialBindings };
