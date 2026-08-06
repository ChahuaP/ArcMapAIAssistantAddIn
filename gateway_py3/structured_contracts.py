from __future__ import annotations

from typing import Dict

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


def workflow_contract_for_capabilities(capabilities) -> StructuredOutputContract:
    """Build the fixed provider-wire Workflow tool from selected cards.

    Operation arguments travel as canonical JSON text. The server remains the
    authority that decodes and validates each operation-specific object.
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
    for card in cards:
        parameters = card.get("parameters_schema")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError("workflow capability parameters_schema is invalid: " + card["id"])
    step_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "operation": {"type": "string", "enum": operation_ids},
            "arguments_json": {"type": "string", "minLength": 2},
            "reason": {"type": "string"},
        },
        "required": ["id", "operation", "arguments_json", "reason"],
        "additionalProperties": False,
    }
    workflow_schema = {
        "type": "object",
        "properties": {
            "action": {"const": "execute"},
            "summary": {"type": "string"},
            "steps": {"type": "array", "minItems": 1, "items": step_schema},
        },
        "required": ["action", "summary", "steps"],
        "additionalProperties": False,
    }
    return StructuredOutputContract(
        name="submit_workflow_v3",
        description="Submit a workflow through the fixed provider wire contract.",
        schema=_wrapper({"workflow_draft": workflow_schema}, ["workflow_draft"]),
    )


def structured_output_contract(name: str) -> StructuredOutputContract:
    try:
        return STRUCTURED_OUTPUT_CONTRACTS[name]
    except KeyError:
        raise ValueError("unknown structured response contract: %s" % name)
