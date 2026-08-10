"""Credential references backed only by Windows CurrentUser DPAPI."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Dict, Optional, Protocol

from ..paths import credential_store_path
from ..secure_storage import protect_bytes, unprotect_bytes


_SCHEMA = "geopilot-credential-vault-v1"


class CredentialVault(Protocol):
    def get(self, credential_ref: str) -> str: ...
    def has(self, credential_ref: str) -> bool: ...


class DpapiCredentialVault:
    """Small strict vault; files contain references and DPAPI envelopes only."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else credential_store_path()
        self._lock = threading.Lock()

    def get(self, credential_ref: str) -> str:
        envelope = self._load()[self._validate_ref(credential_ref)]
        value = unprotect_bytes(envelope).decode("utf-8")
        if not value:
            raise ValueError("credential is empty: %s" % credential_ref)
        return value

    def has(self, credential_ref: str) -> bool:
        return self._validate_ref(credential_ref) in self._load()

    def put(self, credential_ref: str, secret: str) -> None:
        ref = self._validate_ref(credential_ref)
        if not isinstance(secret, str) or not secret.strip():
            raise ValueError("credential secret must be non-empty text.")
        with self._lock:
            credentials = self._load()
            credentials[ref] = protect_bytes(secret.strip().encode("utf-8"))
            self._write(credentials)

    def delete(self, credential_ref: str) -> None:
        ref = self._validate_ref(credential_ref)
        with self._lock:
            credentials = self._load()
            credentials.pop(ref, None)
            self._write(credentials)

    @staticmethod
    def _validate_ref(value: str) -> str:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError("credential_ref must be non-empty canonical text.")
        return value

    def _load(self) -> Dict[str, str]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as stream:
            document = json.load(stream)
        if not isinstance(document, dict) or set(document) != {"schema", "credentials"}:
            raise ValueError("credential vault document has an invalid contract.")
        if document["schema"] != _SCHEMA or not isinstance(document["credentials"], dict):
            raise ValueError("credential vault schema is incompatible.")
        result: Dict[str, str] = {}
        for key, envelope in document["credentials"].items():
            ref = self._validate_ref(key)
            if not isinstance(envelope, str) or not envelope.startswith("dpapi:v1:"):
                raise ValueError("credential vault contains a plaintext or invalid value.")
            result[ref] = envelope
        return result

    def _write(self, credentials: Dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".writing")
        document = {"schema": _SCHEMA, "credentials": credentials}
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(self.path))
