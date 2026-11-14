const { BrowserWindow } = require('electron');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { productName } = require('./brand.js');

const escape = value => String(value ?? '—').replace(/[&<>"']/g, char =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const time = value => new Date(value).toLocaleString('zh-CN', { hour12: false });
const row = (values, tag = 'td') => '<tr>' + values.map(value => '<' + tag + '>' + escape(value) + '</' + tag + '>').join('') + '</tr>';
function table({ title, columns, rows }) {
    return '<h2>' + escape(title) + '</h2><table><thead>' + row(columns, 'th') + '</thead><tbody>' +
        rows.map(values => row(values)).join('') + '</tbody></table>';
}

async function publishReport(directory, item, document) {
    const target = path.join(directory, 'business-report.pdf');
    const facts = [['办理编号', item.id], ['办理人', item.operator], ['开始时间', time(item.startedAt)], ['办结时间', time(item.finishedAt)]];
    const materials = item.materials.map(value => [value.role, value.layer ? value.name + ' · ' + value.layer : value.name, value.source]);
    const source = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy"
        content="default-src 'none'; style-src 'unsafe-inline'"><title>${escape(document.title)}办理报告</title>
        <style>
        @page { size:A4; margin:22mm 20mm; }
        body{margin:0;color:#111;font:11pt/1.62 'Microsoft YaHei',sans-serif;overflow-wrap:anywhere;}
        header{text-align:center;padding-bottom:10px;border-bottom:2px solid #111;}
        header p{margin:0;color:#333;font-size:10pt;} h1{font-size:22pt;margin:6px 0 0;line-height:1.4;}
        h2{font-size:12pt;margin:17px 0 6px;color:#111;break-after:avoid;}
        table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:10pt;}
        thead{display:table-header-group;} tr{break-inside:avoid;} th,td{border:1px solid #999;padding:6px 9px;text-align:left;vertical-align:top;}
        th{background:#eee;color:#111;} .facts td:first-child{width:20%;background:#f6f6f6;}
        .note{margin:6px 0;white-space:pre-wrap;}
        </style><header><p>${escape(productName)}</p><h1>${escape(document.title)}办理报告</h1></header>
        <h2>办理信息</h2><table class="facts"><tbody>${facts.map(values => row(values)).join('')}</tbody></table>
        ${table({ title: '输入材料', columns: ['材料用途', '材料名称', '来源'], rows: materials.length ? materials : [['—', '无独立输入材料', '—']] })}
        ${document.note ? '<h2>办理说明</h2><p class="note">' + escape(document.note) + '</p>' : ''}
        ${document.tables.map(table).join('')}</html>`;
    const report = new BrowserWindow({ show: false, webPreferences: { javascript: false, sandbox: true } });
    try {
        await report.loadURL('data:text/html;charset=utf-8;base64,' + Buffer.from(source).toString('base64'));
        const bytes = await report.webContents.printToPDF({ pageSize: 'A4', preferCSSPageSize: true,
            printBackground: true, displayHeaderFooter: true, headerTemplate: '<span></span>',
            footerTemplate: '<div style="width:100%;text-align:center;font-size:9px;color:#333"><span class="pageNumber"></span> / <span class="totalPages"></span></div>' });
        const pending = target + '.pending';
        fs.writeFileSync(pending, bytes, { flag: 'wx' });
        fs.renameSync(pending, target);
        return createHash('sha256').update(bytes).digest('hex');
    } finally { report.destroy(); }
}

module.exports = { publishReport };
