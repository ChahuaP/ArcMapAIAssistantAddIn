from __future__ import annotations

import copy
from pathlib import Path
import re
from typing import Any, Dict, List

from arcmap_runtime_py2.condition_protocol import (
    CONDITION_OPERATOR_HELP,
    LEAF_CONDITION_OPERATORS,
    NUMBER_FIELD_TYPES,
    TEXT_FIELD_TYPES,
    VALUE_CONDITION_OPERATORS,
    canonical_operator,
    is_number_value,
    normalize_condition_tree,
)
from arcmap_runtime_py2.context_fingerprint import context_hash

from .catalog_loader import CatalogError, OperationCatalog
from .output_policy import OutputPolicyError, canonical_output_policy, output_policy_type, validate_output_policy


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
        try:
            policy = canonical_output_policy(operation["output_policy"], operation["side_effects"])
            validate_output_policy(policy, operation["side_effects"])
        except OutputPolicyError as exc:
            raise ValidationError("%s has invalid output_policy: %s" % (operation["id"], exc))


def prepare_workflow(workflow: Dict[str, Any], catalog: OperationCatalog, context: Dict[str, Any]) -> Dict[str, Any]:
    validate_workflow_shape(workflow)
    prepared = copy.deepcopy(workflow)
    normalize_workflow(prepared)
    normalize_workflow_arguments(prepared, catalog)
    validate_workflow(prepared, catalog)
    validate_workflow_semantics(prepared, catalog, context)
    return prepared


def validate_workflow_shape(workflow: Dict[str, Any]) -> None:
    if not isinstance(workflow, dict):
        raise ValidationError("Workflow must be an object.")
    steps = workflow.get("steps")
    if steps is not None and not isinstance(steps, list):
        raise ValidationError("Workflow steps must be an array.")
    for index, step in enumerate(steps or []):
        if not isinstance(step, dict):
            raise ValidationError("Workflow step %s must be an object." % (index + 1))
        arguments = step.get("arguments")
        if arguments is not None and not isinstance(arguments, dict):
            step_id = step.get("id") or "step_%s" % (index + 1)
            raise ValidationError("%s arguments must be an object." % step_id)


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
        raise ValidationError("Clarify, unsupported, and answer workflow objects must not contain executable steps.")

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


def normalize_workflow_arguments(workflow: Dict[str, Any], catalog: OperationCatalog) -> None:
    for step in workflow.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation_id = step.get("operation")
        if operation_id not in catalog.operations:
            continue
        arguments = step.get("arguments")
        if not isinstance(arguments, dict):
            continue
        properties = (catalog.operations[operation_id].get("parameters_schema") or {}).get("properties") or {}
        if "where" in properties and isinstance(arguments.get("where"), dict):
            arguments["where"] = normalize_condition_tree(arguments["where"])


def _existing_directory(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    path = Path(value.strip())
    return path.exists() and path.is_dir()


def _valid_output_workspace(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    path = Path(text)
    if text.lower().endswith(".gdb"):
        return path.exists() or (path.parent.exists() and path.parent.is_dir())
    return path.exists() and path.is_dir()


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
        _validate_condition_value_types(operation, arguments, available_layers)
        _validate_output_location(operation, arguments, context)
        _validate_output_name(arguments)
        _validate_layer_add_path(step, arguments, available_layers)

        seen_step_ids.add(step_id)
        _register_step_output(step, operation, available_layers)


def friendly_validation_message(error: Exception) -> str:
    message = str(error)
    if "Workflow steps must be an array" in message:
        return "workflow.steps 必须是数组，例如 \"steps\": [{...}]，不能写成 {\"item\": {...}}。请修正 workflow_json 后继续，不要向用户追问。"
    if "Workflow step " in message and " must be an object" in message:
        return "workflow.steps 里的每个步骤都必须是对象。请修正 workflow_json 后继续，不要向用户追问。"
    if " arguments must be an object" in message:
        return "workflow step 的 arguments 必须是对象。请修正 workflow_json 后继续，不要向用户追问。"
    if "Workflow action must be execute" in message:
        return "workflow 必须带 action。如果已有 steps，请设置 action=execute；如果只是回答、追问或不支持，steps 必须为空。请修正后继续，不要向用户追问。"
    if "Step missing field: operation" in message:
        return "我还不能确定要执行哪一种 GIS 操作。请把任务再说具体一点，比如要缓冲、裁剪、选择、导出，还是添加图层。"
    if "Step missing field: arguments" in message:
        return "这个任务的参数还不完整。请补充图层名、字段名、距离、输出名等必要信息。"
    if "Step missing field: reason" in message:
        return "每个执行步骤都必须带 reason，说明这一步为什么这样做。请补上 reason 后继续生成 workflow，不要向用户追问。"
    if "Step missing field:" in message:
        return "这个任务信息还不完整。请把要操作的图层、参数和输出位置再说清楚一点。"
    if "missing required argument:" in message:
        name = message.rsplit(":", 1)[-1].strip()
        if name == "output_name":
            return (
                "写数据步骤缺少 output_name。请根据 user_request 自行判断用户是否指定了输出名；"
                "如果指定了，就按用户命名传 output_name；如果没有指定，就由模型起一个清晰名字。"
                "不要向用户追问，不要让系统默认按图层名生成。"
            )
        return "这个操作还缺少必要参数“%s”。请根据 user_request 和上下文补齐；确实无法判断时才向用户追问。" % name
    if " must be array." in message:
        return message + " 数组参数必须写成 JSON 数组，例如 [{\"x\":120,\"y\":30}]，不能写成 {\"item\":[...]}。请修正 workflow_json 后继续，不要向用户追问。"
    if " must be integer." in message:
        return message + " 整数参数必须写成 JSON 数字，例如 4326，不能写成字符串 \"4326\"。请修正 workflow_json 后继续，不要向用户追问。"
    if " must be number." in message:
        return message + " 数值参数必须写成 JSON 数字，不能写成字符串。请修正 workflow_json 后继续，不要向用户追问。"
    if " must be object." in message:
        return message + " 对象参数必须写成 JSON 对象。请修正 workflow_json 后继续，不要向用户追问。"
    if "has unknown arguments:" in message:
        if "folder_path" in message:
            return "workflow operation 里不能使用 folder_path；folder_path 只属于 file_resolve。导出到文件夹时，请按 operation schema 使用 output_folder。请修正 workflow，不要向用户追问。"
        return message
    if "属性条件缺少 op" in message:
        return "属性条件 where 缺少 op。布尔条件必须写成 {\"op\":\"and\",\"conditions\":[...]} 或 {\"op\":\"or\",\"conditions\":[...]}，不能写 {\"and\":[...]}；叶子条件必须写 op，例如 {\"field\":\"NAME\",\"op\":\"like\",\"value\":\"%南京%\"}。请修正 workflow，不要向用户追问。"
    if "输出文件夹不存在" in message or "输出工作空间不可用" in message:
        return message + " 如果这是用户指定的位置，请先调用 output_folder_resolve 核实并向用户追问；如果用户没有指定输出位置，请移除输出位置参数，让系统使用 MXD 默认输出目录。"
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
            if "output_path" in extra:
                raise ValidationError(
                    "%s 不要传 output_path。output_path 只由 GeoPilot 执行时根据 output_name 和输出位置生成；"
                    "workflow 只允许传 operation schema 里声明的 output_name、output_folder 或 output_workspace，"
                    "不要为了 output_path 修订自建工具。"
                    % step_id
                )
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
        if not any(layer.get("layer_ref") == value for layer in available_layers):
            raise ValidationError(
                "步骤引用“%s”不是已加载图层的成果；file 输出不能作为图层引用。"
                % value
            )
        return value
    generated = _generated_layers_matching_value(value, available_layers)
    if len(generated) == 1:
        raise ValidationError(
            "“%s”是同一工作流的前序成果；必须使用 %s。"
            % (value, generated[0]["layer_ref"])
        )
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
        raise ValidationError(
            "当前地图里没有精确匹配“%s”的图层。可用图层有：%s。"
            "请从当前图层列表中选择一个。"
            % (value, "、".join(candidates))
        )
    raise ValidationError(
        "当前地图里没有“%s”图层。请先添加图层，或说明要使用哪个已有图层。"
        % value
    )


def _generated_layers_matching_value(
    value: Any,
    available_layers: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    target_name = Path(str(value)).stem
    return [
        layer
        for layer in available_layers
        if layer.get("layer_ref", "").startswith("from_step:")
        and target_name == str(layer.get("name"))
    ]


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
        if "value" in condition:
            raise ValidationError("between 条件必须使用 values，不能提供 value。")
    if op == "in":
        values = condition.get("values")
        if not isinstance(values, list) or not values:
            raise ValidationError("in 条件必须提供非空 values。")
        if "value" in condition:
            raise ValidationError("in 条件必须使用 values，不能提供 value。")
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


def _validate_condition_value_types(
    operation: Dict[str, Any],
    arguments: Dict[str, Any],
    available_layers: List[Dict[str, Any]]
) -> None:
    requirements = operation.get("context_requirements") or {}
    if not requirements.get("condition_fields") or not isinstance(arguments.get("where"), dict):
        return
    layer_value = _primary_layer_value(operation, arguments)
    if not layer_value:
        return
    layers = _matching_layers_exact(layer_value, available_layers)
    if len(layers) != 1 or layers[0].get("fields_unknown"):
        return
    field_types = {}
    for field in layers[0].get("fields", []) or []:
        name = field.get("name")
        if name:
            field_types[str(name).lower()] = str(field.get("type") or "")
    if field_types:
        _validate_condition_value_types_node(arguments["where"], field_types)


def _validate_condition_value_types_node(condition: Dict[str, Any], field_types: Dict[str, str]) -> None:
    op = _condition_operator(condition, strict=False)
    if op in ("and", "or"):
        for child in condition.get("conditions") or []:
            if isinstance(child, dict):
                _validate_condition_value_types_node(child, field_types)
        return
    if op == "not":
        child = condition.get("condition")
        if isinstance(child, dict):
            _validate_condition_value_types_node(child, field_types)
        return
    field_name = condition.get("field")
    if not field_name:
        return
    field_type = field_types.get(str(field_name).lower())
    if not field_type:
        return
    if op == "like" and field_type not in TEXT_FIELD_TYPES:
        raise ValidationError(
            "like 条件只能用于文本字段，“%s”字段类型是 %s。"
            % (field_name, field_type)
        )
    if field_type in NUMBER_FIELD_TYPES:
        _validate_numeric_condition_values(condition, op, field_name)


def _validate_numeric_condition_values(condition: Dict[str, Any], op: str, field_name: Any) -> None:
    if op in ("is_null", "is_not_null"):
        return
    values = []
    if op in VALUE_CONDITION_OPERATORS:
        values = [condition.get("value")]
    elif op in ("between", "in"):
        values = condition.get("values") or []
    for value in values:
        if not is_number_value(value):
            raise ValidationError("数值字段“%s”的条件值必须是数字。" % field_name)


def _validate_output_location(
    operation: Dict[str, Any],
    arguments: Dict[str, Any],
    context: Dict[str, Any],
) -> None:
    if operation.get("side_effects") != "writes_data":
        return
    output_format = str(arguments.get("output_format") or "").strip().lower()
    if arguments.get("output_folder") and arguments.get("output_workspace"):
        raise ValidationError("输出位置不能同时使用 output_folder 和 output_workspace。请只保留一个。")
    if (
        output_format in ("shp", "shapefile")
        and arguments.get("output_workspace")
        and str(arguments["output_workspace"]).lower().endswith(".gdb")
    ):
        raise ValidationError(
            "shp 输出必须使用 output_folder 指向已存在文件夹，不能输出到 GDB。"
        )
    if arguments.get("output_folder"):
        if not _existing_directory(arguments["output_folder"]):
            raise ValidationError(
                "输出文件夹不存在：%s。请使用已存在的文件夹，"
                "或在已保存 MXD 中省略输出位置使用默认输出位置。"
                % arguments["output_folder"]
            )
        return
    if arguments.get("output_workspace"):
        if not _valid_output_workspace(arguments["output_workspace"]):
            raise ValidationError(
                "输出工作空间不可用：%s。请使用已存在的文件夹/GDB，"
                "或在已保存 MXD 中省略输出位置使用默认输出位置。"
                % arguments["output_workspace"]
            )
        return
    workspace = (operation.get("output_policy") or {}).get("workspace", "")
    if context.get("is_saved") and workspace.startswith("mxd_default"):
        return
    raise ValidationError(
        "这个操作会生成新数据，但当前输出位置还不明确。请告诉我输出到哪个文件夹或 GDB。"
    )


def _validate_output_name(arguments: Dict[str, Any]) -> None:
    output_name = arguments.get("output_name")
    if not output_name:
        return
    text = str(output_name)
    if (
        text != text.strip()
        or text in (".", "..")
        or "." in text
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', text)
    ):
        raise ValidationError(
            "输出名称“%s”不能用于输出。请只传文件名主体，不要包含扩展名、"
            "路径或系统非法字符。"
            % output_name
        )


def _validate_layer_add_path(
    step: Dict[str, Any],
    arguments: Dict[str, Any],
    available_layers: List[Dict[str, Any]],
) -> None:
    if step.get("operation") != "layer.add_layer":
        return
    path = arguments.get("path")
    if not path:
        return
    generated = _generated_layers_matching_value(path, available_layers)
    if generated:
        raise ValidationError(
            "layer.add_layer 不得重复添加自动加载成果；请直接使用 %s。"
            % generated[0]["layer_ref"]
        )
    value = str(path).replace("/", "\\")
    if re.match(r"^[A-Za-z]:\\", value) and not Path(value).exists():
        raise ValidationError("没有找到这个文件：%s。请确认路径是否正确。" % value)


def _register_step_output(
    step: Dict[str, Any],
    operation: Dict[str, Any],
    available_layers: List[Dict[str, Any]],
) -> None:
    arguments = step.get("arguments") or {}
    names = []
    if step.get("operation") == "layer.add_layer" and arguments.get("path"):
        names.append(Path(arguments["path"]).stem)
    policy = canonical_output_policy(
        operation.get("output_policy"),
        operation.get("side_effects", ""),
    )
    if (
        arguments.get("output_name")
        and operation.get("side_effects") == "writes_data"
        and output_policy_type(policy) in ("feature_class", "raster")
        and policy.get("add_to_map") is True
    ):
        names.append(arguments["output_name"])
    for name in names:
        available_layers.append(
            {
                "layer_ref": "from_step:%s" % step["id"],
                "name": str(name),
                "longName": str(name),
                "fields": [],
                "fields_unknown": True,
            }
        )


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
    return canonical_operator(condition, strict=strict, error_cls=ValidationError, missing_message="属性条件缺少 op。")


def _available_layer_names(layers: List[Dict[str, Any]]) -> List[str]:
    names = []
    for layer in layers:
        name = layer.get("name")
        if name and name not in names:
            names.append(str(name))
        if len(names) >= 10:
            break
    return names
