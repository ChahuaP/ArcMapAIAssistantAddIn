# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os


def _read_app_version():
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "VERSION"))
    with open(path, "rb") as stream:
        value = stream.read().decode("ascii").strip()
    parts = value.split(".")
    if not value or any(not part or not part.isdigit() for part in parts):
        raise RuntimeError("VERSION must contain a numeric dotted release version.")
    return value


APP_VERSION = _read_app_version()
