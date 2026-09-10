# -*- coding: utf-8 -*-

import imp
import json
import os
import pythonaddins
import sys
import time


RUNTIME_MODULE = "arcmap_ai_assistant_runtime"
RUNTIME_FILE = "runtime.py"
INSTALL_CONFIG = os.path.join("ArcMapAIAssistant", "install.json")
_RUNTIME = None


try:
    unicode
except NameError:
    unicode = str
    basestring = str


def installed_runtime_path():
    install_dir = installed_app_dir()
    if not install_dir:
        raise RuntimeError("ArcMap Harness install config is missing install_dir.")
    return os.path.join(install_dir, "arcmap_runtime_py2")


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
    except Exception as exc:
        raise RuntimeError("Invalid ArcMap Harness install config: %s" % exc)
    install_dir = payload.get("install_dir", "")
    if not isinstance(install_dir, basestring) or not install_dir:
        raise RuntimeError("ArcMap Harness install config is missing install_dir.")
    return install_dir


def _log_error(exc):
    """Every suppressed onClick failure must leave a trace on disk."""
    try:
        import traceback as _tb
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "ArcMapAIAssistant", "logs")
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        with open(os.path.join(log_dir, "addin_error.log"), "ab") as stream:
            stream.write(("[%s] %s " % (time.strftime("%Y-%m-%d %H:%M:%S"), exc)) + _tb.format_exc() + chr(10))
    except Exception:
        pass


def show_message(text):
    pythonaddins.MessageBox(text, "ArcMap AI Assistant", 0)


def load_runtime_module():
    install_dir = installed_app_dir()
    runtime_path = installed_runtime_path()
    runtime_file = os.path.join(runtime_path, RUNTIME_FILE)
    if not os.path.isfile(runtime_file):
        raise RuntimeError("Runtime file not found: %s" % runtime_file)
    if install_dir not in sys.path:
        sys.path.insert(0, install_dir)
    if runtime_path not in sys.path:
        sys.path.insert(0, runtime_path)
    return imp.load_source(RUNTIME_MODULE, runtime_file)


def load_runtime():
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = load_runtime_module()
    return _RUNTIME


class OpenAssistantButton(object):
    """Implementation for ArcMapAIAssistant_addin.openAssistantButton (Button)."""

    def __init__(self):
        self.enabled = True
        self.checked = False

    def onClick(self):
        runtime = None
        try:
            runtime = load_runtime()
            runtime.bind_ui_thread()
            runtime.open_or_handle_bridge_command()
        except Exception as exc:
            _log_error(exc)
            if not runtime or not getattr(runtime, "suppress_last_error_popup", lambda: False)():
                show_message(u"执行失败：%s" % exc)
