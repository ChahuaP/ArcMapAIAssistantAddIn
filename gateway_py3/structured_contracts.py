"""Structured output contracts for provider wire calls.

The workflow planner uses native function-tool calling: each operation is a
separate tool with its own ``parameters`` JSON Schema.  The model sees the
exact field names and types each operation requires, so it never has to guess
argument shapes (the root cause of the old ``arguments_json`` opaque-string
design).
"""
from __future__ import annotations

from typing import Any, Dict, List

from .audit_contract import AUDIT_CONTRACT
from .task_contract import TASK_CONTRACT
from .llm_providers import StructuredOutputContract


def _wrapper(properties, required):
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


STRUCTURED_OUTPUT_CONTRACTS: Dict[str, StructuredOutputContract] = {
    "task_contract": TASK_CONTRACT,
    "audit": AUDIT_CONTRACT.tool_contract,
}


def tool_name_for_operation(operation_id: str) -> str:
    """Map an operation id (``layer.add_layer``) to a function-tool name.

    OpenAI function names allow ``[a-zA-Z0-9_-]`` (no dots); dots in
    operation ids become hyphens.  No operation id contains a hyphen, so
    the mapping is reversible.
    """
    return "step_" + operation_id.replace(".", "-")


def operation_id_from_tool(tool_name: str) -> str:
    """Inverse of :func:`tool_name_for_operation`."""
    if not tool_name.startswith("step_"):
        return tool_name
    return tool_name[5:].replace("-", ".")


def workflow_tools_for_capabilities(capabilities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build one OpenAI function tool per operation (native function calling).

    Each tool carries the operation's real ``parameters_schema`` as its
    ``parameters`` JSON Schema, so the model is structurally constrained to
    the correct argument names and types — no opaque ``arguments_json``
    string, no guessing.
    """
    if not isinstance(capabilities, list) or not capabilities:
        raise ValueError("workflow capabilities must be a non-empty array")
    cards = sorted(capabilities, key=lambda item: item.get("id", "") if isinstance(item, dict) else "")
    operation_ids = [item.get("id") for item in cards if isinstance(item, dict)]
    if (
        len(operation_ids) != len(cards)
        or any(not isinstance(operation_id, str) or not operation_id for operation_id in operation_ids)
        or len(set(operation_ids)) != len(operation_ids)
    ):
        raise ValueError("workflow capability identities are invalid")
    tools: List[Dict[str, Any]] = []
    for card in cards:
        parameters = card.get("parameters_schema")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError("workflow capability parameters_schema is invalid: " + card["id"])
        tools.append({
            "type": "function",
            "function": {
                "name": tool_name_for_operation(card["id"]),
                "description": card.get("summary", card["id"]),
                "parameters": parameters,
            },
        })
    return tools


def workflow_capability_index(capabilities: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Map tool-name → operation card for draft parsing."""
    return {tool_name_for_operation(c["id"]): c for c in capabilities if isinstance(c, dict)}


def structured_output_contract(name: str) -> StructuredOutputContract:
    try:
        return STRUCTURED_OUTPUT_CONTRACTS[name]
    except KeyError:
        raise ValueError("unknown structured response contract: %s" % name)
