import unittest
from pathlib import Path


ROOT = Path(__file__).parents[2]


class RunUiContractTests(unittest.TestCase):
    def test_kernel_and_agents_depend_only_on_provider_neutral_runtime(self):
        for folder in ("kernel", "intelligence"):
            for path in (ROOT / "gateway_py3" / folder).glob("*.py"):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("model_runtime.adapters", source, str(path))
                self.assertNotIn("minimax_client", source, str(path))
        self.assertFalse((ROOT / "gateway_py3/llm_providers.py").exists())
        self.assertFalse((ROOT / "gateway_py3/minimax_client.py").exists())
        self.assertFalse((ROOT / "gateway_py3/intelligence/model_runtime.py").exists())

    def test_ui_uses_sse_driven_run_wait(self):
        text = "\n".join((ROOT / "gateway_py3/web" / name).read_text(encoding="utf-8")
                         for name in ("app.js", "app_render.js"))
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
        self.assertIn('src="app_events.js?v=', sources)

    def test_model_settings_use_user_facing_language_only(self):
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "gateway_py3/web").glob("*")
            if path.suffix in (".js", ".html")
        )
        for internal_term in (
            "Windows DPAPI",
            "credential_ref",
            "当前安装连接",
            "Token Plan",
            "生产 Agent 按角色模型计划运行",
            "第三章实验锁",
            "Provider Registry",
            "compiler、planner、auditor、repairer",
            "openConfigFile",
            "config_file",
            "openChangelog",
            "changelogModal",
        ):
            self.assertNotIn(internal_term, sources)
        self.assertIn("模型服务", sources)
        self.assertIn("API Key", sources)

    def test_ui_and_readme_explain_indeterminate_recovery(self):
        renderer = (ROOT / "gateway_py3/web/app_render.js").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        # §4.8: ExecutionIndeterminate is a terminal stage in the new kernel
        self.assertIn("execution_indeterminate", renderer)
        self.assertIn("无法判定", renderer)
        self.assertNotIn("可以删除后", renderer)

    def test_cli_is_read_only_and_uses_current_gateway_routes(self):
        skill_root = ROOT / "agent_integrations/geopilot-arcmap"
        text = (skill_root / "scripts/geopilot_cli.py").read_text(encoding="utf-8")
        for old_option in ("--provider", "--model", "--execute", "--confirmed", "--allow-edits", '"run"', "--detail"):
            self.assertNotIn(old_option, text)
        self.assertIn('"/api/diagnostics"', text)
        self.assertIn('"/arcmap/bridges"', text)
        self.assertFalse((skill_root / "references").exists())
        self.assertNotIn("version:", (skill_root / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
