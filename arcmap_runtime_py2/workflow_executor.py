# -*- coding: utf-8 -*-
from __future__ import absolute_import

import importlib
import imp
import json
import os

import context_reader


try:
    reload
except NameError:
    from importlib import reload

try:
    basestring
except NameError:
    basestring = (str,)


CATALOG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "operation_catalog"))
CUSTOM_TOOLS_ROOT = os.path.join(
    os.environ.get("APPDATA", os.path.expanduser("~")),
    "ArcMapAIAssistant",
    "custom_tools",
    "enabled"
)


class WorkflowExecutionError(Exception):
    pass


def execute(workflow_row, context, confirm_callback=None):
    workflow = workflow_row["workflow"]
    expected_hash = workflow_row["context_hash"]
    actual_hash = context_reader.context_hash(context)
    if expected_hash != actual_hash:
        raise WorkflowExecutionError(u"ArcGIS 地图结构已变化。请重新同步上下文，并重新生成任务后再执行。")

    operations = _load_operations()
    step_outputs = {}
    results = []

    for step in workflow["steps"]:
        step_id = str(step["id"])
        operation_id = step["operation"]
        if operation_id not in operations:
            raise WorkflowExecutionError("Unknown operation: %s" % operation_id)
        operation = operations[operation_id]
        arguments = step["arguments"]
        _validate_arguments(step_id, arguments, operation["parameters_schema"])
        _validate_write_policy(operation, context, arguments)
        runtime_arguments = _prepare_runtime_arguments(operation, context, arguments, step_outputs)
        _confirm_edit_if_needed(operation, context, runtime_arguments, step_outputs, confirm_callback)
        result = _call_executor(operation["executor"], context, runtime_arguments, step_outputs)
        result = _finalize_runtime_result(operation, context, runtime_arguments, result)
        step_outputs[step_id] = result
        results.append({"step_id": step_id, "operation": operation_id, "result": result})

    return {"ok": True, "summary": workflow["summary"], "steps": results}


def _load_operations():
    with open(os.path.join(CATALOG_ROOT, "catalog.json"), "r") as f:
        catalog = json.load(f)
    operations = {}
    for rel_path in catalog["packs"]:
        with open(os.path.join(CATALOG_ROOT, rel_path), "r") as f:
            pack = json.load(f)
        for operation in pack["operations"]:
            operations[operation["id"]] = operation
    if os.path.isdir(CUSTOM_TOOLS_ROOT):
        for name in sorted(os.listdir(CUSTOM_TOOLS_ROOT)):
            spec_path = os.path.join(CUSTOM_TOOLS_ROOT, name, "operation_spec.json")
            if not os.path.isfile(spec_path):
                continue
            with open(spec_path, "r") as f:
                operation = json.load(f)
            operation = _canonicalize_operation(operation)
            operations[operation["id"]] = operation
    return operations


def _canonicalize_operation(operation):
    result = dict(operation)
    result["parameters_schema"] = _canonicalize_parameters_schema(result.get("parameters_schema", {}))
    if not isinstance(result.get("context_requirements"), dict):
        result["context_requirements"] = {}
    if not isinstance(result.get("output_policy"), dict):
        result["output_policy"] = {}
    return result


def _canonicalize_parameters_schema(schema):
    if not isinstance(schema, dict):
        return {"type": "object", "required": [], "properties": {}, "additionalProperties": False}
    if schema.get("type") == "object":
        result = dict(schema)
        properties = result.get("properties", {})
        result["properties"] = _canonicalize_parameter_properties(properties if isinstance(properties, dict) else {})
        required = result.get("required", [])
        result["required"] = required if isinstance(required, list) else []
        result.setdefault("additionalProperties", False)
        return result

    properties = {}
    required = []
    for name, value in schema.items():
        if not isinstance(value, dict):
            continue
        prop = dict(value)
        required_flag = prop.pop("required", False)
        if required_flag is True or str(required_flag).lower() in ("true", "1", "yes"):
            required.append(name)
        properties[name] = _canonicalize_parameter_property(prop)
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False
    }


def _canonicalize_parameter_properties(properties):
    result = {}
    for name, value in properties.items():
        result[name] = _canonicalize_parameter_property(value if isinstance(value, dict) else {})
    return result


def _canonicalize_parameter_property(prop):
    result = dict(prop)
    if result.get("type") == "layer":
        result["type"] = "string"
        result["x-geopilot-kind"] = "layer"
    return result


def _validate_write_policy(operation, context, arguments):
    if operation["side_effects"] != "writes_data" or context.get("is_saved"):
        return
    if arguments.get("output_workspace") or arguments.get("output_folder"):
        return
    raise WorkflowExecutionError(u"当前 MXD 未保存。请先说明输出位置，或保存 MXD 后重新生成任务。")


def _confirm_edit_if_needed(operation, context, arguments, step_outputs, confirm_callback):
    if operation.get("side_effects") != "edits_data":
        return
    if confirm_callback is None:
        raise WorkflowExecutionError(u"该任务会直接修改原始数据，需要在 ArcGIS 中确认后才能执行。")
    estimate = _call_estimator(operation["executor"], context, arguments, step_outputs)
    message = estimate.get("summary") or u"该任务会直接修改原始数据。是否继续？"
    if not confirm_callback(message):
        raise WorkflowExecutionError(u"用户取消了直接修改数据的操作。")


def _validate_arguments(step_id, arguments, schema):
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    for name in required:
        if name not in arguments:
            raise WorkflowExecutionError("%s missing argument: %s" % (step_id, name))
    if schema.get("additionalProperties") is False:
        extra = sorted(set(arguments.keys()) - set(properties.keys()))
        if extra:
            raise WorkflowExecutionError("%s has unknown arguments: %s" % (step_id, extra))


def _call_executor(executor_path, context, arguments, step_outputs):
    if executor_path.startswith("custom_tool:"):
        return _call_custom_executor(executor_path, context, arguments, step_outputs)
    module_name, function_name = executor_path.rsplit(".", 1)
    if module_name.startswith("operations."):
        common = importlib.import_module("operations.common")
        reload(common)
        try:
            condition_utils = importlib.import_module("operations.condition_utils")
            reload(condition_utils)
        except Exception:
            pass
    module = importlib.import_module(module_name)
    module = reload(module)
    function = getattr(module, function_name)
    return function(context, arguments, step_outputs)


def _prepare_runtime_arguments(operation, context, arguments, step_outputs):
    if not _is_custom_operation(operation):
        return arguments
    runtime_arguments = dict(arguments)
    common = _operations_common()
    for name in _layer_argument_names(operation):
        if name not in runtime_arguments:
            continue
        runtime_arguments[name] = _resolve_layer_argument(common, context, runtime_arguments[name], step_outputs)
    if _is_custom_writes_data(operation) and runtime_arguments.get("output_name") and not runtime_arguments.get("output_path"):
        runtime_arguments["output_path"] = common.output_feature_class(
            context,
            runtime_arguments["output_name"],
            runtime_arguments.get("output_workspace")
        )
    return runtime_arguments


def _finalize_runtime_result(operation, context, arguments, result):
    if result is None or not isinstance(result, dict):
        result = {"ok": True}
    if not _is_custom_writes_data(operation):
        return result
    output_path = arguments.get("output_path")
    if not output_path:
        return result
    common = _operations_common()
    result.setdefault("output", output_path)
    result["layer"] = common.add_output_layer(output_path)
    return result


def _is_custom_writes_data(operation):
    return operation.get("side_effects") == "writes_data" and _is_custom_operation(operation)


def _is_custom_operation(operation):
    return operation.get("executor", "").startswith("custom_tool:")


def _layer_argument_names(operation):
    properties = (operation.get("parameters_schema") or {}).get("properties") or {}
    names = []
    for name in properties:
        if properties.get(name, {}).get("x-geopilot-kind") == "layer":
            names.append(name)
            continue
        lowered = name.lower()
        if "layer" in lowered and "output" not in lowered:
            names.append(name)
    return names


def _resolve_layer_argument(common, context, value, step_outputs):
    if isinstance(value, list):
        return [_resolve_layer_argument(common, context, item, step_outputs) for item in value]
    if isinstance(value, basestring):
        return common.find_layer(context, value, step_outputs)
    return value


def _operations_common():
    common = importlib.import_module("operations.common")
    return reload(common)


def _call_custom_executor(executor_path, context, arguments, step_outputs):
    module, function_name = _load_custom_module(executor_path)
    function = getattr(module, function_name)
    return function(context, arguments, step_outputs)


def _call_estimator(executor_path, context, arguments, step_outputs):
    if executor_path.startswith("custom_tool:"):
        module, function_name = _load_custom_module(executor_path)
        estimator = getattr(module, "estimate_" + function_name, None)
        if estimator is None:
            return {"summary": u"该任务会直接修改原始数据。是否继续？"}
        return estimator(context, arguments, step_outputs)
    module_name, function_name = executor_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    module = reload(module)
    estimator = getattr(module, "estimate_" + function_name, None)
    if estimator is None:
        return {"summary": u"该任务会直接修改原始数据。是否继续？"}
    return estimator(context, arguments, step_outputs)


def _load_custom_module(executor_path):
    parts = executor_path.split(":")
    if len(parts) != 3:
        raise WorkflowExecutionError(u"自定义工具 executor 格式不正确。")
    tool_id = parts[1]
    function_name = parts[2]
    executor_file = os.path.join(CUSTOM_TOOLS_ROOT, tool_id, "executor.py")
    if not os.path.isfile(executor_file):
        raise WorkflowExecutionError(u"自定义工具文件不存在：%s" % executor_file)
    module = imp.load_source("geopilot_custom_%s" % tool_id.replace("-", "_"), executor_file)
    import arcpy
    module.arcpy = arcpy
    return module, function_name
