const recipe = (kind, label, target = '') => ({ kind, label, target });

function visualRecipe(event) {
    if (event.kind === 'dispatch_preparing') return recipe('read', '正在准备材料');
    if (event.kind === 'dispatch') return recipe('think', '正在分析任务');
    if (event.kind === 'plan_reused') return recipe('reuse', '正在复用已验证流程');
    if (event.kind !== 'operation_progress') return null;
    const { phase, details: item } = event;
    if (phase === 'material_reading') return recipe('read', item.layer ? '正在读取图层' : '正在读取材料', item.layer);
    if (phase === 'material_resolved' && item.status === 'succeeded') {
        return recipe(item.reused ? 'reuse' : 'read', item.reused ? '已复用材料' : '已读取图层', item.layer);
    }
    if (phase === 'control_coordinates') return recipe('transform', '正在核对坐标系');
    if (phase === 'control_overlay') return recipe('calculate', '正在计算重叠面积');
    if (phase === 'control_export') return recipe('publish', '正在整理地块结果');
    if (phase === 'operation_verified') return recipe('check', '已校验成果');
    if (phase === 'workflow_publishing') return recipe('publish', '正在保存成果');
    if (phase === 'workflow_step_finished') {
        if (item.status !== 'succeeded') return recipe('stop', '工具步骤未完成');
        return item.reused ? recipe('reuse', '正在复用已验证步骤') : recipe('check', '正在核对步骤成果');
    }
    if (phase !== 'workflow_step_started') return null;
    const { operation, title, layer } = item;
    const domain = operation.split('.')[0];
    const kind = {
        crs: 'transform', geometry: 'draw', semantics: 'link', knowledge: 'link',
        governance: 'check', presentation: 'publish', evidence: 'check', system: 'check',
    }[domain] || 'calculate';
    const specific = {
        'data.describe': 'read', 'data.list_fields': 'read', 'data.export_selection': 'publish',
        'vector.spatial_join': 'link', 'semantics.read_documents': 'read', 'control_check': 'check',
        'imagery.classify': 'draw', 'imagery.change': 'calculate',
    }[operation] || kind;
    return recipe(specific, '正在' + title, layer);
}

module.exports = { visualRecipe };
