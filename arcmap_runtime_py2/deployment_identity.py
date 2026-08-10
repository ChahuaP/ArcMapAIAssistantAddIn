# -*- coding: utf-8 -*-
"""Read this installed Py2 runtime's immutable deployment identity."""
from __future__ import absolute_import

import json
import os
import re

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def deployment_hash():
    path = os.path.join(os.path.dirname(__file__), "deployment_identity.json")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        document = json.loads(raw.decode("utf-8-sig"))
    except (IOError, OSError, ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(u"Py2 deployment identity is unreadable: %s" % path)
    if not isinstance(document, dict) or set(document.keys()) != set(["deployment_hash"]):
        raise RuntimeError(u"Py2 deployment identity has an invalid schema: %s" % path)
    value = document.get("deployment_hash")
    if not isinstance(value, unicode) or not _SHA256.match(value):
        raise RuntimeError(u"Py2 deployment identity must be lowercase sha256: %s" % path)
    return value


try:
    unicode
except NameError:
    unicode = str
