const path = require('node:path');

const [runtime, output] = process.argv.slice(2).map(value => path.resolve(value));
const esbuild = require(path.join(runtime, 'node_modules', 'esbuild'));
esbuild.buildSync({
    entryPoints: [path.resolve(__dirname, '../apps/desktop/map.js')],
    bundle: true,
    minify: true,
    legalComments: 'linked',
    target: 'chrome140',
    nodePaths: [path.join(runtime, 'node_modules')],
    outfile: path.join(output, 'map.js'),
});
