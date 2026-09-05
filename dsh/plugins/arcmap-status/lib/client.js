window.__ModuleLoader__.load({
	id: "arcmap-status",
	factory: (require) => {
		var module = { exports: {} };
		var exports = module.exports;
		let react_jsx_runtime = require("react/jsx-runtime");
		let react = require("react");

		const STATUS_URL = "http://127.0.0.1:8765/status";
		const POLL_MS = 5000;

		async function readStatus(signal) {
			const response = await fetch(STATUS_URL, { signal });
			if (!response.ok) throw new Error("HTTP " + response.status);
			return await response.json();
		}

		function describe(status) {
			if (!status) return { text: "离线", color: "#ff7875", detail: "边界服务未连接" };
			const arcmapCount = status.arcmap && status.arcmap.alive_count || 0;
			if (arcmapCount > 0) {
				return {
					text: "在线",
					color: "#66bb6a",
					detail: "ArcMap 在线 · " + arcmapCount + " 个地图目标",
				};
			}
			return { text: "离线", color: "#ff7875", detail: "ArcMap 未运行" };
		}

		/** Sidebar footer status row: polls the boundary /status endpoint. */
		function ArcMapStatusRow() {
			const [status, setStatus] = (0, react.useState)(null);
			const [tick, setTick] = (0, react.useState)(0);
			(0, react.useEffect)(() => {
				const controller = new AbortController();
				readStatus(controller.signal)
					.then(setStatus)
					.catch(() => setStatus(null));
				const timer = setInterval(() => setTick(function (n) { return n + 1; }), POLL_MS);
				return () => { controller.abort(); clearInterval(timer); };
			}, [tick]);
			const view = describe(status);
			return (0, react_jsx_runtime.jsxs)("button", {
				"data-arcmap-status": "1",
				onClick: function () { setTick(function (n) { return n + 1; }); },
				title: view.detail + "（点击刷新）",
				style: {
					display: "flex", alignItems: "center", gap: "8px",
					width: "calc(100% - 16px)", margin: "0 8px 8px",
					padding: "7px 10px", fontSize: "12px", cursor: "pointer",
					background: "transparent", color: "inherit",
					border: "1px solid rgba(128,128,128,0.35)", textAlign: "left",
				},
				children: [
					(0, react_jsx_runtime.jsx)("span", {
						style: {
							width: "8px", height: "8px", borderRadius: "50%",
							background: view.color, flexShrink: 0,
						},
					}),
					(0, react_jsx_runtime.jsx)("span", { children: view.text }),
				],
			});
		}

		const inject = ["slots"];

		function apply(ctx) {
			ctx.slots.inject("sidebar.footer.action", function* () {
				yield ctx.slots.register(
					{ name: "sidebar.footer.action", id: "arcmap-status-row" },
					ArcMapStatusRow);
			});
		}

		exports.apply = apply;
		exports.inject = inject;
		return module.exports;
	},
});
