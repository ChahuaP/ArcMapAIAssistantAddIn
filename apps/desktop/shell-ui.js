(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const { Button, ConfigProvider, Drawer, Splitter, theme } = antd;
    const paths = {
        app: 'M3 4h18v16H3zM3 9h18M8 9v11', left: 'M3 4h18v16H3zM9 4v16',
        right: 'M3 4h18v16H3zM15 4v16', back: 'M19 12H5m6-6-6 6 6 6',
        plus: 'M12 5v14M5 12h14', send: 'M12 19V5M6 11l6-6 6 6',
        map: 'm3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6Zm6-3v15m6-12v15',
        grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
        settings: 'M9 4h6l1 3 3 1 2 4-2 4-3 1-1 3H9l-1-3-3-1-2-4 2-4 3-1zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z',
        mic: 'M9 6a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0zm-4 5v1a7 7 0 0 0 14 0v-1m-7 8v3',
        speaker: 'M3 9v6h4l5 4V5L7 9zm13 0a5 5 0 0 1 0 6m2-9a9 9 0 0 1 0 12',
        paperclip: 'm20 11-8 8a5 5 0 0 1-7-7l9-9a3.5 3.5 0 0 1 5 5l-9 9a2 2 0 0 1-3-3l8-8',
        clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM12 7v5l3 2',
        file: 'M6 3h7l5 5v13H6zm7 0v6h5M9 13h6m-6 4h6',
        close: 'm6 6 12 12M18 6 6 18', folder: 'M3 7h7l2 2h9v11H3z',
        layers: 'm12 3 9 5-9 5-9-5zm-9 9 9 5 9-5M3 16l9 5 9-5',
        sun: 'M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0zM12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1m0-14-1 1M6 18l-1 1',
        server: 'M3 4h18v7H3zM3 14h18v7H3zM7 7h.1M7 17h.1',
        globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM3 12h18M12 3c-4 4-4 14 0 18 4-4 4-14 0-18z',
        info: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM12 11v5m0-8h.1',
        link: 'M10 14l4-4m-6 6-2 2a3 3 0 0 1-4-4l5-5a3 3 0 0 1 4 0m2 6a3 3 0 0 0 4 0l5-5a3 3 0 0 0-4-4l-2 2',
        trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v6m4-6v6',
    };
    function icon(name) {
        return h('svg', { viewBox: '0 0 24 24', width: 17, height: 17, fill: 'none', stroke: 'currentColor',
            strokeWidth: 1.7, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
        h('path', { d: paths[name] }));
    }
    function button(glyph, label, props = {}) {
        return h(Button, { icon: icon(glyph), ...props }, label);
    }
    function iconButton(glyph, label, props = {}) {
        return button(glyph, null, { type: 'text', title: label, 'aria-label': label, ...props });
    }
    const productName = '南京市规划和自然资源局六合分局智慧治理系统';
    const brand = (full = false) => h('span', { className: 'brand' + (full ? ' brand-full' : ''), title: full ? productName : undefined },
        h('img', { src: '/mark.svg', alt: '', width: 28 }), h('span', null, full ? productName : '规智'));
    function Theme({ appearance, children }) {
        const dark = appearance.dark;
        useEffect(() => { document.documentElement.dataset.theme = dark ? 'dark' : 'light'; }, [dark]);
        return h(ConfigProvider, { button: { autoInsertSpace: false },
            csp: { nonce: document.querySelector('meta[name=style-nonce]').content },
            theme: { algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm,
                token: { colorPrimary: dark ? '#8ec9ff' : '#086ca9', colorText: dark ? '#eef3fa' : '#152235',
                    colorBgContainer: dark ? '#172333' : '#ffffff', borderRadius: 8, controlHeight: 40,
                    fontFamily: '"Segoe UI","Microsoft YaHei UI",sans-serif' },
                components: { Segmented: { trackBg: dark ? '#131e2d' : '#eef3f9',
                    itemSelectedBg: dark ? '#263c55' : '#fff', itemColor: dark ? '#bac8d9' : '#506176' },
                Tabs: { horizontalItemGutter: 24, inkBarColor: dark ? '#8ec9ff' : '#086ca9' } } } }, children);
    }
    function Titlebar({ settings, onboarding, back, left, right, arcgis, connected, resize }) {
        return h('header', { className: 'titlebar' },
            h('div', { className: 'title-group' },
                !onboarding && (settings ? iconButton('back', '返回工作台', { onClick: back })
                    : iconButton('left', '折叠或展开导航栏', { onClick: left, onKeyDown: resize(0) })),
                brand(true), settings && h('span', { className: 'title-path' }, '设置')),
            !settings && !onboarding && h('div', { className: 'title-group' },
                button('link', '第三方扩展', { type: 'text', onClick: arcgis,
                    title: connected ? 'ArcGIS 已连接' : '查看第三方扩展' }),
                right && iconButton('right', '折叠或展开材料与成果', { onClick: right, onKeyDown: resize(1) })));
    }

    function Workbench({ appearance, saveAppearance, sidebar, conversation, inspector, map, composer,
        mode, setMode, activity, onCancel, arcgis, connected, fail }) {
        const [width, setWidth] = useState(innerWidth);
        const [panels, setPanels] = useState(appearance.panels);
        const [drawer, showDrawer] = useState(false);
        const [split, setSplit] = useState(32);
        const restored = useRef(appearance.expandedPanels || [56, 362]);
        useEffect(() => { setPanels(appearance.panels); }, [appearance.panels]);
        useEffect(() => {
            if (appearance.expandedPanels) restored.current = appearance.expandedPanels;
        }, [appearance.expandedPanels]);
        useEffect(() => {
            const resize = () => setWidth(innerWidth);
            addEventListener('resize', resize);
            return () => removeEventListener('resize', resize);
        }, []);
        const compact = width < 1040;
        const left = panels[0] >= 160 ? 176 : 56;
        const right = !inspector || compact || !panels[1] || mode !== 'chat' ? 0 : Math.min(panels[1], Math.max(250, width - left - 410));
        function persist(next) {
            setPanels(next);
            next.forEach((size, index) => { if (size > 0) restored.current[index] = size; });
            saveAppearance({ panels: next }).catch(fail);
        }
        function toggle(index) {
            if (index === 1 && compact) return showDrawer(value => !value);
            const next = [...panels];
            next[index] = index === 0 ? (left === 56 ? 176 : 56) : next[index] ? 0 : restored.current[index];
            persist(next);
        }
        function resize(index) {
            return event => {
                if (index === 0 || compact || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
                event.preventDefault();
                const next = [...panels];
                const direction = (event.key === 'ArrowRight' ? 1 : -1) * (index === 0 ? 1 : -1);
                next[index] = Math.min(520, Math.max(250, (next[index] || 250) + direction * 16));
                persist(next);
            };
        }
        const sideSizes = sizes => [left, compact || !inspector || mode !== 'chat' ? panels[1] : right ? Math.round(sizes[2]) : 0];
        const mapVisible = mode !== 'chat';
        const chatVisible = mode !== 'map';
        const modeButtons = [
            ['chat', 'left', '仅显示对话'],
            ['split', 'app', '并排显示'],
            ['map', 'right', '仅显示地图'],
        ].map(([value, glyph, label]) => iconButton(glyph, label, { key: value, 'aria-pressed': mode === value,
            className: mode === value ? 'workbench-mode-active' : '', onClick: () => setMode(value) }));
        return h('div', { className: 'desktop-frame' },
            h(Titlebar, { arcgis, connected, left: () => toggle(0), right: inspector && mode === 'chat' && (() => toggle(1)), resize }),
            h(Splitter, { className: 'workbench-panels', lazy: true,
                onResizeEnd: sizes => persist(sideSizes(sizes)) },
                h(Splitter.Panel, { size: left, min: left, max: left, resizable: false }, sidebar(left > 56)),
                h(Splitter.Panel, { min: 0 }, h('div', { className: 'workbench-center' },
                    h('div', { className: 'workbench-modes', 'aria-label': '工作区布局' }, ...modeButtons),
                    h(Splitter, { className: 'workbench-content', lazy: true,
                        onResizeEnd: ([a, b]) => { if (a && b) setSplit(a / (a + b) * 100); } },
                        h(Splitter.Panel, { size: chatVisible ? mapVisible ? split + '%' : '100%' : 0,
                            min: chatVisible && mapVisible ? 270 : 0, resizable: chatVisible && mapVisible }, chatVisible && conversation),
                        h(Splitter.Panel, { size: mapVisible ? chatVisible ? (100 - split) + '%' : '100%' : 0,
                            min: mapVisible && chatVisible ? 420 : 0, resizable: chatVisible && mapVisible }, map)),
                    !chatVisible && h('div', { className: 'workbench-chat-dock' }, composer))),
                h(Splitter.Panel, { size: right, min: right ? 250 : 0, max: 520, resizable: Boolean(right) }, right > 0 && inspector)),
            inspector && compact && mode === 'chat' && h(Drawer, { title: '材料与成果', width: 360, getContainer: false,
                rootStyle: { position: 'absolute' }, open: drawer, onClose: () => showDrawer(false) }, inspector),
            activity && h('div', { className: 'workbench-ai-edge', 'aria-hidden': true }),
            activity && h('div', { className: 'workbench-ai-status', role: 'status' },
                icon('layers'), h('span', null, activity.label),
                activity.target && h('strong', null, activity.target),
                button('close', '接管', { size: 'small', onClick: onCancel })));
    }
    window.GuizhiShell = { Theme, Workbench, Titlebar, brand, button, iconButton, icon, productName };
})();
