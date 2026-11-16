"""Wait for the running Windows desktop and activate a verified release on readiness."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

# Embedded Python is invoked in isolated mode from a directory outside the release.
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent))
from install_state import absolute, active, contained, descriptor, desktop_command, lock, write_pointer


def wait_for_exit(pid):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return
        raise OSError("无法确认原程序已退出")
    try:
        if kernel.WaitForSingleObject(handle, 45_000) != 0:
            raise TimeoutError("原程序未退出，更新未切换版本")
    finally:
        kernel.CloseHandle(handle)


def result(profile, root, phase, message=""):
    target = contained(profile, "update-result.json")
    temp = contained(profile, "update-result-" + uuid4().hex + ".pending")
    try:
        temp.write_text(json.dumps({"root": str(root), "phase": phase, "message": message}, ensure_ascii=False), encoding="utf-8")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def start(release, profile, components, ready=None):
    command = desktop_command(release, profile, set(components))
    if ready:
        command += ["--update-ready", str(ready)]
    return subprocess.Popen(command, cwd=release, close_fds=True, creationflags=subprocess.DETACHED_PROCESS,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def apply(root, profile, pid, source, target):
    if profile.is_relative_to(root) or root.is_relative_to(profile):
        raise ValueError("用户设置应与安装目录分开")
    initial = active(root)
    if not initial or initial["current"]["release"] != source or source == target:
        raise ValueError("安装已变化，请重新检查更新")
    old, _ = descriptor(root, initial["current"])
    candidate = {"release": target, "components": []}
    new, version = descriptor(root, candidate)
    ready = contained(profile, "update-ready-" + uuid4().hex + ".json")
    child = None
    exited = False
    try:
        wait_for_exit(pid)
        exited = True
        with lock(root):
            if active(root) != initial:
                raise ValueError("安装已变化，请重新检查更新")
            child = start(new, profile, [], ready)
            deadline = time.monotonic() + 50
            while not ready.is_file() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.15)
            if not ready.is_file() or child.poll() is not None:
                raise RuntimeError("新版启动检查未通过，已恢复上一版")
            probe = json.loads(ready.read_text(encoding="utf-8"))
            if probe != {"version": version, "profile": str(profile)}:
                raise RuntimeError("新版启动身份不匹配，已恢复上一版")
            write_pointer(root, {"current": candidate, "previous": initial["current"]})
        result(profile, root, "complete")
    except (OSError, ValueError, RuntimeError) as issue:
        if child and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        result(profile, root, "failed", str(issue))
        if exited and active(root) == initial:
            start(old, profile, initial["current"]["components"])
        return 1
    finally:
        ready.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    install, data, parent, current, next_release = sys.argv[1:]
    raise SystemExit(apply(absolute(install), absolute(data), int(parent), current, next_release))
