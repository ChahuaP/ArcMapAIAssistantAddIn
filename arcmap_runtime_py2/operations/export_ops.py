# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from operations import common


def export_map_png(context, arguments, step_outputs):
    mxd = common.current_mxd()
    output = common.output_file(context, arguments["output_name"], ".png")
    resolution = int(arguments.get("resolution", 150))
    arcpy.mapping.ExportToPNG(mxd, output, resolution=resolution)
    return {"output": output}


def export_map_pdf(context, arguments, step_outputs):
    mxd = common.current_mxd()
    output = common.output_file(context, arguments["output_name"], ".pdf")
    resolution = int(arguments.get("resolution", 150))
    arcpy.mapping.ExportToPDF(mxd, output, resolution=resolution)
    return {"output": output}


def export_table_csv(context, arguments, step_outputs):
    layer = common.find_layer(context, arguments["layer"])
    output = common.output_file(context, arguments["output_name"], ".csv")
    common.export_table_to_csv(layer, output, bool(arguments.get("selected_only", False)))
    return {"output": output}
