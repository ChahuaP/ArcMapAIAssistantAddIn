from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Dict, List, Tuple

from .agent_tools import AgentToolError, AgentToolRuntime
from .catalog_loader import OperationCatalog
from .custom_tool_contract import PLANNER_CUSTOM_TOOL_CONTRACT
from .file_resolver import FileResolver
from .llm_providers import FULL_AGENT_MODE, SEMI_AGENT_MODE, ChatProvider, create_provider
from .logs import write_event
from .output_folder_resolver import OutputFolderResolver
from .validators import ValidationError, context_hash, friendly_validation_message, prepare_workflow
from .workflow_store import WorkflowStore


MAX_TOOL_ROUNDS = 8
MAX_FILE_SEARCH_NUDGES = 1
MAX_VALIDATION_REPAIRS = 3

SYSTEM_PROMPT = """You are GeoPilot.
You help non-technical ArcGIS/ArcMap users turn natural Chinese requests into safe GIS workflow drafts.

Hard rules:
- Use tool calls when you need local file resolution, operation schemas, current ArcGIS context, or workflow validation.
- Never write Python code in final user-facing answers or normal workflow steps. The only exception is executor_code inside toolbuilder_create_draft or toolbuilder_revise_draft tool calls.
- Never write SQL where clauses. Attribute filters must use structured where objects only.
- Attribute where operators are limited to: eq, ne, gt, gte, lt, lte, between, in, like, is_null, is_not_null, and, or, not.
- Use like only on text fields and with SQL wildcards for text patterns. Text contains must be {"field":"NAME","op":"like","value":"%南京%"}. Do not use contains, starts_with, ends_with, regex, or raw SQL.
- Boolean attribute filters must use full structured form: {"op":"and","conditions":[...]} or {"op":"or","conditions":[...]}. Never use shorthand such as {"and":[...]} or {"or":[...]}. Every leaf condition must include op.
- between uses values with exactly two items. in uses a non-empty values list. eq/ne/gt/gte/lt/lte/like use value. is_null/is_not_null do not use value.
- You are the planner. Tools only provide facts. Decide which tools to call, inspect the map/layers/fields/attribute samples when needed, then compose the workflow.
- Prefer composing existing atomic operations into a multi-step workflow. For example: inspect fields and samples, select by attribute/location, export selected features, split by field, export KML/KMZ, clear selection, then refresh/zoom if needed.
- Do not create a custom tool just because the user asks for a new workflow shape. Custom tools are only for reusable GIS algorithms or processing primitives that cannot be expressed by chaining the existing catalog operations.
- If an existing catalog operation is a real batch primitive for the user's goal, use it directly. For example, export.split_by_field with output_format=kmz is valid for "按字段分别导出 KML/KMZ"; otherwise compose smaller selection/export steps.
- Do not split user text into conditions by local string rules. Natural phrases are often partial or semantic: "k街道的乔木" may require inspecting a layer profile, finding that one field contains values like "xxx区k街道" and another field contains values like "乔木用地", then building an and condition with like wildcards.
- Layer mentions may appear as @图层名 from the UI. Treat @ as a selection marker and use the matching ArcGIS layer.
- Field mentions may appear as #字段名 from the UI. Treat # as a selection marker only. Workflow arguments must use the real field name without #.
- Never invent ArcPy tools. Workflow steps may only use registered operation ids from the catalog.
- Never execute anything. You only propose a workflow; the user and ArcGIS runtime execute later.
- The final proposal must be submitted with workflow_propose, or as a JSON object with action, summary, and steps.
- action must be exactly execute, clarify, unsupported, or answer.
- answer is for normal conversation that does not need ArcGIS execution, such as explaining previous project activity.
- clarify must be one clear Chinese question the user can answer.
- unsupported must explain the missing capability in Chinese and contain no executable steps.
- execute must contain ordered steps with id, operation, arguments, and reason.
- Every execute step must include reason. workflow_validate and workflow_propose require it. If validation says reason is missing, add the missing reason yourself and continue; do not ask the user to clarify.
- Do not invent argument names. If the operation index is not enough, call catalog_get_operation_schema before proposing that operation.
- For workflow operation arguments, use output_folder when the schema says output_folder. folder_path is only for the file_resolve tool.
- For output destinations, call output_folder_resolve with structured arguments only: path, parent_path, known_folder, folder_name. Use known_folder=desktop for the user's desktop, documents for documents, downloads for downloads, and project_output for the active project output folder.
- Never use file_resolve for output folders. file_resolve is only for local GIS input files to open or process.
- If the user names an output folder that does not resolve, ask one clear Chinese question or choose the active project output folder only when the user did not specify an output destination.
- When the user provides a numeric size with a unit, map it to the operation schema exactly. For example, "外接圆半径0.001度" is a concrete radius, not a clarification request; if the schema has radius_unit, set it to degrees.
- output_name is the final user-visible base filename or dataset name. It may be Chinese. Do not translate or romanize a user-provided Chinese name.
- output_name must be only the name body: no path, no extension such as .obj/.shp/.kmz, no dot, and no Windows-illegal characters <>:"/\|?*.
- For every writes_data step, you must decide output_name yourself from user_request. If the user specified a desired output/file name, preserve that exact naming intent as output_name after removing only a managed extension. If the user did not specify a name, create a clear descriptive name yourself. GeoPilot will not infer names from user text for you.
- In workflow_propose, never pass output_path. GeoPilot injects output_path only during execution from output_name plus output_folder/output_workspace.
- Before workflow_propose, self-check all writes_data steps: user-requested naming is preserved in output_name; unnamed outputs have model-chosen descriptive output_name; output_name is never omitted.
- If the user asks for default GDB output, read arcgis_context.default_gdb or call arcgis_get_context, then pass that exact path as output_workspace.
- You parse natural language into structured tool arguments. Tools do not parse natural language for you.
- For local files, call file_resolve with structured arguments only: path, folder_path, drive, directory, directory_parts, file_name, extensions.
- Do not pass Chinese sentences or phrases into file_resolve.
- Do not invent or expand file paths. Use path/folder_path only when the user provided that exact path. If the user only provided a drive and file name, call file_resolve with drive and file_name only.
- Do not reuse path components from recent_conversation unless the user explicitly refers to the same folder or previous result.
- If the user asks to open local files and then process those opened files, call file_resolve and use the resolved layer_name values as later step inputs.
- If file_resolve returns status clarify with child_directories, that question is only for the user after local search choices are exhausted. First use those directory names as local facts and call file_resolve again on a small number of plausible next directories. Do not blindly enumerate every directory, and do not recursively scan a drive root.
- Do not stop after the first file_resolve clarify when child_directories is not empty. You must try at least one plausible child directory before asking the user.
- Only ask the user to clarify after file_resolve has no useful child_directories, too many equal candidates, or no plausible next directory remains.
- Final clarify text must be a normal Chinese question. Summarize what was checked briefly; do not dump long directory lists unless the user explicitly needs choices.
- All current ArcGIS layers are listed in arcgis_context.layers. You choose the intended layer from that list.
- For existing ArcGIS layers, pass the chosen layer_ref, such as layer:0, in layer arguments. Do not pass a similar layer name when layer names are close.
- If a later step uses a dataset produced by an earlier step, reference it as from_step:step_id when the layer name is not enough.
- Writes_data operations add their generated output layer to ArcGIS automatically. Do not add layer.add_layer for outputs created by earlier workflow steps.
- Do not mention JSON, schema, tool calls, operation ids, catalog internals, or validation internals to the user.
- Do not overwrite existing data.
- If the current mode is full_agent, prefer the active project workdir for local data lookup and output planning.
- In full_agent mode, generated data should use arcgis_context.project_output_workspace when the user does not provide an output location.
- If existing operation chains cannot satisfy the user but the capability is feasible as a reusable ArcPy algorithm, call toolbuilder_create_draft to create a disabled draft tool package. Do not stop at unsupported just because no built-in operation exists.
- If operation_index already contains an enabled custom.* operation that matches the user's goal, use that operation in workflow_propose. Do not create or revise a custom tool just to run an already enabled capability.
- toolbuilder_create_draft is an agent tool, not an ArcGIS operation id. Never call catalog_get_operation_schema for toolbuilder.create_draft or toolbuilder_create_draft.
- When the user reports a custom tool bug, bad parameter design, or wants a review change, find the matching entry in custom_tools, call toolbuilder_get_draft, then call toolbuilder_revise_draft with the same tool_id. Do not create a duplicate custom tool for revisions.
- toolbuilder_get_draft and toolbuilder_revise_draft accept an internal UUID, a custom.* operation id such as custom.feature_to_star_polygon, or a custom_tool:<uuid>:execute executor reference. Never ask the user to provide existing custom tool executor code.
- If a workflow using a custom.* operation fails validation because an argument is unknown or the schema is missing a needed workflow argument, revise that custom tool schema in place instead of asking the user to rephrase.
- Custom tool executor_code is not free-form application code. It must run inside ArcMap Python 2.7 as one small function: def execute(context, arguments, step_outputs): ...
- Custom tool executor_code must start with # -*- coding: utf-8 -*- and use syntax valid in Python 2.7 only. Do not use f-strings, type annotations, pathlib, dataclasses, async, or raise ... from ...
- Custom tool executor_code must not use ArcGIS Pro APIs: no arcpy.mp and no ArcGISProject. It must not call arcpy.mapping.MapDocument, arcpy.mapping.ListLayers, or inspect CURRENT maps.
- Custom tool executor_code must not call getOutput. GeoPilot passes ArcMap Layer objects, not geoprocessing Result objects.
- Custom tool executor_code receives already-resolved arguments from GeoPilot. For layer parameters, arguments["input_layer"] is the ArcMap layer object; do not search for layers by name.
- Custom tool executor_code must not hide geometry or ArcPy failures with broad except/pass/continue. Unexpected errors must raise so the user can use "让 AI 修工具".
- For writes_data custom tools, define required output_name in operation_spec and write executor_code only to arguments["output_path"]. Do not declare output_path in parameters_schema. Do not read managed output arguments such as output_workspace, output_folder, output_format, output_name, or misspelled output variants such as outputfolder inside executor_code, and do not build output paths from arcpy.env.workspace or user arguments. Variable names like output_path_full do not count; the code must read arguments["output_path"] or arguments.get("output_path").
- Use output_policy.type deliberately: feature_class for ArcGIS vector outputs with gdb/shp formats, file for ordinary files such as .obj/.json/.csv with a declared extension, and raster for .tif raster outputs. File outputs may call open(arguments["output_path"], "w" or "wb") and must not open any other path.
- For CreateFeatureclass_management, split arguments["output_path"] with os.path.dirname/basename and pass spatial_reference from arcpy.Describe(input_layer).spatialReference. Do not pass context["spatial_reference"], spatialReference.name, factoryCode, strings, or layer.spatialReference.
- For SHAPE@ Polygon geometry in ArcMap, geom.getPart(i) returns an Array of Point objects. Iterate points directly and handle None ring separators; do not treat part.getObject(j) as a ring object with .count.
- Keep executor_code ASCII except the encoding header. Put Chinese descriptions in operation_spec, not in Python comments or string literals.
- Prefer stable ArcMap geoprocessing calls and arcpy.da cursors. When a built-in ArcPy tool exists, call it directly instead of manually reimplementing geometry logic.
- Custom operations already present in operation_index are enabled and reviewed. Do not tell the user they still need review.
- Custom tools listed in custom_tools with pending_review or rejected status are known tools, not unsupported capabilities. Do not use them as executable workflow operations until enabled. If the user wants to run them, answer that they need review/enablement first; if the user reports a bug or requests a change, revise the same tool in place.
- Custom geometry tools that add offsets to X/Y coordinates operate in the input coordinate system units. If the current spatial reference is geographic, raw coordinate radii are degrees; never invent a meter default for those tools.
Custom tool development contract:
""" + PLANNER_CUSTOM_TOOL_CONTRACT + """
"""


class PlannerError(Exception):
    pass


class AgenticPlanner:
    def __init__(
        self,
        catalog: OperationCatalog | None = None,
        client: ChatProvider | None = None,
        store: WorkflowStore | None = None,
        file_resolver: FileResolver | None = None,
        output_folder_resolver: OutputFolderResolver | None = None
    ):
        self.catalog = catalog or OperationCatalog()
        self.client = client
        self.store = store or WorkflowStore()
        self.file_resolver = file_resolver or FileResolver()
        self.output_folder_resolver = output_folder_resolver or OutputFolderResolver()

    def plan(
        self,
        command: str,
        context: Dict[str, Any],
        mode: str = "semi_agent",
        project_id: str | None = None
    ) -> Dict[str, Any]:
        if mode == SEMI_AGENT_MODE:
            self.store.clear_workflows(mode=SEMI_AGENT_MODE)
            project_id = ""
        project = self.store.get_project(project_id) if mode == FULL_AGENT_MODE and project_id else None
        if mode == FULL_AGENT_MODE and not project:
            raise PlannerError("全代理模式需要先选择一个项目工作目录。")
        if project:
            context = _context_for_project(context, project)
        client = self.client or create_provider(mode=mode)
        tool_runtime = AgentToolRuntime(self.catalog, self.store, context, self.file_resolver, self.output_folder_resolver, project)
        tools = tool_runtime.tools()
        messages = self._messages(command, context, tool_runtime.operation_index(), mode, project)
        trace: List[Dict[str, Any]] = []

        write_event("agent.request", {
            "command": command,
            "context_hash": context_hash(context),
            "operation_count": len(self.catalog.operations),
            "mode": mode,
            "project_id": project_id or ""
        })

        validation_feedback_count = 0
        pending_question = ""
        file_search_nudges = 0
        for _ in range(MAX_TOOL_ROUNDS):
            response = client.chat_agent(messages, tools)
            assistant_message = response["message"]
            usage = response.get("usage", {})
            messages.append(_message_for_history(assistant_message))
            trace.append({"type": "assistant", "usage": usage, "message": _redacted_message(assistant_message)})

            try:
                proposal = self._proposal_from_message(assistant_message)
            except AgentToolError as exc:
                return self._store_clarification(command, context, friendly_validation_message(exc), trace, mode, project_id)
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
                finalized, feedback = self._try_finalize(command, context, proposal, trace, mode, project_id)
                if finalized is not None:
                    return finalized
                validation_feedback_count += 1
                if validation_feedback_count > _repair_limit_for_feedback(feedback):
                    return self._store_unfinalized_feedback(command, context, feedback, trace, mode, project_id)
                messages.append(_tool_message(_proposal_tool_call_id(assistant_message), {"ok": False, "error": feedback}))
                continue

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                content_workflow = _json_workflow_from_content(assistant_message.get("content"))
                if content_workflow is None:
                    content_text = _assistant_content(assistant_message)
                    tool_repair_feedback = _latest_unresolved_toolbuilder_repair_feedback(trace)
                    if tool_repair_feedback:
                        validation_feedback_count += 1
                        if validation_feedback_count > _repair_limit_for_feedback(tool_repair_feedback):
                            return self._store_unfinalized_feedback(command, context, tool_repair_feedback, trace, mode, project_id)
                        messages.append(_assistant_repair_message(tool_repair_feedback))
                        continue
                    if (
                        _file_result_can_continue(trace)
                        and _generic_clarification(content_text)
                        and file_search_nudges < MAX_FILE_SEARCH_NUDGES
                    ):
                        messages.append(_file_search_nudge_message())
                        file_search_nudges += 1
                        continue
                    if content_text and not _generic_clarification(content_text):
                        if _needs_public_rewrite(content_text):
                            return self._store_unfinalized_feedback(command, context, content_text, trace, mode, project_id)
                        return self._store_answer(command, context, content_text, trace, mode, project_id)
                    summary = pending_question or "这个任务还不够明确，请补充要操作的数据、处理方式或输出位置。"
                    return self._store_clarification(command, context, summary, trace, mode, project_id)
                if (
                    _premature_file_clarification(content_workflow, trace)
                    and _generic_clarification(str(content_workflow.get("summary", "")))
                    and file_search_nudges < MAX_FILE_SEARCH_NUDGES
                ):
                    messages.append(_file_search_nudge_message())
                    file_search_nudges += 1
                    continue
                content_workflow = _merge_pending_question(content_workflow, pending_question)
                finalized, feedback = self._try_finalize(command, context, content_workflow, trace, mode, project_id)
                if finalized is not None:
                    return finalized
                if not _is_validation_repair_feedback(feedback):
                    return self._store_unfinalized_feedback(command, context, feedback, trace, mode, project_id)
                validation_feedback_count += 1
                if validation_feedback_count > _repair_limit_for_feedback(feedback):
                    return self._store_unfinalized_feedback(command, context, feedback, trace, mode, project_id)
                messages.append(_assistant_repair_message(feedback))
                continue

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
                if not result.get("repairable"):
                    pending_question = _question_from_tool_result(result) or pending_question

                if name == "workflow_propose":
                    if result.get("ok"):
                        finalized, feedback = self._try_finalize(command, context, result["workflow"], trace, mode, project_id)
                        if finalized is not None:
                            return finalized
                        validation_feedback_count += 1
                        if validation_feedback_count > _repair_limit_for_feedback(feedback):
                            return self._store_unfinalized_feedback(command, context, feedback, trace, mode, project_id)
                        messages.append(_tool_message(tool_call.get("id"), {"ok": False, "error": feedback}))
                        continue
                    validation_feedback_count += 1
                    error_text = result.get("error", "这个任务信息还不完整。")
                    if validation_feedback_count > _repair_limit_for_feedback(error_text):
                        return self._store_unfinalized_feedback(command, context, error_text, trace, mode, project_id)

                messages.append(_tool_message(tool_call.get("id"), result))

        repair_feedback = _latest_unresolved_toolbuilder_repair_feedback(trace)
        return self._store_unfinalized_feedback(command, context, pending_question or repair_feedback or "这个任务还不够明确，请补充要操作的数据、处理方式或输出位置。", trace, mode, project_id)

    def _messages(
        self,
        command: str,
        context: Dict[str, Any],
        operation_index: List[Dict[str, str]],
        mode: str,
        project: Dict[str, Any] | None
    ) -> List[Dict[str, Any]]:
        recent_conversation = []
        if mode == FULL_AGENT_MODE:
            recent_conversation = _recent_conversation(self.store, project.get("id") if project else None, mode, 18)
        payload = {
            "user_request": command,
            "mode": mode,
            "arcgis_context": _context_summary(context),
            "project": _project_summary(project, self.store) if project else None,
            "operation_index": operation_index,
            "custom_tools": _custom_tool_status(self.store),
            "recent_conversation": recent_conversation
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
        trace: List[Dict[str, Any]],
        mode: str,
        project_id: str | None
    ) -> Tuple[Dict[str, Any] | None, str]:
        generic_unsupported_feedback = _generic_unsupported_feedback(workflow, trace)
        if generic_unsupported_feedback:
            return None, generic_unsupported_feedback
        validation_clarification_feedback = _validation_clarification_feedback(workflow, trace)
        if validation_clarification_feedback:
            return None, validation_clarification_feedback
        exploration_feedback = _attribute_exploration_feedback(workflow, trace)
        if exploration_feedback:
            return None, exploration_feedback
        try:
            prepared = prepare_workflow(workflow, self.catalog, context)
        except ValidationError as exc:
            return None, friendly_validation_message(exc)
        return self._store_workflow(command, context, prepared, trace, mode, project_id), ""

    def _store_unfinalized_feedback(
        self,
        command: str,
        context: Dict[str, Any],
        feedback: str,
        trace: List[Dict[str, Any]],
        mode: str = "semi_agent",
        project_id: str | None = None
    ) -> Dict[str, Any]:
        summary, as_answer = _public_unfinalized_feedback(feedback, trace)
        if as_answer:
            return self._store_answer(command, context, summary, trace, mode, project_id)
        return self._store_clarification(command, context, summary, trace, mode, project_id)

    def _store_workflow(
        self,
        command: str,
        context: Dict[str, Any],
        workflow: Dict[str, Any],
        trace: List[Dict[str, Any]],
        mode: str = "semi_agent",
        project_id: str | None = None
    ) -> Dict[str, Any]:
        row = self.store.create_draft(command, context_hash(context), workflow, trace, mode=mode, project_id=project_id or "")
        write_event("agent.final_workflow", {
            "workflow_id": row["id"],
            "workflow": workflow
        })
        return row

    def _store_clarification(
        self,
        command: str,
        context: Dict[str, Any],
        summary: str,
        trace: List[Dict[str, Any]],
        mode: str = "semi_agent",
        project_id: str | None = None
    ) -> Dict[str, Any]:
        workflow = {"action": "clarify", "summary": summary, "steps": []}
        row = self.store.create_draft(command, context_hash(context), workflow, trace, mode=mode, project_id=project_id or "")
        write_event("agent.final_workflow", {
            "workflow_id": row["id"],
            "workflow": workflow
        })
        return row

    def _store_answer(
        self,
        command: str,
        context: Dict[str, Any],
        summary: str,
        trace: List[Dict[str, Any]],
        mode: str = "semi_agent",
        project_id: str | None = None
    ) -> Dict[str, Any]:
        workflow = {"action": "answer", "summary": summary, "steps": []}
        row = self.store.create_draft(command, context_hash(context), workflow, trace, mode=mode, project_id=project_id or "")
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
            "has_attribute_value_samples": _has_attribute_value_samples(layer),
            "selected_count": layer.get("selected_count"),
            "visible": layer.get("visible"),
            "geometry_type": layer.get("geometry_type"),
            "dataSource": layer.get("dataSource")
        })
    return {
        "mxd_path": context.get("mxd_path"),
        "is_saved": context.get("is_saved"),
        "default_gdb": context.get("default_gdb"),
        "project_output_workspace": context.get("project_output_workspace"),
        "project": context.get("project"),
        "active_view": context.get("active_view"),
        "spatial_reference": context.get("spatial_reference"),
        "layers": layers
    }


def _project_summary(project: Dict[str, Any], store: WorkflowStore) -> Dict[str, Any]:
    return {
        "id": project["id"],
        "name": project["name"],
        "workdir": project["workdir"],
        "output_workspace": str(_project_output_workspace(project)),
        "memory": store.list_project_memories(project["id"], limit=12),
    }


def _context_for_project(context: Dict[str, Any], project: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(context or {})
    output_workspace = _project_output_workspace(project)
    try:
        output_workspace.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PlannerError("项目输出目录不可用：%s" % exc)
    result["project"] = {
        "id": project["id"],
        "name": project["name"],
        "workdir": project["workdir"],
    }
    result["project_output_workspace"] = str(output_workspace)
    return result


def _project_output_workspace(project: Dict[str, Any]) -> Path:
    return Path(project["workdir"]).expanduser().resolve() / "GeoPilot_Output"


def _custom_tool_status(store: WorkflowStore) -> List[Dict[str, str]]:
    tools = []
    for row in store.list_pending_tools():
        payload = row.get("payload") or {}
        spec = payload.get("operation_spec") or {}
        revision = payload.get("revision") or {}
        tools.append({
            "id": str(row.get("id") or ""),
            "name": str(row.get("name") or ""),
            "capability": str(row.get("capability") or ""),
            "status": str(row.get("status") or ""),
            "operation_id": str(spec.get("id") or ""),
            "revision": str(revision.get("number") or "1")
        })
    return tools


def _recent_conversation(store: WorkflowStore, project_id: str | None = None, mode: str | None = None, limit: int = 6) -> List[Dict[str, Any]]:
    history = []
    for row in reversed(store.list_recent(limit=limit, project_id=project_id, mode=mode)):
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


def _attribute_exploration_feedback(workflow: Dict[str, Any], trace: List[Dict[str, Any]]) -> str:
    if not _workflow_uses_attribute_where(workflow):
        return ""
    if _trace_has_tool(trace, "arcgis_get_layer_profile") or _trace_has_tool(trace, "arcgis_get_context"):
        return ""
    return "属性条件需要先查看目标图层的字段和值样例。请先调用 arcgis_get_layer_profile 或 arcgis_get_context，基于真实字段和值样例理解用户意图后，再生成结构化 where。"


def _generic_unsupported_feedback(workflow: Dict[str, Any], trace: List[Dict[str, Any]]) -> str:
    if workflow.get("action") not in ("clarify", "unsupported"):
        return ""
    summary = str(workflow.get("summary") or "")
    if "当前版本" not in summary or "不支持" not in summary:
        return ""
    if "请换成已有能力" not in summary and "GIS 处理目标" not in summary:
        return ""
    pending_custom = _trace_pending_custom_tool(trace)
    if pending_custom:
        return (
            "不要把已存在但未启用的自定义工具说成当前版本不支持。"
            "工具 %s 当前状态是 %s；请回复用户需要先在自建工具审核列表启用，"
            "如果用户是在反馈工具问题或要求修改，则调用 toolbuilder_get_draft 后用 toolbuilder_revise_draft 修订同一个工具。"
        ) % (pending_custom.get("operation_id", "custom.*"), pending_custom.get("status", "pending_review"))
    return (
        "不要输出“当前版本还不支持这个操作。请换成已有能力...”这种通用拒绝。"
        "如果已有 catalog operation 可以完成，请生成 workflow；如果现有能力缺失但可用 ArcPy 实现，请调用 toolbuilder_create_draft 创建待审核自定义工具；"
        "只有确认 ArcPy/ArcMap 也无法可靠实现时，才返回 unsupported。"
    )


def _validation_clarification_feedback(workflow: Dict[str, Any], trace: List[Dict[str, Any]]) -> str:
    if workflow.get("action") not in ("clarify", "answer"):
        return ""
    tool_repair_feedback = _latest_unresolved_toolbuilder_repair_feedback(trace)
    if tool_repair_feedback:
        return tool_repair_feedback
    summary = str(workflow.get("summary") or "")
    if not _looks_like_validation_feedback(summary):
        return ""
    return (
        "不要把 workflow_validate 的校验错误原样返回给用户。请根据上一条校验错误修正 workflow 后继续提交。"
        "属性 where 必须使用标准结构：{\"op\":\"and\",\"conditions\":[...]}，叶子条件必须有 op；"
        "导出目录参数按 operation schema 使用 output_folder，不要写 folder_path。不要向用户追问。"
    )


def _repair_limit_for_feedback(feedback: str) -> int:
    return MAX_VALIDATION_REPAIRS if _is_validation_repair_feedback(feedback) else 1


def _public_unfinalized_feedback(feedback: str, trace: List[Dict[str, Any]]) -> Tuple[str, bool]:
    text = str(feedback or "").strip()
    if not _needs_public_rewrite(text):
        return text, False
    tool = _latest_successful_toolbuilder_tool(trace)
    if tool:
        return _toolbuilder_public_summary(tool), True
    pending_custom = _trace_pending_custom_tool(trace)
    if pending_custom:
        operation_id = pending_custom.get("operation_id") or "custom.*"
        return (
            "自定义工具 %s 已存在但还没有启用。请先在自建工具审核列表启用；需要修改时直接告诉我修改点。"
            % operation_id,
            True
        )
    return "这个任务还没有形成可执行方案。请补充要处理的图层、输出名称或更具体的 GIS 处理目标。", False


def _needs_public_rewrite(feedback: str) -> bool:
    return _is_internal_planner_feedback(feedback) or _is_generic_unsupported_text(feedback)


def _is_internal_planner_feedback(feedback: str) -> bool:
    text = str(feedback or "")
    markers = (
        "不要输出",
        "不要把已存在但未启用的自定义工具说成当前版本不支持",
        "请先调用 arcgis_get_layer_profile",
        "基于真实字段和值样例理解用户意图",
        "workflow_validate 的校验错误",
        "不要向用户追问",
        "<minimax:tool_call>",
        "toolbuilder_create_draft 创建待审核自定义工具",
        "toolbuilder_revise_draft 修订同一个工具",
        "自定义工具草稿没有通过 GeoPilot 契约校验",
        "不要把这个错误转成用户追问",
    )
    return any(marker in text for marker in markers)


def _is_validation_repair_feedback(feedback: str) -> bool:
    text = str(feedback or "")
    markers = (
        "workflow 必须带 action",
        "不要把 workflow_validate",
        "属性条件 where 缺少 op",
        "写数据步骤缺少 output_name",
        "缺少必要参数",
        "叶子条件必须写 op",
        "叶子条件必须有 op",
        "workflow operation 里不能使用 folder_path",
        "输出文件夹不存在",
        "输出工作空间不可用",
        "输出位置还不明确",
        "自定义工具草稿没有通过 GeoPilot 契约校验",
        "toolbuilder_create_draft 返回 ok=false",
        "toolbuilder_revise_draft 返回 ok=false",
        "每个执行步骤都必须带 reason",
        "Step missing field",
        "Workflow action",
    )
    return any(marker in text for marker in markers)


def _looks_like_validation_feedback(summary: str) -> bool:
    text = summary.strip()
    markers = (
        "属性条件缺少 op",
        "where 缺少 op",
        "has unknown arguments",
        "missing required argument",
        "Workflow action",
        "Step missing field",
    )
    return any(marker in text for marker in markers)


def _latest_unresolved_toolbuilder_repair_feedback(trace: List[Dict[str, Any]]) -> str:
    for item in reversed(trace):
        if item.get("type") != "tool":
            continue
        if item.get("name") not in ("toolbuilder_create_draft", "toolbuilder_revise_draft"):
            continue
        result = item.get("result") or {}
        if result.get("ok"):
            return ""
        if result.get("repairable"):
            instruction = result.get("instruction")
            error = result.get("error")
            if isinstance(instruction, str) and instruction.strip():
                return instruction.strip()
            if isinstance(error, str) and error.strip():
                return (
                    "自定义工具草稿没有通过 GeoPilot 契约校验。"
                    "请根据错误修正 operation_spec、executor_code 或 tests 后再次调用 toolbuilder，"
                    "不要把这个错误转成用户追问。错误：%s"
                ) % error.strip()
    return ""


def _is_generic_unsupported_text(feedback: str) -> bool:
    text = str(feedback or "")
    return (
        "当前版本" in text
        and "不支持" in text
        and ("请换成已有能力" in text or "GIS 处理目标" in text)
    )


def _latest_successful_toolbuilder_tool(trace: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    for item in reversed(trace):
        if item.get("type") != "tool":
            continue
        if item.get("name") not in ("toolbuilder_create_draft", "toolbuilder_revise_draft"):
            continue
        result = item.get("result") or {}
        tool = result.get("tool")
        if result.get("ok") and isinstance(tool, dict):
            return tool
    return None


def _toolbuilder_public_summary(tool: Dict[str, Any]) -> str:
    payload = tool.get("payload") if isinstance(tool.get("payload"), dict) else {}
    spec = payload.get("operation_spec") if isinstance(payload.get("operation_spec"), dict) else {}
    revision = payload.get("revision") if isinstance(payload.get("revision"), dict) else {}
    operation_id = str(spec.get("id") or tool.get("operation_id") or "custom.*")
    revision_number = _safe_int(revision.get("number"), 1)
    verb = "已修订" if revision_number > 1 else "已生成"
    return "%s自定义工具 %s，当前等待审核。请先在自建工具审核列表启用；启用后我会用它生成可执行任务。" % (verb, operation_id)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _trace_pending_custom_tool(trace: List[Dict[str, Any]]) -> Dict[str, str] | None:
    for item in reversed(trace):
        if item.get("type") != "tool":
            continue
        result = item.get("result") or {}
        if result.get("status") != "custom_tool_not_enabled":
            continue
        return {
            "operation_id": str(result.get("operation_id") or ""),
            "status": str(result.get("tool_status") or ""),
        }
    return None


def _workflow_uses_attribute_where(workflow: Dict[str, Any]) -> bool:
    for step in workflow.get("steps") or []:
        arguments = step.get("arguments") if isinstance(step, dict) else None
        if isinstance(arguments, dict) and isinstance(arguments.get("where"), dict):
            return True
    return False


def _trace_has_tool(trace: List[Dict[str, Any]], name: str) -> bool:
    return any(item.get("type") == "tool" and item.get("name") == name for item in trace)


def _has_attribute_value_samples(layer: Dict[str, Any]) -> bool:
    for field in layer.get("fields", []) or []:
        if field.get("value_samples"):
            return True
    return False


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


def _assistant_repair_message(feedback: str) -> Dict[str, str]:
    return {
        "role": "user",
        "content": "The previous workflow or tool call did not pass validation. Repair it and continue; do not ask the user. Validation feedback: %s" % feedback
    }


def _message_for_history(message: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "role": message.get("role", "assistant"),
        "content": message.get("content")
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
        "reasoning_content": message.get("reasoning_content"),
        "tool_calls": message.get("tool_calls")
    }
