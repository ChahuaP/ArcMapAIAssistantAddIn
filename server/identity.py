"""Release identity: the single VERSION file at the repo root.

Pinned at 2.0.0 deliberately: the installed Py2 add-in compares this value
against its own build before trusting the callback server on 8765.
"""
from __future__ import annotations

from .paths import VERSION_PATH


def app_version() -> str:
    value = VERSION_PATH.read_text(encoding="ascii").strip()
    if not value or any(part == "" or not part.isdigit() for part in value.split(".")):
        raise RuntimeError("VERSION must contain a numeric dotted release version.")
    return value


APP_VERSION = app_version()
