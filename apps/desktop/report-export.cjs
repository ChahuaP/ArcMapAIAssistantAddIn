const { BrowserWindow } = require('electron');
const fs = require('node:fs');

async function printReport(file) {
    if (fs.statSync(file).size > (8 << 20)) throw new Error('办理报告超出 PDF 导出大小限制');
    const source = fs.readFileSync(file, 'utf8');
    const window = new BrowserWindow({ show: false, webPreferences: { javascript: false, sandbox: true } });
    try {
        const policy = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:">`;
        await window.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(policy + source));
        return await window.webContents.printToPDF({ pageSize: 'A4', printBackground: true });
    } finally { window.destroy(); }
}

module.exports = { printReport };
