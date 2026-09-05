# -*- coding: utf-8 -*-
from __future__ import absolute_import

import io
import os

import arcpy

try:
    import path_utils
    from operations import common
except ImportError:
    from .. import path_utils
    from . import common


def export_table_csv(context, arguments, step_outputs):
    source = common.find_layer(context, arguments["layer"], step_outputs)
    output = common.output_file(context, arguments["output_name"], "csv")
    result = arcpy.TableToTable_conversion(source, path_utils.dirname(output), path_utils.basename(output))
    actual = path_utils.to_unicode_path(result.getOutput(0))
    if path_utils.normcase(path_utils.normpath(actual)) != path_utils.normcase(path_utils.normpath(output)):
        raise common.OperationError(u"TableToTable 未完成受管 CSV 输出：%s" % output)
    return {"output": output}


def export_map_png(context, arguments, step_outputs):
    output = common.output_file(context, arguments["output_name"], "png")
    # ExportToPNG on the COM-initiated UI callback crashed ArcMap natively;
    # snapshot the document and let a standalone ArcGIS Python process do the
    # rasterization from the copy.
    mxd = common.current_mxd()
    copy_path = output + u".snapshot.mxd"
    mxd.saveACopy(copy_path)
    try:
        if _export_png_out_of_process(copy_path, output):
            done = path_utils.isfile(output)
        else:
            arcpy.mapping.ExportToPNG(mxd, output)
            done = True
    finally:
        try:
            os.unlink(copy_path)
        except OSError:
            pass
    if not done or not path_utils.isfile(output):
        raise common.OperationError(u"ExportToPNG 未生成受管 PNG 输出：%s" % output)
    return {"output": output}


_ARCGIS_PY = u"C:\Python27\ArcGIS10.2\python.exe"


def _export_png_out_of_process(mxd_copy, output):
    import subprocess as _sp
    import tempfile as _tf
    if not os.path.isfile(_ARCGIS_PY):
        return False
    runner = _tf.mktemp(suffix=".py", prefix="png_")
    body = (
        u"# -*- coding: utf-8 -*-" + unichr(10)
        + u"import arcpy, io" + unichr(10)
        + u"mxd = arcpy.mapping.MapDocument(u" + repr(mxd_copy) + u")" + unichr(10)
        + u"arcpy.mapping.ExportToPNG(mxd, u" + repr(output) + u")" + unichr(10)
        + u"print('PNG-OK')" + unichr(10)
    )
    with io.open(runner, "w", encoding="utf-8") as stream:
        stream.write(body)
    try:
        proc = _sp.Popen([_ARCGIS_PY, runner], stdout=_sp.PIPE,
                         stderr=_sp.STDOUT, creationflags=0x08000000)
        out, _ = proc.communicate()
        if isinstance(out, str):
            out = out.decode("utf-8", "replace")
        if proc.returncode != 0 or u"PNG-OK" not in (out or u""):
            return False
        return True
    finally:
        try:
            os.unlink(runner)
        except OSError:
            pass
