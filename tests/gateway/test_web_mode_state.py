import pathlib
import unittest

from gateway_py3.static_server import is_static_path


ROOT = pathlib.Path(__file__).resolve().parents[2]


def _web_source():
    web_root = ROOT / "gateway_py3" / "web"
    return "\n".join([
        _web_file("index.html"),
        _web_file("app.js"),
        _web_file("app_arcmap.js"),
        _web_file("app_render.js"),
        _web_file("app_mentions.js"),
        _web_file("app_voice.js"),
        _web_file("styles.css"),
        _web_file("components.css"),
    ])


def _web_file(name):
    return (ROOT / "gateway_py3" / "web" / name).read_text(encoding="utf-8")


def _server_source():
    gateway = ROOT / "gateway_py3"
    return "\n".join([
        (gateway / "app.py").read_text(encoding="utf-8"),
        (gateway / "llm_providers.py").read_text(encoding="utf-8"),
        (gateway / "routes" / "__init__.py").read_text(encoding="utf-8"),
        (gateway / "routes" / "common.py").read_text(encoding="utf-8"),
        (gateway / "routes" / "planner.py").read_text(encoding="utf-8"),
        (gateway / "routes" / "voice.py").read_text(encoding="utf-8"),
        (gateway / "voice.py").read_text(encoding="utf-8"),
    ])


class WebModeStateTests(unittest.TestCase):
    def test_polling_config_does_not_overwrite_selected_mode(self):
        html = _web_source()

        self.assertIn("const MODE_STORAGE_KEY = 'geopilot.currentMode';", html)
        self.assertIn("let modeInitialized = false;", html)
        self.assertIn("if (!modeInitialized) {", html)
        self.assertIn("storeMode(mode);", html)

    def test_full_agent_chat_renders_continuous_conversation_stream(self):
        html = _web_source()

        self.assertIn("if (currentMode === 'full_agent') {", html)
        self.assertIn("visibleWorkflows(workflows).slice().reverse()", html)
        self.assertIn("appendAssistantForWorkflow(item, false, false);", html)

    def test_assistant_markdown_and_thinking_have_dedicated_rendering(self):
        html = _web_source()

        self.assertIn("function renderAssistantMarkdown(text)", html)
        self.assertIn("function splitThinking(text)", html)
        self.assertIn("function renderMarkdown(text)", html)
        self.assertIn("class=\"think-panel\"", html)

    def test_answer_workflows_are_displayed_without_arcgis_execution_prompt(self):
        html = _web_source()

        self.assertIn("action === 'answer'", html)
        self.assertIn("这是一条普通回复，不需要发送到 ArcGIS。", html)

    def test_task_panel_renders_all_visible_tasks(self):
        html = _web_source()

        self.assertIn("const items = visibleWorkflows(workflows);", html)
        self.assertIn("items.forEach(item => list.appendChild(taskCard(item)));", html)
        self.assertIn("显示当前会话的全部任务", html)
        self.assertIn("显示半代理模式的全部任务", html)
        self.assertNotIn("显示会话", html)
        self.assertNotIn("查看对话", html)

    def test_full_agent_has_no_project_creation_ui(self):
        html = _web_source()

        self.assertNotIn("conversation-sidebar", html)
        self.assertNotIn("sidebarHistory", html)
        self.assertNotIn("modeNote", html)
        self.assertNotIn("sidebarProjectForm", html)
        self.assertNotIn("createProject", html)
        self.assertNotIn("/projects", html)
        self.assertNotIn("project_id", html)

    def test_pending_user_message_survives_workflow_polling(self):
        html = _web_source()

        self.assertIn("let transientUserMessage = '';", html)
        self.assertIn("function appendTransientConversation", html)
        self.assertIn("if (transientUserMessage && currentMode !== 'full_agent' && !selectedWorkflowId) return;", html)

    def test_long_model_wait_has_visible_progress_state(self):
        html = _web_source()

        self.assertIn("let modelWait = null;", html)
        self.assertIn("function startModelWait(label)", html)
        self.assertIn("function renderModelWait()", html)
        self.assertIn("modelWaitBubble", html)
        self.assertIn("已等待", html)
        self.assertIn("同步 ArcMap", html)
        self.assertIn("执行到 ArcMap", html)

    def test_custom_tools_can_be_deleted_from_ui(self):
        html = _web_source()

        self.assertIn("async function deleteTool(id)", html)
        self.assertIn("api(`/tools/${id}/delete`", html)
        self.assertIn("自建工具已删除。", html)

    def test_clear_full_agent_session_context(self):
        html = _web_source()

        self.assertIn("已清空全代理会话上下文。", html)
        self.assertIn("return {mode: currentMode};", html)

    def test_failed_custom_tool_workflow_can_request_ai_revision(self):
        html = _web_source()
        server = _server_source()

        self.assertIn("function usesCustomTool(workflow)", html)
        self.assertIn("让 AI 修这个工具", html)
        self.assertIn("let repairingWorkflowIds = new Set();", html)
        self.assertIn("repairingWorkflowIds.add(id);", html)
        self.assertIn("修复中...", html)
        self.assertIn("async function repairCustomTool(id)", html)
        self.assertIn("api(`/workflows/${id}/repair-custom-tool`", html)
        self.assertIn("repair-custom-tool", server)
        self.assertIn("toolbuilder_get_draft", server)
        self.assertIn("toolbuilder_revise_draft", server)

    def test_file_loaded_page_calls_local_gateway_api(self):
        html = _web_source()

        self.assertIn("const API_ORIGIN = window.location.protocol === 'file:' ? 'http://127.0.0.1:8765' : '';", html)
        self.assertIn("response = await fetch(apiUrl(path), options || {});", html)
        self.assertIn("function apiUrl(path)", html)

    def test_gateway_allows_file_page_preflight(self):
        app = (ROOT / "gateway_py3" / "app.py").read_text(encoding="utf-8")

        self.assertIn("def do_OPTIONS(self):", app)
        self.assertIn('self.send_header("Access-Control-Allow-Origin", "*")', app)
        self.assertIn('self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")', app)

    def test_web_uses_arcmap_bridge_for_sync_and_execution(self):
        html = _web_source()

        self.assertIn("async function loadArcMapBridges()", html)
        self.assertIn("api('/arcmap/bridges')", html)
        self.assertIn("api('/arcmap/execute-approved'", html)
        self.assertIn("发送并执行", html)
        self.assertNotIn("自动同步", html)
        self.assertNotIn("function syncArcMap", html)

    def test_model_config_is_provider_driven(self):
        html = _web_source()
        server = _server_source()

        self.assertIn("模型配置", html)
        self.assertNotIn("模型已配置", html)
        self.assertIn("provider_options", server)
        self.assertIn("deepseek-v4-flash-thinking", server)
        self.assertIn("deepseek-v4-flash-non-thinking", server)
        self.assertIn("deepseek-v4-pro-thinking", server)
        self.assertIn("deepseek-v4-pro-non-thinking", server)
        self.assertIn("MiniMax-M2.5", server)
        self.assertIn("MiniMax-M2.7", server)
        self.assertIn("MiniMax-M3", server)
        self.assertIn('id="activeModelHint"', html)
        self.assertIn("optgroup", html)
        self.assertIn("modelOptionLabel", html)
        self.assertIn("config_error", html)
        self.assertIn("QWEN_PROVIDER", server)
        self.assertIn("阿里百炼", server)
        self.assertIn("qwen3.7-plus", server)
        self.assertIn("qwen3.6-flash-2026-04-16", server)
        self.assertIn("qwen3.6-35b-a3b", server)
        self.assertIn("qwen3.7-max-2026-05-17", server)
        self.assertIn("qwen3.6-plus-2026-04-02", server)
        self.assertIn("qwen3.7-max-preview", server)
        self.assertIn("DASHSCOPE_API_KEY", server)
        self.assertIn("BAILIAN_TOKEN_PLAN_API_KEY", server)
        self.assertIn("token_plan_api_key", server)
        self.assertIn("Token Plan API Key", html)
        self.assertIn("providerRuntimeKeyLabel", html)
        self.assertIn("providerKeyFieldHtml", html)
        self.assertIn("function renderModelConfig(config)", html)
        self.assertIn("function collectProviderConfig()", html)
        self.assertIn('id="providerKeyFields"', html)

    def test_voice_input_uses_qwen_asr_and_mode_model_correction(self):
        html = _web_source()
        voice_js = _web_file("app_voice.js")
        server = _server_source()

        self.assertIn('id="voiceButton"', html)
        self.assertIn("function toggleVoiceInput()", html)
        self.assertIn("window.toggleVoiceInput = toggleVoiceInput", html)
        self.assertTrue(is_static_path("/app_voice.js"))
        self.assertIn("api('/voice/transcribe'", html)
        self.assertIn("context: latestArcgisContext || {}", html)
        self.assertIn("applyVoiceText(data.text || '')", voice_js)
        self.assertIn("renderVoiceComparison(data.raw_text || '', data.text || '')", voice_js)
        self.assertIn('id="voiceCompare"', html)
        self.assertIn("正在识别语音并校正指令", voice_js)
        self.assertNotIn("校正图层名", voice_js)
        self.assertNotIn("submitPlan", voice_js)
        self.assertNotIn("'/plan'", voice_js)
        self.assertIn("语音识别使用阿里百炼 API Key 或 Token Plan API Key", html)
        self.assertIn("qwen3-asr-flash", server)
        self.assertIn("create_provider(mode=mode)", server)
        self.assertIn("chat_text", server)
        self.assertIn("DASHSCOPE_API_KEY", server)
        self.assertIn("MiniMax-M3", server)

    def test_web_uses_event_stream_instead_of_workflow_polling(self):
        html = _web_source()
        app = (ROOT / "gateway_py3" / "app.py").read_text(encoding="utf-8")
        topics = (ROOT / "gateway_py3" / "routes" / "event_topics.py").read_text(encoding="utf-8")

        self.assertIn("new EventSource(apiUrl('/events'))", html)
        self.assertIn("scheduleEventRefresh(type)", html)
        self.assertIn("loadWorkbenchState()", html)
        self.assertIn("appState", html)
        self.assertIn("workflows.changed", html)
        self.assertIn("agent.progress", html)
        self.assertIn("activePlanRequestId", html)
        self.assertIn("payload.request_id", html)
        self.assertIn("completedStageIndex", html)
        self.assertIn("'generate_workflow'", html)
        self.assertIn("index < active ? 'done'", html)
        self.assertIn("serve_event_stream(self, STATE.events)", app)
        self.assertIn('"workflows.changed"', topics)
        self.assertNotIn("setInterval(pollUpdates", html)
        self.assertNotIn("POLL_INTERVAL_MS", html)
        self.assertNotIn("async function pollUpdates", html)

    def test_web_exposes_arcmap_target_selection(self):
        html = _web_source()

        self.assertIn("openArcMapTargets()", html)
        self.assertIn("selectArcMapBridge", html)
        self.assertIn("hwnd", html)
        self.assertIn("pid", html)

    def test_skill_mentions_multi_arcmap_selection(self):
        skill = (ROOT / "agent_integrations" / "geopilot-arcmap" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("arcmap-list", skill)
        self.assertIn("arcmap-select", skill)
        self.assertIn("--hwnd", skill)
        self.assertIn("multiple ArcMap instances", skill)


if __name__ == "__main__":
    unittest.main()
