import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class RunUiContractTests(unittest.TestCase):
    def test_ui_has_all_ablation_buttons(self):
        text = (ROOT / "gateway_py3/web/index.html").read_text(encoding="utf-8")
        for mode in ("direct_single", "context_single", "constrained_single", "multi_agent"):
            self.assertIn('data-mode="%s"' % mode, text)

    def test_ui_polls_runs_to_terminal_status(self):
        text = (ROOT / "gateway_py3/web/app.js").read_text(encoding="utf-8")
        self.assertIn("async function waitForRun(id)", text)
        self.assertIn("`/runs/${id}`", text)
        self.assertIn("'cancelled'", text)
        self.assertIn("'context_failed'", text)
        terminal_expression = text.split("async function waitForRun(id)", 1)[1].split("async function", 1)[0]
        self.assertNotIn("recovery_required", terminal_expression)
        self.assertIn("'indeterminate'", terminal_expression)
        self.assertNotIn("运行状态轮询超时", text)

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
        undeletable_statuses = renderer.split("const undeletableStatuses", 1)[1].split(";", 1)[0]
        self.assertIn("indeterminate", renderer)
        self.assertIn("结果无法判定", renderer)
        self.assertIn("'indeterminate'", undeletable_statuses)
        self.assertNotIn("可以删除后", renderer)
        self.assertIn("indeterminate", readme)
        self.assertIn("权威结果", readme)
        self.assertIn("不可删除", readme)

    def test_cli_exposes_automatic_run_controls(self):
        text = (ROOT / "agent_integrations/geopilot-arcmap/scripts/geopilot_cli.py").read_text(encoding="utf-8")
        for option in ("--provider", "--model", "--execute", "--confirmed", "--allow-edits"):
            self.assertIn(option, text)

    def test_route_uses_uuid_parser(self):
        text = (ROOT / "gateway_py3/routes/__init__.py").read_text(encoding="utf-8")
        self.assertIn("uuid.UUID(value)", text)
        self.assertIn("canonical UUID", text)
