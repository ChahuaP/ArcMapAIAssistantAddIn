(() => {
    const compact = value => String(value ?? '').normalize('NFKC').trim().replace(/[\s。！!？?，、,]+$/u, '');
    const reset = new Set(['新的对话', '重新开始对话', '新建对话', '开始新对话', '开始新的对话',
        '新任务', '开始新任务', '开始新的任务', '结束本轮', '结束这一轮任务', '结束当前任务', '结束任务']);
    const action = value => {
        const command = compact(value);
        if (reset.has(command)) return 'reset';
        if (['发送', '提交任务'].includes(command)) return 'send';
        if (['打开成果', '打开工作台', '打开结果'].includes(command)) return 'workbench';
        return null;
    };
    const normalizeBusiness = text => text.toLowerCase().replace(/\s+/g, '').replace(/[，。！!？?、]+$/u, '');
    const businessPrefix = /^(?:请(?:帮我)?|帮我)?(?:开始|重新)?(?:办理|发起|启动)/u;
    const isBusinessRequest = text => businessPrefix.test(normalizeBusiness(text));
    const reference = item => item.id + '@' + item.revision;
    function matchBusiness(prompt, catalog) {
        const query = normalizeBusiness(prompt);
        if (!query) return { exact: false, items: [] };
        const explicit = query.replace(businessPrefix, '').replace(/业务$/u, '');
        const matches = item => [item.title, ...item.aliases].map(normalizeBusiness);
        const exact = catalog.filter(item => matches(item).includes(explicit));
        if (exact.length) return { exact: exact.length === 1, items: exact };
        return { exact: false, items: catalog.filter(item => matches(item).some(alias => alias.length > 2 && query.includes(alias))) };
    }
    const parametersReady = (fields, values) => Object.keys(fields).every(name => values[name]?.trim());
    const parameterValues = (fields, values) => Object.fromEntries(Object.keys(fields).map(name => {
        const value = values[name].trim();
        try { return [name, JSON.parse(value)]; } catch { return [name, value]; }
    }));
    const commands = { action, parametersReady, parameterValues, normalizeBusiness, isBusinessRequest, reference, matchBusiness };
    if (typeof module === 'object') module.exports = commands;
    else window.GuizhiTaskCommands = commands;
})();
