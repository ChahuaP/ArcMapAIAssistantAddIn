
"""Verify staged desktop releases and select an installed version atomically."""
import argparse
from contextlib import contextmanager
from hashlib import file_digest
import json
import msvcrt
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

VERSION = r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
RELEASE = r"[a-z0-9][a-z0-9.-]{0,90}"
OPTIONAL = {"speech", "arcmap"}
REQUIRED = (
    "core/VERSION", "core/src/guizhi/__main__.py", "core/apps/desktop/entry.cjs",
    "runtime/python/python.exe", "runtime/node/node.exe",
    "runtime/dsh/node_modules/@deepseek-ai/dsh/package.json",
    "runtime/dsh/node_modules/@deepseek-ai/dsh-sdk-client/package.json",
    "renderer/node_modules/electron/dist/electron.exe", "renderer/node_modules/react/umd/react.production.min.js",
    "renderer/node_modules/react-dom/umd/react-dom.production.min.js",
)


def checksum(path):
    with path.open("rb") as file:
        return file_digest(file, "sha256").hexdigest()


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or path != path.resolve() or path == Path(path.anchor):
        raise ValueError("请选择独立的本地安装目录")
    return path


def contained(root, *parts):
    path = root.joinpath(*parts)
    if path != path.resolve():
        raise ValueError("安装目录中存在重定向路径")
    return path


def files(root):
    # Dependency files can exceed MAX_PATH even under the default install directory.
    # Use Windows extended-length paths without relying on a machine-wide policy.
    path = str(root)
    native_root = Path("\\\\?\\UNC\\" + path[2:] if root.drive.startswith("\\\\") else "\\\\?\\" + path)
    for base, dirs, names in os.walk(native_root, followlinks=False):
        here = Path(base)
        for name in dirs + names:
            path = here / name
            if path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError(f"安装资源包含重定向路径 {path.relative_to(native_root)}")
        for name in names:
            path = here / name
            if not path.is_file():
                raise ValueError("安装资源包含非普通文件")
            yield path.relative_to(native_root).as_posix(), path


def write_pointer(root, value):
    file = contained(root, "active.json")
    pending = file.with_name("active-" + uuid4().hex + ".pending")
    try:
        with pending.open("x", encoding="ascii") as stream:
            json.dump(value, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(file)
    finally:
        pending.unlink(missing_ok=True)


@contextmanager
def lock(root):
    file = contained(root, "install.lock")
    with file.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def descriptor(root, entry, *, verify_all=False):
    release = entry["release"]
    if not re.fullmatch(RELEASE, release):
        raise ValueError("安装版本标识无效")
    candidate = contained(root, "releases", release)
    file = contained(candidate, "installation.json")
    if checksum(file) != entry["manifest"]:
        raise ValueError("安装清单校验失败")
    manifest = json.loads(file.read_text(encoding="utf-8"))
    selected = set(entry["components"])
    available = set(manifest["components"])
    if (manifest["schema"] != 1 or not selected <= available <= OPTIONAL or
            not re.fullmatch(VERSION, manifest["version"]) or
            not release.startswith(manifest["build"] + "-")):
        raise ValueError("安装版本与组件清单不匹配")
    expected = {key: value for key, value in manifest["files"].items()
                if key.split("/", 1)[0] not in OPTIONAL - selected}
    check = set(REQUIRED)
    if "speech" in selected:
        check.add("speech/identity.json")
    if "arcmap" in selected:
        check.add("arcmap/ArcMapBridge.exe")
    if not check <= expected.keys():
        raise ValueError("安装组件不完整")
    if verify_all:
        for name, path in files(candidate):
            if name == "installation.json":
                continue
            if expected.pop(name, None) != checksum(path):
                raise ValueError(f"安装文件校验失败 {name}")
        if expected:
            raise ValueError("安装资源缺失")
    else:
        for name in check:
            if checksum(contained(candidate, *name.split("/"))) != expected[name]:
                raise ValueError(f"安装文件校验失败 {name}")
    if (candidate / "core/VERSION").read_text(encoding="ascii").strip() != manifest["version"]:
        raise ValueError("应用版本与安装清单不一致")
    return candidate, manifest["version"]


def active(root):
    file = contained(root, "active.json")
    return json.loads(file.read_text(encoding="ascii")) if file.is_file() else None


def desktop_command(candidate, user_data, components):
    desktop = candidate / "renderer"
    command = [str(desktop / "node_modules/electron/dist/electron.exe"),
               str(candidate / "core/apps/desktop/entry.cjs"),
               "--runtime", str(candidate / "runtime"), "--renderer-runtime", str(desktop),
               "--user-data", str(user_data), "--installation-root", str(candidate.parent.parent)]
    if "speech" in components:
        command += ["--bundled-speech", str(candidate / "speech")]
    if "arcmap" in components:
        command += ["--bundled-arcmap", str(candidate / "arcmap/ArcMapBridge.exe")]
    return command


def verify_start(candidate, version, components):
    with TemporaryDirectory(prefix="guizhi-install-") as location:
        temporary = Path(location)
        request = temporary / "health.json"
        request.write_text('{"operation":"system.health"}', encoding="ascii")
        python = candidate / "runtime/python/python.exe"
        main = candidate / "core/src/guizhi/__main__.py"
        check = subprocess.run([str(python), "-I", "-B", str(main), "--version"],
                               capture_output=True, timeout=20, check=True)
        if check.stdout.decode("ascii").strip() != version:
            raise ValueError("GIS 运行版本检查失败")
        health = subprocess.run([str(python), "-I", "-B", str(main), "--workspace", str(temporary / "workspace"),
                                 "execute", str(request)], capture_output=True, timeout=40, check=True)
        if json.loads(health.stdout)["result"]["health"]["mode"] != "independent":
            raise ValueError("本地 GIS 启动检查失败")
        receipt = temporary / "startup.json"
        command = desktop_command(candidate, temporary / "profile", components)
        subprocess.run(command + ["--verify-startup", str(receipt)], capture_output=True, timeout=40, check=True)
        if json.loads(receipt.read_text(encoding="utf-8")) != {"workbench": True, "portrait": True}:
            raise ValueError("桌面启动检查失败")


def activate(root, entry):
    with lock(root):
        previous = active(root)
        candidate, version = descriptor(root, entry, verify_all=True)
        if previous:
            _, prior_version = descriptor(root, previous["current"])
            if tuple(map(int, version.split("."))) < tuple(map(int, prior_version.split("."))):
                raise ValueError("请使用恢复入口切换到上一版本")
        verify_start(candidate, version, set(entry["components"]))
        if not previous or previous["current"] != entry:
            write_pointer(root, {"current": entry, "previous": previous["current"] if previous else None})
    return version


def restore(root):
    with lock(root):
        state = active(root)
        if not state or not state["previous"]:
            raise ValueError("未找到上一版安装")
        previous = state["previous"]
        candidate, version = descriptor(root, previous, verify_all=True)
        verify_start(candidate, version, set(previous["components"]))
        write_pointer(root, {"current": previous, "previous": None})
    return version


def launch(root, user_data):
    state = active(root)
    if not state:
        raise ValueError("未找到已验证的规智安装")
    if user_data.is_relative_to(root) or root.is_relative_to(user_data):
        raise ValueError("用户设置应与安装目录分开")
    entry = state["current"]
    candidate, _ = descriptor(root, entry)
    subprocess.Popen(desktop_command(candidate, user_data, set(entry["components"])),
                     cwd=candidate, close_fds=True, creationflags=subprocess.DETACHED_PROCESS,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("activate")
    setup.add_argument("--release", required=True)
    setup.add_argument("--manifest", required=True)
    setup.add_argument("--component", choices=sorted(OPTIONAL), action="append", default=[])
    run = commands.add_parser("launch")
    run.add_argument("--user-data", type=Path, required=True)
    commands.add_parser("restore")
    args = parser.parse_args()
    try:
        root = absolute(args.root)
        if args.command == "activate":
            activate(root, {"release": args.release, "manifest": args.manifest, "components": sorted(set(args.component))})
        elif args.command == "restore":
            restore(root)
        else:
            launch(root, absolute(args.user_data))
        return 0
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            message += "\n" + exc.stderr.decode("utf-8", "replace")[-1500:]
        if args.command == "launch":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "规智", 0x10)
        elif args.command == "activate":
            error = args.root / "last-install-error.txt"
            if args.root.is_dir() and error.resolve() == error and args.root.resolve() == args.root:
                error.write_text(message, encoding="utf-8")
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
