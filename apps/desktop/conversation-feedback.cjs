const { randomUUID } = require('node:crypto');
const https = require('node:https');
const { redactCredentials } = require('./diagnostics-upload.cjs');
const { maxConversationBytes } = require('./conversation-schema.cjs');

const endpoint = 'https://123.56.108.57:8443/guizhi/v1/conversations';
const label = { answered: '已答复', succeeded: '已完成', failed: '未完成', cancelled: '已停止',
    interrupted: '已中断', timed_out: '已超时', missing_materials: '待补材料',
    invalid_materials: '材料有误', no_result: '待补信息', budget_exhausted: '达到限额' };

function prepareConversation(tasks, version) {
    if (!tasks.length) throw new Error('本次会话暂无内容');
    const text = tasks.slice().reverse().map((task, index) => {
        const materials = [task.project, task.control, ...Object.values(task.materials || {}).flat(),
            ...(task.images || [])].filter(Boolean);
        const names = [...new Set(materials.map(item => (item.name || item.file || '').split(/[\\/]/).pop()).filter(Boolean))];
        const steps = (task.progress || []).map(step => [step.label, step.target].filter(Boolean).join(' · '));
        return ['第 ' + (index + 1) + ' 轮 · ' + task.createdAt + ' · ' + (label[task.status] || '未完成'),
            '用户\n' + task.prompt,
            task.response && '小六\n' + task.response,
            names.length > 0 && '附件名称\n' + names.join('\n'),
            steps.length > 0 && '办理过程\n' + steps.join('\n'),
            task.runId && '运行编号 ' + task.runId,
            task.diagnosticId && '故障编号 ' + task.diagnosticId,
        ].filter(Boolean).join('\n\n');
    }).join('\n\n————————————\n\n');
    const preview = { schema: 1, id: randomUUID(), time: new Date().toISOString(), version,
        turns: tasks.length, text: redactCredentials(text) };
    const body = Buffer.from(JSON.stringify(preview));
    if (body.length > maxConversationBytes) throw new Error('本次会话超过 2 MB，无法完整上传');
    return { preview, body };
}

function uploadConversation(body, url = endpoint) {
    return new Promise((resolve, reject) => {
        const request = https.request(url, { method: 'POST', signal: AbortSignal.timeout(20000),
            headers: { 'content-type': 'application/json', 'content-length': body.length } }, response => {
            response.resume();
            if (response.statusCode === 202) resolve();
            else if (response.statusCode === 429) reject(new Error('发送过于频繁，请稍后重试'));
            else reject(new Error('服务器未接收会话，请稍后重试'));
        });
        request.once('error', () => reject(new Error('未能连接会话上传服务，请检查网络后重试')));
        request.end(body);
    });
}

module.exports = { prepareConversation, uploadConversation };
