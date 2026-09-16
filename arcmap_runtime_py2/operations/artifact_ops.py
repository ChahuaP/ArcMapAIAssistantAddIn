# -*- coding: utf-8 -*-
from __future__ import absolute_import


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
