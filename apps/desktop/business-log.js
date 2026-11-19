(() => {
    const h = React.createElement;
    const { useEffect, useState } = React;
    const { Alert, Button, Empty, Input, Pagination, Spin, Table, Tag } = antd;
    const api = window.guizhi;
    const date = value => new Date(value).toLocaleString('zh-CN', { hour12: false });
    const phrase = id => '删除 ' + id.slice(0, 8).toUpperCase();

    function Report({ id }) {
        const [page, setPage] = useState(1);
        const [result, setResult] = useState(null);
        const [pages, setPages] = useState(1);
        const [error, setError] = useState('');
        useEffect(() => {
            let active = true;
            api.reportPage(id, page).then(value => { if (active) { setResult(value); setPages(value.pages); } },
                issue => { if (active) setError(window.GuizhiErrorText(issue)); });
            return () => { active = false; };
        }, [id, page]);
        function change(next) { setError(''); setResult(null); setPage(next); }
        return h('section', { className: 'business-pdf', 'aria-label': 'PDF 办理报告' },
            pages > 1 && h(Pagination, { current: page, pageSize: 1, total: pages, size: 'small',
                showSizeChanger: false, onChange: change }),
            error ? h(Alert, { type: 'error', message: error })
                : result ? h('img', { src: 'data:image/png;base64,' + result.image, alt: '办理报告第 ' + page + ' 页' })
                    : h('div', { className: 'business-pdf-loading', role: 'status' }, h(Spin, { size: 'small' }), '正在读取报告…'));
    }

    function ReportPanel({ id }) {
        const [open, setOpen] = useState(false);
        return h('details', { className: 'business-report-panel', onToggle: event => setOpen(event.currentTarget.open) },
            h('summary', null, '查看报告', h('span', null, 'PDF')),
            open && h(Report, { key: id, id }));
    }

    function BusinessLog({ revision, catalog }) {
        const [page, setPage] = useState(1);
        const [data, setData] = useState(null);
        const [selected, setSelected] = useState(null);
        const [removing, setRemoving] = useState(null);
        const [value, setValue] = useState('');
        const [busy, setBusy] = useState(false);
        const [error, setError] = useState('');
        const [update, setUpdate] = useState(0);
        const fail = issue => setError(window.GuizhiErrorText(issue));
        useEffect(() => {
            let active = true;
            api.businessLog(page).then(value => { if (active) setData(value); },
                issue => { if (active) fail(issue); });
            return () => { active = false; };
        }, [page, revision, update]);
        const inlinePanel = React.useRef(null);
        useEffect(() => {
            if (selected || removing) inlinePanel.current?.scrollIntoView({ block: 'nearest' });
        }, [selected, removing]);
        async function detail(id) {
            setRemoving(null);
            setError('');
            try { setSelected(await api.businessDetail(id)); } catch (issue) { fail(issue); }
        }
        async function remove() {
            setError('');
            setBusy(true);
            try {
                await api.businessDelete(removing, value);
                if (selected?.id === removing) setSelected(null);
                setRemoving(null);
                setUpdate(current => current + 1);
            } catch (issue) { fail(issue); }
            finally { setBusy(false); }
        }
        const columns = [
            { title: '业务类型', dataIndex: 'type' },
            { title: '办理时间', dataIndex: 'finishedAt', render: date },
            { title: '办理人', dataIndex: 'operator' },
            { title: '办理状态', dataIndex: 'statusLabel', render: (label, item) => h(Tag, { color: item.status === 'succeeded' ? 'success' : undefined }, label) },
            { title: '操作', key: 'action', render: (_, item) => h('div', { className: 'business-row-actions' },
                h(Button, { type: 'link', onClick: () => detail(item.id) }, '查看'),
                h(Button, { type: 'text', danger: true, disabled: busy, onClick: () => { setSelected(null); setValue(''); setRemoving(item.id); } }, '删除')) },
        ];
        return h('main', { className: 'business-log', 'aria-label': '业务中心' },
            h('h1', null, '业务中心'),
            h('section', { className: 'business-catalog' }, h('h2', null, '全部业务'), catalog),
            error && h(Alert, { type: 'error', message: error, closable: true, onClose: () => setError('') }),
            !data ? h(Spin, { spinning: !error }) : h(React.Fragment, null,
                h('dl', { className: 'business-dashboard' }, ...[['累计办理', data.total], ['本月办理', data.month], ['本周办理', data.week]]
                    .map(([label, count]) => h('div', { key: label }, h('dt', null, label), h('dd', null, count, h('small', null, '件'))))),
                data.types.length > 0 && h('section', { className: 'business-types' }, h('h2', null, '业务分布'),
                    h('ul', null, ...data.types.map(type => h('li', { key: type.name }, type.name, h('strong', null, type.count))))),
                h('section', { className: 'business-records' }, h('h2', null, '已办业务'),
                    h(Table, { rowKey: 'id', columns, dataSource: data.items, pagination: false, size: 'middle',
                        scroll: { x: 585 }, locale: { emptyText: h(Empty, { image: Empty.PRESENTED_IMAGE_SIMPLE, description: '暂无办理记录' }) } }),
                    data.total > 0 && h(Pagination, { current: data.page, pageSize: data.pageSize, total: data.total,
                        showSizeChanger: false, onChange: setPage }))),
            selected && h('section', { className: 'business-records', ref: inlinePanel },
                h('div', { className: 'section-head' }, h('h2', null, selected.type),
                    h(Button, { onClick: () => setSelected(null) }, '收起')),
                h('dl', { className: 'business-facts' }, ...[['办理编号', selected.id], ['办理人', selected.operator],
                    ['开始时间', date(selected.startedAt)], ['结束时间', date(selected.finishedAt)], ['办理状态', selected.statusLabel]]
                    .map(([label, text]) => h('div', { key: label }, h('dt', null, label), h('dd', null, text)))),
                h('h3', null, '输入材料'),
                selected.materials.length ? h('ul', { className: 'business-materials' }, ...selected.materials.map((item, index) =>
                    h('li', { key: index }, h('strong', null, item.role), h('span', null, item.name, item.layer ? ' · ' + item.layer : ''),
                        h('small', null, item.source)))) : h('p', { className: 'muted' }, '无独立输入材料'),
                h('h3', null, '办理结果'),
                !selected.response && selected.statistics?.coverage_ratio != null && h('p', null,
                    '重叠面积 ' + selected.statistics.covered_area_m2.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) +
                    ' 平方米，覆盖率 ' + (selected.statistics.coverage_ratio * 100).toFixed(2) + '%。'),
                selected.response && (selected.status === 'succeeded' ? h('div', { className: 'answer-markdown', onClick: window.GuizhiMarkdown.open,
                    dangerouslySetInnerHTML: { __html: window.GuizhiMarkdown.render(selected.response) } })
                    : h('p', null, window.GuizhiErrorText(selected.response))),
                selected.reportDigest && h(ReportPanel, { id: selected.id }),
                !selected.response && !selected.reportDigest && h('p', null, selected.statusLabel)),
            removing && h('section', { className: 'business-records', ref: inlinePanel },
                h('h2', null, '删除办理记录'),
                h('p', null, '删除后，日志和办理统计将不再显示这条记录。已生成的报告和数据会保留。'),
                h('label', { className: 'business-confirm' }, h('span', null, '请输入 ', h('code', null, phrase(removing))),
                    h(Input, { value, 'aria-label': '删除确认文字', onChange: event => setValue(event.target.value) })),
                error && h(Alert, { type: 'error', message: error }),
                h('div', { className: 'business-row-actions' },
                    h(Button, { disabled: busy, onClick: () => setRemoving(null) }, '取消'),
                    h(Button, { danger: true, type: 'primary', disabled: value !== phrase(removing), loading: busy, onClick: remove }, '确认删除'))));
    }
    window.GuizhiBusinessLog = { BusinessLog, ReportPanel };
})();
