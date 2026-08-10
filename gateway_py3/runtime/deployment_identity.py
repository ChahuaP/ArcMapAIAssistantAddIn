"""Strict deployment identity reader for installed Gateway components."""
from __future__ import annotations

import json
import re
from pathlib import Path

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def read_identity(path: Path) -> str:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("deployment identity is unreadable: %s" % path) from exc
    if not isinstance(document, dict) or set(document) != {"deployment_hash"}:
        raise RuntimeError("deployment identity has an invalid schema: %s" % path)
    value = document["deployment_hash"]
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RuntimeError("deployment identity must be lowercase sha256: %s" % path)
    return value


def gateway_identity_path() -> Path:
    import sys
    return Path(sys.executable).resolve().parent / "deployment_identity.json"
