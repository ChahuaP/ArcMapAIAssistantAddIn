# -*- coding: utf-8 -*-
from __future__ import absolute_import

import gc
import ctypes
import os
import re
import uuid

import arcpy

try:
    import execution_session
    import output_publisher
    import path_utils
except ImportError:
    from . import execution_session
    from . import output_publisher
    from . import path_utils


TEMP_BASE = path_utils.join_path(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "ArcMapAIAssistant",
    "render_temp",
)
TEMP_ROOT = path_utils.join_path(TEMP_BASE, "arcmap-%s" % os.getpid())
PROCESS_DIR = re.compile(r"^arcmap-([0-9]+)$")
TEMP_NAME = re.compile(r"^\.geopilot-render-[0-9a-f]{32}\.mxd$")


def export_png(output, **kwargs):
    _export("png", output, kwargs)


def export_pdf(output, **kwargs):
    _export("pdf", output, kwargs)


def _export(format_name, output, kwargs):
    session = execution_session.current()
    if session is None:
        raise RuntimeError("Map export requires an active execution session.")

    output = path_utils.to_unicode_path(output)
    cleanup_stale()
    temporary_mxd = path_utils.join_path(TEMP_ROOT, ".geopilot-render-%s.mxd" % uuid.uuid4().hex)
    current_mxd = arcpy.mapping.MapDocument("CURRENT")
    render_mxd = None
    try:
        current_mxd.saveACopy(temporary_mxd)
        render_mxd = arcpy.mapping.MapDocument(temporary_mxd)
        output_publisher.publish(session.publication_plan(), render_mxd)
        if format_name == "png":
            arcpy.mapping.ExportToPNG(render_mxd, output, **kwargs)
        elif format_name == "pdf":
            arcpy.mapping.ExportToPDF(render_mxd, output, **kwargs)
        else:
            raise RuntimeError("Unsupported map export format: %s" % format_name)
    finally:
        render_mxd = None
        gc.collect()
        if path_utils.exists(temporary_mxd):
            path_utils.remove(temporary_mxd)


def cleanup_stale():
    path_utils.makedirs(TEMP_BASE)
    for directory_name in path_utils.listdir(TEMP_BASE):
        match = PROCESS_DIR.match(directory_name)
        if not match:
            continue
        pid = int(match.group(1))
        if pid != os.getpid() and _pid_alive(pid):
            continue
        directory = path_utils.join_path(TEMP_BASE, directory_name)
        if not path_utils.isdir(directory):
            continue
        for name in path_utils.listdir(directory):
            if not TEMP_NAME.match(name):
                continue
            path = path_utils.join_path(directory, name)
            if path_utils.isfile(path):
                path_utils.remove(path)
        if not path_utils.listdir(directory):
            os.rmdir(path_utils.to_unicode_path(directory))
    path_utils.makedirs(TEMP_ROOT)


def _pid_alive(pid):
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True
