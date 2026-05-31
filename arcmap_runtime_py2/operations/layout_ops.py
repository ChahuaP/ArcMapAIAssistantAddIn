# -*- coding: utf-8 -*-
from __future__ import absolute_import

import arcpy

from operations import common


ELEMENT_TYPES = (
    "TEXT_ELEMENT",
    "LEGEND_ELEMENT",
    "MAPSURROUND_ELEMENT",
    "PICTURE_ELEMENT",
    "DATAFRAME_ELEMENT",
)


def list_elements(context, arguments, step_outputs):
    mxd = common.current_mxd()
    element_type = arguments.get("element_type") or "ALL"
    if element_type == "ALL":
        elements = []
        for item_type in ELEMENT_TYPES:
            elements.extend(_element_items(mxd, item_type))
    else:
        elements = _element_items(mxd, element_type)
    return {"elements": elements, "count": len(elements)}


def set_text(context, arguments, step_outputs):
    mxd = common.current_mxd()
    element = _find_text_element(mxd, arguments["element_name"], arguments.get("match") or "EXACT")
    old_text = common._text(getattr(element, "text", ""))
    element.text = common._text(arguments["text"])
    common.refresh()
    return {
        "element_name": common._text(getattr(element, "name", arguments["element_name"])),
        "old_text": old_text,
        "text": element.text
    }


def set_active_view(context, arguments, step_outputs):
    mxd = common.current_mxd()
    view_mode = arguments["view_mode"]
    if view_mode == "PAGE_LAYOUT":
        mxd.activeView = "PAGE_LAYOUT"
    elif view_mode == "DATA_VIEW":
        df = common.active_data_frame(mxd)
        mxd.activeView = df.name
    else:
        raise common.OperationError(u"Unsupported view_mode: %s" % view_mode)
    common.refresh()
    return {"active_view": view_mode}


def export_pdf(context, arguments, step_outputs):
    mxd = common.current_mxd()
    output = common.output_file(context, arguments["output_name"], ".pdf", arguments.get("output_folder"))
    resolution = int(arguments.get("resolution", 300))
    image_quality = arguments.get("image_quality") or "BEST"
    arcpy.mapping.ExportToPDF(mxd, output, resolution=resolution, image_quality=image_quality)
    return {"output": output, "resolution": resolution, "image_quality": image_quality}


def export_png(context, arguments, step_outputs):
    mxd = common.current_mxd()
    output = common.output_file(context, arguments["output_name"], ".png", arguments.get("output_folder"))
    resolution = int(arguments.get("resolution", 300))
    world_file = bool(arguments.get("world_file", False))
    arcpy.mapping.ExportToPNG(mxd, output, resolution=resolution, world_file=world_file)
    return {"output": output, "resolution": resolution, "world_file": world_file}


def _element_items(mxd, element_type):
    items = []
    for element in arcpy.mapping.ListLayoutElements(mxd, element_type):
        items.append(_element_item(element, element_type))
    return items


def _element_item(element, element_type):
    item = {
        "type": element_type,
        "name": common._text(getattr(element, "name", "")),
        "element_position_x": _number_or_none(getattr(element, "elementPositionX", None)),
        "element_position_y": _number_or_none(getattr(element, "elementPositionY", None)),
        "element_width": _number_or_none(getattr(element, "elementWidth", None)),
        "element_height": _number_or_none(getattr(element, "elementHeight", None)),
    }
    if element_type == "TEXT_ELEMENT":
        item["text"] = common._text(getattr(element, "text", ""))
    return item


def _find_text_element(mxd, element_name, match):
    expected = common._text(element_name)
    if match == "EXACT":
        matches = [
            element for element in arcpy.mapping.ListLayoutElements(mxd, "TEXT_ELEMENT")
            if common._text(getattr(element, "name", "")) == expected
        ]
    elif match == "WILDCARD":
        matches = list(arcpy.mapping.ListLayoutElements(mxd, "TEXT_ELEMENT", expected))
    else:
        raise common.OperationError(u"Unsupported text element match mode: %s" % match)
    if not matches:
        raise common.OperationError(u"没有找到文本版面元素：%s。请先用 layout.list_elements 查看元素名称。" % expected)
    if len(matches) > 1:
        names = [common._text(getattr(element, "name", "")) for element in matches]
        raise common.OperationError(u"匹配到多个文本版面元素：%s。请使用精确名称。" % u"、".join(names))
    return matches[0]


def _number_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
