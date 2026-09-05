# -*- coding: utf-8 -*-
from __future__ import absolute_import

import importlib
import imp
import json
import os
import sys

import arcpy

try:
    import context_reader
    import artifact_observation
    import execution_session
    import exception_text
    import map_state_observation
    import path_utils
    from shared_runtime.output_contract import (
        OutputContractError,
        output_policy_type,
        validate_output_policy,
    )
    from shared_runtime.operation_schema import OperationSchemaError, validate_parameter_schema
    from shared_runtime import platform_paths
except ImportError:
    from . import context_reader
    from . import artifact_observation
    from . import execution_session
    from . import exception_text
    from . import map_state_observation
    from . import path_utils
    from shared_runtime.output_contract import (
        OutputContractError,
        output_policy_type,
        validate_output_policy,
    )
    from shared_runtime.operation_schema import OperationSchemaError, validate_parameter_schema
    from shared_runtime import platform_paths


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
    platform_paths.appdata_path("custom_tools", "enabled")
)


class WorkflowExecutionError(Exception):
    def __init__(self, message, step_id=None, capability_id=None, contract_path=None, expected=None, actual=None):
        Exception.__init__(self, message)
        self.step_id = step_id
        self.capability_id = capability_id
        self.contract_path = contract_path
        self.expected = expected
        self.actual = actual


def execute(workflow_row, context, confirm_callback=None):
    # Deferred silent execution must never surface GP dialogs: the overwrite
    # confirmation is an invisible UI-thread prompt that hangs ArcMap
    # (observed as AppHangB1 during buffer retries).
    arcpy.env.overwriteOutput = True
    workflow = workflow_row["workflow"]
    expected_hash = workflow_row.get("content_hash") or u""
    if expected_hash:
        actual_hash = context_reader.context_hash(context)
        if expected_hash != actual_hash:
            raise WorkflowExecutionError(u"ArcGIS 地图结构已变化。请重新同步上下文，并重新生成任务后再执行。")

    # Inject the task staging directory so operations write outputs into an
    # isolated per-run workspace instead of the MXD folder or a default GDB.
    # The Gateway reads staged artifacts from this directory after execution.
    # The Gateway is the only authority that names the run-scoped staging
    # directory. Local derivation would permit stale runtimes to escape it.
    run_id = workflow_row.get("run_id") or u""
    staging_root = workflow_row.get("staging_root") or u""
    if run_id:
        if not staging_root:
            raise WorkflowExecutionError(u"Gateway lease acknowledgement lacks staging_root.")
        context = dict(context)
        context["staging_root"] = staging_root
        context["run_id"] = run_id

    operations = _load_operations()
    step_outputs = {}
    results = []
    try:
        with execution_session.ExecutionSession() as session:
            for step in workflow["steps"]:
                step_id = str(step["id"])
                operation_id = step["operation"]
                try:
                    if operation_id not in operations:
                        raise WorkflowExecutionError("Unknown operation: %s" % operation_id)
                    operation = operations[operation_id]
                    arguments = step["arguments"]
                    _validate_arguments(step_id, arguments, operation["parameters_schema"])
                    runtime_arguments = _prepare_runtime_arguments(operation, context, arguments, step_outputs)
                    _confirm_edit_if_needed(operation, context, runtime_arguments, step_outputs, confirm_callback)
                    input_snapshot = _input_snapshot(operation, context, runtime_arguments, step_outputs)
                    result = _call_executor(operation["executor"], context, runtime_arguments, step_outputs)
                    _commit_map_state_if_needed(operation)
                    result = _finalize_runtime_result(operation, context, runtime_arguments, result)
                    for output_index, output_path in enumerate(_result_output_paths(operation, result)):
                        registered_step_id = step_id if output_index == 0 else "%s#%d" % (step_id, output_index)
                        session.register_output(
                            registered_step_id,
                            output_path,
                            output_policy_type(operation.get("output_policy") or {}),
                        )
                    publication_state = _publication_state(operation, result)
                    try:
                        observation = artifact_observation.observe_and_verify(
                            operation, runtime_arguments, result, context, step_outputs, publication_state,
                            input_snapshot, _execution_contract_proof(workflow_row, step_id, operation_id))
                    except artifact_observation.ArtifactVerificationError as exc:
                        raise WorkflowExecutionError(
                            u"步骤 %s（%s）后置条件失败：%s" % (step_id, operation_id, exc.contract_path),
                            step_id, operation_id, exc.contract_path, exc.expected, exc.actual)
                    result["input_snapshot"] = input_snapshot
                    result["observation"] = observation
                    result = session.canonicalize_runtime_references(result)
                except WorkflowExecutionError:
                    raise
                except Exception as exc:
                    raise WorkflowExecutionError(u"步骤 %s（%s）执行失败：%s" % (step_id, operation_id, _exception_text(exc)))
                step_outputs[step_id] = result
                results.append({"step_id": step_id, "operation": operation_id, "result": result})
    except WorkflowExecutionError:
        raise
    except Exception as exc:
        raise WorkflowExecutionError(u"工作流执行会话失败：%s" % _exception_text(exc))

    result = {"ok": True, "summary": workflow["summary"], "steps": results}
    return execution_session.ExecutionOutcome(result)


def _commit_map_state_if_needed(operation):
    """Commit ArcMap UI/COM state centrally after every map-mutating step."""
    if operation.get("side_effects") != "changes_map":
        return
    arcpy.RefreshTOC()
    arcpy.RefreshActiveView()


def _load_operations():
    with path_utils.open_text(path_utils.join_path(CATALOG_ROOT, "catalog.json"), "r") as f:
        catalog = json.load(f)
    operations = {}
    def register(operation):
        operation = _validated_operation(operation)
        operation_id = operation.get("id")
        if not isinstance(operation_id, basestring) or not operation_id:
            raise WorkflowExecutionError(u"operation id 必须是非空字符串。")
        if operation_id in operations:
            raise WorkflowExecutionError(u"重复 operation id：%s。" % operation_id)
        operations[operation_id] = operation

    for rel_path in catalog["packs"]:
        with path_utils.open_text(path_utils.join_path(CATALOG_ROOT, rel_path), "r") as f:
            pack = json.load(f)
        for operation in pack["operations"]:
            register(operation)
    if path_utils.isdir(CUSTOM_TOOLS_ROOT):
        for name in sorted(path_utils.listdir(CUSTOM_TOOLS_ROOT)):
            spec_path = path_utils.join_path(CUSTOM_TOOLS_ROOT, name, "operation_spec.json")
            if not path_utils.isfile(spec_path):
                continue
            with path_utils.open_text(spec_path, "r") as f:
                operation = json.load(f)
            register(operation)
    return operations


def _validated_operation(operation):
    result = dict(operation)
    result["parameters_schema"] = _validated_parameters_schema(result.get("parameters_schema", {}))
    try:
        result["output_policy"] = validate_output_policy(
            result.get("output_policy"), result.get("side_effects")
        )
    except OutputContractError as exc:
        raise WorkflowExecutionError(str(exc))
    if not isinstance(result.get("context_requirements"), dict):
        result["context_requirements"] = {}
    return result


def _validated_parameters_schema(schema):
    try:
        validate_parameter_schema(schema)
    except OperationSchemaError as exc:
        raise WorkflowExecutionError(str(exc))
    properties = schema["properties"]
    result = dict(schema)
    result["properties"] = _validated_parameter_properties(properties)
    result["required"] = list(schema["required"])
    return result


def _validated_parameter_properties(properties):
    result = {}
    for name, value in properties.items():
        if not isinstance(value, dict):
            raise WorkflowExecutionError(u"参数 %s 的 schema 必须是对象。" % name)
        result[name] = _validated_parameter_property(value)
    return result


def _validated_parameter_property(prop):
    result = dict(prop)
    kind = result.get("x-geopilot-kind")
    if kind is not None and kind not in ("layer", "path"):
        raise WorkflowExecutionError(u"x-geopilot-kind 只能是 layer 或 path。")
    return result


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
    module = importlib.import_module(module_name)
    function = getattr(module, function_name)
    return function(context, arguments, step_outputs)


def _prepare_runtime_arguments(operation, context, arguments, step_outputs):
    runtime_arguments = _adapt_semantic_arguments(arguments, operation.get("parameters_schema") or {})
    if not _is_custom_operation(operation):
        return runtime_arguments
    runtime_arguments = _normalize_declared_path_arguments(runtime_arguments, operation.get("parameters_schema") or {})
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
        )
    return runtime_arguments


def _adapt_semantic_arguments(arguments, schema):
    """Translate the closed Gateway ABI only at the ArcPy boundary."""
    try:
        from shared_runtime import semantic_abi
    except ImportError:
        import semantic_abi
    properties = schema.get("properties") or {}
    result = dict(arguments)
    for name, descriptor in properties.items():
        if name not in result or not isinstance(descriptor, dict):
            continue
        semantic = descriptor.get("x-geopilot-semantic")
        value = result[name]
        if semantic == "selection_type":
            result[name] = semantic_abi.selection_to_arcpy(value)
        elif semantic == "spatial_predicate":
            result[name] = semantic_abi.spatial_predicate_to_arcpy(value)
    return result


def _finalize_runtime_result(operation, context, arguments, result):
    if result is None or not isinstance(result, dict):
        result = {"ok": True}
    if not _is_custom_writes_data(operation):
        return result
    output_path = arguments.get("output_path")
    if not output_path:
        return result
    result.setdefault("output", output_path)
    return result


def _is_custom_writes_data(operation):
    return operation.get("side_effects") == "writes_data" and _is_custom_operation(operation)


def _output_adds_to_map(policy):
    if policy.get("add_to_map") is False:
        return False
    return output_policy_type(policy) == "feature_class"


def _result_adds_to_map(operation, result):
    return (
        operation.get("side_effects") == "writes_data"
        and isinstance(result, dict)
        and bool(result.get("output"))
        and _output_adds_to_map(operation.get("output_policy") or {})
    )


def _result_has_output(operation, result):
    return bool(_result_output_paths(operation, result))


def _result_output_paths(operation, result):
    if operation.get("side_effects") != "writes_data" or not isinstance(result, dict):
        return []
    if result.get("output"):
        return [result["output"]]
    if isinstance(result.get("outputs"), list):
        return list(result["outputs"])
    return []


def _publication_state(operation, result):
    if _result_adds_to_map(operation, result):
        return "scheduled"
    if operation.get("side_effects") == "changes_map":
        declared = (((operation.get("capability_contract") or {}).get("outputs") or {}).get("map_publication"))
        return declared if declared in ("published", "map_state_updated") else "map_state_updated"
    return "none"


def _input_snapshot(operation, context, arguments, step_outputs):
    """Capture only observable pre-state; edits_data is intentionally non-rollbackable."""
    snapshot = {"side_effects": operation.get("side_effects"), "inputs": {}}
    try:
        common = _operations_common()
        for name in _layer_argument_names(operation):
            if name in arguments:
                resolved = _resolve_layer_argument(common, context, arguments[name], step_outputs)
                snapshot["inputs"][name] = _observe_layer_argument(resolved)
        map_state_before = map_state_observation.capture_before(
            operation, arguments, context, step_outputs,
        )
        if map_state_before is not None:
            snapshot["map_state_before"] = map_state_before
    except Exception as exc:
        raise WorkflowExecutionError(u"无法采集步骤输入快照：%s" % _exception_text(exc))
    return snapshot


def _observe_layer_argument(value):
    if isinstance(value, list):
        return [_observe_layer_argument(item) for item in value]
    return artifact_observation._observe(value, "feature_class")


def _execution_contract_proof(workflow_row, step_id, capability_id):
    contract = workflow_row.get("execution_contract")
    if not isinstance(contract, dict) or contract.get("schema") != "geopilot-execution-contract/v1":
        return None
    matches = [
        proof for proof in contract.get("cardinality_proofs", [])
        if isinstance(proof, dict)
        and proof.get("step_id") == step_id
        and proof.get("capability_id") == capability_id
        and proof.get("contract_path") == "outputs.cardinality"
    ]
    return matches[0] if len(matches) == 1 else None


def _is_custom_operation(operation):
    return operation.get("executor", "").startswith("custom_tool:")


def _layer_argument_names(operation):
    properties = (operation.get("parameters_schema") or {}).get("properties") or {}
    names = []
    for name in properties:
        if properties.get(name, {}).get("x-geopilot-kind") == "layer":
            names.append(name)
    return names


def _resolve_layer_argument(common, context, value, step_outputs):
    if isinstance(value, list):
        return [_resolve_layer_argument(common, context, item, step_outputs) for item in value]
    if isinstance(value, basestring):
        return common.find_layer(context, value, step_outputs)
    return value


def _operations_common():
    return importlib.import_module("operations.common")


def _exception_text(exc):
    return exception_text.exception_text(exc)


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


def _normalize_declared_path_arguments(arguments, schema):
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        raise WorkflowExecutionError(u"自定义工具缺少有效 parameters_schema。")
    result = dict(arguments)
    for name, specification in properties.items():
        if (isinstance(specification, dict) and
                specification.get("x-geopilot-kind") == "path" and name in result):
            result[name] = _normalize_declared_path_value(result[name])
    return result


def _normalize_declared_path_value(value):
    if isinstance(value, basestring):
        return path_utils.to_unicode_path(value)
    if isinstance(value, list):
        return [_normalize_declared_path_value(item) for item in value]
    raise WorkflowExecutionError(u"声明为 path 的自定义工具参数必须是字符串或字符串列表。")
