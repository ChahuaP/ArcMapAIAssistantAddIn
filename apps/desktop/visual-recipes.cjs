const recipe = (kind, label, target = '') => ({ kind, label, target });

function visualRecipe(event) {
    if (event.kind === 'session_event' && event.type === 'tool/call') {
        const labels = {
            inspect_current_map: '正在核对当前地图',
            read_current_map_image: '正在查看当前地图画面',
            describe_bound_layers: '正在读取图层信息',
            lookup_layer_field: '正在查询属性值',
            sum_layer_area: '正在汇总面积',
            inspect_layer_crs: '正在核对图层坐标系',
        };
        const label = labels[event.tool?.replace(/^mcp__guizhi__/, '')];
        return label ? recipe('read', label) : null;
    }
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
    if (phase === 'workflow_step_finished' && item.status !== 'succeeded') return recipe('stop', '工具步骤未完成');
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
