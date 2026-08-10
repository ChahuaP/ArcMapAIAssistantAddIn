"""Single-source GeoPilot release identity."""
from __future__ import annotations

from .paths import REPO_ROOT


def _read_app_version() -> str:
    path = REPO_ROOT / "VERSION"
    value = path.read_text(encoding="ascii").strip()
    if not value or any(part == "" or not part.isdigit() for part in value.split(".")):
        raise RuntimeError("VERSION must contain a numeric dotted release version.")
    return value


APP_VERSION = _read_app_version()
