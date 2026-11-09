
"""Increment architecture, feature and fix versions consistently."""
from typing import Literal


def increment(version: str, level: Literal["feature", "fix"]):
    architecture, feature, fix = map(int, version.split("."))
    if level == "feature":
        return f"{architecture}.{feature + 1}.0"
    return f"{architecture}.{feature}.{fix + 1}"
