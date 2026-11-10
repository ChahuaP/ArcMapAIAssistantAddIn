(() => {
    const h = React.createElement;
    const { useEffect, useRef, useState } = React;
    const api = window.guizhi;
    const { button, iconButton, icon } = window.GuizhiShell;
    const { SkillEditor, deliverables } = window.GuizhiSkillEditor;
    const { reference, normalizeBusiness } = window.GuizhiTaskCommands;
    const domainTitles = { data: '数据', vector: '空间分析', geometry: '几何', table: '属性表',
        crs: '坐标', presentation: '地图与版面', evidence: '留痕', system: '运行状态', semantics: '审批材料',
        knowledge: '业务知识', governance: '依据与解释', raster: '栅格', cad: 'CAD',
        annual: '年度地类', imagery: '影像识别', delivery: '共享交付', business: '业务' };
    function Business({ catalog, refresh, choose, close, manage, initialMarkdown, plans }) {
        const dialog = useRef(null);
        const [editor, setEditor] = useState(initialMarkdown ? { markdown: initialMarkdown } : null);
        const [section, setSection] = useState(manage ? 'manage' : 'browse');
        const [query, setQuery] = useState('');
        const [error, setError] = useState('');
        const [operations, setOperations] = useState({});
        const fail = issue => setError(window.GuizhiErrorText(issue));
        function page(value) {
            setEditor(null);
            setSection(value);
            setError('');
            if (value === 'tools' && !Object.keys(operations).length) api.capabilities().then(setOperations).catch(fail);
        }
        const keyword = normalizeBusiness(query);
        const matches = catalog.filter(item => normalizeBusiness([item.title, item.summary, ...item.aliases].join(' ')).includes(keyword));
        useEffect(() => { dialog.current?.showModal(); }, []);
        const content = h(React.Fragment, null,
            editor ? h(React.Fragment, null,
                button('back', '返回业务', { type: 'text', onClick: () => setEditor(null) }),
                h(SkillEditor, { initialMarkdown: editor.markdown, onPublished: refresh, plans })) :
                h(React.Fragment, null,
                    manage && h('nav', { className: 'business-toolbar', 'aria-label': '业务管理分类' },
                        h(antd.Segmented, { options: [{ label: '业务技能', value: 'manage' },
                            { label: '工具目录', value: 'tools' }], value: section, onChange: page }),
                        section === 'manage' && button('plus', '新建业务', { type: 'primary', onClick: () => setEditor({ markdown: '' }) })),
                    section !== 'tools' ? h(React.Fragment, null,
                        h('input', { type: 'search', className: 'business-search', 'aria-label': '搜索业务',
                            placeholder: '搜索业务名称或常用说法', value: query, onChange: event => setQuery(event.target.value) }),
                        matches.length ? h('div', { className: 'skill-cards' }, ...matches.map(item => h('article', {
                            className: 'skill-card', key: reference(item),
                        }, h('div', { className: 'section-head' }, h('h3', null, icon('layers'), item.title),
                            section === 'manage' && h('small', null, 'v' + item.release_version)),
                        item.summary && h('p', null, item.summary),
                        h('dl', { className: 'skill-facts' },
                            h('dt', null, '材料'), h('dd', null, item.materials.filter(role => !role.resource)
                                .map(role => role.label).join('、') || '无需附件'),
                            h('dt', null, '成果'), h('dd', null, [...new Set(item.outputs.map(kind => deliverables[kind] || '业务成果'))].join('、') || '办理留痕')),
                        section === 'manage' ? button('layers', '编辑', { onClick: () =>
                            api.skillShow(reference(item)).then(result => setEditor({ markdown: result.markdown })).catch(fail) }) :
                            button('plus', '开始办理', { type: 'primary', onClick: () => choose(item) }))))
                            : h('p', { className: 'muted' }, '没有匹配的业务')) :
                        h('div', { className: 'capability-list' }, ...Object.entries(domainTitles).map(([id, title]) => {
                            const tools = Object.entries(operations).filter(([name]) => name.startsWith(id + '.') ||
                                (id === 'business' && !name.includes('.')));
                            return tools.length > 0 && h('details', { key: id, className: 'capability-group' },
                                h('summary', null, title), h('ul', null, ...tools.map(([name, spec]) => h('li', { key: name }, spec.title))));
                        }))),
            error && h('p', { className: 'error', role: 'alert' }, error));
        if (manage) return h('div', { className: 'business-page' }, content);
        return h('dialog', { className: 'business-dialog', ref: dialog,
            onCancel: event => { event.preventDefault(); close(); } },
            h('div', { className: 'dialog-head' }, h('h2', null, '业务中心'),
                iconButton('close', '关闭业务中心', { onClick: close })), content);
    }
    window.GuizhiBusiness = { Business };
})();
