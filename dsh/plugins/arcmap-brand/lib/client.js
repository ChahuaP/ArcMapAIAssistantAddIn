window.__ModuleLoader__.load({
	id: "arcmap-brand",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		let react_jsx_runtime = require("react/jsx-runtime");

		/** Stylized map-frame mark drawn in currentColor so themes recolor it. */
		function ArcMapBrandMark({ size, className }) {
			const pixels = Number(size) || 24;
			return (0, react_jsx_runtime.jsx)("svg", {
				width: pixels, height: pixels, viewBox: "0 0 24 24",
				className: className || undefined,
				"aria-label": "ArcMap Harness", role: "img",
				children: (0, react_jsx_runtime.jsxs)("g", {
					fill: "none", stroke: "currentColor", strokeWidth: 1.7,
					strokeLinejoin: "round", strokeLinecap: "round",
					children: [
						(0, react_jsx_runtime.jsx)("path", { d: "M3 6.5 8.5 4.5 15.5 6.5 21 4.5V17.5L15.5 19.5 8.5 17.5 3 19.5Z" }),
						(0, react_jsx_runtime.jsx)("path", { d: "M8.5 4.5V17.5" }),
						(0, react_jsx_runtime.jsx)("path", { d: "M15.5 6.5V19.5" }),
						(0, react_jsx_runtime.jsx)("circle", { cx: 11.9, cy: 10.8, r: 1.5 }),
					],
				}),
			});
		}

		/** The ArcMap Harness wordmark that replaces the DeepSeek wordmark. */
		function ArcMapBrandName() {
			return (0, react_jsx_runtime.jsx)("span", {
				style: { fontWeight: 600, letterSpacing: "0.02em" },
				children: "ArcMap Harness",
			});
		}

		const inject = ["slots"];

		const FAVICON = "data:image/svg+xml," + encodeURIComponent(
			'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">' +
			'<rect width="24" height="24" fill="#0d47a1"/>' +
			'<g fill="none" stroke="#ffffff" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round">' +
			'<path d="M3 6.5 8.5 4.5 15.5 6.5 21 4.5V17.5L15.5 19.5 8.5 17.5 3 19.5Z"/>' +
			'<path d="M8.5 4.5V17.5"/><path d="M15.5 6.5V19.5"/>' +
			'<circle cx="11.9" cy="10.8" r="1.5"/></g></svg>');

		function applyFavicon() {
			document.querySelectorAll('link[rel*="icon"]').forEach(function (link) {
				link.remove();
			});
			const link = document.createElement("link");
			link.rel = "icon";
			link.type = "image/svg+xml";
			link.href = FAVICON;
			document.head.appendChild(link);
		}

		/** Baked-in host strings with no locale seam, healed on every render. */
		let versionText = null;
		function currentSwaps() {
			const swaps = new Map();
			swaps.set("探索未至之境", {
				text: "开始你的AI GIS之旅",
			});
			if (versionText) {
				swaps.set("预览版", { text: versionText });
			}
			return swaps;
		}

		function swapBakedSlogan(root) {
			if (!root) return;
			const swaps = currentSwaps();
			const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
			let node = walker.nextNode();
			while (node) {
				const swap = swaps.get((node.textContent || "").trim());
				if (swap !== undefined && node.textContent.trim() !== swap.text) {
					node.textContent = swap.text;
				}
				node = walker.nextNode();
			}
		}

		function fetchVersion() {
			fetch("http://127.0.0.1:8765/health")
				.then(function (response) { return response.json(); })
				.then(function (document_) {
					if (document_ && document_.app_version) {
						versionText = "v" + document_.app_version;
						swapBakedSlogan(document.body);
					}
				})
				.catch(function () {});
		}

		function apply(ctx) {
			applyFavicon();
			fetchVersion();
			ctx.slots.inject("sidebar.brand.mark", () => ctx.slots.inject("sidebar.brand.name", function* () {
				yield ctx.slots.register({ name: "sidebar.brand.mark" }, ArcMapBrandMark);
				yield ctx.slots.register({ name: "sidebar.brand.name" }, ArcMapBrandName);
			}));
			ctx.slots.inject("conversation.hero.brand.mark", function* () {
				yield ctx.slots.register({ name: "conversation.hero.brand.mark" }, ArcMapBrandMark);
			});
			// The SPA rewrites document.title after boot, so the title rides
			// the same observer that heals the baked slogan strings.
			const rebrand = () => {
				if (document.title !== "ArcMap Harness") document.title = "ArcMap Harness";
				swapBakedSlogan(document.body);
			};
			const observer = new MutationObserver(rebrand);
			observer.observe(document.body, { childList: true, subtree: true, characterData: true });
			rebrand();
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	},
});
