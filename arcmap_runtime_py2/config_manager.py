# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os

try:
    import path_utils
except ImportError:
    from . import path_utils


def config_dir():
    root = os.environ.get("APPDATA")
    if not root:
        root = os.path.expanduser("~")
    return path_utils.join_path(root, "ArcMapAIAssistant")


def config_path():
    return path_utils.join_path(config_dir(), "config.json")


def save_deepseek_key(api_key):
    api_key = (api_key or "").strip()
    if not api_key.startswith("sk-"):
        raise ValueError(u"DeepSeek API key 应该以 sk- 开头。")
    path = config_path()
    folder = path_utils.dirname(path)
    if not path_utils.isdir(folder):
        path_utils.makedirs(folder)

    config = load_config()
    for key in ("deepseek_api_key", "model", "base_url"):
        if key in config:
            del config[key]
    config.setdefault("default_mode", "semi_agent")
    config.setdefault("semi_agent_provider", "deepseek")
    config.setdefault("semi_agent_model", "deepseek-v4-flash-thinking")
    config.setdefault("full_agent_provider", "minimax")
    config.setdefault("full_agent_model", "MiniMax-M3")
    providers = config.setdefault("providers", {})
    providers["deepseek"] = {
        "api_key": api_key,
        "model": "deepseek-v4-flash-thinking",
        "base_url": "https://api.deepseek.com"
    }
    with path_utils.open_binary(path, "wb") as f:
        f.write(json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    return path


def load_config():
    path = config_path()
    if not path_utils.isfile(path):
        return {}
    with path_utils.open_binary(path, "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def has_deepseek_key():
    key = ((load_config().get("providers") or {}).get("deepseek") or {}).get("api_key")
    return bool(key)
