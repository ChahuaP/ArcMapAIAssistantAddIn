from __future__ import annotations

import json
from typing import Any, Dict, List

from .catalog_loader import CatalogError, OperationCatalog
from .custom_tool_contract import (
    TOOLBUILDER_GET_TOOL_DESCRIPTION,
    TOOLBUILDER_GET_TOOL_PARAMETERS,
    TOOLBUILDER_REVISE_TOOL_DESCRIPTION,
    TOOLBUILDER_REVISE_TOOL_PARAMETERS,
    TOOLBUILDER_TOOL_DESCRIPTION,
    TOOLBUILDER_TOOL_PARAMETERS,
    is_toolbuilder_catalog_id,
    toolbuilder_catalog_misuse_result,
)
from .file_resolver import FileResolver
from .layer_profiles import layer_value_profile, matching_layers_exact
from .output_folder_resolver import OutputFolderResolver
from .tool_builder import ToolBuilderError, create_draft_tool, get_tool_package, revise_draft_tool
from .validators import ValidationError, friendly_validation_message, prepare_workflow
from .run_store import RunStore


class AgentToolError(Exception):
    pass


class AgentToolRuntime:
    def __init__(
        self,
        catalog: OperationCatalog,
        store: RunStore,
        context: Dict[str, Any],
        file_resolver: FileResolver | None = None,
        output_folder_resolver: OutputFolderResolver | None = None
    ):
        self.catalog = catalog
        self.store = store
        self.context = context
        self.file_resolver = file_resolver or FileResolver()
        self.output_folder_resolver = output_folder_resolver or OutputFolderResolver()

    def tools(self) -> List[Dict[str, Any]]:
        tools = [
            _tool(
                "catalog_list_operations",
                "List all registered ArcGIS operation ids with short user-facing summaries.",
                {"type": "object", "properties": {}, "additionalProperties": False}
            ),
            _tool(
                "catalog_get_operation_schema",
                "Get the full schema and execution metadata for one registered operation id.",
                {
                    "type": "object",
                    "required": ["operation_id"],
                    "properties": {"operation_id": {"type": "string"}},
                    "additionalProperties": False
                }
            ),
            _tool(
                "arcgis_get_context",
                "Get the latest ArcGIS context snapshot synchronized from ArcMap.",
                {"type": "object", "properties": {}, "additionalProperties": False}
            ),
            _tool(
                "arcgis_get_layer_profile",
                "Get one current ArcGIS layer's fields and sampled attribute values. Use this to understand natural language attribute intent before writing where conditions.",
                {
                    "type": "object",
                    "required": ["layer"],
                    "properties": {
                        "layer": {"type": "string", "description": "Exact layer_ref or layer name from arcgis_context.layers, for example layer:0 or roads."}
                    },
                    "additionalProperties": False
                }
            ),
            _tool(
                "file_resolve",
                "Find local GIS files from structured path arguments. Do not pass natural language. This never scans an entire drive recursively.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Exact local file or folder path, for example D:\\Data\\roads.shp or D:\\Data\\shapefile."},
                        "folder_path": {"type": "string", "description": "Exact local folder path. Same behavior as path when it is a folder."},
                        "drive": {"type": "string", "description": "Drive letter only, for example D."},
                        "directory": {"type": "string", "description": "Relative directory under drive, for example Data\\shapefile."},
                        "directory_parts": {"type": "array", "items": {"type": "string"}, "description": "Relative directory parts under drive, for example ['Data', 'shapefile']."},
                        "file_name": {"type": "string", "description": "Exact file name to find inside the structured location, for example nanjing.shp."},
                        "extensions": {"type": "array", "items": {"type": "string"}, "description": "Allowed file extensions for folder listing, for example ['shp']."}
                    },
                    "additionalProperties": False
                }
            ),
            _tool(
                "output_folder_resolve",
                "Resolve an existing local output folder from structured arguments. Use this for export/output folders; never use file_resolve for output destinations.",
                {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Exact local output folder path, for example D:\\Data\\exports."},
                        "parent_path": {"type": "string", "description": "Exact existing parent folder path, for example D:\\Data."},
                        "known_folder": {"type": "string", "enum": ["desktop", "documents", "downloads"], "description": "Known local folder root selected by the user."},
                        "folder_name": {"type": "string", "description": "Direct child folder name under parent_path or known_folder, for example test."}
                    },
                    "additionalProperties": False
                }
            ),
            _tool(
                "workflow_validate",
                "Validate a proposed workflow locally before final proposal. Pass workflow_json as a valid JSON string, not a nested object. Every execute step must include id, operation, arguments, and reason. Returns normalized workflow or a Chinese correction question.",
                {
                    "type": "object",
                    "required": ["workflow_json"],
                    "properties": {
                        "workflow_json": {"type": "string"}
                    },
                    "additionalProperties": False
                }
            ),
            _tool(
                "workflow_propose",
                "Submit the final workflow proposal. Pass workflow_json as a valid JSON string, not a nested object. The gateway will validate it before showing it to the user.",
                {
                    "type": "object",
                    "required": ["workflow_json"],
                    "properties": {
                        "workflow_json": {"type": "string"}
                    },
                    "additionalProperties": False
                }
            ),
            _tool(
                "toolbuilder_create_draft",
                TOOLBUILDER_TOOL_DESCRIPTION,
                TOOLBUILDER_TOOL_PARAMETERS
            ),
            _tool(
                "toolbuilder_get_draft",
                TOOLBUILDER_GET_TOOL_DESCRIPTION,
                TOOLBUILDER_GET_TOOL_PARAMETERS
            ),
            _tool(
                "toolbuilder_revise_draft",
                TOOLBUILDER_REVISE_TOOL_DESCRIPTION,
                TOOLBUILDER_REVISE_TOOL_PARAMETERS
            )
        ]
        return tools

    def operation_index(self) -> List[Dict[str, str]]:
        return [
            {
                "id": operation["id"],
                "category": operation["category"],
                "summary": operation["summary"],
                "capability_contract": self.catalog.capabilities.get(operation["id"]),
                "side_effects": operation["side_effects"]
            }
            for operation in self.catalog.all_operations()
        ]

    def handle(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if name == "catalog_list_operations":
            return {"operations": self.operation_index()}
        if name == "catalog_get_operation_schema":
            return self._catalog_get_operation_schema(arguments)
        if name == "arcgis_get_context":
            return {"context": self.context}
        if name == "arcgis_get_layer_profile":
            return self._arcgis_get_layer_profile(arguments)
        if name == "file_resolve":
            return self._file_resolve(arguments)
        if name == "output_folder_resolve":
            return self._output_folder_resolve(arguments)
        if name == "workflow_validate":
            return self._workflow_validate(arguments)
        if name == "workflow_propose":
            return self._workflow_validate(arguments)
        if name == "toolbuilder_create_draft":
            return self._toolbuilder_create_draft(arguments)
        if name == "toolbuilder_get_draft":
            return self._toolbuilder_get_draft(arguments)
        if name == "toolbuilder_revise_draft":
            return self._toolbuilder_revise_draft(arguments)
        raise AgentToolError("Unknown agent tool: %s" % name)

    def _catalog_get_operation_schema(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        operation_id = _required_string(arguments, "operation_id")
        if is_toolbuilder_catalog_id(operation_id):
            return toolbuilder_catalog_misuse_result(operation_id)
        try:
            operation = self.catalog.get(operation_id)
        except CatalogError as exc:
            if operation_id.startswith("custom."):
                custom_status = _custom_tool_catalog_status(self.store, operation_id)
                if custom_status:
                    return custom_status
            raise AgentToolError(str(exc))
        return {"operation": operation}

    def _arcgis_get_layer_profile(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        _reject_unknown(arguments, {"layer"})
        layer_value = _required_string(arguments, "layer")
        matches = matching_layers_exact(layer_value, self.context.get("layers", []) or [])
        if len(matches) != 1:
            if len(matches) > 1:
                return {"ok": False, "error": "图层“%s”不唯一，请使用 layer_ref。" % layer_value}
            return {"ok": False, "error": "当前地图没有精确匹配“%s”的图层。" % layer_value}
        return {
            "ok": True,
            "layer": layer_value_profile(matches[0]),
        }

    def _file_resolve(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        _reject_unknown(arguments, {"path", "folder_path", "drive", "directory", "directory_parts", "file_name", "extensions"})
        return self.file_resolver.resolve(arguments).to_tool_result()

    def _output_folder_resolve(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        _reject_unknown(arguments, {"path", "parent_path", "known_folder", "folder_name"})
        return self.output_folder_resolver.resolve(arguments)

    def _workflow_validate(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        _reject_unknown(arguments, {"workflow_json"})
        workflow_json = arguments.get("workflow_json")
        if not isinstance(workflow_json, str) or not workflow_json.strip():
            return {"ok": False, "repairable": True, "error": "workflow_json 必须是非空 JSON 字符串。请修正 workflow_json 后继续，不要向用户追问。"}
        try:
            workflow = json.loads(workflow_json)
        except ValueError as exc:
            return {"ok": False, "repairable": True, "error": "workflow_json 必须是可解析的 JSON：%s。请修正 workflow_json 后继续，不要向用户追问。" % exc}
        if not isinstance(workflow, dict):
            return {"ok": False, "repairable": True, "error": "workflow_json 解析后必须是 workflow 对象。请修正 workflow_json 后继续，不要向用户追问。"}
        try:
            prepared = prepare_workflow(workflow, self.catalog, self.context)
        except ValidationError as exc:
            return {"ok": False, "repairable": True, "error": friendly_validation_message(exc)}
        return {"ok": True, "workflow": prepared}

    def _toolbuilder_create_draft(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            tool = create_draft_tool(self.store, arguments)
        except ToolBuilderError as exc:
            return _toolbuilder_repairable_error(str(exc), "toolbuilder_create_draft")
        return {
            "ok": True,
            "status": "pending_review",
            "tool": tool,
            "message": "新工具已生成待审核包，启用前不会进入 ArcGIS 执行目录。"
        }

    def _toolbuilder_get_draft(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            tool = get_tool_package(self.store, _required_string(arguments, "tool_id"))
        except (KeyError, ToolBuilderError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "tool": tool}

    def _toolbuilder_revise_draft(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            tool = revise_draft_tool(self.store, arguments)
        except (KeyError, ToolBuilderError) as exc:
            return _toolbuilder_repairable_error(str(exc), "toolbuilder_revise_draft")
        return {
            "ok": True,
            "status": "pending_review",
            "tool": tool,
            "message": "工具已在原工具上修订，重新进入待审核状态。"
        }


def _toolbuilder_repairable_error(error: str, tool_name: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "status": "toolbuilder_validation_error",
        "repairable": True,
        "error": error,
        "instruction": (
            "%s 返回 ok=false，说明自定义工具草稿没有通过 GeoPilot 契约校验。"
            "请根据 error 修正 operation_spec、executor_code 或 tests 后再次调用 %s，"
            "不要把这个错误转成用户追问。"
        ) % (tool_name, tool_name)
    }


def _tool(name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters
        }
    }


def _custom_tool_catalog_status(store: RunStore, operation_id: str) -> Dict[str, Any] | None:
    for tool in store.list_pending_tools():
        spec = (tool.get("payload") or {}).get("operation_spec") or {}
        if spec.get("id") != operation_id:
            continue
        status = str(tool.get("status") or "")
        if status == "enabled":
            return {
                "ok": False,
                "status": "custom_tool_catalog_stale",
                "operation_id": operation_id,
                "tool_id": tool.get("id"),
                "tool_status": status,
                "instruction": "这个自定义工具已启用，但当前 catalog 快照还没加载到它。请重新读取能力或稍后重试，不要说当前版本不支持。"
            }
        return {
            "ok": False,
            "status": "custom_tool_not_enabled",
            "operation_id": operation_id,
            "tool_id": tool.get("id"),
            "tool_status": status,
            "instruction": (
                "这个自定义工具已存在但尚未启用，当前不能作为 workflow operation 执行。"
                "不要说当前版本不支持；如果用户要执行，请告诉用户先在自建工具审核列表启用；"
                "如果用户要修改或修复，请调用 toolbuilder_get_draft 后用 toolbuilder_revise_draft 修订同一个工具。"
            )
        }
    return None


def _required_string(arguments: Dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AgentToolError("%s must be a non-empty string." % key)
    return value.strip()


def _optional_string(arguments: Dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _extensions(arguments: Dict[str, Any]) -> set[str]:
    values = arguments.get("extensions")
    if not isinstance(values, list) or not values:
        values = ["shp", "lyr", "tif", "img", "sde", "gdb"]
    extensions = set()
    for value in values:
        item = str(value).strip().lower()
        if not item:
            continue
        extensions.add(item if item.startswith(".") else "." + item)
    return extensions


def _reject_unknown(arguments: Dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise AgentToolError("Unknown arguments: %s" % unknown)
