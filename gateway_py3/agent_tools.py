from __future__ import annotations

from typing import Any, Dict, List

from .catalog_loader import CatalogError, OperationCatalog
from .file_resolver import FileResolver
from .validators import ValidationError, friendly_validation_message, prepare_workflow
from .workflow_store import WorkflowStore


class AgentToolError(Exception):
    pass


class AgentToolRuntime:
    def __init__(
        self,
        catalog: OperationCatalog,
        store: WorkflowStore,
        context: Dict[str, Any],
        file_resolver: FileResolver | None = None
    ):
        self.catalog = catalog
        self.store = store
        self.context = context
        self.file_resolver = file_resolver or FileResolver()

    def tools(self) -> List[Dict[str, Any]]:
        return [
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
                "workflow_validate",
                "Validate a proposed workflow locally before final proposal. Returns normalized workflow or a Chinese correction question.",
                {
                    "type": "object",
                    "required": ["workflow"],
                    "properties": {
                        "workflow": {"type": "object"}
                    },
                    "additionalProperties": False
                }
            ),
            _tool(
                "workflow_propose",
                "Submit the final workflow proposal. The gateway will validate it before showing it to the user.",
                {
                    "type": "object",
                    "required": ["action", "summary", "steps"],
                    "properties": {
                        "action": {"type": "string", "enum": ["execute", "clarify", "unsupported"]},
                        "summary": {"type": "string"},
                        "steps": {"type": "array", "items": {"type": "object"}}
                    },
                    "additionalProperties": False
                }
            )
        ]

    def operation_index(self) -> List[Dict[str, str]]:
        return [
            {
                "id": operation["id"],
                "category": operation["category"],
                "summary": operation["summary"],
                "model_card": operation.get("model_card", ""),
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
        if name == "file_resolve":
            return self._file_resolve(arguments)
        if name == "workflow_validate":
            return self._workflow_validate(arguments)
        if name == "workflow_propose":
            return self._workflow_validate({
                "workflow": _workflow_from_arguments(arguments)
            })
        raise AgentToolError("Unknown agent tool: %s" % name)

    def _catalog_get_operation_schema(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        operation_id = _required_string(arguments, "operation_id")
        try:
            operation = self.catalog.get(operation_id)
        except CatalogError as exc:
            raise AgentToolError(str(exc))
        return {"operation": operation}

    def _file_resolve(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        _reject_unknown(arguments, {"path", "folder_path", "drive", "directory", "directory_parts", "file_name", "extensions"})
        return self.file_resolver.resolve(arguments).to_tool_result()

    def _workflow_validate(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        _reject_unknown(arguments, {"workflow"})
        workflow = arguments.get("workflow")
        if not isinstance(workflow, dict):
            return {"ok": False, "error": "workflow 必须是一个对象。"}
        try:
            prepared = prepare_workflow(workflow, self.catalog, self.context)
        except ValidationError as exc:
            return {"ok": False, "error": friendly_validation_message(exc)}
        return {"ok": True, "workflow": prepared}


def _tool(name: str, description: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters
        }
    }


def _required_string(arguments: Dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AgentToolError("%s must be a non-empty string." % key)
    return value.strip()


def _reject_unknown(arguments: Dict[str, Any], allowed: set[str]) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise AgentToolError("Unknown arguments: %s" % unknown)


def _workflow_from_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(arguments.get("workflow"), dict):
        return arguments["workflow"]
    return {
        "action": arguments.get("action"),
        "summary": arguments.get("summary"),
        "steps": arguments.get("steps")
    }
