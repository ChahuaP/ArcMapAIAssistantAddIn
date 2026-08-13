from __future__ import absolute_import

import hashlib
import json
import os


_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "runtime_gate.schema.json")


def _load_schema():
    with open(_SCHEMA_PATH, "rb") as handle:
        raw = handle.read()
    document = json.loads(raw.decode("utf-8"))
    expected = set(["schema_version", "protocol", "request_common_required",
                    "request_mode_required", "result_common_required", "result_mode_required"])
    if not isinstance(document, dict) or set(document.keys()) != expected:
        raise RuntimeError("runtime-gate schema has invalid root fields")
    return document, hashlib.sha256(raw).hexdigest()


SCHEMA, SCHEMA_SHA256 = _load_schema()
RUNTIME_GATE_PROTOCOL = SCHEMA["protocol"]


def validate_request(document):
    _validate(document, "request")


def validate_result(document, mode):
    _validate(document, "result", mode)


def _validate(document, direction, mode=None):
    if not isinstance(document, dict):
        raise RuntimeError("runtime-gate %s must be an object" % direction)
    actual_mode = document.get("mode") if direction == "request" else mode
    mode_requirements = SCHEMA[direction + "_mode_required"]
    if actual_mode not in mode_requirements:
        raise RuntimeError("runtime-gate mode is invalid")
    required = set(SCHEMA[direction + "_common_required"] + mode_requirements[actual_mode])
    missing = sorted(required.difference(document.keys()))
    if missing:
        raise RuntimeError("runtime-gate %s lacks required fields: %s" %
                           (direction, ",".join(missing)))
    allowed = set(required)
    if direction == "result":
        allowed.add("ok")
    unexpected = sorted(set(document.keys()).difference(allowed))
    if unexpected:
        raise RuntimeError("runtime-gate %s has unknown fields: %s" %
                           (direction, ",".join(unexpected)))
    if document.get("protocol") != RUNTIME_GATE_PROTOCOL:
        raise RuntimeError("runtime-gate protocol version mismatch")
    if document.get("schema_hash") != SCHEMA_SHA256:
        raise RuntimeError("runtime-gate schema hash mismatch")


__all__ = ["RUNTIME_GATE_PROTOCOL", "SCHEMA", "SCHEMA_SHA256",
           "validate_request", "validate_result"]
