import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]


class WebModeStateTests(unittest.TestCase):
    def test_polling_config_does_not_overwrite_selected_mode(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("const MODE_STORAGE_KEY = 'geopilot.currentMode';", html)
        self.assertIn("let modeInitialized = false;", html)
        self.assertIn("if (!modeInitialized) {", html)
        self.assertIn("storeMode(mode);", html)

    def test_full_agent_chat_renders_project_conversation_stream(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("if (currentMode === 'full_agent') {", html)
        self.assertIn("visibleWorkflows(workflows).slice().reverse()", html)
        self.assertIn("appendAssistantForWorkflow(item, false, false);", html)

    def test_assistant_markdown_and_thinking_have_dedicated_rendering(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("function renderAssistantMarkdown(text)", html)
        self.assertIn("function splitThinking(text)", html)
        self.assertIn("function renderMarkdown(text)", html)
        self.assertIn("class=\"think-panel\"", html)

    def test_answer_workflows_are_displayed_without_arcgis_execution_prompt(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("action === 'answer'", html)
        self.assertIn("这是一条普通回复，不需要发送到 ArcGIS。", html)

    def test_task_panel_renders_all_visible_tasks(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("const items = visibleWorkflows(workflows);", html)
        self.assertIn("items.forEach(item => list.appendChild(taskCard(item)));", html)
        self.assertIn("显示当前项目的全部任务", html)
        self.assertIn("显示半代理模式的全部任务", html)
        self.assertNotIn("显示会话", html)
        self.assertNotIn("查看对话", html)

    def test_project_form_is_not_closed_by_config_polling_in_full_mode(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("if (!fullMode) document.getElementById('sidebarProjectForm').hidden = true;", html)

    def test_pending_user_message_survives_workflow_polling(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("let transientUserMessage = '';", html)
        self.assertIn("function appendTransientConversation", html)
        self.assertIn("if (transientUserMessage && currentMode !== 'full_agent' && !selectedWorkflowId) return;", html)

    def test_long_model_wait_has_visible_progress_state(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("let modelWait = null;", html)
        self.assertIn("function startModelWait(label)", html)
        self.assertIn("function renderModelWait()", html)
        self.assertIn("modelWaitBubble", html)
        self.assertIn("已等待", html)
        self.assertIn("模型正在处理上下文和工具选择。", html)

    def test_custom_tools_can_be_deleted_from_ui(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("async function deleteTool(id)", html)
        self.assertIn("api(`/tools/${id}/delete`", html)
        self.assertIn("自建工具已删除。", html)

    def test_projects_can_be_deleted_from_ui(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("async function deleteProject(id, name)", html)
        self.assertIn("api(`/projects/${encodeURIComponent(id)}/delete`", html)
        self.assertIn("不会删除磁盘文件", html)
        self.assertIn("项目已删除。", html)

    def test_clear_project_conversation_refreshes_context_state(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("已清空项目对话和上下文。", html)
        self.assertIn("if (currentMode === 'full_agent') await loadProjects();", html)

    def test_failed_custom_tool_workflow_can_request_ai_revision(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")
        app = (ROOT / "gateway_py3" / "app.py").read_text(encoding="utf-8")

        self.assertIn("function usesCustomTool(workflow)", html)
        self.assertIn("让 AI 修工具", html)
        self.assertIn("let repairingWorkflowIds = new Set();", html)
        self.assertIn("repairingWorkflowIds.add(id);", html)
        self.assertIn("修复中...", html)
        self.assertIn("async function repairCustomTool(id)", html)
        self.assertIn("api(`/workflows/${id}/repair-custom-tool`", html)
        self.assertIn("repair-custom-tool", app)
        self.assertIn("toolbuilder_get_draft", app)
        self.assertIn("toolbuilder_revise_draft", app)

    def test_file_loaded_page_calls_local_gateway_api(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("const API_ORIGIN = window.location.protocol === 'file:' ? 'http://127.0.0.1:8765' : '';", html)
        self.assertIn("response = await fetch(apiUrl(path), options || {});", html)
        self.assertIn("function apiUrl(path)", html)

    def test_gateway_allows_file_page_preflight(self):
        app = (ROOT / "gateway_py3" / "app.py").read_text(encoding="utf-8")

        self.assertIn("def do_OPTIONS(self):", app)
        self.assertIn('self.send_header("Access-Control-Allow-Origin", "*")', app)
        self.assertIn('self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")', app)

    def test_web_uses_arcmap_bridge_for_sync_and_execution(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("async function loadArcMapBridges()", html)
        self.assertIn("api('/arcmap/bridges')", html)
        self.assertIn("async function syncArcMap()", html)
        self.assertIn("api('/arcmap/sync'", html)
        self.assertIn("api('/arcmap/execute-approved'", html)
        self.assertIn("发送并执行", html)

    def test_skill_mentions_multi_arcmap_selection(self):
        skill = (ROOT / "agent_integrations" / "geopilot-arcmap" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("arcmap-list", skill)
        self.assertIn("arcmap-select", skill)
        self.assertIn("--hwnd", skill)
        self.assertIn("multiple ArcMap instances", skill)


if __name__ == "__main__":
    unittest.main()
