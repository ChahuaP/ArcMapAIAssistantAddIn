from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

from .paths import log_dir


def write_event(kind: str, payload: Dict[str, Any]) -> None:
    path = log_dir() / f"{time.strftime('%Y-%m-%d')}.jsonl"
    redacted = _redact(payload)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kind": kind,
        "payload": redacted
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if "key" in key.lower() or "authorization" in key.lower():
                result[key] = "***"
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
