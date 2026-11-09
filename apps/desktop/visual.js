(() => {
    const view = new URLSearchParams(location.search).get('view');
    document.body.dataset.view = view || 'status';
    if (view && view !== 'status') return;
    const shapes = {
        think: 'M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z',
        read: 'M5 3h10l4 4v14H5V3Zm9 0v5h5M8 12h8M8 16h6',
        link: 'M9 8l3-3a4 4 0 0 1 6 6l-3 3M15 16l-3 3a4 4 0 0 1-6-6l3-3m0 4 6-6',
        transform: 'M4 8h16m-4-4 4 4-4 4M20 16H4m4-4-4 4 4 4',
        calculate: 'M4 5v15h16M8 16v-4m5 4V8m5 8V5',
        check: 'M12 3 5 6v6c0 5 7 9 7 9s7-4 7-9V6l-7-3ZM9 12l2 2 4-4',
        publish: 'M4 13v7h16v-7m-8 3V4m-4 4 4-4 4 4',
        reuse: 'M5 10a7 7 0 1 1 2 7M5 5v5h5',
        draw: 'M4 19h4l11-11-4-4L4 15v4Zm9-13 4 4',
        stop: 'M8 5v14m8-14v14',
    };
    const box = document.getElementById('status');
    const icon = document.querySelector('#icon path');
    const label = document.getElementById('label');
    const button = document.getElementById('takeover');
    icon.setAttribute('d', shapes.think);
    window.visual?.onStep(step => {
        box.dataset.kind = step.kind;
        icon.setAttribute('d', shapes[step.kind]);
        label.textContent = step.label;
        button.disabled = step.kind === 'stop';
        button.title = step.shortcut ? '接管（Ctrl+Alt+Esc）' : '接管';
    });
    button.addEventListener('click', () => window.visual?.takeover());
})();
