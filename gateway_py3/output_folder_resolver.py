from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict


KNOWN_FOLDER_NAMES = ("desktop", "documents", "downloads")
INVALID_FOLDER_CHARS = set('<>:"/\\|?*\x00')


class OutputFolderResolver:
    def __init__(self, known_roots: Dict[str, Path] | None = None):
        self.known_roots = {key: Path(value) for key, value in (known_roots or {}).items()}

    def resolve(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        path = _optional_string(arguments, "path")
        parent_path = _optional_string(arguments, "parent_path")
        folder_name = _optional_string(arguments, "folder_name")
        known_folder = _optional_string(arguments, "known_folder").lower()

        if path and (parent_path or known_folder or folder_name):
            return _clarify("输出目录参数不能混用 path 和 parent_path/known_folder/folder_name。")
        if path:
            return _resolve_existing_folder(Path(path.replace("/", "\\")))

        base = None
        if parent_path:
            base = Path(parent_path.replace("/", "\\"))
        elif known_folder:
            base = self._known_folder(known_folder)
            if base is None:
                return _clarify("未知输出位置：%s。请使用 desktop、documents 或 downloads。" % known_folder)
        else:
            return _clarify("请提供输出文件夹的完整路径，或用 known_folder 加 folder_name 表达桌面/文档/下载下的目录。")

        if not base.exists() or not base.is_dir():
            return _clarify("输出父目录不存在：%s。" % str(base))
        target = base
        if folder_name:
            if not _valid_folder_name(folder_name):
                return _clarify("输出文件夹名不能包含路径分隔符或 Windows 非法字符：%s。" % folder_name)
            target = base / folder_name
        return _resolve_existing_folder(target)

    def _known_folder(self, name: str) -> Path | None:
        if name not in KNOWN_FOLDER_NAMES:
            return None
        if name in self.known_roots:
            return self.known_roots[name]
        home = Path(os.environ.get("USERPROFILE") or str(Path.home()))
        if name == "desktop":
            return home / "Desktop"
        if name == "documents":
            return home / "Documents"
        if name == "downloads":
            return home / "Downloads"
        return None


def _resolve_existing_folder(path: Path) -> Dict[str, Any]:
    if path.exists() and path.is_dir():
        return {"status": "resolved", "path": str(path)}
    if path.exists():
        return _clarify("输出位置不是文件夹：%s。" % str(path))
    return _clarify("输出文件夹不存在：%s。请先创建该文件夹，或改用已有文件夹。" % str(path))


def _optional_string(arguments: Dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _valid_folder_name(value: str) -> bool:
    text = value.strip()
    return bool(text) and text not in (".", "..") and not any(char in INVALID_FOLDER_CHARS for char in text)


def _clarify(question: str) -> Dict[str, Any]:
    return {"status": "clarify", "question": question}
