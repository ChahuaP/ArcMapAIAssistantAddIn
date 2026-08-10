"""Windows CurrentUser DPAPI envelopes for GeoPilot secrets and journal facts."""
from __future__ import annotations

import base64
import json
from typing import Any

try:
    import win32crypt
except ImportError as exc:  # pragma: no cover - deployment invariant
    raise RuntimeError("GeoPilot requires Windows DPAPI (win32crypt).") from exc


_PREFIX = "dpapi:v1:"
_DESCRIPTION = "GeoPilot CurrentUser protected data"


def protect_bytes(value: bytes) -> str:
    if not isinstance(value, bytes):
        raise TypeError("DPAPI accepts bytes only.")
    return _PREFIX + base64.b64encode(
        win32crypt.CryptProtectData(value, _DESCRIPTION, None, None, None, 0)
    ).decode("ascii")


def unprotect_bytes(envelope: str) -> bytes:
    if not isinstance(envelope, str) or not envelope.startswith(_PREFIX):
        raise ValueError("protected value must use the GeoPilot DPAPI v1 envelope.")
    try:
        protected = base64.b64decode(envelope[len(_PREFIX):], validate=True)
        _description, value = win32crypt.CryptUnprotectData(protected, None, None, None, 0)
    except Exception as exc:
        raise ValueError("GeoPilot DPAPI envelope cannot be decrypted for this Windows user.") from exc
    return value


def protect_json(value: Any) -> str:
    return protect_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def unprotect_json(envelope: str) -> Any:
    try:
        return json.loads(unprotect_bytes(envelope).decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("GeoPilot encrypted JSON is invalid.") from exc
