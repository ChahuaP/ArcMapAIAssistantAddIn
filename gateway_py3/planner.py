from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from .catalog_loader import OperationCatalog
from .deepseek_client import DeepSeekClient
from .file_resolver import FileResolver
from .logs import write_event
from .router import OperationRouter
from .validators import ValidationError, context_hash, validate_workflow
from .workflow_store import WorkflowStore


SYSTEM_PROMPT = """You are ArcMap AI Assistant planner.
You must return only valid JSON.
Never write Python code.
Never write SQL where clauses. For attribute filters, return structured where objects only.
Never invent ArcPy tools.
Only use operations listed in selected_operations for executable workflow steps.
Return exactly one of these action values: execute, clarify, unsupported.
If the request is ambiguous, return action="clarify", steps=[] and put one clear, user-facing Chinese question in summary.
If any required parameter is missing or unclear, ask a concise follow-up question instead of guessing.
If the referenced layer or field is absent or ambiguous in arcmap_context, ask the user to clarify and mention the closest visible candidates when helpful.
For writes_data operations, if the output location is unclear, ask where to store the output instead of choosing a hidden default.
For edits_data operations, explain in the summary that the operation will directly modify source data and requires confirmation.
When arcmap_context.is_saved is true, mxd_default output policies mean the MXD folder is an explicit default.
When the user asks to output to the default GDB, use arcmap_context.default_gdb as output_workspace.
If the request needs an operation not available in selected_operations or catalog_index, return action="unsupported", steps=[] and explain the missing capability in user-facing Chinese.
Never mention selected_operations, catalog_index, JSON, schema, operation cards, step ids, internal tools, or prompt instructions to the user.
For executable workflows, summary must be a plain Chinese sentence describing what will happen.
If the request contains multiple GIS operations, return all of them as ordered workflow steps.
When a later step uses a dataset produced by an earlier step, reference it as from_step:step_id.
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
        store: WorkflowStore | None = None,
        file_resolver: FileResolver | None = None
    ):
        self.catalog = catalog or OperationCatalog()
        self.router = router or OperationRouter(self.catalog)
        self.client = client or DeepSeekClient()
        self.store = store or WorkflowStore()
        self.file_resolver = file_resolver or FileResolver()

    def plan(self, command: str, context: Dict[str, Any]) -> Dict[str, Any]:
        command = self._effective_command(command)
        parsed = self.file_resolver.parse_command(command)
        planning_text = parsed.clean_text

        local_workflow = _local_workflow(planning_text)
        if local_workflow is not None:
            local_workflow = prepare_workflow(planning_text, local_workflow, self.catalog, context)
            row = self.store.create_draft(command, context_hash(context), local_workflow, [])
            write_event("plan.local_response", {
                "workflow_id": row["id"],
                "workflow": local_workflow
            })
            return row

        basemap_workflow = _local_basemap_workflow(planning_text)
        if basemap_workflow is not None:
            local_workflow = prepare_workflow(planning_text, basemap_workflow, self.catalog, context)
            row = self.store.create_draft(command, context_hash(context), local_workflow, [])
            write_event("plan.local_response", {
                "workflow_id": row["id"],
                "workflow": local_workflow
            })
            return row

        file_resolution = parsed.file_resolution
        if file_resolution is not None:
            if getattr(file_resolution, "status", "") != "resolved":
                local_workflow = prepare_workflow(planning_text, file_resolution.workflow(), self.catalog, context)
                row = self.store.create_draft(command, context_hash(context), local_workflow, [])
                write_event("plan.local_response", {
                    "workflow_id": row["id"],
                    "workflow": local_workflow
                })
                return row

            prefix_workflow = file_resolution.workflow()
            excluded_followup_ids = {"layer.add_layer"}

            if not _has_followup_operation(planning_text, self.catalog, excluded_followup_ids):
                local_workflow = prepare_workflow(planning_text, prefix_workflow, self.catalog, context)
                row = self.store.create_draft(command, context_hash(context), local_workflow, [])
                write_event("plan.local_response", {
                    "workflow_id": row["id"],
                    "workflow": local_workflow
                })
                return row

            return self._plan_with_prefix(command, planning_text, context, prefix_workflow)

        selected = self.router.select(planning_text, context)
        if not selected:
            selected = self.router.fallback(planning_text, context)

        selected_cards = [self.catalog.model_card(operation) for operation in selected]
        selected_ids = [operation["id"] for operation in selected]
        messages = self._messages(planning_text, context, selected_cards)

        write_event("plan.request", {
            "command": command,
            "planning_text": planning_text,
            "context_hash": context_hash(context),
            "selected_operations": selected_ids
        })
        workflow = self.client.chat_json(messages)
        usage = workflow.pop("_usage", {})
        workflow = prepare_workflow(planning_text, workflow, self.catalog, context)

        row = self.store.create_draft(command, context_hash(context), workflow, selected_ids)
        write_event("plan.response", {
            "workflow_id": row["id"],
            "usage": usage,
            "workflow": workflow
        })
        return row

    def _plan_with_prefix(self, command: str, planning_text: str, context: Dict[str, Any], prefix_workflow: Dict[str, Any]) -> Dict[str, Any]:
        prefix_workflow = prepare_workflow(planning_text, prefix_workflow, self.catalog, context)
        if prefix_workflow.get("action") != "execute":
            row = self.store.create_draft(command, context_hash(context), prefix_workflow, [])
            write_event("plan.local_response", {
                "workflow_id": row["id"],
                "workflow": prefix_workflow
            })
            return row

        augmented_context = _context_with_preplanned_layers(context, prefix_workflow)
        selected = [
            operation
            for operation in self.router.select(planning_text, augmented_context, limit=12)
            if operation["id"] != "layer.add_layer"
        ]
        if not selected:
            selected = [
                operation
                for operation in self.router.fallback(planning_text, augmented_context)
                if operation["id"] != "layer.add_layer"
            ]

        selected_cards = [self.catalog.model_card(operation) for operation in selected]
        selected_ids = _prefix_operation_ids(prefix_workflow) + [operation["id"] for operation in selected]
        messages = self._messages(planning_text, augmented_context, selected_cards, prefix_workflow)

        write_event("plan.request", {
            "command": command,
            "planning_text": planning_text,
            "context_hash": context_hash(context),
            "selected_operations": selected_ids,
            "preplanned_steps": prefix_workflow.get("steps", [])
        })
        suffix_workflow = self.client.chat_json(messages)
        usage = suffix_workflow.pop("_usage", {})
        suffix_workflow = prepare_workflow(planning_text, suffix_workflow, self.catalog, augmented_context)
        workflow = _merge_preplanned_workflow(planning_text, prefix_workflow, suffix_workflow, self.catalog, augmented_context)

        row = self.store.create_draft(command, context_hash(context), workflow, selected_ids)
        write_event("plan.response", {
            "workflow_id": row["id"],
            "usage": usage,
            "workflow": workflow
        })
        return row

    def _effective_command(self, command: str) -> str:
        for clarification in self.store.recent_clarifications():
            if _looks_like_clarification_answer(command, clarification):
                return clarification["command"] + "\n用户补充：" + command
        return command

    def _messages(
        self,
        command: str,
        context: Dict[str, Any],
        selected_cards: List[Dict[str, Any]],
        preplanned_workflow: Dict[str, Any] | None = None
    ) -> List[Dict[str, str]]:
        stable_catalog_payload = json.dumps(selected_cards, ensure_ascii=False, sort_keys=True)
        catalog_index_payload = json.dumps(_catalog_index(self.catalog), ensure_ascii=False, sort_keys=True)
        context_payload = json.dumps(_context_summary(context), ensure_ascii=False, sort_keys=True)
        user_payload = json.dumps({"command": command}, ensure_ascii=False)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": "catalog_index=" + catalog_index_payload},
            {"role": "system", "content": "selected_operations=" + stable_catalog_payload},
        ]
        if preplanned_workflow is not None:
            preplanned_payload = json.dumps(preplanned_workflow.get("steps", []), ensure_ascii=False, sort_keys=True)
            messages.append({
                "role": "system",
                "content": (
                    "preplanned_steps=" + preplanned_payload +
                    "\nThese steps will run before your returned steps. Return only the remaining steps after them. "
                    "Do not repeat or ask about any preplanned steps; they are already resolved. "
                    "You may reference the added layers by their names in arcmap_context."
                )
            })
        messages.append({"role": "user", "content": "arcmap_context=" + context_payload + "\nuser_request=" + user_payload})
        return messages


def _context_summary(context: Dict[str, Any]) -> Dict[str, Any]:
    layers = []
    for layer in context.get("layers", []):
        layers.append({
            "layer_ref": layer.get("layer_ref"),
            "name": layer.get("name"),
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


def normalize_workflow(workflow: Dict[str, Any], catalog: OperationCatalog | None = None) -> None:
    action = workflow.get("action")
    steps = workflow.get("steps") or []
    _ensure_step_ids(steps)
    for step in steps:
        normalize_step(step, catalog)
    if isinstance(action, str):
        action = action.strip().lower()
    workflow["action"] = _normalize_action(action, steps)
    workflow.setdefault("steps", [])


def prepare_workflow(command: str, workflow: Dict[str, Any], catalog: OperationCatalog, context: Dict[str, Any]) -> Dict[str, Any]:
    normalize_workflow(workflow, catalog)
    apply_default_gdb_output(command, workflow, catalog, context)
    clarify_if_needed(workflow, catalog, context)
    try:
        validate_workflow(workflow, catalog)
    except ValidationError as exc:
        return _clarify_from_validation_error(exc)
    return workflow


def _merge_preplanned_workflow(
    command: str,
    prefix_workflow: Dict[str, Any],
    suffix_workflow: Dict[str, Any],
    catalog: OperationCatalog,
    context: Dict[str, Any]
) -> Dict[str, Any]:
    if suffix_workflow.get("action") != "execute":
        return suffix_workflow

    prefix_steps = [dict(step) for step in prefix_workflow.get("steps", [])]
    suffix_steps = [dict(step) for step in suffix_workflow.get("steps", [])]
    id_map: Dict[str, str] = {}
    next_index = len(prefix_steps) + 1
    for step in suffix_steps:
        old_id = step.get("id")
        new_id = "step_%s" % next_index
        next_index += 1
        if isinstance(old_id, str) and old_id:
            id_map[old_id] = new_id
        step["id"] = new_id

    for step in suffix_steps:
        step["arguments"] = _remap_from_step_references(step.get("arguments") or {}, id_map)

    merged = {
        "action": "execute",
        "summary": _merged_summary(prefix_workflow, suffix_workflow),
        "steps": prefix_steps + suffix_steps
    }
    return prepare_workflow(command, merged, catalog, context)


def _prefix_operation_ids(workflow: Dict[str, Any]) -> List[str]:
    ids = []
    for step in workflow.get("steps", []):
        operation_id = step.get("operation")
        if operation_id and operation_id not in ids:
            ids.append(operation_id)
    return ids


def _remap_from_step_references(value: Any, id_map: Dict[str, str]) -> Any:
    if isinstance(value, str) and value.startswith("from_step:"):
        old_id = value[len("from_step:"):]
        return "from_step:" + id_map.get(old_id, old_id)
    if isinstance(value, list):
        return [_remap_from_step_references(item, id_map) for item in value]
    if isinstance(value, dict):
        return {key: _remap_from_step_references(item, id_map) for key, item in value.items()}
    return value


def _merged_summary(prefix_workflow: Dict[str, Any], suffix_workflow: Dict[str, Any]) -> str:
    prefix = prefix_workflow.get("summary") or ""
    suffix = suffix_workflow.get("summary") or ""
    if prefix and suffix:
        return prefix.rstrip("。") + "，然后" + suffix.lstrip("将").rstrip("。") + "。"
    return suffix or prefix or "将执行工作流。"


def _ensure_step_ids(steps: List[Dict[str, Any]]) -> None:
    used_ids = set()
    for step in steps:
        step_id = step.get("id")
        if isinstance(step_id, str) and step_id.strip():
            step["id"] = step_id.strip()
            used_ids.add(step["id"])

    next_index = 1
    for step in steps:
        step_id = step.get("id")
        if isinstance(step_id, str) and step_id.strip():
            continue
        while True:
            candidate = "step_%s" % next_index
            next_index += 1
            if candidate not in used_ids:
                step["id"] = candidate
                used_ids.add(candidate)
                break


def apply_default_gdb_output(command: str, workflow: Dict[str, Any], catalog: OperationCatalog, context: Dict[str, Any]) -> None:
    if workflow.get("action") != "execute":
        return
    command_requests_default_gdb = _mentions_default_gdb(command)
    steps = workflow.get("steps") or []
    step_requests_default_gdb = any(
        _mentions_default_gdb(((step.get("arguments") or {}).get("output_workspace") or ""))
        for step in steps
        if isinstance(step.get("arguments"), dict)
    )
    if not command_requests_default_gdb and not step_requests_default_gdb:
        return

    default_gdb = context.get("default_gdb")
    if not default_gdb:
        workflow["action"] = "clarify"
        workflow["summary"] = "当前 ArcGIS 上下文里没有读到默认 GDB。请先在 ArcGIS 中设置默认地理数据库，或直接说明输出到哪个 .gdb。"
        workflow["steps"] = []
        return

    for step in steps:
        operation_id = step.get("operation")
        if operation_id not in catalog.operations:
            continue
        operation = catalog.operations[operation_id]
        schema = operation.get("parameters_schema") or {}
        properties = schema.get("properties") or {}
        if operation.get("side_effects") == "writes_data" and "output_workspace" in properties:
            arguments = step.setdefault("arguments", {})
            if "output_workspace" not in arguments or _mentions_default_gdb(arguments.get("output_workspace")):
                arguments["output_workspace"] = default_gdb


def clarify_if_needed(workflow: Dict[str, Any], catalog: OperationCatalog, context: Dict[str, Any]) -> None:
    if workflow.get("action") != "execute":
        return
    for step in workflow.get("steps", []):
        operation_id = step.get("operation")
        if operation_id not in catalog.operations:
            continue
        operation = catalog.operations[operation_id]
        arguments = step.get("arguments") or {}
        clarification = _step_clarification(operation, arguments, context)
        if clarification:
            workflow["action"] = "clarify"
            workflow["summary"] = clarification
            workflow["steps"] = []
            return


def _step_clarification(operation: Dict[str, Any], arguments: Dict[str, Any], context: Dict[str, Any]) -> str | None:
    schema = operation.get("parameters_schema", {})
    for name in schema.get("required", []):
        value = arguments.get(name)
        if value is None or value == "":
            return "这个操作还缺少必要参数“%s”。请补充后我再继续。" % name

    layer_arguments = _layer_argument_names(operation)
    for name in layer_arguments:
        value = arguments.get(name)
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            issue = _layer_reference_issue(str(item), context)
            if issue:
                return issue

    if _requires_field(operation):
        issue = _field_arguments_issue(operation, arguments, context)
        if issue:
            return issue

    operation_id = operation.get("id")
    if operation_id == "table.add_field":
        issue = _new_field_issue(arguments, context)
        if issue:
            return issue

    if _writes_data_without_output_location(operation, arguments, context):
        return "这个操作会生成新数据，但当前输出位置还不明确。请告诉我输出到哪个文件夹或 GDB。"
    return None


def _writes_data_without_output_location(operation: Dict[str, Any], arguments: Dict[str, Any], context: Dict[str, Any]) -> bool:
    if operation.get("side_effects") != "writes_data":
        return False
    if arguments.get("output_workspace") or arguments.get("output_folder"):
        return False
    workspace = (operation.get("output_policy") or {}).get("workspace", "")
    if context.get("is_saved") and workspace.startswith("mxd_default"):
        return False
    return not context.get("is_saved")


def _mentions_default_gdb(value: Any) -> bool:
    text = str(value or "").replace(" ", "").lower()
    return any(marker in text for marker in (
        "默认gdb",
        "defaultgdb",
        "default.gdb",
        "默认地理数据库",
        "默认数据库"
    ))


def _clarify_from_validation_error(error: ValidationError) -> Dict[str, Any]:
    message = str(error)
    return {
        "action": "clarify",
        "summary": _friendly_validation_message(message),
        "steps": []
    }


def _friendly_validation_message(message: str) -> str:
    if "Step missing field: operation" in message:
        return "我还不能确定要执行哪一种 GIS 操作。请把任务再说具体一点，比如要缓冲、裁剪、选择、导出，还是添加图层。"
    if "Step missing field: arguments" in message:
        return "这个任务的参数还不完整。请补充图层名、字段名、距离、输出名等必要信息。"
    if "Step missing field:" in message:
        return "这个任务信息还不完整。请把要操作的图层、参数和输出位置再说清楚一点。"
    if "missing required argument:" in message:
        return "这个操作还缺少必要参数“%s”。请补充后我再继续。" % message.rsplit(":", 1)[-1].strip()
    if "has unknown arguments:" in message:
        return "有些参数我没看懂。请换一种更明确的说法，说明图层、字段、距离、输出名或输出位置。"
    if "Unknown operation" in message:
        return "当前版本还不支持这个操作。请换成已有能力，或告诉我你想完成的 GIS 处理目标。"
    return "这个任务描述我还没法稳定执行。请换一种更明确的说法。"


def _layer_argument_names(operation: Dict[str, Any]) -> List[str]:
    properties = operation.get("parameters_schema", {}).get("properties", {})
    names = []
    for name in properties:
        lowered = name.lower()
        if "layer" in lowered and "output" not in lowered:
            names.append(name)
    return names


def _layer_reference_issue(value: str, context: Dict[str, Any]) -> str | None:
    if value.startswith("from_step:"):
        return None
    if value.startswith("layer_ref:"):
        value = value[len("layer_ref:"):]
    layers = context.get("layers", [])
    names = [layer.get("name") or "" for layer in layers]
    long_names = [layer.get("longName") or "" for layer in layers]
    refs = [layer.get("layer_ref") or "" for layer in layers]
    all_values = list(dict.fromkeys([item for item in names + long_names + refs if item]))
    exact = [item for item in all_values if item == value]
    if len(exact) == 1:
        return None
    lowered = value.lower()
    insensitive = [item for item in all_values if item.lower() == lowered]
    if len(insensitive) == 1:
        return None
    if len(exact) > 1 or len(insensitive) > 1:
        return "“%s”匹配到多个图层。请说明要使用哪一个图层。" % value
    candidates = _closest_candidates(value, names)
    if candidates:
        return "当前地图里没有“%s”图层。可用图层有：%s。请确认要使用哪个图层。" % (value, "、".join(candidates))
    return "当前地图里没有“%s”图层。请先添加图层，或说明要使用哪个已有图层。" % value


def _field_reference_issue(value: Any, context: Dict[str, Any], layer_value: str | None = None) -> str | None:
    values = value if isinstance(value, list) else [value]
    layers = context.get("layers", [])
    if layer_value:
        matched = _context_layers_matching(layer_value, context)
        if len(matched) == 1:
            if matched[0].get("fields_unknown"):
                return None
            layers = matched
    field_names = set()
    for layer in layers:
        for field in layer.get("fields", []):
            name = field.get("name")
            if name:
                field_names.add(name.lower())
    for item in values:
        if str(item).lower() not in field_names:
            return "当前地图字段里没有“%s”。请确认字段名。" % item
    return None


def _requires_field(operation: Dict[str, Any]) -> bool:
    requirements = operation.get("context_requirements") or {}
    return bool(requirements.get("requires_fields"))


def _field_arguments_issue(operation: Dict[str, Any], arguments: Dict[str, Any], context: Dict[str, Any]) -> str | None:
    layer_value = _primary_layer_value(operation, arguments)
    requirements = operation.get("context_requirements") or {}
    fields = []
    for name in requirements.get("field_arguments", []):
        if name in arguments:
            value = arguments[name]
            fields.extend(value if isinstance(value, list) else [value])
    for name in ("field", "fields", "dissolve_fields"):
        if name in arguments:
            value = arguments[name]
            fields.extend(value if isinstance(value, list) else [value])
    if requirements.get("condition_fields"):
        fields.extend(_condition_fields(arguments.get("where")))
    if requirements.get("assignment_fields"):
        assignments = arguments.get("assignments")
        if isinstance(assignments, dict):
            fields.extend(assignments.keys())
    return _field_reference_issue(fields, context, layer_value) if fields else None


def _primary_layer_value(operation: Dict[str, Any], arguments: Dict[str, Any]) -> str | None:
    for name in ("layer", "input_layer", "target_layer"):
        if isinstance(arguments.get(name), str):
            return arguments[name]
    names = _layer_argument_names(operation)
    if names and isinstance(arguments.get(names[0]), str):
        return arguments[names[0]]
    return None


def _new_field_issue(arguments: Dict[str, Any], context: Dict[str, Any]) -> str | None:
    layer_value = arguments.get("layer")
    field_name = arguments.get("field_name")
    if not layer_value or not field_name:
        return None
    matched = _context_layers_matching(str(layer_value), context)
    if len(matched) != 1:
        return None
    existing = {field.get("name", "").lower() for field in matched[0].get("fields", [])}
    if str(field_name).lower() in existing:
        return "“%s”字段已经存在。请换一个新字段名。" % field_name
    return None


def _context_layers_matching(value: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = value[len("layer_ref:"):] if value.startswith("layer_ref:") else value
    matches = []
    for layer in context.get("layers", []):
        if raw in (layer.get("layer_ref"), layer.get("name"), layer.get("longName")):
            matches.append(layer)
    if not matches:
        lowered = raw.lower()
        for layer in context.get("layers", []):
            if lowered in ((layer.get("name") or "").lower(), (layer.get("longName") or "").lower()):
                matches.append(layer)
    return matches


def _condition_fields(condition: Any) -> List[str]:
    if not isinstance(condition, dict):
        return []
    op = str(condition.get("op", condition.get("operator", ""))).lower()
    if op in ("and", "or"):
        fields = []
        for child in condition.get("conditions") or []:
            fields.extend(_condition_fields(child))
        return fields
    if op == "not":
        return _condition_fields(condition.get("condition"))
    field = condition.get("field")
    return [str(field)] if field else []


def _closest_candidates(value: str, names: List[str]) -> List[str]:
    if not names:
        return []
    lowered = value.lower()
    matches = [name for name in names if lowered in name.lower() or name.lower() in lowered]
    if matches:
        return matches[:5]
    return names[:5]


def _looks_like_clarification_answer(command: str, clarification: Dict[str, Any]) -> bool:
    text = (command or "").strip()
    if not text:
        return False
    summary = clarification["workflow"].get("summary", "")
    operation_words = ("缓冲", "裁剪", "相交", "融合", "投影", "导出", "选择", "缩放", "添加", "刷新")
    has_operation = any(word in text for word in operation_words)
    lowered = text.lower()
    location_markers = ("输出", "保存", "存到", "放到", "文件夹", "gdb", ":\\", ":/", "盘")
    if any(marker in summary for marker in ("输出到哪个", "哪个文件夹", "哪个 GDB", "哪个GDB", "输出位置")):
        return any(marker in lowered for marker in location_markers) and not has_operation
    if any(marker in summary for marker in ("想输出什么内容", "输出什么内容", "导出什么")):
        content_words = ("地图", "pdf", "png", "csv", "属性表", "表", "要素", "图层")
        return any(word in lowered for word in content_words) and not has_operation
    if any(marker in summary for marker in ("哪个图层", "哪一个图层", "使用哪个图层", "没有“")):
        return not any(marker in lowered for marker in location_markers) and not has_operation
    if any(marker in summary for marker in ("哪个字段", "字段名", "没有字段")):
        return not any(marker in lowered for marker in location_markers) and not has_operation
    if len(text) <= 40:
        return not has_operation
    markers = (
        "输出", "保存", "存到", "放到", "文件夹", "gdb", ":\\", ":/", "盘",
        "用", "选择", "是", "不是", "就这个", "这个", "字段", "图层"
    )
    if any(marker in lowered for marker in markers):
        return True
    if any(marker in summary for marker in ("输出", "图层", "字段", "参数", "哪个", "哪一个")):
        return not any(word in text for word in operation_words)
    return False


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


def _local_basemap_workflow(command: str) -> Dict[str, Any] | None:
    provider = _basemap_provider(command)
    if provider is None:
        return None
    return {
        "action": "unsupported",
        "summary": "当前 ArcMap Python Add-in 版本暂不支持自动添加底图。ArcMap 手工可以通过 GIS Servers 添加 WMS/WMTS，但 ArcPy 不能稳定从 URL 直接创建底图图层；后续需要 C# ArcObjects 或预制 .lyr 方案。",
        "steps": []
    }


def _basemap_provider(command: str) -> str | None:
    text = (command or "").lower()
    compact = re.sub(r"\s+", "", text)
    mentions_basemap = any(word in compact for word in ("底图", "basemap", "高德", "天地图", "openstreetmap", "osm", "worldimagery", "esri", "wmts", "xyz"))
    if not mentions_basemap:
        return None
    return "unsupported"


def _has_followup_operation(command: str, catalog: OperationCatalog, excluded_ids: set[str] | None = None) -> bool:
    excluded_ids = excluded_ids or {"layer.add_layer"}
    normalized = (command or "").lower()
    for operation in catalog.all_operations():
        if operation["id"] in excluded_ids:
            continue
        haystack = " ".join([
            operation["id"],
            operation.get("summary", ""),
            " ".join(operation.get("keywords", []))
        ]).lower()
        for keyword in _operation_terms(haystack):
            if keyword and keyword in normalized:
                return True
    return False


def _operation_terms(haystack: str) -> List[str]:
    terms = [item for item in re.split(r"[\s,，、/]+", haystack) if len(item) >= 2]
    compact_terms = []
    for term in terms:
        compact_terms.append(term)
        if term.endswith("分析") and len(term) > 2:
            compact_terms.append(term[:-2])
        if term.startswith("analysis."):
            compact_terms.append(term.split(".", 1)[1])
        if term.startswith("selection."):
            compact_terms.append(term.split(".", 1)[1])
        if term.startswith("table."):
            compact_terms.append(term.split(".", 1)[1])
        if term.startswith("view."):
            compact_terms.append(term.split(".", 1)[1])
    return compact_terms


def _context_with_preplanned_layers(context: Dict[str, Any], workflow: Dict[str, Any]) -> Dict[str, Any]:
    augmented = dict(context)
    layers = list(context.get("layers", []))
    for step in workflow.get("steps", []):
        if step.get("operation") != "layer.add_layer":
            continue
        path = (step.get("arguments") or {}).get("path")
        if not path:
            continue
        name = _path_stem(path)
        layers.append({
            "layer_ref": "from_step:%s" % step["id"],
            "name": name,
            "longName": name,
            "visible": True,
            "isFeatureLayer": True,
            "dataSource": path,
            "fields": [],
            "fields_unknown": True,
            "selected_count": 0,
            "geometry_type": None
        })
    augmented["layers"] = layers
    return augmented


def _path_stem(path: str) -> str:
    return Path(path).stem


def _catalog_index(catalog: OperationCatalog) -> List[Dict[str, str]]:
    return [
        {
            "id": operation["id"],
            "category": operation["category"],
            "summary": operation["summary"]
        }
        for operation in catalog.all_operations()
    ]
