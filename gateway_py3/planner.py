from __future__ import annotations

import json
from typing import Any, Dict, List

from .catalog_loader import OperationCatalog
from .deepseek_client import DeepSeekClient
from .logs import write_event
from .router import OperationRouter
from .validators import context_hash, validate_workflow
from .workflow_store import WorkflowStore


SYSTEM_PROMPT = """You are ArcMap AI Assistant planner.
You must return only valid JSON.
Never write Python code.
Never invent ArcPy tools.
Only use operations listed in selected_operations for executable workflow steps.
Return exactly one of these action values: execute, clarify, unsupported.
If the request is ambiguous, return action="clarify", steps=[] and put one clear, user-facing Chinese question in summary.
If the request needs an operation not available in selected_operations or catalog_index, return action="unsupported", steps=[] and explain the missing capability in user-facing Chinese.
Never mention selected_operations, catalog_index, JSON, schema, operation cards, step ids, internal tools, or prompt instructions to the user.
For executable workflows, summary must be a plain Chinese sentence describing what will happen.
Use explicit step ids: step_1, step_2.
Do not overwrite existing data.
"""


class PlannerError(Exception):
    pass


class Planner:
    def __init__(
        self,
        catalog: OperationCatalog | None = None,
        router: OperationRouter | None = None,
        client: DeepSeekClient | None = None,
        store: WorkflowStore | None = None
    ):
        self.catalog = catalog or OperationCatalog()
        self.router = router or OperationRouter(self.catalog)
        self.client = client or DeepSeekClient()
        self.store = store or WorkflowStore()

    def plan(self, command: str, context: Dict[str, Any]) -> Dict[str, Any]:
        local_workflow = _local_workflow(command)
        if local_workflow is not None:
            row = self.store.create_draft(command, context_hash(context), local_workflow, [])
            write_event("plan.local_response", {
                "workflow_id": row["id"],
                "workflow": local_workflow
            })
            return row

        selected = self.router.select(command, context)
        if not selected:
            selected = self.router.fallback(command, context)

        selected_cards = [self.catalog.model_card(operation) for operation in selected]
        selected_ids = [operation["id"] for operation in selected]
        messages = self._messages(command, context, selected_cards)

        write_event("plan.request", {
            "command": command,
            "context_hash": context_hash(context),
            "selected_operations": selected_ids
        })
        workflow = self.client.chat_json(messages)
        usage = workflow.pop("_usage", {})
        normalize_workflow(workflow, self.catalog)
        validate_workflow(workflow, self.catalog)

        row = self.store.create_draft(command, context_hash(context), workflow, selected_ids)
        write_event("plan.response", {
            "workflow_id": row["id"],
            "usage": usage,
            "workflow": workflow
        })
        return row

    def _messages(self, command: str, context: Dict[str, Any], selected_cards: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        stable_catalog_payload = json.dumps(selected_cards, ensure_ascii=False, sort_keys=True)
        catalog_index_payload = json.dumps(_catalog_index(self.catalog), ensure_ascii=False, sort_keys=True)
        context_payload = json.dumps(_context_summary(context), ensure_ascii=False, sort_keys=True)
        user_payload = json.dumps({"command": command}, ensure_ascii=False)
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": "catalog_index=" + catalog_index_payload},
            {"role": "system", "content": "selected_operations=" + stable_catalog_payload},
            {"role": "user", "content": "arcmap_context=" + context_payload + "\nuser_request=" + user_payload}
        ]


def _context_summary(context: Dict[str, Any]) -> Dict[str, Any]:
    layers = []
    for layer in context.get("layers", []):
        layers.append({
            "layer_ref": layer.get("layer_ref"),
            "name": layer.get("name"),
            "fields": [field.get("name") for field in layer.get("fields", [])[:80]],
            "selected_count": layer.get("selected_count"),
            "visible": layer.get("visible"),
            "geometry_type": layer.get("geometry_type")
        })
    return {
        "mxd_path": context.get("mxd_path"),
        "is_saved": context.get("is_saved"),
        "active_view": context.get("active_view"),
        "spatial_reference": context.get("spatial_reference"),
        "layers": layers
    }


def normalize_workflow(workflow: Dict[str, Any], catalog: OperationCatalog | None = None) -> None:
    action = workflow.get("action")
    steps = workflow.get("steps") or []
    for step in steps:
        normalize_step(step, catalog)
    if isinstance(action, str):
        action = action.strip().lower()
    workflow["action"] = _normalize_action(action, steps)
    workflow.setdefault("steps", [])


def normalize_step(step: Dict[str, Any], catalog: OperationCatalog | None = None) -> None:
    if "arguments" not in step:
        for alias in ("parameters", "params", "args"):
            if isinstance(step.get(alias), dict):
                step["arguments"] = step[alias]
                break
    if "arguments" not in step and catalog is not None and step.get("operation") in catalog.operations:
        operation = catalog.operations[step["operation"]]
        properties = operation.get("parameters_schema", {}).get("properties", {})
        inferred = {name: step[name] for name in properties if name in step}
        if inferred:
            step["arguments"] = inferred
    if "reason" not in step or not isinstance(step.get("reason"), str) or not step["reason"].strip():
        step["reason"] = _default_reason(step, catalog)


def _default_reason(step: Dict[str, Any], catalog: OperationCatalog | None = None) -> str:
    operation_id = step.get("operation")
    if catalog is not None and operation_id in catalog.operations:
        return catalog.operations[operation_id].get("summary") or ("执行 " + operation_id)
    return "执行该步骤"


def _normalize_action(action: str | None, steps: List[Dict[str, Any]]) -> str:
    if action in ("execute", "clarify", "unsupported"):
        return action
    if action in ("run", "do", "workflow", "plan", "tool", "operation", "buffer", "analysis", "执行", "运行", "工作流"):
        if steps:
            return "execute"
    if action in ("ask", "question", "need_clarification", "clarification", "需要补充", "追问"):
        return "clarify"
    if action in ("not_supported", "unsupported_operation", "cannot_do", "不支持", "暂不支持"):
        return "unsupported"
    return "execute" if steps else "clarify"


def _local_workflow(command: str) -> Dict[str, Any] | None:
    normalized = command.replace(" ", "").lower()
    wants_attribute_table_window = "属性表" in normalized and any(word in normalized for word in ("打开", "弹出", "窗口"))
    if wants_attribute_table_window:
        return {
            "action": "unsupported",
            "summary": "当前版本还不能直接打开 ArcGIS 的属性表窗口。可以先查看字段，或后续增加专门的“打开属性表”原子操作。",
            "steps": []
        }
    return None


def _catalog_index(catalog: OperationCatalog) -> List[Dict[str, str]]:
    return [
        {
            "id": operation["id"],
            "category": operation["category"],
            "summary": operation["summary"]
        }
        for operation in catalog.all_operations()
    ]
