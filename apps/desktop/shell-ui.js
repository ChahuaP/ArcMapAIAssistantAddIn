(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const { Button, ConfigProvider, Drawer, Splitter, theme } = antd;
    const paths = {
        app: 'M3 4h18v16H3zM3 9h18M8 9v11', left: 'M3 4h18v16H3zM9 4v16',
        right: 'M3 4h18v16H3zM15 4v16', back: 'M19 12H5m6-6-6 6 6 6',
        splitView: 'M3 5h7v14H3zm11 0h7v14h-7z',
        plus: 'M12 5v14M5 12h14', send: 'M12 19V5M6 11l6-6 6 6',
        map: 'm3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6Zm6-3v15m6-12v15',
        mapView: 'M3 4h18v16H3zM16 11c0 2-4 6-4 6s-4-4-4-6a4 4 0 1 1 8 0Zm-4-1h.01',
        grid: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
        layout: 'M5 3h14v18H5zM8 6h8v2H8zm0 5h8v4H8zm0 7h5',
        analysis: 'M3 3v18h18M7 16l4-5 4 2 5-6m-4 0h4v4',
        statistics: 'M3 3v18h18M8 16v-4m5 4V8m5 8V5',
        briefcase: 'M3 7h18v13H3zM8 7V4h8v3M3 12c6 3 12 3 18 0m-9 1v3',
        book: 'M5 4h14v17H7a2 2 0 0 1-2-2zm0 15a2 2 0 0 1 2-2h12M9 8h6m-6 4h6',
        workflow: 'M3 9h5v6H3zm13-6h5v5h-5zm0 13h5v5h-5zM8 12h4m4-6h-4v12h4',
        shield: 'M12 3 4 6v6c0 4 4 7 8 9 4-2 8-5 8-9V6zm-3 9 2 2 4-4',
        balance: 'M12 3v17m-5 0h10M4 7h16M6 7l-3 7h6zm12 0-3 7h6z',
        share: 'M6 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm12-6a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm0 12a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM8 11l8-4m-8 6 8 4',
        calendar: 'M4 6h16v14H4zm4-3v5m8-5v5M4 10h16M8 14h2m4 0h2m-8 3h2',
        parcels: 'M3 4h18v16H3zM10 4v16m0-9h11m-5 0v9',
        pencil: 'm14 6 4 4M4 20l4-1 12-12-4-4L4 15z',
        settings: 'M9 4h6l1 3 3 1 2 4-2 4-3 1-1 3H9l-1-3-3-1-2-4 2-4 3-1zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z',
        mic: 'M9 6a3 3 0 0 1 6 0v6a3 3 0 0 1-6 0zm-4 5v1a7 7 0 0 0 14 0v-1m-7 8v3',
        speaker: 'M3 9v6h4l5 4V5L7 9zm13 0a5 5 0 0 1 0 6m2-9a9 9 0 0 1 0 12',
        paperclip: 'm20 11-8 8a5 5 0 0 1-7-7l9-9a3.5 3.5 0 0 1 5 5l-9 9a2 2 0 0 1-3-3l8-8',
        clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM12 7v5l3 2',
        history: 'M3 6v5h5m-5 0a9 9 0 1 1 3 8m6-12v5l3 2',
        refresh: 'M20 4v5h-5M4 20v-5h5M20 9A8 8 0 0 0 6 6m-2 9a8 8 0 0 0 14 3',
        file: 'M6 3h7l5 5v13H6zm7 0v6h5M9 13h6m-6 4h6',
        image: 'M3 4h18v16H3zM3 17l5-5 4 4 4-6 5 5M9 8h.1',
        close: 'm6 6 12 12M18 6 6 18', folder: 'M3 7h7l2 2h9v11H3z',
        folderOpen: 'M3 19V5h7l2 2h7v3m-16 9 4-9h15l-4 9z',
        layers: 'm12 3 9 5-9 5-9-5zm-9 9 9 5 9-5M3 16l9 5 9-5',
        newLayer: 'm3 8 8-4 8 4-8 4zm0 4 8 4m-8 0 8 4m5-5h6m-3-3v6',
        removeLayer: 'm3 8 8-4 8 4-8 4zm0 4 8 4m-8 0 8 4m5-5h6',
        sun: 'M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0zM12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1m0-14-1 1M6 18l-1 1',
        server: 'M3 4h18v7H3zM3 14h18v7H3zM7 7h.1M7 17h.1',
        globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM3 12h18M12 3c-4 4-4 14 0 18 4-4 4-14 0-18z',
        info: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM12 11v5m0-8h.1',
        link: 'M10 14l4-4m-6 6-2 2a3 3 0 0 1-4-4l5-5a3 3 0 0 1 4 0m2 6a3 3 0 0 0 4 0l5-5a3 3 0 0 0-4-4l-2 2',
        trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v6m4-6v6',
        extent: 'M9 4H4v5m11-5h5v5m0 6v5h-5M4 15v5h5M9 9h6v6H9z',
        target: 'M12 3v3m0 12v3M3 12h3m12 0h3m-3 0a6 6 0 1 1-12 0 6 6 0 0 1 12 0Z',
        zoomIn: 'M15.5 15.5 21 21M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0ZM7 10h6m-3-3v6',
        zoomOut: 'M15.5 15.5 21 21M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0ZM7 10h6',
        select: 'm4 4 15 7-7 2-2 7-6-16Z',
        filter: 'M3 5h18l-7 8v5l-4 2v-7z',
        spatialSelect: 'M3 7V4h4m4 0h4v4m-8 7H3v-4m13-1a5 5 0 1 1 0 10 5 5 0 0 1 0-10zm0 3v4m-2-2h4',
        clear: 'M8 4H4v4m12-4h4v4m0 8v4h-4M4 16v4h4m0-12 8 8m0-8-8 8',
        table: 'M3 4h18v16H3zM3 9h18M3 14h18M9 9v11',
        columnAdd: 'M3 4h14v16H3zM3 9h14m-8 0v11m8-5h6m-3-3v6',
        columnRemove: 'M3 4h14v16H3zM3 9h14m-8 0v11m8-5h6',
        sort: 'M4 5h13M4 10h9m-9 5h5m9-3v8m-3-3 3 3 3-3',
        calculator: 'M5 3h14v18H5zM8 7h8m-7 5h.1m5.9 0h.1m-6.1 4h.1m5.9 0h.1',
        add: 'M3 5h7l2 2h9v13H3zM12 10v7m-3.5-3.5h7',
        download: 'M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5',
        area: 'M3 3h18v18H3zM7 17l10-10m-5 0h5v5m-5 5H7v-5',
        buffer: 'M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0Zm5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z',
        intersect: 'M4 4h12v12H4zM8 8h12v12H8z',
        overlayIdentity: 'M3 4h13v13H3zM9 9h12v12H9zm0 4 4 4m-1-8 4 4',
        clip: 'M6 3v15h15M3 6h15v15',
        erase: 'm4 12 8-8a2 2 0 0 1 3 0l5 5a2 2 0 0 1 0 3l-7 7H8l-4-4a2 2 0 0 1 0-3zm4-4 8 8m-3 3h8',
        merge: 'M5 4v3c0 3 2 5 6 5h8M5 20v-3c0-3 2-5 6-5m4-4 4 4-4 4',
        union: 'M4 4h12v4h4v12H8v-4H4z',
        dissolve: 'M3 8h6V4h12v12h-7v4H3zM9 8v2m0 3v2m0 3v2',
        difference: 'M4 4h12v4H8v8H4zm12 4h4v12H8v-4h8z',
        spatialJoin: 'M9 7H4v6h5zm11 4h-5v6h5zM9 10h3v4h3',
        count: 'M5 8h14M5 16h14M10 4 8 20m8-16-2 16',
        node: 'M5 5h4v4H5zm10 10h4v4h-4zM9 9l6 6',
        move: 'M12 2v20M2 12h20M9 5l3-3 3 3M9 19l3 3 3-3M5 9l-3 3 3 3m14-6 3 3-3 3',
        hand: 'M8 13V6a1.5 1.5 0 0 1 3 0v5-7a1.5 1.5 0 0 1 3 0v7-6a1.5 1.5 0 0 1 3 0v7-4a1.5 1.5 0 0 1 3 0v6a7 7 0 0 1-13 4l-3-4a1.5 1.5 0 0 1 2-2z',
        undo: 'M4 5v7h7m-7 0 5-5a6 6 0 1 1 5 10',
        save: 'M4 3h13l3 3v15H4zM8 3v6h8V3m-8 18v-7h8v7',
        chevron: 'm6 9 6 6 6-6',
        check: 'm5 12 4 4L19 6',
        chat: 'M4 5h16v12H9l-5 4zM8 9h8m-8 4h5',
        more: 'M5 12h.1m6.9 0h.1m6.9 0h.1',
        grip: 'M8 5h.1m7.9 0h.1M8 12h.1m7.9 0h.1M8 19h.1m7.9 0h.1',
        box: 'M4 4h5m6 0h5v5m0 6v5h-5m-6 0H4v-5m0-6V4',
        split: 'M4 4h16v16H4zM8 22 16 2',
        copy: 'M8 8h12v12H8zM16 4H4v12',
        cut: 'M8 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm0 8a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm-1-6 14 10M7 14 21 4',
        paste: 'M9 4H5v17h14V4h-4m-6-1h6v4H9zM8 12h8m-8 4h8',
        swatches: 'M4 4h7v7H4zm9 0h7v7h-7zm-9 9h7v7H4zm9 0h7v7h-7zm0 7 7-7',
    };
    const businessIcons = {
        'control-check': 'shield', 'farmland-balance': 'balance', 'shared-delivery': 'share',
        'cad-coordinates': 'node', 'annual-land': 'calendar', 'permit-history': 'file', 'imagery-change': 'image',
    };
    const businessIcon = id => businessIcons[id] || 'briefcase';
    function icon(name, size = 17) {
        const path = paths[name];
        return path ? h('svg', { viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor',
            strokeWidth: name === 'more' || name === 'grip' ? 3 : 1.7,
            strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
        h('path', { d: path })) : null;
    }
    function button(glyph, label, props = {}) {
        return h(Button, { icon: icon(glyph), ...props }, label);
    }
    function iconButton(glyph, label, props = {}) {
        return button(glyph, null, { type: 'text', title: label, 'aria-label': label, ...props });
    }
    const { productName, agentName } = window.GuizhiBrand;
    const brand = (full = false) => h('span', { className: 'brand' + (full ? ' brand-full' : ''), title: full ? productName : undefined },
        h('img', { src: '/mark.svg', alt: '', width: 28 }), h('span', null, full ? productName : agentName));
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
        mode, setMode, activity, onCancel, arcgis, connected, fail, page }) {
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
            ['chat', 'chat', '仅显示对话'],
            ['split', 'splitView', '并排显示'],
            ['map', 'mapView', '仅显示地图'],
        ].map(([value, glyph, label]) => iconButton(glyph, label, { key: value, 'aria-pressed': mode === value,
            className: mode === value ? 'workbench-mode-active' : '', onClick: () => setMode(value) }));
        return h('div', { className: 'desktop-frame' },
            h(Titlebar, { arcgis, connected, left: () => toggle(0), right: inspector && mode === 'chat' && (() => toggle(1)), resize }),
            h(Splitter, { className: 'workbench-panels', lazy: true,
                onResizeEnd: sizes => persist(sideSizes(sizes)) },
                h(Splitter.Panel, { size: left, min: left, max: left, resizable: false }, sidebar(left > 56)),
                h(Splitter.Panel, { min: 0 }, page || h('div', { className: 'workbench-center' },
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
    window.GuizhiShell = { Theme, Workbench, Titlebar, brand, button, iconButton, icon, businessIcon, productName };
})();
