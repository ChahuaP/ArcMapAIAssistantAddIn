# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from . import common
from .common import OperationError, dataset
from shared_runtime import semantic_abi


# -*- coding: utf-8 -*-
"""Analysis tools run in a dedicated ArcGIS Python subprocess.

In-process GP analysis inside the COM-initiated UI callback kills ArcMap
natively (observed with Buffer); the identical call in a standalone ArcGIS
Python process succeeds. Heavy analysis therefore runs out of process: the
subprocess writes outputs to the same staging GDB, and the map only ADDs the
finished result afterwards.
"""
import io
import json
import os
import subprocess
import tempfile

_ARCGIS_PY = u"C:\Python27\ArcGIS10.2\python.exe"


def unicode_repr(value):
    # py2 repr(u'x') already yields u'x'; the double-u came from our prefix.
    return repr(unicode(value))


def _run_gp(tool, arguments_json):
    """Run one GP tool in a standalone ArcGIS Python subprocess.

    In-process GP analysis inside the COM-initiated UI callback kills ArcMap
    natively (observed with Buffer); the identical call standalone succeeds.
    Paths travel via a UTF-8 JSON argument file so no string escaping can
    corrupt them.
    """
    if not os.path.isfile(_ARCGIS_PY):
        return False
    script_path = tempfile.mktemp(suffix=".py", prefix="gp_")
    args_path = script_path + ".json"
    with open(args_path, "wb") as stream:
        stream.write(arguments_json.encode("utf-8"))
    runner = (
        u"# -*- coding: utf-8 -*-" + unichr(10)
        + u"import arcpy, json, io" + unichr(10)
        + u"arcpy.env.overwriteOutput = True" + unichr(10)
        + u"with io.open(" + unicode_repr(args_path) + u", 'r', encoding='utf-8') as f:" + unichr(10)
        + u"    args = json.load(f)" + unichr(10)
        + u"getattr(arcpy, args['tool'])(*args['args'])" + unichr(10)
        + u"print('GP-OK')" + unichr(10)
    )
    with io.open(script_path, "w", encoding="utf-8") as stream:
        stream.write(runner)
    try:
        proc = subprocess.Popen(
            [_ARCGIS_PY, script_path],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=0x08000000)
        output, _ = proc.communicate()
        if isinstance(output, str):
            try:
                output = output.decode("utf-8", "replace")
            except Exception:
                output = output.decode("gbk", "replace")
        if proc.returncode != 0 or u"GP-OK" not in (output or u""):
            raise common.OperationError(
                u"GP subprocess failed: %s" % (output or u"")[-400:])
        return True
    finally:
        for path in (script_path, args_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def _output(context, arguments):
    return common.output_feature_class(
        context,
        arguments["output_name"],
    )


def _add_to_map(output):
    """Bring a finished GP-subprocess output onto the active data frame.

    Analysis results live in the server-managed staging GDB; without this the
    operation executes but the user sees nothing change in ArcMap.
    """
    try:
        mxd = common.current_mxd()
        df = common.active_data_frame(mxd)
        layer = arcpy.mapping.Layer(output)
        arcpy.mapping.AddLayer(df, layer, "AUTO_ARRANGE")
        return True
    except Exception:
        return False


def buffer(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["input_layer"], step_outputs)
    output = _output(context, arguments)
    distance = semantic_abi.quantity_to_arcpy(arguments["distance"])
    payload = json.dumps({
        "tool": "Buffer_analysis",
        "args": [unicode(dataset(layer)), unicode(output), unicode(distance)],
    }, ensure_ascii=False)
    if not _run_gp("Buffer_analysis", payload):
        arcpy.Buffer_analysis(dataset(layer), output, distance)
    _add_to_map(output)
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
