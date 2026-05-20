from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .agent_tools import AgentToolError, AgentToolRuntime
from .catalog_loader import OperationCatalog
from .deepseek_client import DeepSeekClient
from .file_resolver import FileResolver
from .logs import write_event
from .validators import ValidationError, context_hash, friendly_validation_message, prepare_workflow
from .workflow_store import WorkflowStore


MAX_TOOL_ROUNDS = 8
MAX_FILE_SEARCH_NUDGES = 1

SYSTEM_PROMPT = """You are ArcMap AI Assistant.
You help non-technical ArcGIS/ArcMap users turn natural Chinese requests into safe GIS workflow drafts.

Hard rules:
- Use tool calls when you need local file resolution, operation schemas, current ArcGIS context, or workflow validation.
- Never write Python code.
- Never write SQL where clauses. Attribute filters must use structured where objects only.
- Never invent ArcPy tools. Workflow steps may only use registered operation ids from the catalog.
- Never execute anything. You only propose a workflow; the user and ArcGIS runtime execute later.
- The final proposal must be submitted with workflow_propose, or as a JSON object with action, summary, and steps.
- action must be exactly execute, clarify, or unsupported.
- clarify must be one clear Chinese question the user can answer.
- unsupported must explain the missing capability in Chinese and contain no executable steps.
- execute must contain ordered steps with id, operation, arguments, and reason.
- Do not invent argument names. If the operation index is not enough, call catalog_get_operation_schema before proposing that operation.
- output_name must use ASCII letters, numbers, and underscores only, and must not start with a number.
- If the user asks for default GDB output, read arcgis_context.default_gdb or call arcgis_get_context, then pass that exact path as output_workspace.
- You parse natural language into structured tool arguments. Tools do not parse natural language for you.
- For local files, call file_resolve with structured arguments only: path, folder_path, drive, directory, directory_parts, file_name, extensions.
- Do not pass Chinese sentences or phrases into file_resolve.
- Do not invent or expand file paths. Use path/folder_path only when the user provided that exact path. If the user only provided a drive and file name, call file_resolve with drive and file_name only.
- Do not reuse path components from recent_conversation unless the user explicitly refers to the same folder or previous result.
- If the user asks to open local files and then process those opened files, call file_resolve and use the resolved layer_name values as later step inputs.
- If file_resolve returns status clarify with child_directories, the question is only a fallback for the user. First use those directory names as local facts and call file_resolve again on a small number of plausible next directories. Do not blindly enumerate every directory, and do not recursively scan a drive root.
- Do not stop after the first file_resolve clarify when child_directories is not empty. You must try at least one plausible child directory before asking the user.
- Only ask the user to clarify after file_resolve has no useful child_directories, too many equal candidates, or no plausible next directory remains.
- Final clarify text must be a normal Chinese question. Summarize what was checked briefly; do not dump long directory lists unless the user explicitly needs choices.
- If a later step uses a dataset produced by an earlier step, reference it as from_step:step_id when the layer name is not enough.
- Do not mention JSON, schema, tool calls, operation ids, catalog internals, or validation internals to the user.
- Do not overwrite existing data.
"""


class PlannerError(Exception):
    pass


class AgenticPlanner:
    def __init__(
        self,
        catalog: OperationCatalog | None = None,
        client: DeepSeekClient | None = None,
        store: WorkflowStore | None = None,
        file_resolver: FileResolver | None = None
    ):
        self.catalog = catalog or OperationCatalog()
        self.client = client or DeepSeekClient()
        self.store = store or WorkflowStore()
        self.file_resolver = file_resolver or FileResolver()

    def plan(self, command: str, context: Dict[str, Any]) -> Dict[str, Any]:
        tool_runtime = AgentToolRuntime(self.catalog, self.store, context, self.file_resolver)
        tools = tool_runtime.tools()
        messages = self._messages(command, context, tool_runtime.operation_index())
        trace: List[Dict[str, Any]] = []

        write_event("agent.request", {
            "command": command,
            "context_hash": context_hash(context),
            "operation_count": len(self.catalog.operations)
        })

        validation_feedback_count = 0
        pending_question = ""
        file_search_nudges = 0
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.chat_agent(messages, tools)
            assistant_message = response["message"]
            usage = response.get("usage", {})
            messages.append(_message_for_history(assistant_message))
            trace.append({"type": "assistant", "usage": usage, "message": _redacted_message(assistant_message)})

            try:
                proposal = self._proposal_from_message(assistant_message)
            except AgentToolError as exc:
                return self._store_clarification(command, context, friendly_validation_message(exc), trace)
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
                finalized, feedback = self._try_finalize(command, context, proposal, trace)
                if finalized is not None:
                    return finalized
                validation_feedback_count += 1
                if validation_feedback_count > 1:
                    return self._store_clarification(command, context, feedback, trace)
                messages.append(_tool_message(_proposal_tool_call_id(assistant_message), {"ok": False, "error": feedback}))
                continue

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                content_workflow = _json_workflow_from_content(assistant_message.get("content"))
                if content_workflow is None:
                    content_text = _assistant_content(assistant_message)
                    if (
                        _file_result_can_continue(trace)
                        and _generic_clarification(content_text)
                        and file_search_nudges < MAX_FILE_SEARCH_NUDGES
                    ):
                        messages.append(_file_search_nudge_message())
                        file_search_nudges += 1
                        continue
                    summary = (
                        content_text
                        if content_text and not _generic_clarification(content_text)
                        else pending_question or "这个任务还不够明确，请补充要操作的数据、处理方式或输出位置。"
                    )
                    return self._store_clarification(command, context, summary, trace)
                if (
                    _premature_file_clarification(content_workflow, trace)
                    and _generic_clarification(str(content_workflow.get("summary", "")))
                    and file_search_nudges < MAX_FILE_SEARCH_NUDGES
                ):
                    messages.append(_file_search_nudge_message())
                    file_search_nudges += 1
                    continue
                content_workflow = _merge_pending_question(content_workflow, pending_question)
                finalized, feedback = self._try_finalize(command, context, content_workflow, trace)
                if finalized is not None:
                    return finalized
                return self._store_clarification(command, context, feedback, trace)

            for tool_call in tool_calls:
                try:
                    name, arguments = _tool_call_parts(tool_call)
                except AgentToolError as exc:
                    result = {"ok": False, "error": friendly_validation_message(exc)}
                    messages.append(_tool_message(tool_call.get("id"), result))
                    trace.append({"type": "tool", "name": "<invalid>", "arguments": {}, "result": result})
                    continue
                write_event("agent.tool_call", {"name": name, "arguments": arguments})
                try:
                    result = tool_runtime.handle(name, arguments)
                except (AgentToolError, ValidationError, ValueError) as exc:
                    result = {"ok": False, "error": friendly_validation_message(exc)}
                write_event("agent.tool_result", {"name": name, "result": result})
                trace.append({"type": "tool", "name": name, "arguments": arguments, "result": result})
                pending_question = _question_from_tool_result(result) or pending_question

                if name == "workflow_propose":
                    if result.get("ok"):
                        return self._store_workflow(command, context, result["workflow"], trace)
                    validation_feedback_count += 1
                    if validation_feedback_count > 1:
                        return self._store_clarification(command, context, result.get("error", "这个任务信息还不完整。"), trace)

                messages.append(_tool_message(tool_call.get("id"), result))

        return self._store_clarification(command, context, pending_question or "这个任务还不够明确，请补充要操作的数据、处理方式或输出位置。", trace)

    def _messages(self, command: str, context: Dict[str, Any], operation_index: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        payload = {
            "user_request": command,
            "arcgis_context": _context_summary(context),
            "operation_index": operation_index,
            "recent_conversation": _recent_conversation(self.store)
        }
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)}
        ]

    def _proposal_from_message(self, message: Dict[str, Any]) -> Dict[str, Any] | None:
        for tool_call in message.get("tool_calls") or []:
            name, arguments = _tool_call_parts(tool_call)
            if name == "workflow_propose":
                return _workflow_from_proposal_arguments(arguments)
        return None

    def _try_finalize(
        self,
        command: str,
        context: Dict[str, Any],
        workflow: Dict[str, Any],
        trace: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any] | None, str]:
        try:
            prepared = prepare_workflow(workflow, self.catalog, context)
        except ValidationError as exc:
            return None, friendly_validation_message(exc)
        return self._store_workflow(command, context, prepared, trace), ""

    def _store_workflow(self, command: str, context: Dict[str, Any], workflow: Dict[str, Any], trace: List[Dict[str, Any]]) -> Dict[str, Any]:
        row = self.store.create_draft(command, context_hash(context), workflow, trace)
        write_event("agent.final_workflow", {
            "workflow_id": row["id"],
            "workflow": workflow
        })
        return row

    def _store_clarification(self, command: str, context: Dict[str, Any], summary: str, trace: List[Dict[str, Any]]) -> Dict[str, Any]:
        workflow = {"action": "clarify", "summary": summary, "steps": []}
        row = self.store.create_draft(command, context_hash(context), workflow, trace)
        write_event("agent.final_workflow", {
            "workflow_id": row["id"],
            "workflow": workflow
        })
        return row


def _context_summary(context: Dict[str, Any]) -> Dict[str, Any]:
    layers = []
    for layer in context.get("layers", []) or []:
        layers.append({
            "layer_ref": layer.get("layer_ref"),
            "name": layer.get("name"),
            "longName": layer.get("longName"),
            "fields": [field.get("name") for field in layer.get("fields", [])[:80]],
            "field_types": {field.get("name"): field.get("type") for field in layer.get("fields", [])[:80] if field.get("name")},
            "selected_count": layer.get("selected_count"),
            "visible": layer.get("visible"),
            "geometry_type": layer.get("geometry_type")
        })
    return {
        "mxd_path": context.get("mxd_path"),
        "is_saved": context.get("is_saved"),
        "default_gdb": context.get("default_gdb"),
        "active_view": context.get("active_view"),
        "spatial_reference": context.get("spatial_reference"),
        "layers": layers
    }


def _recent_conversation(store: WorkflowStore) -> List[Dict[str, Any]]:
    history = []
    try:
        rows = store.list_recent(limit=6)
    except Exception:
        return history
    for row in reversed(rows):
        workflow = row.get("workflow") or {}
        history.append({
            "command": row.get("command"),
            "status": row.get("status"),
            "action": workflow.get("action"),
            "summary": workflow.get("summary")
        })
    return history


def _tool_call_parts(tool_call: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    function = tool_call.get("function") or {}
    name = function.get("name")
    raw_arguments = function.get("arguments") or "{}"
    if not isinstance(name, str) or not name:
        raise AgentToolError("Tool call missing function name.")
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except ValueError:
        raise AgentToolError("Tool arguments must be valid JSON.")
    if not isinstance(arguments, dict):
        raise AgentToolError("Tool arguments must be an object.")
    return name, arguments


def _workflow_from_proposal_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(arguments.get("workflow"), dict):
        return arguments["workflow"]
    return {
        "action": arguments.get("action"),
        "summary": arguments.get("summary"),
        "steps": arguments.get("steps")
    }


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
        )
    }


def _message_for_history(message: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "role": message.get("role", "assistant"),
        "content": message.get("content")
    }
    if message.get("tool_calls"):
        result["tool_calls"] = message["tool_calls"]
    return result


def _tool_message(tool_call_id: str | None, result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id or "workflow_propose",
        "content": json.dumps(result, ensure_ascii=False, sort_keys=True)
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
        "tool_calls": message.get("tool_calls")
    }
