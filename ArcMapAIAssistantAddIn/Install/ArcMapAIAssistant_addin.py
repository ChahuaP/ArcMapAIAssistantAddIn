# -*- coding: utf-8 -*-

import imp
import json
import os
import pythonaddins
import sys


RUNTIME_MODULE = "arcmap_ai_assistant_runtime"
RUNTIME_FILE = "runtime.py"
INSTALL_CONFIG = os.path.join("ArcMapAIAssistant", "install.json")


try:
    unicode
except NameError:
    unicode = str
    basestring = str


def installed_runtime_path():
    override = os.environ.get("ARCMAP_AI_RUNTIME_PATH")
    if override:
        return override
    install_dir = installed_app_dir()
    if install_dir:
        return os.path.join(install_dir, "arcmap_runtime_py2")
    root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(root, "ArcMapAIAssistant", "app", "arcmap_runtime_py2")


def installed_app_dir():
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return ""
    config_path = os.path.join(appdata, INSTALL_CONFIG)
    if not os.path.isfile(config_path):
        return ""
    try:
        with open(config_path, "rb") as config_file:
            raw = config_file.read()
        if not isinstance(raw, unicode):
            raw = raw.decode("utf-8", "replace")
        raw = raw.lstrip(u"\ufeff")
        payload = json.loads(raw)
    except Exception:
        return ""
    install_dir = payload.get("install_dir", "")
    return install_dir if isinstance(install_dir, basestring) else ""


def show_message(text):
    pythonaddins.MessageBox(text, "ArcMap AI Assistant", 0)


def load_runtime_module():
    runtime_path = installed_runtime_path()
    runtime_file = os.path.join(runtime_path, RUNTIME_FILE)
    if not os.path.isfile(runtime_file):
        raise RuntimeError("Runtime file not found: %s" % runtime_file)
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)
    return imp.load_source(RUNTIME_MODULE, runtime_file)


def load_runtime():
    runtime = load_runtime_module()
    return runtime


def run_command(command_text):
    return load_runtime().handle_command(command_text)


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
