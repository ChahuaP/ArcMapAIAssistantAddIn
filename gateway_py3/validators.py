from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Dict, List

from arcmap_runtime_py2.context_fingerprint import context_hash

from .catalog_loader import CatalogError, OperationCatalog


class ValidationError(Exception):
    pass


CONDITION_OPERATOR_ALIASES = {
    "等于": "eq",
    "不等于": "ne",
    "大于": "gt",
    "大于等于": "gte",
    "小于": "lt",
    "小于等于": "lte",
    "之间": "between",
    "包含于": "in",
    "模糊匹配": "like",
    "为空": "is_null",
    "非空": "is_not_null",
}
LEAF_CONDITION_OPERATORS = {
    "eq",
    "=",
    "ne",
    "!=",
    "<>",
    "gt",
    ">",
    "gte",
    ">=",
    "lt",
    "<",
    "lte",
    "<=",
    "between",
    "in",
    "like",
    "is_null",
    "is_not_null",
}
VALUE_CONDITION_OPERATORS = {"eq", "=", "ne", "!=", "<>", "gt", ">", "gte", ">=", "lt", "<", "lte", "<=", "like"}
CONDITION_OPERATOR_HELP = "eq, ne, gt, gte, lt, lte, between, in, like, is_null, is_not_null, and, or, not"


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
    apply_project_output_location(prepared, catalog, context)
    apply_output_name_timestamp(prepared, catalog)
    remove_generated_output_add_layers(prepared, catalog)
    validate_workflow(prepared, catalog)
    validate_workflow_semantics(prepared, catalog, context)
    return prepared


def validate_workflow(workflow: Dict[str, Any], catalog: OperationCatalog) -> None:
    if not isinstance(workflow, dict):
        raise ValidationError("Workflow must be an object.")
    if not isinstance(workflow.get("summary"), str) or not workflow["summary"].strip():
        raise ValidationError("Workflow summary is required.")
    action = workflow.get("action")
    if action not in ("execute", "clarify", "unsupported", "answer"):
        raise ValidationError("Workflow action must be execute, clarify, unsupported, or answer.")
    steps = workflow.get("steps")
    if not isinstance(steps, list):
        raise ValidationError("Workflow steps must be an array.")
    if action == "execute" and not steps:
        raise ValidationError("Executable workflow must contain at least one step.")
    if action in ("clarify", "unsupported", "answer") and steps:
        raise ValidationError("Clarify, unsupported, and answer workflows must not contain executable steps.")

    seen_step_ids = set()
    for step in steps:
        _validate_step(step, catalog, seen_step_ids)
        seen_step_ids.add(step["id"])


def normalize_workflow(workflow: Dict[str, Any]) -> None:
    if isinstance(workflow.get("action"), str):
        workflow["action"] = workflow["action"].strip().lower()
    workflow.setdefault("steps", [])
    for step in workflow.get("steps") or []:
        if isinstance(step, dict) and "id" in step:
            step["id"] = str(step["id"])


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


def apply_output_name_timestamp(workflow: Dict[str, Any], catalog: OperationCatalog) -> None:
    if workflow.get("action") != "execute":
        return
    suffix = datetime.now().strftime("%Y%m%d_%H%M%S")
    for step in workflow.get("steps") or []:
        operation_id = step.get("operation")
        if operation_id not in catalog.operations:
            continue
        operation = catalog.operations[operation_id]
        if operation.get("side_effects") != "writes_data":
            continue
        arguments = step.get("arguments")
        if not isinstance(arguments, dict):
            continue
        output_name = arguments.get("output_name")
        if not output_name or _has_timestamp_suffix(str(output_name)):
            continue
        arguments["output_name"] = "%s_%s" % (output_name, suffix)


def remove_generated_output_add_layers(workflow: Dict[str, Any], catalog: OperationCatalog) -> None:
    if workflow.get("action") != "execute":
        return
    generated_names: set[str] = set()
    kept_steps = []
    for step in workflow.get("steps") or []:
        operation_id = step.get("operation")
        arguments = step.get("arguments")
        if (
            operation_id == "layer.add_layer"
            and isinstance(arguments, dict)
            and _path_points_to_generated_output(arguments.get("path"), generated_names)
        ):
            continue
        kept_steps.append(step)
        if operation_id in catalog.operations:
            operation = catalog.operations[operation_id]
            if operation.get("side_effects") == "writes_data" and isinstance(arguments, dict):
                _register_generated_output_name(arguments.get("output_name"), generated_names)
    workflow["steps"] = kept_steps


def apply_project_output_location(workflow: Dict[str, Any], catalog: OperationCatalog, context: Dict[str, Any]) -> None:
    if workflow.get("action") != "execute":
        return
    project_output = context.get("project_output_workspace")
    if not isinstance(project_output, str) or not project_output.strip():
        return
    for step in workflow.get("steps") or []:
        operation_id = step.get("operation")
        if operation_id not in catalog.operations:
            continue
        operation = catalog.operations[operation_id]
        if operation.get("side_effects") != "writes_data":
            continue
        arguments = step.get("arguments")
        if not isinstance(arguments, dict):
            continue
        if arguments.get("output_workspace") or arguments.get("output_folder"):
            continue
        properties = (operation.get("parameters_schema") or {}).get("properties") or {}
        if "output_workspace" in properties:
            arguments["output_workspace"] = project_output.strip()
        elif "output_folder" in properties:
            arguments["output_folder"] = project_output.strip()


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
        _validate_condition_arguments(operation, arguments)
        _validate_field_references(operation, arguments, context, available_layers)
        _validate_output_location(operation, arguments, context)
        _validate_output_name(arguments)
        _validate_layer_add_path(step, arguments)

        seen_step_ids.add(step_id)
        _register_step_output(step, available_layers)


def friendly_validation_message(error: Exception) -> str:
    message = str(error)
    if "Workflow action must be execute" in message:
        return "任务类型不明确。请说明是要执行操作、普通回答、继续补充信息，还是这个能力当前不支持。"
    if "Step missing field: operation" in message:
        return "我还不能确定要执行哪一种 GIS 操作。请把任务再说具体一点，比如要缓冲、裁剪、选择、导出，还是添加图层。"
    if "Step missing field: arguments" in message:
        return "这个任务的参数还不完整。请补充图层名、字段名、距离、输出名等必要信息。"
    if "Step missing field: reason" in message:
        return "每个执行步骤都必须带 reason，说明这一步为什么这样做。请补上 reason 后继续生成 workflow，不要向用户追问。"
    if "Step missing field:" in message:
        return "这个任务信息还不完整。请把要操作的图层、参数和输出位置再说清楚一点。"
    if "missing required argument:" in message:
        return "这个操作还缺少必要参数“%s”。请补充后我再继续。" % message.rsplit(":", 1)[-1].strip()
    if "has unknown arguments:" in message:
        return message
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
    _validate_arguments(step["id"], step["operation"], arguments, operation["parameters_schema"])


def _validate_arguments(step_id: str, operation_id: str, arguments: Dict[str, Any], schema: Dict[str, Any]) -> None:
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    additional = schema.get("additionalProperties", True)

    for name in required:
        if name not in arguments:
            raise ValidationError(f"{step_id} missing required argument: {name}")
    if additional is False:
        extra = sorted(set(arguments) - set(properties))
        if extra:
            allowed = sorted(properties)
            if operation_id.startswith("custom."):
                raise ValidationError(
                    "%s（%s）不认识参数：%s。这个自建工具当前允许的参数是：%s。"
                    "如果这些参数本来就应该支持，请修订这个自建工具的 operation_spec，而不是新建工具。"
                    % (step_id, operation_id, "、".join(extra), "、".join(allowed))
                )
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
    if expected == "number" and not isinstance(value, (int, float)):
        raise ValidationError(f"{step_id}.{name} must be number.")
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
        if isinstance(arguments[name], list):
            arguments[name] = [
                _validate_and_normalize_layer_reference(value, available_layers, seen_step_ids)
                for value in arguments[name]
            ]
        else:
            arguments[name] = _validate_and_normalize_layer_reference(arguments[name], available_layers, seen_step_ids)


def _validate_and_normalize_layer_reference(
    value: Any,
    available_layers: List[Dict[str, Any]],
    seen_step_ids: set[str]
) -> Any:
    if not isinstance(value, str):
        return value
    if value.startswith("from_step:"):
        ref = value[len("from_step:"):]
        if ref not in seen_step_ids:
            raise ValidationError("步骤引用“%s”还没有产生。请检查工作流顺序。" % value)
        return value
    matches = _matching_layers_exact(value, available_layers)
    if len(matches) == 1:
        layer_ref = matches[0].get("layer_ref")
        if isinstance(layer_ref, str) and layer_ref:
            return layer_ref
        return value
    if len(matches) > 1:
        raise ValidationError("“%s”匹配到多个图层。请说明要使用哪一个图层。" % value)
    candidates = _available_layer_names(available_layers)
    if candidates:
        raise ValidationError("当前地图里没有精确匹配“%s”的图层。可用图层有：%s。请从当前图层列表中选择一个。" % (value, "、".join(candidates)))
    raise ValidationError("当前地图里没有“%s”图层。请先添加图层，或说明要使用哪个已有图层。" % value)


def _validate_condition_arguments(operation: Dict[str, Any], arguments: Dict[str, Any]) -> None:
    properties = (operation.get("parameters_schema") or {}).get("properties") or {}
    if "where" not in properties or "where" not in arguments:
        return
    _validate_condition_node(arguments.get("where"))


def _validate_condition_node(condition: Any) -> None:
    if not isinstance(condition, dict) or not condition:
        raise ValidationError("属性条件 where 必须是结构化对象。")
    op = _condition_operator(condition)
    if op in ("and", "or"):
        children = condition.get("conditions")
        if not isinstance(children, list) or not children:
            raise ValidationError("%s 条件必须包含非空 conditions。" % op)
        for child in children:
            _validate_condition_node(child)
        return
    if op == "not":
        child = condition.get("condition")
        if not isinstance(child, dict):
            raise ValidationError("not 条件必须包含 condition。")
        _validate_condition_node(child)
        return
    if op not in LEAF_CONDITION_OPERATORS:
        raise ValidationError(
            "属性条件操作符“%s”不支持。可用操作符：%s。文本包含请使用 op=like，value 写成 %%关键词%%；不要使用 contains、starts_with、ends_with 或 regex。"
            % (op, CONDITION_OPERATOR_HELP)
        )
    if not condition.get("field"):
        raise ValidationError("属性条件缺少字段名。")
    if op in VALUE_CONDITION_OPERATORS and "value" not in condition:
        raise ValidationError("%s 条件必须提供 value。" % op)
    if op == "between":
        values = condition.get("values")
        if not isinstance(values, list) or len(values) != 2:
            raise ValidationError("between 条件必须提供两个 values。")
    if op == "in":
        values = condition.get("values")
        if not isinstance(values, list) or not values:
            raise ValidationError("in 条件必须提供非空 values。")
    if op in ("is_null", "is_not_null") and "value" in condition:
        raise ValidationError("%s 条件不能提供 value。" % op)


def _validate_field_references(
    operation: Dict[str, Any],
    arguments: Dict[str, Any],
    context: Dict[str, Any],
    available_layers: List[Dict[str, Any]]
) -> None:
    requirements = operation.get("context_requirements") or {}
    if not requirements.get("requires_fields"):
        return
    _normalize_field_markers(arguments)
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
        matched = _matching_layers_exact(layer_value, available_layers)
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
            "dataSource": layer.get("dataSource"),
            "fields": layer.get("fields", []),
            "fields_unknown": layer.get("fields_unknown", False)
        })
    return layers


def _layer_argument_names(operation: Dict[str, Any]) -> List[str]:
    properties = operation.get("parameters_schema", {}).get("properties", {})
    names = []
    for name, property_schema in properties.items():
        if isinstance(property_schema, dict) and property_schema.get("x-geopilot-kind") == "layer":
            names.append(name)
            continue
        lowered = name.lower()
        if "layer" in lowered and "output" not in lowered:
            names.append(name)
    return names


def _matching_layers_exact(value: str, layers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    raw = value[len("layer_ref:"):] if value.startswith("layer_ref:") else value
    if raw.startswith("@"):
        raw = raw[1:]
    matches = []
    for layer in layers:
        if raw in (
            layer.get("layer_ref"),
            layer.get("name"),
            layer.get("longName"),
            layer.get("dataSource")
        ):
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
    op = _condition_operator(condition, strict=False)
    if op in ("and", "or"):
        fields = []
        for child in condition.get("conditions") or []:
            fields.extend(_condition_fields(child))
        return fields
    if op == "not":
        return _condition_fields(condition.get("condition"))
    field = condition.get("field")
    return [str(field)] if field else []


def _normalize_field_markers(arguments: Dict[str, Any]) -> None:
    for key in ("field", "field_name"):
        if key in arguments:
            arguments[key] = _field_name(arguments[key])
    for key in ("fields", "dissolve_fields"):
        if isinstance(arguments.get(key), list):
            arguments[key] = [_field_name(value) for value in arguments[key]]
    if isinstance(arguments.get("where"), dict):
        _normalize_condition_field_markers(arguments["where"])
    if isinstance(arguments.get("assignments"), dict):
        normalized = {}
        for key, value in arguments["assignments"].items():
            normalized[_field_name(key)] = value
        arguments["assignments"] = normalized


def _normalize_condition_field_markers(condition: Dict[str, Any]) -> None:
    op = _condition_operator(condition, strict=False)
    if op in ("and", "or"):
        for child in condition.get("conditions") or []:
            if isinstance(child, dict):
                _normalize_condition_field_markers(child)
        return
    if op == "not":
        child = condition.get("condition")
        if isinstance(child, dict):
            _normalize_condition_field_markers(child)
        return
    if "field" in condition:
        condition["field"] = _field_name(condition["field"])


def _field_name(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text[1:] if text.startswith("#") else text


def _condition_operator(condition: Dict[str, Any], strict: bool = True) -> str:
    op = condition.get("op", condition.get("operator"))
    if op is None:
        if strict:
            raise ValidationError("属性条件缺少 op。")
        return ""
    text = str(op).strip().lower()
    return CONDITION_OPERATOR_ALIASES.get(text, text)


def _available_layer_names(layers: List[Dict[str, Any]]) -> List[str]:
    names = []
    for layer in layers:
        name = layer.get("name")
        if name and name not in names:
            names.append(str(name))
        if len(names) >= 10:
            break
    return names


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


def _has_timestamp_suffix(value: str) -> bool:
    return bool(re.search(r"(?:^|_)\d{8}(?:_\d{6})?$", value))


def _register_generated_output_name(output_name: Any, generated_names: set[str]) -> None:
    if not output_name:
        return
    text = str(output_name).lower()
    generated_names.add(text)
    generated_names.add(_without_timestamp_suffix(text))


def _path_points_to_generated_output(path: Any, generated_names: set[str]) -> bool:
    if not path or not generated_names:
        return False
    value = str(path).replace("/", "\\")
    name = Path(value).stem.lower()
    return name in generated_names or _without_timestamp_suffix(name) in generated_names


def _without_timestamp_suffix(value: str) -> str:
    match = re.match(r"^(.*)_\d{8}_\d{6}$", value)
    if match:
        return match.group(1)
    return value
