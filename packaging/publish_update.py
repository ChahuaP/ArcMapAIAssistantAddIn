"""Prepare an immutable signed update from the installer stage and publish it atomically."""
import argparse
import base64
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile

HASH = re.compile(r"[0-9a-f]{64}\Z")
BUILD = re.compile(r"\d+\.\d+\.\d+-[a-f0-9]{16}\Z")


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def sign(key, contents):
    with NamedTemporaryFile(delete=False) as source:
        source.write(contents)
    path = Path(source.name)
    try:
        return subprocess.check_output(["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", str(key), "-in", str(path)])
    finally:
        path.unlink()


def prepare(stage, archive, key, existing):
    payload = (stage / "payload").resolve(strict=True)
    install = json.loads((payload / "installation.json").read_text(encoding="utf-8"))
    hashes = install["files"]
    sizes, unique = {}, {}
    for name, expected in hashes.items():
        file = payload.joinpath(*name.split("/"))
        if digest(file) != expected:
            raise ValueError(f"Staged payload changed: {name}")
        sizes[name] = file.stat().st_size
        unique.setdefault(expected, file)
    manifest = encode({"schema": 1, "install": install, "sizes": sizes})
    data = encode({"schema": 1, "platform": "win32-x64", "version": install["version"], "build": install["build"],
                   "manifest_sha256": sha256(manifest).hexdigest()})
    pointer = encode({"payload": base64.b64encode(data).decode(), "signature": base64.b64encode(sign(key, data)).decode()})
    included = sorted(set(unique) - existing)
    temporary = archive.with_suffix(".pending")
    try:
        with ZipFile(temporary, "x", ZIP_DEFLATED, compresslevel=3) as package:
            package.writestr("latest.json", pointer)
            package.writestr("manifest.json", manifest)
            for checksum in included:
                package.write(unique[checksum], "objects/" + checksum)
        temporary.rename(archive)
    finally:
        temporary.unlink(missing_ok=True)
    return {"build": install["build"], "objects": len(included), "archive_bytes": archive.stat().st_size}


def pointer_data(pointer, manifest):
    value = json.loads(base64.b64decode(json.loads(pointer)["payload"], validate=True))
    if not BUILD.fullmatch(value["build"]) or value["manifest_sha256"] != sha256(manifest).hexdigest():
        raise ValueError("Manifest and release pointer differ")
    return value


def write_new(path, data):
    with path.open("xb") as file:
        file.write(data)
    path.chmod(0o644)


def stage_release(archive, public, pending):
    if pending.exists():
        raise ValueError("Release pending pointer already exists")
    objects = public / "objects"
    manifests = public / "manifests"
    for folder in (public, objects, manifests):
        folder.mkdir(mode=0o755, exist_ok=True)
    with ZipFile(archive) as package, TemporaryDirectory(prefix=".verify-", dir=pending.parent) as scratch:
        pointer, manifest = package.read("latest.json"), package.read("manifest.json")
        value = pointer_data(pointer, manifest)
        expected = set(json.loads(manifest)["install"]["files"].values())
        if any(not HASH.fullmatch(item) for item in expected):
            raise ValueError("Invalid object name")
        for item in package.infolist():
            if item.filename in {"latest.json", "manifest.json"}:
                continue
            prefix, sep, checksum = item.filename.partition("/")
            if prefix != "objects" or not sep or checksum not in expected:
                raise ValueError("Unexpected object")
            file = Path(scratch) / checksum
            with package.open(item) as incoming, file.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
            if digest(file) != checksum:
                raise ValueError(f"Object failed checksum verification: {checksum}")
            target = objects / checksum
            if target.exists():
                if digest(target) != checksum:
                    raise ValueError(f"Published object differs: {checksum}")
            else:
                file.chmod(0o644)
                file.rename(target)
        for checksum in expected:
            file = objects / checksum
            if not file.is_file() or digest(file) != checksum:
                raise ValueError(f"Missing or damaged object: {checksum}")
        destination = manifests / (value["build"] + ".json")
        if destination.exists():
            if destination.read_bytes() != manifest:
                raise ValueError("Cannot overwrite an immutable release manifest")
        else:
            write_new(destination, manifest)
        write_new(pending, pointer)
    return value


def activate(public, pending):
    pointer = json.loads(pending.read_bytes())
    build = json.loads(base64.b64decode(pointer["payload"], validate=True))["build"]
    if not BUILD.fullmatch(build):
        raise ValueError("Invalid release build")
    value = pointer_data(pending.read_bytes(), (public / "manifests" / (build + ".json")).read_bytes())
    latest = public / "latest.json"
    if latest.exists():
        current = json.loads(base64.b64decode(json.loads(latest.read_bytes())["payload"], validate=True))
        number = lambda item: tuple(map(int, item["version"].split(".")))
        if number(current) >= number(value) and current != value:
            raise ValueError("The update channel cannot move backwards or replace an existing release")
    os.replace(pending, latest)
    return value


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="operation", required=True)
    generate = commands.add_parser("prepare")
    generate.add_argument("stage", type=Path)
    generate.add_argument("archive", type=Path)
    generate.add_argument("key", type=Path)
    generate.add_argument("--existing", type=Path)
    verify = commands.add_parser("stage")
    verify.add_argument("archive", type=Path)
    verify.add_argument("public", type=Path)
    verify.add_argument("pending", type=Path)
    publish = commands.add_parser("activate")
    publish.add_argument("public", type=Path)
    publish.add_argument("pending", type=Path)
    args = parser.parse_args()
    if args.operation == "prepare":
        known = set(args.existing.read_text(encoding="ascii").splitlines()) if args.existing else set()
        result = prepare(args.stage, args.archive, args.key, known)
    elif args.operation == "stage":
        result = stage_release(args.archive, args.public, args.pending)
    else:
        result = activate(args.public, args.pending)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
