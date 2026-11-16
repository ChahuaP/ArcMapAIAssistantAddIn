(() => {
    const h = React.createElement;
    const { useEffect, useState } = React;
    const { Dropdown, Modal } = antd;
    const { icon, iconButton } = window.GuizhiShell;
    const api = window.guizhi;

    const sources = [
        ['layer', '地图图层', 'layers'],
        ['material', '材料', 'file'],
        ['image', '图片', 'image'],
    ];

    function ImageAttachments({ images, remove }) {
        const [preview, setPreview] = useState(null);
        const [full, setFull] = useState(null);
        useEffect(() => {
            if (!preview) return;
            let current = true;
            api.conversationImage(preview.id).then(src => { if (current) setFull(src); })
                .catch(error => console.error('图片预览：', error.message));
            return () => { current = false; };
        }, [preview]);
        return h(React.Fragment, null,
            h('div', { className: 'composer-images', 'aria-label': '图片附件' },
                ...images.map(image => h('div', { key: image.id, className: 'composer-image-tile' },
                    h('button', { type: 'button', className: 'composer-image-open', title: image.name,
                        'aria-label': '预览 ' + image.name, onClick: () => { setFull(null); setPreview(image); } },
                        image.thumbnail ? h('img', { src: image.thumbnail, alt: '' }) : icon('image'),
                        h('span', null, image.name)),
                    remove && h('button', { type: 'button', className: 'composer-image-remove',
                        'aria-label': '移除 ' + image.name, title: '移除图片', onClick: () => remove(image) }, icon('close'))))),
            preview && h(Modal, { open: true, title: preview.name, width: 840, centered: true, footer: null,
                onCancel: () => setPreview(null) },
                h('div', { className: 'composer-image-preview' },
                    (full || preview.thumbnail) && h('img', { src: full || preview.thumbnail, alt: preview.name }))));
    }

    function MentionList({ items, activeIndex, setIndex, choose }) {
        useEffect(() => {
            document.getElementById('composer-reference-' + activeIndex)?.scrollIntoView({ block: 'nearest' });
        }, [activeIndex]);
        return h('div', { id: 'composer-references', className: 'composer-mentions', role: 'listbox', 'aria-label': '引用已有内容' },
            !items.length && h('p', { className: 'composer-mentions-empty' }, '没有匹配的图层、材料或图片'),
            ...sources.map(([type, title, glyph]) => {
                const group = items.filter(item => item.type === type);
                return group.length > 0 && h('div', { key: type, role: 'group', 'aria-label': title },
                    h('div', { className: 'composer-mention-group', 'aria-hidden': true }, title),
                    ...group.map(item => {
                        const index = items.indexOf(item);
                        return h('button', { key: item.key, id: 'composer-reference-' + index, type: 'button', role: 'option',
                            className: 'composer-mention-item', 'aria-selected': index === activeIndex,
                            tabIndex: -1, title: item.name,
                            onMouseDown: event => event.preventDefault(), onMouseEnter: () => setIndex(index), onClick: () => choose(item) },
                            item.thumbnail ? h('img', { src: item.thumbnail, alt: '' }) : icon(glyph),
                            h('span', null, item.name));
                    }));
            }));
    }

    function AddMenu({ skill, host, busy, add }) {
        const items = [
            !skill && { key: 'file', label: '添加文件', icon: icon('paperclip') },
            !skill && { key: 'folder', label: '添加文件夹', icon: icon('folder') },
            { key: 'image', label: '添加图片', icon: icon('image') },
            window.GuizhiArcgisMaterialItems(host),
        ].filter(Boolean);
        return h(Dropdown, { trigger: ['click'], placement: 'topLeft', menu: { items, onClick: ({ key }) => add(key) } },
            iconButton('plus', '添加', { disabled: busy, 'aria-haspopup': 'menu' }));
    }

    window.GuizhiComposerMedia = { ImageAttachments, MentionList, AddMenu };
})();
