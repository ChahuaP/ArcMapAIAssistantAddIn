# -*- coding: utf-8 -*-
from __future__ import absolute_import

import importlib
import json
import os

import context_reader


CATALOG_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "operation_catalog"))


class WorkflowExecutionError(Exception):
    pass


def execute(workflow_row, context):
    workflow = workflow_row["workflow"]
    expected_hash = workflow_row["context_hash"]
    actual_hash = context_reader.context_hash(context)
    if expected_hash != actual_hash:
        raise WorkflowExecutionError(u"ArcGIS 地图结构已变化。请重新同步上下文，并重新生成任务后再执行。")

    operations = _load_operations()
    step_outputs = {}
    results = []

    for step in workflow["steps"]:
        operation_id = step["operation"]
        if operation_id not in operations:
            raise WorkflowExecutionError("Unknown operation: %s" % operation_id)
        operation = operations[operation_id]
        arguments = step["arguments"]
        _validate_arguments(step["id"], arguments, operation["parameters_schema"])
        _validate_write_policy(operation, context)
        result = _call_executor(operation["executor"], context, arguments, step_outputs)
        step_outputs[step["id"]] = result
        results.append({"step_id": step["id"], "operation": operation_id, "result": result})

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
    return operations


def _validate_write_policy(operation, context):
    if operation["side_effects"] == "writes_data" and not context.get("is_saved"):
        raise WorkflowExecutionError("This workflow writes output. Save the MXD before executing.")


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
    module_name, function_name = executor_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    module = reload(module)
    function = getattr(module, function_name)
    return function(context, arguments, step_outputs)
