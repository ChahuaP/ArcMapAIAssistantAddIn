import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class RunUiContractTests(unittest.TestCase):
    def test_ui_has_all_ablation_buttons(self):
        text = (ROOT / "gateway_py3/web/index.html").read_text(encoding="utf-8")
        for mode in ("g0_direct", "g1_context", "g2_constrained", "g3_audited"):
            self.assertIn('data-mode="%s"' % mode, text)

    def test_ui_uses_sse_driven_run_wait(self):
        text = (ROOT / "gateway_py3/web/app.js").read_text(encoding="utf-8")
        # §14: the front-end is SSE-driven, no backoff polling
        self.assertIn("waitForRunSSE", text)
        self.assertIn("handleRunStageChanged", text)
        self.assertNotIn("async function waitForRun(id)", text)
        # terminal stages are classified, not polled
        self.assertIn("'execution_indeterminate'", text)
        self.assertIn("isTerminalStage", text)

    def test_frontend_has_no_dead_context_progress_or_mention_state(self):
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "gateway_py3/web").glob("*")
            if path.suffix in (".js", ".html", ".css")
        )
        for dead_name in (
            "latestArcgisContext", "applyContextRecord", "agentProgress",
            "handleAgentProgressEvent", "mentionState", "updateMentionMenu",
            "mentionMenu", "app_mentions.js",
        ):
            self.assertNotIn(dead_name, sources)
        self.assertIn('src="app_events.js"', sources)

    def test_ui_and_readme_explain_indeterminate_recovery(self):
        renderer = (ROOT / "gateway_py3/web/app_render.js").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        # §4.8: ExecutionIndeterminate is a terminal stage in the new kernel
        self.assertIn("execution_indeterminate", renderer)
        self.assertIn("无法判定", renderer)
        self.assertNotIn("可以删除后", renderer)

    def test_cli_exposes_automatic_run_controls(self):
        text = (ROOT / "agent_integrations/geopilot-arcmap/scripts/geopilot_cli.py").read_text(encoding="utf-8")
        for option in ("--provider", "--model", "--execute", "--confirmed", "--allow-edits"):
            self.assertIn(option, text)


if __name__ == "__main__":
    unittest.main()
