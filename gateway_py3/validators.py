from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any, Dict, List

from arcmap_runtime_py2.context_fingerprint import context_hash

from .catalog_loader import CatalogError, OperationCatalog


class ValidationError(Exception):
    pass


def validate_catalog(catalog: OperationCatalog) -> None:
    required = [
        "id",
        "version",
        "category",
        "summary",
        "model_card",
        "parameters_schema",
        "context_requirements",
        "side_effects",
        "output_policy",
        "executor",
        "examples"
    ]
    for operation in catalog.all_operations():
        missing = [key for key in required if key not in operation]
        if missing:
            raise ValidationError(f"{operation.get('id', '<unknown>')} missing fields: {missing}")
        if operation["side_effects"] not in ("read_only", "changes_map", "writes_data", "edits_data"):
            raise ValidationError(f"{operation['id']} has invalid side_effects")


def prepare_workflow(workflow: Dict[str, Any], catalog: OperationCatalog, context: Dict[str, Any]) -> Dict[str, Any]:
    prepared = copy.deepcopy(workflow)
    normalize_workflow(prepared)
    apply_default_output_names(prepared, catalog)
    validate_workflow(prepared, catalog)
    validate_workflow_semantics(prepared, catalog, context)
    return prepared


def validate_workflow(workflow: Dict[str, Any], catalog: OperationCatalog) -> None:
    if not isinstance(workflow, dict):
        raise ValidationError("Workflow must be an object.")
    if not isinstance(workflow.get("summary"), str) or not workflow["summary"].strip():
        raise ValidationError("Workflow summary is required.")
    action = workflow.get("action")
    if action not in ("execute", "clarify", "unsupported"):
        raise ValidationError("Workflow action must be execute, clarify, or unsupported.")
    steps = workflow.get("steps")
    if not isinstance(steps, list):
        raise ValidationError("Workflow steps must be an array.")
    if action == "execute" and not steps:
        raise ValidationError("Executable workflow must contain at least one step.")
    if action in ("clarify", "unsupported") and steps:
        raise ValidationError("Clarify and unsupported workflows must not contain executable steps.")

    seen_step_ids = set()
    for step in steps:
        _validate_step(step, catalog, seen_step_ids)
        seen_step_ids.add(step["id"])


def normalize_workflow(workflow: Dict[str, Any]) -> None:
    if isinstance(workflow.get("action"), str):
        workflow["action"] = workflow["action"].strip().lower()
    workflow.setdefault("steps", [])


def apply_default_output_names(workflow: Dict[str, Any], catalog: OperationCatalog) -> None:
    if workflow.get("action") != "execute":
        return
    for step in workflow.get("steps") or []:
        operation_id = step.get("operation")
        if operation_id not in catalog.operations:
            continue
        arguments = step.get("arguments")
        if not isinstance(arguments, dict):
            continue
        schema = catalog.operations[operation_id].get("parameters_schema") or {}
        if "output_name" not in schema.get("required", []):
            continue
        if arguments.get("output_name"):
            continue
        output_name = _default_output_name_for_step(operation_id, arguments)
        if output_name:
            arguments["output_name"] = output_name


def validate_workflow_semantics(workflow: Dict[str, Any], catalog: OperationCatalog, context: Dict[str, Any]) -> None:
    if workflow.get("action") != "execute":
        return

    available_layers = _initial_layer_index(context)
    seen_step_ids = set()
    for step in workflow.get("steps") or []:
        step_id = step["id"]
        operation = catalog.get(step["operation"])
        arguments = step["arguments"]

        _validate_layer_references(operation, arguments, context, available_layers, seen_step_ids)
        _validate_field_references(operation, arguments, context, available_layers)
        _validate_output_location(operation, arguments, context)
        _validate_output_name(arguments)
        _validate_layer_add_path(step, arguments)

        seen_step_ids.add(step_id)
        _register_step_output(step, available_layers)


def friendly_validation_message(error: Exception) -> str:
    message = str(error)
    if "Workflow action must be execute, clarify, or unsupported." in message:
        return "任务类型不明确。请说明是要执行操作、继续补充信息，还是这个能力当前不支持。"
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
    if message:
        return message
    return "这个任务描述我还没法稳定执行。请换一种更明确的说法。"


def _validate_step(step: Dict[str, Any], catalog: OperationCatalog, seen_step_ids: set[str]) -> None:
    for key in ("id", "operation", "arguments", "reason"):
        if key not in step:
            raise ValidationError(f"Step missing field: {key}")
    if step["id"] in seen_step_ids:
        raise ValidationError(f"Duplicate step id: {step['id']}")
    try:
        operation = catalog.get(step["operation"])
    except CatalogError as exc:
        raise ValidationError(str(exc))
    arguments = step["arguments"]
    if not isinstance(arguments, dict):
        raise ValidationError(f"{step['id']} arguments must be an object.")
    _validate_arguments(step["id"], arguments, operation["parameters_schema"])


def _validate_arguments(step_id: str, arguments: Dict[str, Any], schema: Dict[str, Any]) -> None:
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    additional = schema.get("additionalProperties", True)

    for name in required:
        if name not in arguments:
            raise ValidationError(f"{step_id} missing required argument: {name}")
    if additional is False:
        extra = sorted(set(arguments) - set(properties))
        if extra:
            raise ValidationError(f"{step_id} has unknown arguments: {extra}")

    for name, value in arguments.items():
        if name in properties:
            _validate_type(step_id, name, value, properties[name])


def _validate_type(step_id: str, name: str, value: Any, schema: Dict[str, Any]) -> None:
    expected = schema.get("type")
    if expected == "string" and not isinstance(value, str):
        raise ValidationError(f"{step_id}.{name} must be string.")
    if expected == "boolean" and not isinstance(value, bool):
        raise ValidationError(f"{step_id}.{name} must be boolean.")
    if expected == "integer" and not isinstance(value, int):
        raise ValidationError(f"{step_id}.{name} must be integer.")
    if expected == "array":
        if not isinstance(value, list):
            raise ValidationError(f"{step_id}.{name} must be array.")
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < min_items:
            raise ValidationError(f"{step_id}.{name} must contain at least {min_items} items.")
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            _validate_type(step_id, f"{name}[{index}]", item, item_schema)
    if expected == "object" and not isinstance(value, dict):
        raise ValidationError(f"{step_id}.{name} must be object.")
    enum = schema.get("enum")
    if enum and value not in enum:
        raise ValidationError(f"{step_id}.{name} must be one of {enum}.")


def _validate_layer_references(
    operation: Dict[str, Any],
    arguments: Dict[str, Any],
    context: Dict[str, Any],
    available_layers: List[Dict[str, Any]],
    seen_step_ids: set[str]
) -> None:
    for name in _layer_argument_names(operation):
        if name not in arguments:
            continue
        values = arguments[name] if isinstance(arguments[name], list) else [arguments[name]]
        for value in values:
            if not isinstance(value, str):
                continue
            if value.startswith("from_step:"):
                ref = value[len("from_step:"):]
                if ref not in seen_step_ids:
                    raise ValidationError("步骤引用“%s”还没有产生。请检查工作流顺序。" % value)
                continue
            matches = _matching_layers(value, available_layers)
            if len(matches) == 1:
                continue
            if len(matches) > 1:
                raise ValidationError("“%s”匹配到多个图层。请说明要使用哪一个图层。" % value)
            candidates = _closest_candidates(value, [layer["name"] for layer in available_layers if layer.get("name")])
            if candidates:
                raise ValidationError("当前地图里没有“%s”图层。可用图层有：%s。请确认要使用哪个图层。" % (value, "、".join(candidates)))
            raise ValidationError("当前地图里没有“%s”图层。请先添加图层，或说明要使用哪个已有图层。" % value)


def _validate_field_references(
    operation: Dict[str, Any],
    arguments: Dict[str, Any],
    context: Dict[str, Any],
    available_layers: List[Dict[str, Any]]
) -> None:
    requirements = operation.get("context_requirements") or {}
    if not requirements.get("requires_fields"):
        return
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
    if requirements.get("assignment_fields") and isinstance(arguments.get("assignments"), dict):
        fields.extend(arguments["assignments"].keys())
    if not fields:
        return

    layer_value = _primary_layer_value(operation, arguments)
    layers = available_layers
    if layer_value:
        matched = _matching_layers(layer_value, available_layers)
        if len(matched) == 1:
            layers = matched
    if any(layer.get("fields_unknown") for layer in layers):
        return

    field_names = set()
    for layer in layers:
        for field in layer.get("fields", []):
            name = field.get("name")
            if name:
                field_names.add(name.lower())
    for field in fields:
        if str(field).lower() not in field_names:
            raise ValidationError("当前地图字段里没有“%s”。请确认字段名。" % field)

    if operation.get("id") == "table.add_field":
        field_name = arguments.get("field_name")
        if field_name and str(field_name).lower() in field_names:
            raise ValidationError("“%s”字段已经存在。请换一个新字段名。" % field_name)


def _validate_output_location(operation: Dict[str, Any], arguments: Dict[str, Any], context: Dict[str, Any]) -> None:
    if operation.get("side_effects") != "writes_data":
        return
    if arguments.get("output_workspace") or arguments.get("output_folder"):
        return
    workspace = (operation.get("output_policy") or {}).get("workspace", "")
    if context.get("is_saved") and workspace.startswith("mxd_default"):
        return
    raise ValidationError("这个操作会生成新数据，但当前输出位置还不明确。请告诉我输出到哪个文件夹或 GDB。")


def _validate_output_name(arguments: Dict[str, Any]) -> None:
    output_name = arguments.get("output_name")
    if not output_name:
        return
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(output_name)):
        raise ValidationError("输出名称“%s”不能用于 ArcGIS。请使用英文、数字和下划线，并且不要用数字开头。" % output_name)


def _validate_layer_add_path(step: Dict[str, Any], arguments: Dict[str, Any]) -> None:
    if step.get("operation") != "layer.add_layer":
        return
    path = arguments.get("path")
    if not path:
        return
    value = str(path).replace("/", "\\")
    if re.match(r"^[A-Za-z]:\\", value) and not Path(value).exists():
        raise ValidationError("没有找到这个文件：%s。请确认路径是否正确。" % value)


def _register_step_output(step: Dict[str, Any], available_layers: List[Dict[str, Any]]) -> None:
    arguments = step.get("arguments") or {}
    names = []
    if step.get("operation") == "layer.add_layer" and arguments.get("path"):
        names.append(Path(arguments["path"]).stem)
    if arguments.get("output_name"):
        names.append(arguments["output_name"])
    for name in names:
        available_layers.append({
            "layer_ref": "from_step:%s" % step["id"],
            "name": str(name),
            "longName": str(name),
            "fields": [],
            "fields_unknown": True
        })


def _initial_layer_index(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    layers = []
    for layer in context.get("layers", []) or []:
        layers.append({
            "layer_ref": layer.get("layer_ref"),
            "name": layer.get("name"),
            "longName": layer.get("longName"),
            "fields": layer.get("fields", []),
            "fields_unknown": layer.get("fields_unknown", False)
        })
    return layers


def _layer_argument_names(operation: Dict[str, Any]) -> List[str]:
    properties = operation.get("parameters_schema", {}).get("properties", {})
    names = []
    for name in properties:
        lowered = name.lower()
        if "layer" in lowered and "output" not in lowered:
            names.append(name)
    return names


def _matching_layers(value: str, layers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    raw = value[len("layer_ref:"):] if value.startswith("layer_ref:") else value
    matches = []
    for layer in layers:
        if raw in (layer.get("layer_ref"), layer.get("name"), layer.get("longName")):
            matches.append(layer)
    if matches:
        return matches
    lowered = raw.lower()
    for layer in layers:
        if lowered in ((layer.get("name") or "").lower(), (layer.get("longName") or "").lower(), (layer.get("layer_ref") or "").lower()):
            matches.append(layer)
    return matches


def _primary_layer_value(operation: Dict[str, Any], arguments: Dict[str, Any]) -> str | None:
    for name in ("layer", "input_layer", "target_layer"):
        if isinstance(arguments.get(name), str):
            return arguments[name]
    names = _layer_argument_names(operation)
    if names and isinstance(arguments.get(names[0]), str):
        return arguments[names[0]]
    return None


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


def _default_output_name_for_step(operation_id: str, arguments: Dict[str, Any]) -> str | None:
    suffix = operation_id.split(".", 1)[1] if "." in operation_id else operation_id
    if isinstance(arguments.get("input_layers"), list) and arguments["input_layers"]:
        return _safe_output_name("_".join([str(item) for item in arguments["input_layers"]] + [suffix]))
    for key in ("input_layer", "target_layer", "layer"):
        if arguments.get(key):
            return _safe_output_name("%s_%s" % (arguments[key], suffix))
    return None


def _safe_output_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_]+", "_", str(value)).strip("_")
    if not text:
        return "arcgis_ai_output"
    if text[0].isdigit():
        text = "out_" + text
    return text[:120]

