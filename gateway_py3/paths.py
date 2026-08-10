from __future__ import annotations

import sys
from pathlib import Path

from shared_runtime.platform_paths import appdata_root, localappdata_root


def _frozen_root():
    """Return the base directory for data files in frozen (PyInstaller) mode."""
    meipass = getattr(sys, '_MEIPASS', None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


_BASE = _frozen_root()
REPO_ROOT = _BASE
CATALOG_ROOT = _BASE / "operation_catalog"
WEB_ROOT = _BASE / "gateway_py3" / "web" if getattr(sys, '_MEIPASS', None) else Path(__file__).resolve().parent / "web"


def appdata_dir() -> Path:
    return Path(appdata_root())


def localappdata_dir() -> Path:
    return Path(localappdata_root())


def credential_store_path() -> Path:
    return appdata_dir() / "credentials.json"


def model_configuration_path() -> Path:
    return appdata_dir() / "model_configuration.json"


def data_dir() -> Path:
    path = localappdata_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = localappdata_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
