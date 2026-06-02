# -*- coding: utf-8 -*-
from __future__ import absolute_import

import importlib
import imp
import json
import os
import sys

try:
    import context_reader
    import path_utils
except ImportError:
    from . import context_reader
    from . import path_utils


try:
    reload
except NameError:
    from importlib import reload

try:
    basestring
except NameError:
    basestring = (str,)

try:
    unicode
except NameError:
    unicode = str

PY2 = sys.version_info[0] == 2


CATALOG_ROOT = path_utils.abspath(path_utils.join_path(os.path.dirname(__file__), "..", "operation_catalog"))
CUSTOM_TOOLS_ROOT = path_utils.join_path(
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
        try:
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
        except WorkflowExecutionError:
            raise
        except Exception as exc:
            raise WorkflowExecutionError(u"步骤 %s（%s）执行失败：%s" % (step_id, operation_id, _exception_text(exc)))
        step_outputs[step_id] = result
        results.append({"step_id": step_id, "operation": operation_id, "result": result})

    return {"ok": True, "summary": workflow["summary"], "steps": results}


def _load_operations():
    with path_utils.open_text(path_utils.join_path(CATALOG_ROOT, "catalog.json"), "r") as f:
        catalog = json.load(f)
    operations = {}
    for rel_path in catalog["packs"]:
        with path_utils.open_text(path_utils.join_path(CATALOG_ROOT, rel_path), "r") as f:
            pack = json.load(f)
        for operation in pack["operations"]:
            operations[operation["id"]] = operation
    if path_utils.isdir(CUSTOM_TOOLS_ROOT):
        for name in sorted(path_utils.listdir(CUSTOM_TOOLS_ROOT)):
            spec_path = path_utils.join_path(CUSTOM_TOOLS_ROOT, name, "operation_spec.json")
            if not path_utils.isfile(spec_path):
                continue
            with path_utils.open_text(spec_path, "r") as f:
                operation = json.load(f)
            operation = _canonicalize_operation(operation)
            operations[operation["id"]] = operation
    return operations


def _canonicalize_operation(operation):
    result = dict(operation)
    result["parameters_schema"] = _canonicalize_parameters_schema(result.get("parameters_schema", {}))
    result["output_policy"] = _canonical_output_policy(result.get("output_policy"), result.get("side_effects"))
    _ensure_managed_output_parameters(result)
    if not isinstance(result.get("context_requirements"), dict):
        result["context_requirements"] = {}
    return result


def _canonical_output_policy(policy, side_effects):
    if not isinstance(policy, dict):
        policy = {}
    result = dict(policy)
    if side_effects != "writes_data":
        return result
    output_type = _output_policy_type(result)
    result["type"] = output_type
    if output_type == "feature_class":
        result.setdefault("formats", ["gdb", "shp"])
        result.setdefault("default_format", "gdb")
        result.setdefault("add_to_map", True)
    elif output_type == "raster":
        result.setdefault("formats", ["tif"])
        result.setdefault("default_format", "tif")
        result.setdefault("add_to_map", True)
    elif output_type == "file":
        result.setdefault("add_to_map", False)
    return result


def _output_policy_type(policy):
    value = policy.get("type")
    if not value:
        return "feature_class"
    text = str(value).strip().lower()
    if text in ("vector", "feature", "featureclass"):
        return "feature_class"
    return text


def _ensure_managed_output_parameters(operation):
    if operation.get("side_effects") != "writes_data":
        return
    schema = operation.get("parameters_schema")
    if not isinstance(schema, dict):
        return
    properties = schema.setdefault("properties", {})
    if not isinstance(properties, dict):
        return
    output_type = _output_policy_type(operation.get("output_policy") or {})
    if output_type == "feature_class":
        properties.setdefault("output_workspace", {
            "type": "string",
            "description": "Optional output folder or geodatabase for GDB output. GeoPilot resolves output_path from this value."
        })
        properties.setdefault("output_folder", {
            "type": "string",
            "description": "Optional output folder for shapefile output. GeoPilot resolves output_path from this value."
        })
        properties.setdefault("output_format", {
            "type": "string",
            "enum": ["gdb", "shp"],
            "description": "Output vector format."
        })
    elif output_type in ("file", "raster"):
        properties.setdefault("output_folder", {
            "type": "string",
            "description": "Optional output folder. GeoPilot resolves output_path from this value."
        })


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
        condition_utils = importlib.import_module("operations.condition_utils")
        reload(condition_utils)
    module = importlib.import_module(module_name)
    module = reload(module)
    function = getattr(module, function_name)
    return function(context, arguments, step_outputs)


def _prepare_runtime_arguments(operation, context, arguments, step_outputs):
    if not _is_custom_operation(operation):
        return arguments
    runtime_arguments = _normalize_path_arguments(dict(arguments))
    common = _operations_common()
    for name in _layer_argument_names(operation):
        if name not in runtime_arguments:
            continue
        runtime_arguments[name] = _resolve_layer_argument(common, context, runtime_arguments[name], step_outputs)
    if _is_custom_writes_data(operation) and runtime_arguments.get("output_name") and not runtime_arguments.get("output_path"):
        runtime_arguments["output_path"] = common.output_dataset(
            context,
            runtime_arguments["output_name"],
            operation.get("output_policy") or {},
            runtime_arguments.get("output_workspace"),
            runtime_arguments.get("output_folder"),
            runtime_arguments.get("output_format")
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
    _validate_custom_output_artifact(operation.get("output_policy") or {}, output_path)
    if _output_adds_to_map(operation.get("output_policy") or {}):
        result["layer"] = common.add_output_layer(output_path)
    return result


def _is_custom_writes_data(operation):
    return operation.get("side_effects") == "writes_data" and _is_custom_operation(operation)


def _output_adds_to_map(policy):
    if policy.get("add_to_map") is False:
        return False
    return _output_policy_type(policy) in ("feature_class", "raster")


def _validate_custom_output_artifact(policy, output_path):
    output_type = _output_policy_type(policy)
    if output_type not in ("file", "raster"):
        return
    output_path = path_utils.to_unicode_path(output_path)
    if not path_utils.isfile(output_path):
        raise WorkflowExecutionError(u"自定义工具没有生成输出文件：%s" % output_path)
    if path_utils.getsize(output_path) <= 0:
        raise WorkflowExecutionError(u"自定义工具生成了空文件：%s" % output_path)
    if _obj_output_policy(policy, output_path):
        _validate_obj_file(output_path)


def _obj_output_policy(policy, output_path):
    extension = str(policy.get("extension") or "")
    return extension.lower() == ".obj" or str(output_path).lower().endswith(".obj")


def _validate_obj_file(output_path):
    has_vertex = False
    has_face = False
    with path_utils.open_text(output_path, "r") as handle:
        for line in handle:
            if line.startswith("v "):
                has_vertex = True
            elif line.startswith("f "):
                has_face = True
            if has_vertex and has_face:
                return
    raise WorkflowExecutionError(u"OBJ 输出没有有效顶点和面：%s" % output_path)


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


def _exception_text(exc):
    try:
        return unicode(exc)
    except (UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError):
        try:
            return str(exc).decode("utf-8", "replace")
        except (UnicodeDecodeError, UnicodeEncodeError, TypeError, AttributeError):
            return u"<unprintable exception>"


def _call_custom_executor(executor_path, context, arguments, step_outputs):
    module, function_name = _load_custom_module(executor_path)
    module.open = _custom_tool_open_factory(arguments)
    module.os = _custom_tool_os()
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
    executor_file = path_utils.join_path(CUSTOM_TOOLS_ROOT, tool_id, "executor.py")
    if not path_utils.isfile(executor_file):
        raise WorkflowExecutionError(u"自定义工具文件不存在：%s" % executor_file)
    _reject_legacy_custom_path_code(executor_file)
    module = imp.load_source("geopilot_custom_%s" % tool_id.replace("-", "_"), executor_file)
    import arcpy
    module.arcpy = arcpy
    return module, function_name


def _custom_tool_open_factory(arguments):
    output_path = path_utils.to_unicode_path(arguments.get("output_path"))

    def custom_tool_open(path, mode="r"):
        if not output_path:
            raise WorkflowExecutionError(u"自定义工具没有可写 output_path。")
        if not _same_path(path, output_path):
            raise WorkflowExecutionError(u"自定义工具只能写 arguments[\"output_path\"]。")
        if mode not in ("w", "wb"):
            raise WorkflowExecutionError(u"自定义工具只能用 w/wb 模式写 output_path。")
        if "b" in mode:
            handle = path_utils.open_binary(output_path, mode)
        else:
            handle = path_utils.open_text(output_path, mode)
        return _Utf8WriteHandle(handle, mode)

    return custom_tool_open


class _CustomToolOs(object):
    def __init__(self):
        self.path = _CustomToolPath()

    def __getattr__(self, name):
        return getattr(os, name)


class _CustomToolPath(object):
    def dirname(self, value):
        return path_utils.dirname(value)

    def basename(self, value):
        return path_utils.basename(value)

    def join(self, *parts):
        return path_utils.join_path(*parts)

    def exists(self, value):
        return path_utils.exists(value)

    def isfile(self, value):
        return path_utils.isfile(value)

    def isdir(self, value):
        return path_utils.isdir(value)

    def abspath(self, value):
        return path_utils.abspath(value)

    def normpath(self, value):
        return path_utils.normpath(value)

    def normcase(self, value):
        return path_utils.normcase(value)

    def splitext(self, value):
        return path_utils.splitext(value)


def _custom_tool_os():
    return _CustomToolOs()


class _Utf8WriteHandle(object):
    def __init__(self, handle, mode):
        self._handle = handle
        self._binary = "b" in mode

    def write(self, value):
        return self._handle.write(self._write_value(value))

    def writelines(self, values):
        for value in values:
            self.write(value)

    def close(self):
        return self._handle.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __getattr__(self, name):
        return getattr(self._handle, name)

    def _write_value(self, value):
        if PY2:
            if isinstance(value, unicode):
                return value.encode("utf-8")
            return value
        if self._binary and isinstance(value, str):
            return value.encode("utf-8")
        return value


def _same_path(left, right):
    return _normalize_path(left) == _normalize_path(right)


def _normalize_path(value):
    return path_utils.normalize_path(value)


def _path_text(value):
    return path_utils.to_unicode_path(value)


def _normalize_path_arguments(value, path_context=False, key=None):
    current_path_context = path_context or _is_path_argument_name(key)
    if isinstance(value, dict):
        return dict((item_key, _normalize_path_arguments(item_value, current_path_context, item_key)) for item_key, item_value in value.items())
    if isinstance(value, list):
        return [_normalize_path_arguments(item, current_path_context, key) for item in value]
    if current_path_context and isinstance(value, basestring):
        return path_utils.to_unicode_path(value)
    return value


def _is_path_argument_name(key):
    if not key:
        return False
    text = str(key).lower()
    return "path" in text or "folder" in text or "workspace" in text


def _reject_legacy_custom_path_code(executor_file):
    with path_utils.open_text(executor_file, "r") as handle:
        code = handle.read()
    forbidden = (
        ".decode(",
        ".encode(",
        "sys.getfilesystemencoding",
        "str(output_path)",
        "unicode(output_path)",
    )
    for pattern in forbidden:
        if pattern in code:
            raise WorkflowExecutionError(
                u"自定义工具包含旧路径编码逻辑（%s），需要重新审核后再运行。GeoPilot 现在会统一传入 Unicode 路径，工具不要自行 encode/decode。"
                % pattern
            )
