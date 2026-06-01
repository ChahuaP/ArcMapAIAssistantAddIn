from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from gateway_py3.agent_tools import AgentToolError, AgentToolRuntime


class AgentToolExecutor:
    def __init__(self, runtime: AgentToolRuntime):
        self.runtime = runtime

    def tools(self) -> List[Dict[str, Any]]:
        return self.runtime.tools()

    def operation_index(self) -> List[Dict[str, str]]:
        return self.runtime.operation_index()

    def handle(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return self.runtime.handle(name, arguments)


def tool_call_parts(tool_call: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
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


def workflow_from_proposal_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(arguments.get("workflow"), dict):
        return arguments["workflow"]
    return {
        "action": arguments.get("action"),
        "summary": arguments.get("summary"),
        "steps": arguments.get("steps"),
    }
