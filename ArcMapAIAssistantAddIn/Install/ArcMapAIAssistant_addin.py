# -*- coding: utf-8 -*-

import imp
import os
import pythonaddins
import sys


RUNTIME_PATH = r"D:\Development\Python\Arcpy\arcmap_runtime_py2"
RUNTIME_MODULE = "arcmap_ai_assistant_runtime"
RUNTIME_FILE = "runtime.py"


def show_message(text):
    pythonaddins.MessageBox(text, "ArcMap AI Assistant", 0)


def load_runtime_module():
    runtime_file = os.path.join(RUNTIME_PATH, RUNTIME_FILE)
    if not os.path.isfile(runtime_file):
        raise RuntimeError("Runtime file not found: %s" % runtime_file)
    if RUNTIME_PATH not in sys.path:
        sys.path.insert(0, RUNTIME_PATH)
    return imp.load_source(RUNTIME_MODULE, runtime_file)


def load_runtime():
    runtime = load_runtime_module()
    return runtime


def run_command(command_text):
    return load_runtime().handle_command(command_text)


def auto_sync(event_name):
    try:
        return load_runtime().auto_sync_context(event_name)
    except Exception:
        return False


class AutoSyncExtension(object):
    """Implementation for ArcMapAIAssistant_addin.autoSyncExtension (Extension)."""

    def __init__(self):
        self.enabled = True

    def startup(self, *args):
        auto_sync("startup")

    def openDocument(self, *args):
        auto_sync("openDocument")

    def newDocument(self, *args):
        auto_sync("newDocument")

    def mapsChanged(self, *args):
        auto_sync("mapsChanged")

    def contentsChanged(self, *args):
        auto_sync("contentsChanged")

    def spatialReferenceChanged(self, *args):
        auto_sync("spatialReferenceChanged")

    def itemAdded(self, *args):
        auto_sync("itemAdded")

    def itemDeleted(self, *args):
        auto_sync("itemDeleted")

    def itemReordered(self, *args):
        auto_sync("itemReordered")

    def onStartEditing(self, *args):
        auto_sync("onStartEditing")

    def onStopEditing(self, *args):
        auto_sync("onStopEditing")

    def onSaveEdits(self, *args):
        auto_sync("onSaveEdits")


class OpenAssistantButton(object):
    """Implementation for ArcMapAIAssistant_addin.openAssistantButton (Button)."""

    def __init__(self):
        self.enabled = True
        self.checked = False

    def onClick(self):
        try:
            load_runtime().open_assistant()
        except Exception as exc:
            show_message(u"执行失败：%s" % exc)


class StartGatewayButton(object):
    """Implementation for ArcMapAIAssistant_addin.startGatewayButton (Button)."""

    def __init__(self):
        self.enabled = True
        self.checked = False

    def onClick(self):
        try:
            load_runtime().start_gateway()
        except Exception as exc:
            show_message(u"执行失败：%s" % exc)


class SyncContextButton(object):
    """Implementation for ArcMapAIAssistant_addin.syncContextButton (Button)."""

    def __init__(self):
        self.enabled = True
        self.checked = False

    def onClick(self):
        try:
            load_runtime().sync_context()
        except Exception as exc:
            show_message(u"执行失败：%s" % exc)


class ExecuteWorkflowButton(object):
    """Implementation for ArcMapAIAssistant_addin.executeWorkflowButton (Button)."""

    def __init__(self):
        self.enabled = True
        self.checked = False

    def onClick(self):
        try:
            load_runtime().execute_pending()
        except Exception as exc:
            show_message(u"执行失败：%s" % exc)
