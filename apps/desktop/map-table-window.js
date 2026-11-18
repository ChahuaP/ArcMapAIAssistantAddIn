const { useEffect, useLayoutEffect, useRef, useState } = window.React;
const h = window.React.createElement;

// The native window hosts the existing React subtree. All layer, edit and selection
// state stays in the workspace, including when the table is moved back to its dock.
export function useTableWindow(visible, layerName, closeTable, fail) {
    const [host, setHost] = useState(null);
    const child = useRef(null);
    const sequence = useRef(0);
    const onClose = useRef(closeTable);
    onClose.current = closeTable;

    function dock() {
        const opened = child.current;
        child.current = null;
        setHost(null);
        if (opened && !opened.closed) opened.close();
    }

    function detach(at) {
        if (child.current && !child.current.closed) { child.current.focus(); return; }
        const bounds = at ? ',left=' + Math.round(at.x - 80) + ',top=' + Math.round(at.y - 15) : '';
        const opened = window.open('about:blank', 'guizhi-attribute-table-' + (++sequence.current), 'popup,width=960,height=460' + bounds);
        if (!opened) { fail(new Error('无法打开属性表窗口')); return; }
        child.current = opened;
        const doc = opened.document;
        doc.title = '属性表 · ' + layerName;
        doc.documentElement.lang = 'zh-CN';
        doc.body.className = 'attribute-window';
        const root = doc.createElement('main');
        root.className = 'attribute-window-root';
        doc.body.append(root);
        setHost(root);
    }

    useEffect(() => {
        if (!visible && child.current) dock();
    }, [visible]);
    useEffect(() => () => {
        const opened = child.current;
        child.current = null;
        if (opened && !opened.closed) opened.close();
    }, []);
    useEffect(() => window.guizhi.onAttributeWindowClosed(name => {
        if (child.current && name === 'guizhi-attribute-table-' + sequence.current) {
            child.current = null;
            setHost(null);
            onClose.current();
        }
    }), []);
    useEffect(() => {
        if (host) host.ownerDocument.title = '属性表 · ' + layerName;
    }, [host, layerName]);

    useLayoutEffect(() => {
        if (!host) return;
        const doc = host.ownerDocument;
        const nodes = new Map();
        function copyTheme() { doc.documentElement.dataset.theme = document.documentElement.dataset.theme; }
        function copyStyles() {
            const sources = [...document.head.querySelectorAll('style,link[rel="stylesheet"]')];
            for (const [source, copy] of nodes) {
                if (!source.isConnected) { copy.remove(); nodes.delete(source); }
            }
            let previous = null;
            for (const source of sources) {
                let copy = nodes.get(source);
                if (!copy) {
                    copy = source.cloneNode(true);
                    if (source.nonce) copy.nonce = source.nonce;
                    if (source.href) copy.href = source.href;
                    nodes.set(source, copy);
                } else if (source.tagName === 'STYLE' && copy.textContent !== source.textContent) {
                    copy.textContent = source.textContent;
                }
                if (copy.parentNode !== doc.head || copy.previousElementSibling !== previous) {
                    doc.head.insertBefore(copy, previous ? previous.nextSibling : doc.head.firstChild);
                }
                previous = copy;
            }
        }
        copyStyles();
        copyTheme();
        const styles = new MutationObserver(copyStyles);
        styles.observe(document.head, { childList: true, subtree: true, characterData: true });
        const theme = new MutationObserver(copyTheme);
        theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
        return () => { styles.disconnect(); theme.disconnect(); };
    }, [host]);

    const start = useRef(null);
    const dragHandle = {
        onPointerDown(event) {
            if (host || event.button !== 0) return;
            start.current = { x: event.screenX, y: event.screenY };
            event.currentTarget.setPointerCapture(event.pointerId);
        },
        onPointerUp(event) {
            const origin = start.current;
            start.current = null;
            if (origin && Math.hypot(event.screenX - origin.x, event.screenY - origin.y) >= 10) {
                detach({ x: event.screenX, y: event.screenY });
            }
        },
        onLostPointerCapture() { start.current = null; },
    };
    const container = host ? () => host.ownerDocument.body : undefined;
    function present(content, inTable = true) {
        if (!host || !inTable) return content;
        return window.ReactDOM.createPortal(h(window.antd.ConfigProvider, { getPopupContainer: container }, content), host);
    }
    return { detached: Boolean(host), dock, detach, dragHandle, container, present };
}
