(() => {
    const parser = window.markdownit({ html: false, breaks: true, linkify: false });
    parser.validateLink = url => /^https?:\/\//i.test(url);
    parser.renderer.rules.image = (tokens, index) => parser.utils.escapeHtml(tokens[index].content);

    window.GuizhiMarkdown = {
        render: text => parser.render(text || ''),
        open(event) {
            const anchor = event.target.closest('a[href]');
            if (!anchor) return;
            event.preventDefault();
            void window.guizhi.openLink(anchor.getAttribute('href')).catch(error => console.error(error.message));
        },
    };
})();
