# -*- coding: utf-8 -*-
"""Export a selected layer reference from the saved ArcMap project."""
from __future__ import print_function

import hashlib
import io
import json
import os

import arcpy
import pythonaddins


def checksum(path):
    value = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


class ExportHandoffButton(object):
    def __init__(self):
        self.enabled = True
        self.checked = False

    def onClick(self):
        document = arcpy.mapping.MapDocument("CURRENT")
        try:
            path = document.filePath
            if not path or not os.path.isfile(path):
                raise ValueError(u"请先保存当前 MXD。")
            selected = pythonaddins.GetSelectedTOCLayerOrDataFrame()
            if not selected or not getattr(selected, "isFeatureLayer", False):
                raise ValueError(u"请在图层列表中选择一个要素图层。")
            matches = [(frame_index, layer_index) for frame_index, frame in enumerate(arcpy.mapping.ListDataFrames(document))
                       for layer_index, layer in enumerate(arcpy.mapping.ListLayers(document, "", frame))
                       if frame.name == document.activeDataFrame.name and layer.longName == selected.longName]
            if len(matches) != 1:
                raise ValueError(u"请确保所选图层在当前数据框中的名称唯一。")
            frame, layer = matches[0]
            chosen = sorted(selected.getSelectionSet())
            if len(chosen) > 100000:
                raise ValueError(u"最多交接十万个已选要素。")
            target = pythonaddins.SaveDialog(u"保存规智交接文件", u"保存", os.path.dirname(path))
            if not target:
                return
            if not target.lower().endswith(".json"):
                target += ".json"
            value = {"action": "import", "map": path, "map_revision": checksum(path), "frame": frame, "layer": layer,
                     "expected_layer_name": selected.name, "selected_only": bool(chosen), "selected_ids": chosen or None}
            text = json.dumps(value, ensure_ascii=False, indent=2)
            with io.open(target, "w", encoding="utf-8") as stream:
                stream.write(text)
            pythonaddins.MessageBox(u"在规智的 ArcMap 交接中打开刚保存的文件。", u"规智", 0)
        except Exception as issue:
            pythonaddins.MessageBox(unicode(issue), u"规智交接", 0)
        finally:
            del document
