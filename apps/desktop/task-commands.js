(() => {
    const compact = value => String(value ?? '').normalize('NFKC').trim().replace(/[\s。！!？?，、,]+$/u, '');
    const reset = new Set(['新建对话', '开始新对话', '开始新的对话', '新任务', '开始新任务', '开始新的任务', '结束本轮', '结束这一轮任务', '结束当前任务', '结束任务']);
    const action = value => {
        const command = compact(value);
        if (reset.has(command)) return 'reset';
        if (['发送', '提交任务'].includes(command)) return 'send';
        if (['打开成果', '打开工作台', '打开结果'].includes(command)) return 'workbench';
        return null;
    };
    const parametersReady = (fields, values) => Object.keys(fields).every(name => values[name]?.trim());
    const parameterValues = (fields, values) => Object.fromEntries(Object.keys(fields).map(name => {
        const value = values[name].trim();
        try { return [name, JSON.parse(value)]; } catch { return [name, value]; }
    }));
    if (typeof module === 'object') module.exports = { action, parametersReady, parameterValues };
    else {
        window.GuizhiTaskCommand = action;
        window.GuizhiTaskFields = { parametersReady, parameterValues };
    }
})();
