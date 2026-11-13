// Rewrite only module specifiers in the consumers of a shared, byte-identical Zod.
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');

const [source, target] = process.argv.slice(2);
const buildRequire = createRequire(path.join(source, 'package.json'));
const { parse } = buildRequire('acorn');
const { simple } = buildRequire('acorn-walk');
const owners = JSON.parse(fs.readFileSync(0, 'utf8'));

function rewrite(file, alias) {
    const text = fs.readFileSync(file, 'utf8');
    if (!text.includes('zod')) return;
    const tree = parse(text, { ecmaVersion: 'latest', sourceType: file.endsWith('.cjs') ? 'commonjs' : 'module' });
    const changes = [];
    const references = new Set();
    function isZod(node) {
        return typeof node?.value === 'string' && /^zod(?:\/|$)/.test(node.value);
    }
    function moduleName(node) {
        if (!isZod(node)) return;
        changes.push({ start: node.start, end: node.end, value: JSON.stringify(alias + node.value.slice(3)) });
    }
    simple(tree, {
        Literal(node) {
            if (isZod(node)) references.add(node.start);
        },
        ImportDeclaration: node => moduleName(node.source),
        ExportNamedDeclaration: node => moduleName(node.source),
        ExportAllDeclaration: node => moduleName(node.source),
        ImportExpression: node => moduleName(node.source),
        CallExpression(node) {
            const { callee } = node;
            if (callee.name === 'require' || (callee.object?.name === 'require' && callee.property?.name === 'resolve') ||
                    (callee.object?.type === 'MetaProperty' && callee.property?.name === 'resolve')) {
                moduleName(node.arguments[0]);
            }
        },
    });
    for (const change of changes) references.delete(change.start);
    if (references.size) throw new Error(`Unrecognized Zod reference in ${file}`);
    let output = text;
    for (const { start, end, value } of changes.sort((a, b) => b.start - a.start)) {
        output = output.slice(0, start) + value + output.slice(end);
    }
    if (changes.length) fs.writeFileSync(file, output);
}

function rewritePackage(root, alias) {
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
        if (entry.name === 'node_modules') continue;
        const file = path.join(root, entry.name);
        if (entry.isDirectory()) rewritePackage(file, alias);
        else if (/\.(?:mjs|cjs|js)$/.test(entry.name)) rewrite(file, alias);
    }
}

for (const [owner, alias] of Object.entries(owners)) {
    const root = path.join(target, owner);
    rewritePackage(root, alias);
    const file = path.join(root, 'package.json');
    const manifest = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!Object.hasOwn(manifest.dependencies || {}, 'zod')) throw new Error(`Missing Zod dependency in ${owner}`);
    manifest.dependencies[alias] = manifest.dependencies.zod;
    delete manifest.dependencies.zod;
    fs.writeFileSync(file, JSON.stringify(manifest, null, 2));
}
