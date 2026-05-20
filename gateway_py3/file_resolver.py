from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List


GIS_EXTENSIONS = (".shp", ".lyr", ".tif", ".img", ".sde", ".gdb")
class FileResolution:
    def __init__(
        self,
        status: str,
        question: str = "",
        path: str | None = None,
        paths: List[str] | None = None,
        candidates: List[str] | None = None,
        search_root: str = "",
        child_directories: List[str] | None = None,
    ):
        self.status = status
        self.question = question
        self.path = path
        self.paths = paths or ([path] if path else [])
        self.candidates = candidates or []
        self.search_root = search_root
        self.child_directories = child_directories or []
        self.files = [_resolved_file(path) for path in self.paths]

    def to_tool_result(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "files": self.files,
            "question": self.question,
            "candidates": self.candidates,
            "search_root": self.search_root,
            "child_directories": self.child_directories,
        }


class FileResolver:
    def __init__(self, max_seconds: float = 2.0, max_results: int = 20, drive_roots: Dict[str, Path] | None = None):
        self.max_seconds = max_seconds
        self.max_results = max_results
        self.drive_roots = {key.upper(): Path(value) for key, value in (drive_roots or {}).items()}

    def resolve(self, arguments: Dict[str, Any]) -> FileResolution:
        path = _optional_string(arguments, "path") or _optional_string(arguments, "folder_path")
        if path:
            return self._resolve_path(path, _extensions(arguments))

        drive = _optional_string(arguments, "drive")
        if not drive:
            return FileResolution("unsupported", "请先说明文件所在盘符、完整路径或文件夹路径。")

        root = self._drive_root(drive)
        if not root.exists():
            return FileResolution("clarify", "%s 不存在。请确认磁盘或目录。" % str(root))

        directory = _directory_path(arguments)
        search_root = root / directory if directory else root
        if not search_root.exists() or not search_root.is_dir():
            return FileResolution("clarify", "没有找到目录：%s。请确认文件所在的文件夹。" % str(search_root))

        file_name = _optional_string(arguments, "file_name")
        if file_name:
            if search_root == root:
                directories = _child_directories(root)
                if directories:
                    return FileResolution(
                        "clarify",
                        "%s 范围太大，我不会直接扫描整盘。请告诉我 %s 在哪个目录下。" %
                        (str(root), Path(file_name).name),
                        search_root=str(root),
                        child_directories=directories,
                    )
                return FileResolution("clarify", "%s 范围太大，而且没有可用一级目录。请补充更具体的文件夹。" % str(root))
            return self._find_file(search_root, Path(file_name).name)

        if search_root == root:
            directories = _child_directories(root)
            if directories:
                return FileResolution(
                    "clarify",
                    "%s 范围太大，我不会直接扫描整盘。请告诉我要继续查哪个目录。" % str(root),
                    search_root=str(root),
                    child_directories=directories,
                )
            return FileResolution("clarify", "%s 范围太大，而且没有可用一级目录。请补充更具体的文件夹。" % str(root))
        return _resolve_existing_folder(search_root, _extensions(arguments))

    def _drive_root(self, drive: str) -> Path:
        value = drive.strip().upper().rstrip(":：盘")
        return self.drive_roots.get(value, Path(value + ":\\"))

    def _resolve_path(self, path: str, extensions: tuple[str, ...]) -> FileResolution:
        target = Path(path.replace("/", "\\"))
        if target.exists() and target.is_file():
            if target.suffix.lower() not in GIS_EXTENSIONS:
                return FileResolution("unsupported", "%s 不是当前支持的 GIS 数据文件。" % str(target))
            if target.suffix.lower() == ".gdb":
                return FileResolution("clarify", "%s 是文件地理数据库。请告诉我要打开其中哪个要素类。" % str(target))
            return FileResolution("resolved", "", path=str(target))
        if target.exists() and target.is_dir():
            if target.suffix.lower() == ".gdb":
                return FileResolution("clarify", "%s 是文件地理数据库。请告诉我要打开其中哪个要素类。" % str(target))
            return _resolve_existing_folder(target, extensions)
        if target.suffix.lower() in GIS_EXTENSIONS:
            return FileResolution("clarify", "没有找到这个文件：%s。请确认路径是否正确。" % str(target))
        return FileResolution("clarify", "没有找到目录：%s。请确认文件夹路径。" % str(target))

    def _find_file(self, search_root: Path, file_name: str) -> FileResolution:
        matches = _find_limited(search_root, file_name, self.max_seconds, self.max_results)
        if len(matches) == 1:
            if str(matches[0]).lower().endswith(".gdb"):
                return FileResolution("clarify", "%s 是文件地理数据库。请告诉我要打开其中哪个要素类。" % str(matches[0]))
            return FileResolution("resolved", "", path=str(matches[0]))
        if len(matches) > 1:
            return FileResolution(
                "clarify",
                "找到多个同名文件：%s。请说明要打开哪一个。" % "、".join([str(path) for path in matches[:8]]),
                candidates=[str(path) for path in matches]
            )
        directories = _child_directories(search_root)
        return FileResolution(
            "clarify",
            _not_found_summary(search_root, file_name, directories),
            search_root=str(search_root),
            child_directories=directories,
        )


def _optional_string(arguments: Dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _directory_path(arguments: Dict[str, Any]) -> Path:
    parts = arguments.get("directory_parts")
    if isinstance(parts, list):
        cleaned = [str(part).strip(" \t\\/") for part in parts if str(part).strip(" \t\\/")]
        return Path(*cleaned) if cleaned else Path()
    directory = _optional_string(arguments, "directory")
    if directory:
        return Path(directory.replace("/", "\\").strip(" \t\\/"))
    return Path()


def _extensions(arguments: Dict[str, Any]) -> tuple[str, ...]:
    values = arguments.get("extensions")
    if not isinstance(values, list) or not values:
        return GIS_EXTENSIONS
    extensions = []
    for value in values:
        item = str(value).strip().lower()
        if not item:
            continue
        if not item.startswith("."):
            item = "." + item
        if item in GIS_EXTENSIONS:
            extensions.append(item)
    return tuple(extensions) or GIS_EXTENSIONS


def _child_directories(root: Path) -> List[str]:
    try:
        return sorted([child.name for child in root.iterdir() if child.is_dir()])
    except OSError:
        return []


def _not_found_summary(search_root: Path, file_name: str, directories: List[str]) -> str:
    if directories:
        return "在 %s 下没有找到 %s。请告诉我继续查哪个目录。" % (str(search_root), file_name)
    return "在 %s 下没有找到 %s，而且这个目录下没有可继续选择的子目录。请确认文件名或换一个目录。" % (str(search_root), file_name)


def _folder_files(folder: Path, extensions: tuple[str, ...]) -> List[Path]:
    try:
        return sorted([
            child for child in folder.iterdir()
            if child.is_file() and child.suffix.lower() in extensions
        ], key=lambda path: path.name.lower())
    except OSError:
        return []


def _resolve_existing_folder(folder: Path, extensions: tuple[str, ...]) -> FileResolution:
    paths = _folder_files(folder, extensions)
    if not paths:
        directories = _child_directories(folder)
        return FileResolution(
            "clarify",
            _no_folder_files_summary(folder, extensions, directories),
            search_root=str(folder),
            child_directories=directories,
        )
    if len(paths) > 12:
        return FileResolution(
            "clarify",
            "%s 下找到 %s 个可添加数据，数量太多。请补充更具体的文件名或子目录。" % (str(folder), len(paths)),
            candidates=[str(path) for path in paths]
        )
    return FileResolution("resolved", "", paths=[str(path) for path in paths])


def _no_folder_files_summary(folder: Path, extensions: tuple[str, ...], directories: List[str]) -> str:
    label = "、".join(extensions)
    if directories:
        return "在 %s 下没有找到 %s 文件。请告诉我继续查哪个目录。" % (str(folder), label)
    return "在 %s 下没有找到 %s 文件，而且这个目录下没有可继续选择的子目录。请确认文件夹。" % (str(folder), label)


def _find_limited(root: Path, file_name: str, max_seconds: float, max_results: int) -> List[Path]:
    deadline = time.monotonic() + max_seconds
    matches: List[Path] = []
    target = file_name.lower()
    for current_root, directory_names, file_names in os.walk(str(root)):
        if time.monotonic() > deadline or len(matches) >= max_results:
            break
        for name in list(directory_names):
            if name.lower() == target:
                matches.append(Path(current_root) / name)
                if len(matches) >= max_results:
                    break
        directory_names[:] = [name for name in directory_names if not _skip_dir(name)]
        for name in file_names:
            if name.lower() == target:
                matches.append(Path(current_root) / name)
                if len(matches) >= max_results:
                    break
    return matches


def _skip_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered in ("$recycle.bin", "system volume information", "__pycache__") or lowered.endswith(".gdb")


def _resolved_file(path: str) -> Dict[str, str]:
    value = str(path)
    suffix = Path(value).suffix.lower()
    kind = {
        ".shp": "shapefile",
        ".lyr": "layer_file",
        ".tif": "raster",
        ".img": "raster",
        ".sde": "sde_connection",
        ".gdb": "file_geodatabase"
    }.get(suffix, "gis_file")
    layer_name = Path(value).stem
    return {"path": value, "layer_name": layer_name, "name": layer_name, "kind": kind}
