from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List


GIS_EXTENSIONS = (".shp", ".lyr", ".tif", ".img", ".sde", ".gdb")
SHAPEFILE_EXTENSIONS = (".shp",)
FILE_RE = re.compile(r"([^\s，。；;、]+?\.(?:shp|lyr|tif|img|sde|gdb))", re.IGNORECASE)
FULL_PATH_RE = re.compile(r"([A-Za-z]:[\\/][^\n，。；;]+?\.(?:shp|lyr|tif|img|sde|gdb))", re.IGNORECASE)
DRIVE_RE = re.compile(r"([A-Za-z])\s*(?:[:：]|盘)")
DRIVE_PATH_RE = re.compile(r"([A-Za-z]:[\\/][^\n，。；;]*)")


class FileResolution:
    def __init__(
        self,
        status: str,
        summary: str,
        path: str | None = None,
        paths: List[str] | None = None,
        candidates: List[str] | None = None,
        clean_text: str = ""
    ):
        self.status = status
        self.summary = summary
        self.path = path
        self.paths = paths or ([path] if path else [])
        self.candidates = candidates or []
        self.clean_text = clean_text
        self.files = [_resolved_file(path) for path in self.paths]

    def workflow(self) -> Dict[str, Any]:
        if self.status == "resolved" and self.paths:
            if len(self.paths) > 1:
                return {
                    "action": "execute",
                    "summary": "将添加 %s 个本地数据：%s。" % (
                        len(self.paths),
                        "、".join([Path(path).name for path in self.paths])
                    ),
                    "steps": [
                        {
                            "id": "step_%s" % index,
                            "operation": "layer.add_layer",
                            "arguments": {"path": path},
                            "reason": "添加用户指定文件夹中的本地 GIS 数据"
                        }
                        for index, path in enumerate(self.paths, start=1)
                    ]
                }
            return {
                "action": "execute",
                "summary": "将添加本地数据：%s。" % self.paths[0],
                "steps": [
                    {
                        "id": "step_1",
                        "operation": "layer.add_layer",
                        "arguments": {"path": self.paths[0]},
                        "reason": "添加用户指定的本地 GIS 数据"
                    }
                ]
            }
        return {"action": "clarify", "summary": self.summary, "steps": []}


class ParsedCommand:
    def __init__(self, original: str, clean_text: str, file_resolution: FileResolution | None):
        self.original = original
        self.clean_text = clean_text
        self.file_resolution = file_resolution


class FileResolver:
    def __init__(self, max_seconds: float = 2.0, max_results: int = 20, drive_roots: Dict[str, Path] | None = None):
        self.max_seconds = max_seconds
        self.max_results = max_results
        self.drive_roots = {key.upper(): Path(value) for key, value in (drive_roots or {}).items()}

    def resolve_command(self, command: str) -> FileResolution | None:
        return self.parse_command(command).file_resolution

    def parse_command(self, command: str) -> ParsedCommand:
        resolution = self._resolve_command(command)
        clean_text = _clean_after_file_resolution(command, resolution)
        if resolution is not None:
            resolution.clean_text = clean_text
        return ParsedCommand(command, clean_text, resolution)

    def _resolve_command(self, command: str) -> FileResolution | None:
        if _looks_like_add_file(command):
            return self._resolve_file_request(command)
        if _looks_like_add_folder(command):
            return self._resolve_folder_request(command)
        return None

    def _resolve_file_request(self, command: str) -> FileResolution | None:
        base_command, supplement = _split_supplement(command)
        file_name = _file_name(supplement) or _file_name(base_command) or _file_name(command)
        if not file_name:
            return None

        full_path = _full_path(supplement) or _full_path(base_command) or _full_path(command)
        if full_path:
            path = _normalize_path(full_path)
            if _exists(path):
                if path.lower().endswith(".gdb"):
                    return FileResolution("clarify", "%s 是文件地理数据库。请告诉我要打开其中哪个要素类。" % path)
                return FileResolution("resolved", "", path=path)
            return FileResolution("clarify", "没有找到这个文件：%s。请确认路径是否正确。" % path)

        drive = _drive_letter(supplement) or _drive_letter(base_command) or _drive_letter(command)
        root = self._drive_root(drive) if drive else None
        if root is None:
            return FileResolution("clarify", "请告诉我这个文件在哪个盘或哪个文件夹里，例如 D 盘 Data 文件夹。")
        if not root.exists():
            return FileResolution("clarify", "%s 不存在。请确认磁盘或目录。" % str(root))

        fragment_source = supplement or base_command
        fragments = _directory_fragments(fragment_source, root, file_name)
        if not fragments:
            directories = _child_directories(root)
            if directories:
                return FileResolution(
                    "clarify",
                    "%s 范围太大，我不会直接扫描整盘。一级目录有：%s。请告诉我 %s 在哪个目录下。" %
                    (str(root), "、".join(directories[:12]), file_name)
                )
            return FileResolution("clarify", "%s 范围太大，而且没有可用一级目录。请补充更具体的文件夹。" % str(root))

        search_root = _join_fragments(root, fragments)
        if not search_root.exists() or not search_root.is_dir():
            return FileResolution("clarify", "没有找到目录：%s。请确认 %s 所在的文件夹。" % (str(search_root), file_name))

        matches = _find_limited(search_root, file_name, self.max_seconds, self.max_results)
        if len(matches) == 1:
            if str(matches[0]).lower().endswith(".gdb"):
                return FileResolution("clarify", "%s 是文件地理数据库。请告诉我要打开其中哪个要素类。" % str(matches[0]))
            return FileResolution("resolved", "", path=str(matches[0]))
        if len(matches) > 1:
            return FileResolution("clarify", "找到多个同名文件：%s。请说明要打开哪一个。" % "、".join([str(path) for path in matches[:8]]), candidates=[str(path) for path in matches])
        return FileResolution("clarify", _not_found_summary(search_root, file_name))

    def _drive_root(self, drive: str) -> Path:
        return self.drive_roots.get(drive.upper(), Path(drive.upper() + ":\\"))

    def _resolve_folder_request(self, command: str) -> FileResolution | None:
        if not _looks_like_add_folder(command):
            return None
        base_command, supplement = _split_supplement(command)
        folder = _folder_path(supplement) or _folder_path(base_command) or _folder_path(command)
        if folder is None:
            drive = _drive_letter(supplement) or _drive_letter(base_command) or _drive_letter(command)
            root = self._drive_root(drive) if drive else None
            if root is None:
                return None
            if not root.exists():
                return FileResolution("clarify", "%s 不存在。请确认磁盘或目录。" % str(root))
            fragments = _directory_fragments(supplement or base_command, root)
            if not fragments:
                directories = _child_directories(root)
                if directories:
                    return FileResolution(
                        "clarify",
                        "%s 范围太大，我不会直接扫描整盘。一级目录有：%s。请告诉我要继续查哪个目录。" %
                        (str(root), "、".join(directories[:12]))
                    )
                return FileResolution("clarify", "%s 范围太大，而且没有可用一级目录。请补充更具体的文件夹。" % str(root))
            folder = _join_fragments(root, fragments)

        if not folder.exists() or not folder.is_dir():
            return FileResolution("clarify", "没有找到目录：%s。请确认文件夹路径。" % str(folder))

        extensions = _requested_extensions(command)
        paths = _folder_files(folder, extensions)
        if not paths:
            return FileResolution("clarify", _no_folder_files_summary(folder, extensions))
        if len(paths) > 12:
            return FileResolution(
                "clarify",
                "%s 下找到 %s 个可添加数据，数量太多。请补充更具体的文件名或子目录。" % (str(folder), len(paths)),
                candidates=[str(path) for path in paths]
            )
        return FileResolution("resolved", "", paths=[str(path) for path in paths])


def _looks_like_add_file(command: str) -> bool:
    text = command or ""
    if not FILE_RE.search(text):
        return False
    return any(word in text for word in ("打开", "添加", "加载", "导入", "放到地图", "加到地图"))


def _looks_like_add_folder(command: str) -> bool:
    text = command or ""
    if not any(word in text for word in ("打开", "添加", "加载", "导入", "放到地图", "加到地图")):
        return False
    if not any(word in text.lower() for word in ("shp", "shapefile", "shape")):
        return False
    return bool(DRIVE_PATH_RE.search(text) or DRIVE_RE.search(text))


def _file_name(command: str) -> str | None:
    matches = FILE_RE.findall(command or "")
    if not matches:
        return None
    value = matches[-1].strip().strip("\"'")
    for separator in ("的", "下"):
        if separator in value and not re.match(r"^[A-Za-z]:[\\/]", value):
            value = value.rsplit(separator, 1)[-1]
    return Path(value).name


def _full_path(command: str) -> str | None:
    match = FULL_PATH_RE.search(command or "")
    if not match:
        return None
    return match.group(1).strip().strip("\"'")


def _drive_letter(command: str) -> str | None:
    match = DRIVE_RE.search(command or "")
    if not match:
        return None
    return match.group(1).upper()


def _directory_fragments(command: str, root: Path, file_name: str = "") -> List[str]:
    text = command
    if file_name:
        text = text.replace(file_name, " ")
    text = DRIVE_RE.sub(" ", text)
    for word in (
        "用户补充", "补充", "帮我", "请", "打开", "添加", "加载", "导入",
        "文件夹", "目录", "下面", "下", "里的", "里面", "就在", "就是",
        "在", "是", "的", "文件", "两个", "2个", "所有", "全部", "都",
        "shapefile", "shape", "shp"
    ):
        text = text.replace(word, " ")
    parts = []
    for raw in re.split(r"[\s\\/]+", text):
        item = raw.strip(" ，。；;、\"'")
        if item and item not in (root.drive,):
            parts.append(item)
    return parts


def _split_supplement(command: str) -> tuple[str, str]:
    text = command or ""
    marker = "用户补充："
    if marker in text:
        before, after = text.rsplit(marker, 1)
        return before.strip(), after.strip()
    marker = "用户补充:"
    if marker in text:
        before, after = text.rsplit(marker, 1)
        return before.strip(), after.strip()
    return text, ""


def _join_fragments(root: Path, fragments: List[str]) -> Path:
    path = root
    for fragment in fragments:
        path = path / fragment
    return path


def _child_directories(root: Path) -> List[str]:
    try:
        return sorted([child.name for child in root.iterdir() if child.is_dir()])
    except OSError:
        return []


def _not_found_summary(search_root: Path, file_name: str) -> str:
    directories = _child_directories(search_root)
    if directories:
        return "在 %s 下没有找到 %s。下一层目录有：%s。请告诉我继续查哪个目录。" % (
            str(search_root),
            file_name,
            "、".join(directories[:12])
        )
    return "在 %s 下没有找到 %s，而且这个目录下没有可继续选择的子目录。请确认文件名或换一个目录。" % (str(search_root), file_name)


def _folder_path(command: str) -> Path | None:
    for match in DRIVE_PATH_RE.finditer(command or ""):
        raw = match.group(1).strip().strip("\"'")
        candidate = _folder_candidate(raw)
        if candidate:
            return Path(candidate)
    return None


def _folder_candidate(raw: str) -> str:
    cleaned = raw.replace("/", "\\")
    markers = (
        "文件夹下", "目录下", "下的", "下面", "下", "里的", "里面", "中", "内",
        "所有", "全部", "两个", "2个", "图层", "文件"
    )
    cut = len(cleaned)
    for marker in markers:
        index = cleaned.lower().find(marker)
        if index > 0:
            cut = min(cut, index)
    return cleaned[:cut].strip(" \t\\/")


def _requested_extensions(command: str) -> tuple[str, ...]:
    lowered = (command or "").lower()
    if any(word in lowered for word in ("shp", "shapefile", "shape")):
        return SHAPEFILE_EXTENSIONS
    return GIS_EXTENSIONS


def _folder_files(folder: Path, extensions: tuple[str, ...]) -> List[Path]:
    try:
        return sorted([
            child for child in folder.iterdir()
            if child.is_file() and child.suffix.lower() in extensions
        ], key=lambda path: path.name.lower())
    except OSError:
        return []


def _no_folder_files_summary(folder: Path, extensions: tuple[str, ...]) -> str:
    directories = _child_directories(folder)
    label = "、".join(extensions)
    if directories:
        return "在 %s 下没有找到 %s 文件。下一层目录有：%s。请告诉我继续查哪个目录。" % (
            str(folder),
            label,
            "、".join(directories[:12])
        )
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


def _normalize_path(path: str) -> str:
    return str(Path(path.replace("/", "\\")))


def _exists(path: str) -> bool:
    return Path(path).exists()


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
    return {"path": value, "name": Path(value).stem, "kind": kind}


def _clean_after_file_resolution(command: str, resolution: FileResolution | None) -> str:
    if resolution is None:
        return command or ""
    text = command or ""
    replacements = _resolution_path_replacements(resolution)
    for value in sorted(replacements, key=len, reverse=True):
        text = _replace_path_variant(text, value)
    text = _remove_file_request_words(text)
    return _normalize_clean_text(text)


def _resolution_path_replacements(resolution: FileResolution) -> List[str]:
    values = set()
    for path in resolution.paths:
        value = str(path)
        values.add(value)
        parent = str(Path(value).parent)
        if parent and parent != ".":
            values.add(parent)
    if len(resolution.paths) > 1:
        try:
            common = os.path.commonpath(resolution.paths)
            if common and common not in resolution.paths:
                values.add(common)
        except ValueError:
            pass
    return [value for value in values if value]


def _replace_path_variant(text: str, path: str) -> str:
    return text.replace(path, " ").replace(path.replace("\\", "/"), " ")


def _remove_file_request_words(text: str) -> str:
    phrases = (
        "文件夹下", "目录下", "下所有shapefile", "下所有shape", "下所有shp",
        "下全部shapefile", "下全部shape", "下全部shp", "下两个shapefile",
        "下两个shape", "下两个shp", "下2个shapefile", "下2个shape", "下2个shp",
        "下的", "下面", "里的", "里面", "文件夹", "目录",
        "所有shapefile", "所有shape", "所有shp", "全部shapefile", "全部shape",
        "全部shp", "两个shapefile", "两个shape", "两个shp", "2个shapefile",
        "2个shape", "2个shp", "shapefile", "shape", "shp", "文件", "图层",
        "所有", "全部", "两个", "2个", "下"
    )
    result = text
    for phrase in phrases:
        result = result.replace(phrase, " ")
    return result


def _normalize_clean_text(text: str) -> str:
    value = re.sub(r"\s+", " ", text or "")
    value = re.sub(r"\s*([，,、。；;])\s*", r"\1", value)
    value = re.sub(r"[，,、。；;]{2,}", "，", value)
    return value.strip(" ，,、。；;\t\r\n")
