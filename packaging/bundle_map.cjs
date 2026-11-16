const fs = require('node:fs');
const path = require('node:path');

const [runtime, output] = process.argv.slice(2).map(value => path.resolve(value));
const modules = path.join(runtime, 'node_modules');
const esbuild = require(path.join(modules, 'esbuild'));
esbuild.build({
    entryPoints: [path.resolve(__dirname, '../apps/desktop/map.js')],
    bundle: true,
    minify: true,
    legalComments: 'linked',
    target: 'chrome140',
    nodePaths: [modules],
    plugins: [{
        name: 'shared-desktop-react',
        setup(builder) {
            builder.onResolve({ filter: /^react(-dom(\/client)?)?$/ }, request => ({ path: request.path, namespace: 'desktop-react' }));
            builder.onLoad({ filter: /.*/, namespace: 'desktop-react' }, request => ({
                contents: 'module.exports = window.' + (request.path === 'react' ? 'React' : 'ReactDOM'),
            }));
        },
    }],
    metafile: true,
    outfile: path.join(output, 'map.js'),
}).then(writeNotices).catch(error => { console.error(error); process.exitCode = 1; });

function writeNotices(build) {
    // Some dependencies ship their notices in separate files rather than source comments.
    const packages = new Set(Object.keys(build.metafile.inputs).flatMap(input => {
        const parts = input.replaceAll('\\', '/').split('/node_modules/').at(-1).split('/');
        if (!input.includes('node_modules')) return [];
        return [parts[0].startsWith('@') ? parts.slice(0, 2).join('/') : parts[0]];
    }));
    const notices = [];
    for (const name of [...packages].sort()) {
        const root = path.join(modules, name);
        for (const file of fs.readdirSync(root).filter(name => /^(licen[cs]e|copying|notice)([.-]|$)/i.test(name))) {
            if (fs.statSync(path.join(root, file)).isFile()) {
                notices.push(name + ' / ' + file + '\n' + fs.readFileSync(path.join(root, file), 'utf8'));
            }
        }
    }
    fs.writeFileSync(path.join(output, 'map-licenses.txt'), notices.join('\n\n'));
}
