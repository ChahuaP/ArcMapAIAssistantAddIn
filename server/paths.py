"""Filesystem anchors for the boundary server (repo layout and user data)."""
from __future__ import annotations

from pathlib import Path

from shared_runtime.platform_paths import appdata_root, localappdata_root

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_ROOT = REPO_ROOT / "operation_catalog"
VERSION_PATH = REPO_ROOT / "VERSION"


def appdata_dir() -> Path:
    return Path(appdata_root())


def localappdata_dir() -> Path:
    return Path(localappdata_root())


def log_dir() -> Path:
    path = localappdata_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
