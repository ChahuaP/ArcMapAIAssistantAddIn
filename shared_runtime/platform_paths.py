# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os


def _required_environment(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError("Required Windows environment variable is missing: %s" % name)
    return os.path.abspath(value)


def appdata_root():
    return os.path.join(_required_environment("APPDATA"), "ArcMapAIAssistant")


def appdata_path(*parts):
    return os.path.join(appdata_root(), *parts)


def localappdata_root():
    return os.path.join(_required_environment("LOCALAPPDATA"), "ArcMapAIAssistant")


def localappdata_path(*parts):
    return os.path.join(localappdata_root(), *parts)


def user_profile():
    return _required_environment("USERPROFILE")


def command_shell():
    return _required_environment("COMSPEC")
