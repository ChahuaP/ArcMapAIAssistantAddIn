# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from . import common
from .common import OperationError, dataset
from shared_runtime import semantic_abi


# Buffer stays outside the COM callback: in-process GP can crash ArcMap.
import json
import os
import subprocess
import sys
import tempfile


def run_buffer(input_path, output_path, distance):
    python_exe = os.path.join(sys.prefix, "python.exe")
    if not os.path.isfile(python_exe):
        raise common.OperationError(u"ArcGIS Python interpreter is unavailable: %s" % python_exe)
    worker = os.path.join(os.path.dirname(__file__), "gp_worker.py")
    descriptor, args_path = tempfile.mkstemp(suffix=".json", prefix="arcmap-buffer-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps([input_path, output_path, distance], ensure_ascii=False).encode("utf-8"))
        proc = subprocess.Popen([python_exe, worker, args_path], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, creationflags=0x08000000)
        output, _ = proc.communicate()
        if proc.returncode != 0 or b"GP-OK" not in output:
            raise common.OperationError(u"Buffer subprocess failed: %s" % output.decode("utf-8", "replace")[-2000:])
    finally:
        os.unlink(args_path)


def _output(context, arguments):
    return common.output_feature_class(
        context,
        arguments["output_name"],
    )


def buffer(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    output = _output(context, arguments)
    distance = semantic_abi.quantity_to_arcpy(arguments["distance"])
    run_buffer(unicode(dataset(layer)), unicode(output), unicode(distance))
    return {"output": output}


def clip(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    clip_layer = common.find_layer(context, arguments["clip_layer"], step_outputs)
    output = _output(context, arguments)
    arcpy.Clip_analysis(common.dataset(input_layer), common.dataset(clip_layer), output)
    return {"output": output}


def intersect(context, arguments, step_outputs):
    layers = [common.find_layer(context, layer_value, step_outputs) for layer_value in arguments["input_layers"]]
    output = _output(context, arguments)
    arcpy.Intersect_analysis([common.dataset(l) for l in layers], output)
    return {"output": output}


def dissolve(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    output = _output(context, arguments)
    fields = arguments.get("dissolve_fields") or []
    arcpy.Dissolve_management(common.dataset(layer), output, fields)
    return {"output": output}


def project(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    output = _output(context, arguments)
    spatial_reference = arcpy.SpatialReference(arguments["spatial_reference"])
    arcpy.Project_management(common.dataset(layer), output, spatial_reference)
    return {"output": output}


def spatial_join(context, arguments, step_outputs):
    target = common.find_layer(context, arguments["target_layer"], step_outputs)
    join = common.find_layer(context, arguments["join_layer"], step_outputs)
    output = _output(context, arguments)
    arcpy.SpatialJoin_analysis(target, join, output)
    return {"output": output}


def erase(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    erase_layer = common.find_layer(context, arguments["erase_layer"], step_outputs)
    output = _output(context, arguments)
    arcpy.Erase_analysis(input_layer, erase_layer, output)
    return {"output": output}


def identity(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    identity_layer = common.find_layer(context, arguments["identity_layer"], step_outputs)
    output = _output(context, arguments)
    arcpy.Identity_analysis(input_layer, identity_layer, output)
    return {"output": output}


def union(context, arguments, step_outputs):
    layers = [common.find_layer(context, layer_value, step_outputs) for layer_value in arguments["input_layers"]]
    output = _output(context, arguments)
    arcpy.Union_analysis(layers, output)
    return {"output": output}


def symmetrical_difference(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    update_layer = common.find_layer(context, arguments["update_layer"], step_outputs)
    output = _output(context, arguments)
    arcpy.SymDiff_analysis(input_layer, update_layer, output)
    return {"output": output}


def update_overlay(context, arguments, step_outputs):
    input_layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    update_layer = common.find_layer(context, arguments["update_layer"], step_outputs)
    output = _output(context, arguments)
    arcpy.Update_analysis(input_layer, update_layer, output)
    return {"output": output}


def merge(context, arguments, step_outputs):
    layers = [common.find_layer(context, layer_value, step_outputs) for layer_value in arguments["input_layers"]]
    output = _output(context, arguments)
    arcpy.Merge_management(layers, output)
    return {"output": output}


def append(context, arguments, step_outputs):
    inputs = [common.find_layer(context, layer_value, step_outputs) for layer_value in arguments["input_layers"]]
    target = common.find_layer(context, arguments["target_layer"], step_outputs)
    schema_type = arguments.get("schema_type", "NO_TEST")
    arcpy.Append_management(inputs, target, schema_type)
    return {"target_layer": target.name, "appended_layers": len(inputs)}


def estimate_append(context, arguments, step_outputs):
    target = common.find_layer(context, arguments["target_layer"], step_outputs)
    return {"summary": u"将直接修改图层 %s：把 %s 个输入图层追加进去。" % (target.name, len(arguments["input_layers"]))}
