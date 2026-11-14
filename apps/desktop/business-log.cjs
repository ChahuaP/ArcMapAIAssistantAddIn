const fs = require('node:fs');
const path = require('node:path');

const roles = { project: '项目范围', control: '管控范围', input: '输入材料', inputs: '输入图层' };
const materialName = item => item.name || item.file?.split(/[\\/]/).at(-1) || item.layer || '地图图层';
function describeMaterials(groups, definition) {
    const names = Object.fromEntries((definition?.materials || []).map(role => [role.id, role.label]));
    return Object.entries(groups || {}).flatMap(([key, items]) => items.map(item => ({
        role: names[key] || roles[key] || '输入材料', name: materialName(item),
        layer: item.layer && item.layer !== item.name ? item.layer : undefined,
        source: item.ref ? '地图图层' : '本地文件',
    })));
}

function inputReferences(groups, fixed) {
    const entries = Object.entries(groups || {});
    if (!fixed && groups?.inputs) {
        entries.push(...groups.inputs.map((item, index) => ['layer-' + (index + 1), [item]]));
    }
    return Object.fromEntries(entries.map(([role, items]) => [role,
        items.map(item => item.ref?.artifact === 'import' ? item.ref : null)]));
}

const statuses = { succeeded: '已完成', failed: '未完成', interrupted: '已中断', timed_out: '办理超时',
    cancelled: '已取消', no_result: '未生成结果', missing_materials: '待补充材料',
    invalid_materials: '材料不符合要求', budget_exhausted: '超过办理限额' };
const statusLabel = value => statuses[value] || '未完成';
const operator = settings => settings.operator || '未填写';
const confirmation = id => '删除 ' + id.slice(0, 8).toUpperCase();

class BusinessLog {
    constructor(workspace, load, save) {
        this.file = path.join(workspace, 'business-log.json');
        this.load = load;
        this.save = save;
    }
    records() { return this.load(this.file, []); }
    get(id) { return this.records().find(item => item.id === id); }
    add(item) { this.save(this.file, [item, ...this.records().filter(value => value.id !== item.id)]); }
    remove(id, phrase) {
        const items = this.records();
        if (!items.some(item => item.id === id) || phrase !== confirmation(id)) throw new Error('请输入完整的删除确认文字');
        this.save(this.file, items.filter(item => item.id !== id));
    }
    page(requested, now = new Date()) {
        const rows = this.records();
        const month = new Date(now.getFullYear(), now.getMonth(), 1);
        const week = new Date(now.getFullYear(), now.getMonth(), now.getDate() - (now.getDay() + 6) % 7);
        const counts = new Map();
        let monthCount = 0, weekCount = 0;
        for (const item of rows) {
            const date = new Date(item.finishedAt);
            if (date >= month && date <= now) monthCount++;
            if (date >= week && date <= now) weekCount++;
            counts.set(item.type, (counts.get(item.type) || 0) + 1);
        }
        const pageSize = 10;
        const page = Math.max(1, Math.min(Math.ceil(rows.length / pageSize), Number.isInteger(requested) ? requested : 1));
        return { page, pageSize, total: rows.length, week: weekCount, month: monthCount,
            types: [...counts].sort((a, b) => b[1] - a[1]).map(([name, count]) => ({ name, count })),
            items: rows.slice((page - 1) * pageSize, page * pageSize).map(({ id, type, status, finishedAt, operator, statusLabel }) =>
                ({ id, type, status, finishedAt, operator, statusLabel })) };
    }
}

module.exports = { BusinessLog, describeMaterials, inputReferences, statusLabel, operator, confirmation };
