import json
import unittest
from types import SimpleNamespace

from gateway_py3.agent_engine import AgentRunner, AgentSession, AgentToolExecutor
from gateway_py3.agent_engine.tools import workflow_from_proposal_arguments
from gateway_py3.event_bus import EventBus


class AgentEngineTests(unittest.TestCase):
    def test_session_keeps_runtime_scope(self):
        session = AgentSession(
            command="打开 nanjing",
            context={"layers": []},
            mode="full_agent",
            project_id="project-1",
            project={"id": "project-1"},
            context_hash="abc",
            operation_count=3,
            permissions={"auto_execute": True},
        )

        self.assertEqual(session.mode, "full_agent")
        self.assertEqual(session.project_id, "project-1")
        self.assertTrue(session.permissions["auto_execute"])

    def test_tool_executor_wraps_runtime(self):
        runtime = _Runtime()
        executor = AgentToolExecutor(runtime)

        self.assertEqual(executor.operation_index(), [{"id": "view.refresh_view"}])
        self.assertEqual(executor.handle("catalog_list_operations", {})["ok"], True)

    def test_runner_records_trace_and_progress_events(self):
        bus = EventBus()
        state = SimpleNamespace(events=bus)
        runner = AgentRunner(
            state=state,
            client=_Client(),
            tool_executor=AgentToolExecutor(_Runtime()),
            strategy=_Strategy(),
        )
        session = AgentSession(
            command="刷新地图",
            context={},
            mode="semi_agent",
            project_id="",
            project=None,
            context_hash="ctx",
            operation_count=1,
        )

        result = runner.run(session, [{"role": "user", "content": "刷新地图"}])
        events = bus.wait_after(0, timeout=0)

        self.assertEqual(result["workflow"]["steps"][0]["operation"], "view.refresh_view")
        self.assertEqual(result["agent_trace"][0]["type"], "assistant")
        self.assertIn("agent.progress", [event["type"] for event in events])


class _Runtime:
    def tools(self):
        return []

    def operation_index(self):
        return [{"id": "view.refresh_view"}]

    def handle(self, name, arguments):
        return {"ok": True, "name": name, "arguments": arguments}


class _Client:
    def chat_agent(self, messages, tools):
        return {
            "message": {
                "role": "assistant",
                "content": json.dumps({
                    "action": "execute",
                    "summary": "刷新地图。",
                    "steps": [
                        {"id": "step_1", "operation": "view.refresh_view", "arguments": {}, "reason": "刷新当前地图。"}
                    ],
                }, ensure_ascii=False),
            },
            "usage": {"total_tokens": 1},
        }


class _Strategy:
    def proposal_from_message(self, message):
        for tool_call in message.get("tool_calls") or []:
            arguments = json.loads((tool_call.get("function") or {}).get("arguments") or "{}")
            return workflow_from_proposal_arguments(arguments)
        return None

    def try_finalize(self, workflow, trace):
        return {"workflow": workflow, "agent_trace": trace}, ""

    def repair_limit_for_feedback(self, feedback):
        return 1

    def store_unfinalized_feedback(self, feedback, trace):
        return {"feedback": feedback, "agent_trace": trace}

    def store_clarification(self, summary, trace):
        return {"summary": summary, "agent_trace": trace}

    def store_answer(self, summary, trace):
        return {"summary": summary, "agent_trace": trace}

    def latest_unresolved_toolbuilder_repair_feedback(self, trace):
        return ""

    def needs_public_rewrite(self, feedback):
        return False

    def is_validation_repair_feedback(self, feedback):
        return True


if __name__ == "__main__":
    unittest.main()
