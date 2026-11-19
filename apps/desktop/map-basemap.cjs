const { randomUUID } = require('node:crypto');

function basemaps(net) {
    let current;
    function configure(input) {
        if (input === null) { current = undefined; return null; }
        const { kind, address, name, layers } = input;
        if (!['xyz', 'wms'].includes(kind) || typeof address !== 'string' || address.length > 2048 ||
                typeof name !== 'string' || name.length > 100 ||
                (kind === 'wms' && (typeof layers !== 'string' || !layers.trim() || layers.length > 255))) {
            throw new Error('请填写有效的底图名称和服务信息');
        }
        let parsed;
        try { parsed = new URL(address.trim()); }
        catch { throw new Error('请输入完整的 HTTP 或 HTTPS 服务地址'); }
        if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password ||
                parsed.hash || /[{}]/.test(parsed.host)) throw new Error('请输入 HTTP 或 HTTPS 服务地址');
        if (kind === 'xyz' && !['x', 'y', 'z'].every(part => address.includes('{' + part + '}'))) {
            throw new Error('XYZ 地址必须包含 {z}、{x} 和 {y}');
        }
        const id = randomUUID();
        current = { id, kind, address: address.trim(), parsed };
        return { id, kind, layers: layers?.trim(), name: name.trim() || (kind === 'wms' ? layers.trim() : parsed.hostname) };
    }
    async function tile(request, url) {
        const bad = status => new Response(null, { status });
        const [, , id, suffix] = url.pathname.split('/', 4);
        const layer = current;
        if (!layer || id !== layer.id || request.method !== 'GET') return bad(404);
        let target;
        if (layer.kind === 'xyz') {
            const match = /^(\d{1,2}),(\d{1,9}),(\d{1,9})$/.exec(suffix || '');
            if (!match) return bad(400);
            const [, z, x, y] = match;
            if (+z > 24 || +x >= 2 ** +z || +y >= 2 ** +z) return bad(400);
            target = new URL(layer.address.replaceAll('{z}', z).replaceAll('{x}', x).replaceAll('{y}', y));
        } else {
            if (suffix) return bad(400);
            const allowed = ['SERVICE', 'REQUEST', 'VERSION', 'FORMAT', 'TRANSPARENT', 'LAYERS', 'STYLES',
                'WIDTH', 'HEIGHT', 'CRS', 'SRS', 'BBOX'];
            target = new URL(layer.parsed);
            for (const key of [...target.searchParams.keys()]) {
                if (allowed.includes(key.toUpperCase())) target.searchParams.delete(key);
            }
            for (const [key, value] of url.searchParams) {
                if (allowed.includes(key.toUpperCase())) target.searchParams.set(key.toUpperCase(), value);
            }
            if (target.searchParams.get('REQUEST') !== 'GetMap' || !target.searchParams.get('LAYERS') ||
                    !['WIDTH', 'HEIGHT'].every(key => /^\d{1,4}$/.test(target.searchParams.get(key) || '') &&
                    Number(target.searchParams.get(key)) <= 1024)) return bad(400);
        }
        try {
            const upstream = await net.fetch(target.href, { credentials: 'omit', redirect: 'error',
                signal: AbortSignal.any([request.signal, AbortSignal.timeout(10000)]) });
            const type = upstream.headers.get('content-type')?.split(';')[0]?.toLowerCase();
            if (!upstream.ok || !['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(type)) {
                await upstream.body?.cancel();
                return bad(502);
            }
            const parts = [];
            let size = 0;
            for await (const part of upstream.body) {
                size += part.byteLength;
                if (size > 8 * 1024 * 1024) return bad(502);
                parts.push(part);
            }
            return new Response(Buffer.concat(parts), { headers: { 'Content-Type': type, 'Cache-Control': 'private, max-age=300' } });
        } catch { return bad(502); }
    }
    return { configure, tile };
}

module.exports = { basemaps };
