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

    def test_project_form_is_not_closed_by_config_polling_in_full_mode(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("if (!fullMode) document.getElementById('sidebarProjectForm').hidden = true;", html)

    def test_pending_user_message_survives_workflow_polling(self):
        html = (ROOT / "gateway_py3" / "web" / "index.html").read_text(encoding="utf-8")

        self.assertIn("let transientUserMessage = '';", html)
        self.assertIn("function appendTransientConversation", html)
        self.assertIn("if (transientUserMessage && currentMode !== 'full_agent' && !selectedWorkflowId) return;", html)

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


if __name__ == "__main__":
    unittest.main()
