function conversations(tasks) {
    const byTask = new Map();
    for (let index = tasks.length - 1; index >= 0; index--) {
        const task = tasks[index];
        const group = byTask.get(task.previousTask) || { root: task, turns: [] };
        group.turns.push(task);
        byTask.set(task.id, group);
    }
    return [...new Set(tasks.map(task => byTask.get(task.id)))];
}

module.exports = { conversations };
