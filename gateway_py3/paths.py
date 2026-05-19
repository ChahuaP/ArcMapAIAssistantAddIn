from __future__ import annotations

import os
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_ROOT = REPO_ROOT / "operation_catalog"
WEB_ROOT = Path(__file__).resolve().parent / "web"


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
