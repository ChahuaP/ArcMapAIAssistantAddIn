
"""Manage immutable source releases and atomically switch new launches."""
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from guizhi.application.diagnostics import contained, save
from guizhi.infrastructure.vector import digest
from guizhi.infrastructure.workspace import SOURCE_ROOT, supervisor_lock

ROOT_FILES = {".gitattributes", ".gitignore", "LICENSE", "VERSION", "requirements.txt", "requirements.lock"}
ROOT_DIRS = {"src", "apps", "resources", "packaging", "operation_catalog", "shared_runtime", "arcmap_runtime_py2",
             "ArcMapAIAssistantAddIn", "ArcMapBridgeExternal"}
BANNED_DIRS = {".git", "test", "tests", "fixtures", "docs", "quality", "build", "bin", "obj", "out", "dist",
               "node_modules", "__pycache__"}
BANNED_SUFFIXES = {".pyc", ".pyo", ".exe", ".dll", ".pdb", ".log", ".db", ".esriaddin"}
VERSION_PATTERN = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"


def registry_root(value):
    path = Path(value)
    root = path.resolve()
    if not path.is_absolute() or path != root or root == Path(root.anchor) or root.is_relative_to(SOURCE_ROOT):
        raise ValueError("Use an absolute external release registry without redirects")
    return root


def source_name(name):
    parts = PurePosixPath(name).parts
    file = parts[-1]
    if ((len(parts) == 1 and file not in ROOT_FILES) or (len(parts) > 1 and parts[0] not in ROOT_DIRS) or
            any(part.casefold() in BANNED_DIRS for part in parts[:-1]) or
            PurePosixPath(file).suffix.lower() in BANNED_SUFFIXES or file.lower().startswith(("readme", "test_")) or
            (file.lower().endswith(".md") and parts[:2] != ("resources", "skills"))):
        raise ValueError("Release archive contains non-production source")


def extract(archive, destination, *, source=False):
    destination.mkdir(parents=True, exist_ok=False)
    seen = set()
    with ZipFile(archive) as zipped:
        entries = zipped.infolist()
        if len(entries) > 2500 or sum(item.file_size for item in entries) > 160 << 20:
            raise ValueError("Archive exceeds the release size budget")
        for item in entries:
            name = item.filename.rstrip("/")
            parts = name.split("/")
            if (not name or any(not part or part in {".", ".."} or part.endswith((".", " ")) or
                                re.search(r'[<>:"\\|?*\x00-\x1f]', part) or Path(part).is_reserved()
                                for part in parts) or ((item.external_attr >> 16) & 0o170000) == 0o120000):
                raise ValueError("Archive contains a redirected or unsafe path")
            if name.casefold() in seen:
                raise ValueError("Archive contains duplicate names")
            seen.add(name.casefold())
            if item.is_dir():
                continue
            if source:
                source_name(name)
            file = destination.joinpath(*parts)
            file.parent.mkdir(parents=True, exist_ok=True)
            with zipped.open(item) as stream, file.open("xb") as target:
                shutil.copyfileobj(stream, target)


def manifest(root, *, source=False):
    files = {}
    for file in sorted(root.rglob("*")):
        if file.resolve() != file:
            raise ValueError("Release files must not redirect")
        if file.is_file():
            name = file.relative_to(root).as_posix()
            if source:
                source_name(name)
            files[name] = digest(file)
    return files


def pack(root, path, files):
    with ZipFile(path, "x", ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(root / name, name)
    with ZipFile(path) as archive:
        from hashlib import file_digest

        if archive.namelist() != list(files):
            raise ValueError("Candidate package file list differs")
        for name, expected in files.items():
            with archive.open(name) as stream:
                if file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("Candidate package checksum mismatch")


def version_directory(root, version):
    if not re.fullmatch(VERSION_PATTERN, version):
        raise ValueError("Invalid release version")
    return contained(root, "versions", version)


def check_release(directory, expected):
    metadata = contained(directory, "release.json")
    if digest(metadata) != expected:
        raise ValueError("Release metadata checksum mismatch")
    record = json.loads(metadata.read_text(encoding="utf-8"))
    source = contained(directory, "source")
    package = contained(directory, "source.zip")
    if ((source / "VERSION").read_text(encoding="ascii").strip() != record["version"] or
            digest(package) != record["package_sha256"] or manifest(source, source=True) != record["files"]):
        raise ValueError("Release content differs from its verified manifest")
    return record


def active(root):
    root = registry_root(root)
    pointer = json.loads(contained(root, "active.json").read_text(encoding="utf-8"))
    location = version_directory(root, pointer["version"])
    record = check_release(location, pointer["manifest_sha256"])
    if record["version"] != pointer["version"]:
        raise ValueError("Active release version mismatch")
    return {**pointer, "source": str(location / "source"), "package": str(location / "source.zip")}


def publish(root, staged, version, previous):
    pointer_file = contained(root, "active.json")
    lock = contained(root, "registry.lock")
    with lock.open("ab") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
    with supervisor_lock(lock) as acquired:
        if not acquired:
            raise ValueError("Another release is being installed")
        current = active(root) if pointer_file.is_file() else None
        if current != previous:
            raise ValueError("Active version changed during maintenance")
        expected = digest(staged / "release.json")
        check_release(staged, expected)
        final = version_directory(root, version)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            raise ValueError("Target release version already exists")
        staged.rename(final)
        check_release(final, expected)
        pointer = {"version": version, "manifest_sha256": expected,
                   "previous": None if current is None else
                   {key: current[key] for key in ("version", "manifest_sha256")}}
        save(pointer_file, pointer)
    return active(root)


def stage(root):
    parent = contained(root, "staging")
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / uuid4().hex
    directory.mkdir()
    return directory
