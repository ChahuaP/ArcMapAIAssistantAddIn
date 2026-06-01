from __future__ import annotations

import json
from typing import Any, Dict, List

from gateway_py3.agent_tools import AgentToolError
from gateway_py3.agent_engine.events import publish_agent_progress
from gateway_py3.agent_engine.session import AgentSession
from gateway_py3.agent_engine.tools import AgentToolExecutor, tool_call_parts
from gateway_py3.logs import write_event
from gateway_py3.validators import ValidationError, friendly_validation_message


MAX_TOOL_ROUNDS = 8
MAX_FILE_SEARCH_NUDGES = 1


class AgentRunner:
    def __init__(self, state: Any, client: Any, tool_executor: AgentToolExecutor, strategy: Any):
        self.state = state
        self.client = client
        self.tool_executor = tool_executor
        self.strategy = strategy

    def run(self, session: AgentSession, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        tools = self.tool_executor.tools()
        trace: List[Dict[str, Any]] = []
        write_event("agent.request", {
            "command": session.command,
            "context_hash": session.context_hash,
            "operation_count": session.operation_count,
            "mode": session.mode,
        })
        publish_agent_progress(self.state, session, "analyze", "分析任务")

        validation_feedback_count = 0
        pending_question = ""
        file_search_nudges = 0
        for _ in range(MAX_TOOL_ROUNDS):
            publish_agent_progress(self.state, session, "generate_workflow", "生成 workflow")
            response = self.client.chat_agent(messages, tools)
            assistant_message = response["message"]
            usage = response.get("usage", {})
            messages.append(_message_for_history(assistant_message))
            trace.append({"type": "assistant", "usage": usage, "message": _redacted_message(assistant_message)})

            try:
                proposal = self.strategy.proposal_from_message(assistant_message)
            except AgentToolError as exc:
                return self.strategy.store_clarification(friendly_validation_message(exc), trace)
            if proposal is not None:
                if (
                    _premature_file_clarification(proposal, trace)
                    and _generic_clarification(str(proposal.get("summary", "")))
                    and file_search_nudges < MAX_FILE_SEARCH_NUDGES
                ):
                    messages.append(_file_search_nudge_message())
                    file_search_nudges += 1
                    continue
                proposal = _merge_pending_question(proposal, pending_question)
                publish_agent_progress(self.state, session, "validate", "校验任务")
                finalized, feedback = self.strategy.try_finalize(proposal, trace)
                if finalized is not None:
                    publish_agent_progress(self.state, session, "complete", "完成")
                    return finalized
                validation_feedback_count += 1
                if validation_feedback_count > self.strategy.repair_limit_for_feedback(feedback):
                    return self.strategy.store_unfinalized_feedback(feedback, trace)
                messages.append(_tool_message(_proposal_tool_call_id(assistant_message), {"ok": False, "error": feedback}))
                continue

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                finalized = self._handle_no_tool_calls(
                    session,
                    messages,
                    assistant_message,
                    trace,
                    pending_question,
                    file_search_nudges,
                    validation_feedback_count,
                )
                if isinstance(finalized, dict):
                    return finalized
                file_search_nudges, validation_feedback_count = finalized
                continue

            for tool_call in tool_calls:
                try:
                    name, arguments = tool_call_parts(tool_call)
                except AgentToolError as exc:
                    result = {"ok": False, "error": friendly_validation_message(exc)}
                    messages.append(_tool_message(tool_call.get("id"), result))
                    trace.append({"type": "tool", "name": "<invalid>", "arguments": {}, "result": result})
                    continue
                publish_agent_progress(self.state, session, _tool_stage(name), _tool_label(name), name)
                write_event("agent.tool_call", {"name": name, "arguments": arguments})
                try:
                    result = self.tool_executor.handle(name, arguments)
                except (AgentToolError, ValidationError, ValueError) as exc:
                    result = {"ok": False, "error": friendly_validation_message(exc)}
                write_event("agent.tool_result", {"name": name, "result": result})
                trace.append({"type": "tool", "name": name, "arguments": arguments, "result": result})
                if not result.get("repairable"):
                    pending_question = _question_from_tool_result(result) or pending_question

                if name == "workflow_propose":
                    if result.get("ok"):
                        publish_agent_progress(self.state, session, "validate", "校验任务")
                        finalized, feedback = self.strategy.try_finalize(result["workflow"], trace)
                        if finalized is not None:
                            publish_agent_progress(self.state, session, "complete", "完成")
                            return finalized
                        validation_feedback_count += 1
                        if validation_feedback_count > self.strategy.repair_limit_for_feedback(feedback):
                            return self.strategy.store_unfinalized_feedback(feedback, trace)
                        messages.append(_tool_message(tool_call.get("id"), {"ok": False, "error": feedback}))
                        continue
                    validation_feedback_count += 1
                    error_text = result.get("error", "这个任务信息还不完整。")
                    if validation_feedback_count > self.strategy.repair_limit_for_feedback(error_text):
                        return self.strategy.store_unfinalized_feedback(error_text, trace)

                messages.append(_tool_message(tool_call.get("id"), result))

        repair_feedback = self.strategy.latest_unresolved_toolbuilder_repair_feedback(trace)
        publish_agent_progress(self.state, session, "failed", "失败")
        return self.strategy.store_unfinalized_feedback(
            pending_question or repair_feedback or "这个任务还不够明确，请补充要操作的数据、处理方式或输出位置。",
            trace,
        )

    def _handle_no_tool_calls(
        self,
        session: AgentSession,
        messages: List[Dict[str, Any]],
        assistant_message: Dict[str, Any],
        trace: List[Dict[str, Any]],
        pending_question: str,
        file_search_nudges: int,
        validation_feedback_count: int,
    ) -> Dict[str, Any] | tuple[int, int]:
        content_workflow = _json_workflow_from_content(assistant_message.get("content"))
        if content_workflow is None:
            content_text = _assistant_content(assistant_message)
            tool_repair_feedback = self.strategy.latest_unresolved_toolbuilder_repair_feedback(trace)
            if tool_repair_feedback:
                validation_feedback_count += 1
                if validation_feedback_count > self.strategy.repair_limit_for_feedback(tool_repair_feedback):
                    return self.strategy.store_unfinalized_feedback(tool_repair_feedback, trace)
                messages.append(_assistant_repair_message(tool_repair_feedback))
                return file_search_nudges, validation_feedback_count
            if (
                _file_result_can_continue(trace)
                and _generic_clarification(content_text)
                and file_search_nudges < MAX_FILE_SEARCH_NUDGES
            ):
                messages.append(_file_search_nudge_message())
                return file_search_nudges + 1, validation_feedback_count
            if content_text and not _generic_clarification(content_text):
                if self.strategy.needs_public_rewrite(content_text):
                    return self.strategy.store_unfinalized_feedback(content_text, trace)
                publish_agent_progress(self.state, session, "complete", "完成")
                return self.strategy.store_answer(content_text, trace)
            summary = pending_question or "这个任务还不够明确，请补充要操作的数据、处理方式或输出位置。"
            return self.strategy.store_clarification(summary, trace)
        if (
            _premature_file_clarification(content_workflow, trace)
            and _generic_clarification(str(content_workflow.get("summary", "")))
            and file_search_nudges < MAX_FILE_SEARCH_NUDGES
        ):
            messages.append(_file_search_nudge_message())
            return file_search_nudges + 1, validation_feedback_count
        content_workflow = _merge_pending_question(content_workflow, pending_question)
        publish_agent_progress(self.state, session, "validate", "校验任务")
        finalized, feedback = self.strategy.try_finalize(content_workflow, trace)
        if finalized is not None:
            publish_agent_progress(self.state, session, "complete", "完成")
            return finalized
        if not self.strategy.is_validation_repair_feedback(feedback):
            return self.strategy.store_unfinalized_feedback(feedback, trace)
        validation_feedback_count += 1
        if validation_feedback_count > self.strategy.repair_limit_for_feedback(feedback):
            return self.strategy.store_unfinalized_feedback(feedback, trace)
        messages.append(_assistant_repair_message(feedback))
        return file_search_nudges, validation_feedback_count


def _tool_stage(name: str) -> str:
    if name == "arcgis_get_layer_profile":
        return "read_fields"
    if name in ("arcgis_get_context", "catalog_get_operation_schema", "catalog_list_operations"):
        return "read_capabilities"
    if name == "workflow_propose":
        return "validate"
    if name.startswith("toolbuilder_"):
        return "repair_tool"
    if name == "file_resolve":
        return "resolve_files"
    return "tool"


def _tool_label(name: str) -> str:
    return {
        "arcgis_get_layer_profile": "读取字段/样本",
        "arcgis_get_context": "读取地图上下文",
        "catalog_get_operation_schema": "读取能力",
        "catalog_list_operations": "读取能力",
        "workflow_propose": "校验任务",
        "file_resolve": "解析本地文件",
    }.get(name, "调用工具")


def _json_workflow_from_content(content: Any) -> Dict[str, Any] | None:
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _assistant_content(message: Dict[str, Any]) -> str:
    content = message.get("content")
    return content.strip() if isinstance(content, str) and content.strip() else ""


def _question_from_tool_result(result: Dict[str, Any]) -> str:
    status = result.get("status")
    question = result.get("question")
    if status in ("clarify", "unsupported") and isinstance(question, str) and question.strip():
        return question.strip()
    error = result.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    return ""


def _merge_pending_question(workflow: Dict[str, Any], pending_question: str) -> Dict[str, Any]:
    if not pending_question or workflow.get("action") != "clarify":
        return workflow
    summary = workflow.get("summary")
    if isinstance(summary, str) and summary.strip() and not _generic_clarification(summary):
        return workflow
    merged = dict(workflow)
    merged["summary"] = pending_question
    merged["steps"] = []
    return merged


def _generic_clarification(summary: str) -> bool:
    text = summary.strip()
    generic_markers = (
        "不明确",
        "不够明确",
        "不清楚",
        "需要更多信息",
        "需要补充",
        "补充要操作的数据",
        "请补充要操作的数据",
    )
    return any(marker in text for marker in generic_markers)


def _premature_file_clarification(workflow: Dict[str, Any], trace: List[Dict[str, Any]]) -> bool:
    return workflow.get("action") == "clarify" and _file_result_can_continue(trace)


def _file_result_can_continue(trace: List[Dict[str, Any]]) -> bool:
    for item in reversed(trace):
        if item.get("type") != "tool" or item.get("name") != "file_resolve":
            continue
        result = item.get("result") or {}
        return result.get("status") == "clarify" and bool(result.get("child_directories"))
    return False


def _file_search_nudge_message() -> Dict[str, str]:
    return {
        "role": "user",
        "content": (
            "The last file_resolve result included child_directories. "
            "Do not ask the user yet. Pick one plausible child directory from that list "
            "and call file_resolve again with structured arguments. "
            "Only ask the user if no plausible child directory remains."
        ),
    }


def _assistant_repair_message(feedback: str) -> Dict[str, str]:
    return {
        "role": "user",
        "content": "The previous workflow or tool call did not pass validation. Repair it and continue; do not ask the user. Validation feedback: %s" % feedback,
    }


def _message_for_history(message: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "role": message.get("role", "assistant"),
        "content": message.get("content"),
    }
    if message.get("reasoning_content"):
        result["reasoning_content"] = message["reasoning_content"]
    if message.get("tool_calls"):
        result["tool_calls"] = message["tool_calls"]
    return result


def _tool_message(tool_call_id: str | None, result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id or "workflow_propose",
        "content": json.dumps(result, ensure_ascii=False, sort_keys=True),
    }


def _proposal_tool_call_id(message: Dict[str, Any]) -> str | None:
    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        if function.get("name") == "workflow_propose":
            return tool_call.get("id")
    return None


def _redacted_message(message: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role": message.get("role"),
        "content": message.get("content"),
        "reasoning_content": message.get("reasoning_content"),
        "tool_calls": message.get("tool_calls"),
    }
