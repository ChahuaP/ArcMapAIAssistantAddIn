from __future__ import annotations

import os
import sys
from pathlib import Path


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
    root = os.environ.get("APPDATA")
    if root:
        return Path(root) / "ArcMapAIAssistant"
    return Path.home() / ".arcmap_ai_assistant"


def localappdata_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    if root:
        return Path(root) / "ArcMapAIAssistant"
    return Path.home() / ".arcmap_ai_assistant"


def config_path() -> Path:
    return appdata_dir() / "config.json"


def data_dir() -> Path:
    path = localappdata_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = localappdata_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
